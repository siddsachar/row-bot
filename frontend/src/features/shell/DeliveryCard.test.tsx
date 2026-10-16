import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { DeliveryView } from '../../api/types';
import { DeliveryCard } from './DeliveryCard';
import { cardOf } from './TranscriptCards';
import { DeliveryIoContext, type DeliveryIo } from './deliveries';
import { stepVerb } from './tool-activity';

const saving = vi.hoisted(() => ({
  result: { status: 'ok', value: null } as
    { status: 'ok'; value: null } | { status: 'unavailable'; reason: string },
  names: [] as string[],
}));
vi.mock('../../platform/download', () => ({
  saveBrowserDownload: async (load: () => Promise<Blob>, name: string) => {
    saving.names.push(name);
    if (saving.result.status !== 'ok') return saving.result;
    try {
      await load();
      return saving.result;
    } catch {
      return { status: 'unavailable', reason: 'operation_failed' };
    }
  },
}));

const DAY = 24 * 60 * 60 * 1000;
const MB = 1024 * 1024;

function view(patch: Partial<DeliveryView> = {}): DeliveryView {
  return {
    delivery_id: 'send-1a2b3c4d',
    conversation_id: 'conversation-a',
    target: 'devices',
    channel: '',
    channel_label: '',
    place: '',
    shared: false,
    status: 'sent',
    problem: '',
    detail: '',
    limit_bytes: null,
    files: [
      {
        index: 0,
        name: 'report.pdf',
        size_bytes: Math.round(2.1 * MB),
        mime_type: 'application/pdf',
        sent: true,
        downloadable: true,
      },
    ],
    created_at: new Date(Date.now() - 1000).toISOString(),
    expires_at: new Date(Date.now() + 7 * DAY - 2000).toISOString(),
    approval: 'not_needed',
    can_send_to_devices: false,
    sent_instead: null,
    ...patch,
  };
}

function io(views: Record<string, DeliveryView>) {
  return {
    load: vi.fn(async (id: string) => {
      const found = views[id];
      if (!found) throw new Error('not found');
      return found;
    }),
    download: vi.fn(async () => new Blob(['%PDF-1.7'])),
    saveToExports: vi.fn(async () => ({
      file_name: 'report.pdf',
      folder: 'Row-Bot › Exports',
    })),
    reveal: vi.fn(async () => true),
    sendToDevices: vi.fn(async () => {
      const copy = view({ delivery_id: 'send-99990000' });
      views[copy.delivery_id] = copy;
      return copy;
    }),
  } satisfies DeliveryIo;
}

function renderCard(deliveries: DeliveryIo, id = 'send-1a2b3c4d') {
  return render(
    <DeliveryIoContext.Provider value={deliveries}>
      <DeliveryCard deliveryId={id} />
    </DeliveryIoContext.Provider>,
  );
}

beforeEach(() => {
  saving.result = { status: 'ok', value: null };
  saving.names = [];
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it('files sent to your devices download on this device with its session', async () => {
  const deliveries = io({ 'send-1a2b3c4d': view() });
  renderCard(deliveries);

  const card = await screen.findByRole('group', {
    name: 'Sent to your devices: Ready to download',
  });
  expect(card).toHaveTextContent('On all your signed-in devices · 7 days left');
  expect(within(card).getByText('report.pdf')).toBeVisible();
  expect(card).toHaveTextContent('2.1 MB · PDF');
  fireEvent.click(
    within(card).getByRole('button', { name: 'Download report.pdf' }),
  );
  await waitFor(() =>
    expect(deliveries.download).toHaveBeenCalledWith('send-1a2b3c4d', 0),
  );
  expect(saving.names).toEqual(['report.pdf']);
  expect(within(card).queryByRole('alert')).toBeNull();
});

it('the desktop window saves into Exports instead, with Show in folder', async () => {
  saving.result = {
    status: 'unavailable',
    reason: 'desktop_download_unavailable',
  };
  const deliveries = io({ 'send-1a2b3c4d': view() });
  renderCard(deliveries);
  const card = await screen.findByRole('group', {
    name: /^Sent to your devices/,
  });
  fireEvent.click(
    within(card).getByRole('button', { name: 'Download report.pdf' }),
  );
  expect(await within(card).findByRole('status')).toHaveTextContent(
    'Saved to Row-Bot › Exports as report.pdf.',
  );
  fireEvent.click(within(card).getByRole('button', { name: 'Show in folder' }));
  await waitFor(() =>
    expect(deliveries.reveal).toHaveBeenCalledWith('report.pdf'),
  );
});

it('says plainly when the download has ended', async () => {
  const deliveries = io({ 'send-1a2b3c4d': view() });
  deliveries.download.mockRejectedValueOnce({ code: 'sent_file_expired' });
  renderCard(deliveries);
  const card = await screen.findByRole('group', {
    name: /^Sent to your devices/,
  });
  fireEvent.click(
    within(card).getByRole('button', { name: 'Download report.pdf' }),
  );
  expect(await within(card).findByRole('alert')).toHaveTextContent(
    'This download has ended: sent files stay for 7 days.',
  );
});

it('after a week the files can no longer be downloaded', async () => {
  renderCard(
    io({
      'send-1a2b3c4d': view({
        status: 'expired',
        expires_at: new Date(Date.now() - DAY).toISOString(),
        files: [{ ...view().files[0], downloadable: false }],
      }),
    }),
  );
  const card = await screen.findByRole('group', {
    name: 'Sent to your devices: Expired',
  });
  expect(card).toHaveTextContent(
    'Downloads end 7 days after sending. Ask Row-Bot to send it again.',
  );
  expect(within(card).queryByRole('button', { name: /Download/ })).toBeNull();
});

it('files sent to a chat app say where they went, with no download', async () => {
  renderCard(
    io({
      'send-1a2b3c4d': view({
        target: 'channel',
        channel: 'telegram',
        channel_label: 'Telegram',
        place: 'your Telegram chat',
        expires_at: null,
        approval: 'you',
        files: [{ ...view().files[0], downloadable: false }],
      }),
    }),
  );
  const card = await screen.findByRole('group', {
    name: 'Sent to Telegram: Sent',
  });
  expect(card).toHaveTextContent('Your Telegram chat');
  expect(within(card).queryByRole('button')).toBeNull();
});

it('a file too big for the app says so and sends to your devices instead in one click', async () => {
  const deliveries = io({
    'send-1a2b3c4d': view({
      target: 'channel',
      channel: 'telegram',
      channel_label: 'Telegram',
      place: 'your Telegram chat',
      status: 'failed',
      problem: 'too_large',
      detail: 'demo.mp4 (63.0 MB) is too big for Telegram (50 MB limit).',
      limit_bytes: 50 * MB,
      expires_at: null,
      approval: '',
      can_send_to_devices: true,
      files: [
        {
          index: 0,
          name: 'demo.mp4',
          size_bytes: 63 * MB,
          mime_type: 'video/mp4',
          sent: false,
          downloadable: false,
        },
      ],
    }),
  });
  renderCard(deliveries);

  const card = await screen.findByRole('group', {
    name: 'Not sent to Telegram: Too big for Telegram (50 MB limit)',
  });
  expect(card).toHaveTextContent('63.0 MB · MP4 video');
  fireEvent.click(
    within(card).getByRole('button', { name: 'Send to your devices instead' }),
  );
  await waitFor(() =>
    expect(deliveries.sendToDevices).toHaveBeenCalledWith('send-1a2b3c4d'),
  );
  const copy = await screen.findByRole('group', {
    name: 'Sent to your devices: Ready to download',
  });
  expect(
    within(copy).getByRole('button', { name: 'Download report.pdf' }),
  ).toBeVisible();
  expect(
    within(card).queryByRole('button', {
      name: 'Send to your devices instead',
    }),
  ).toBeNull();
});

it('a partly sent send names what didn’t go and offers the rest to your devices', async () => {
  renderCard(
    io({
      'send-1a2b3c4d': view({
        target: 'channel',
        channel: 'telegram',
        channel_label: 'Telegram',
        place: 'your Telegram chat',
        status: 'partly_sent',
        problem: 'send_failed',
        detail: "Telegram didn't take notes.txt.",
        expires_at: null,
        can_send_to_devices: true,
        files: [
          { ...view().files[0], downloadable: false },
          {
            index: 1,
            name: 'notes.txt',
            size_bytes: 900,
            mime_type: 'text/plain',
            sent: false,
            downloadable: false,
          },
        ],
      }),
    }),
  );
  const card = await screen.findByRole('group', {
    name: 'Sent to Telegram: 1 of 2 sent',
  });
  expect(card).toHaveTextContent("Telegram didn't take notes.txt.");
  expect(card).toHaveTextContent('900 B · TXT file · Not sent');
  expect(
    within(card).getByRole('button', { name: 'Send the rest to your devices' }),
  ).toBeVisible();
});

it('an app that isn’t connected says what to do', async () => {
  renderCard(
    io({
      'send-1a2b3c4d': view({
        target: 'channel',
        channel: 'telegram',
        channel_label: 'Telegram',
        status: 'failed',
        problem: 'not_running',
        expires_at: null,
        can_send_to_devices: true,
        files: [{ ...view().files[0], sent: false, downloadable: false }],
      }),
    }),
  );
  const card = await screen.findByRole('group', {
    name: "Not sent to Telegram: Telegram isn't connected",
  });
  expect(card).toHaveTextContent(
    'Start Telegram in Settings › Channels, or send to your devices instead.',
  );
});

it('shows a way to try again when the card can’t be read', async () => {
  const deliveries = io({});
  renderCard(deliveries);
  const card = await screen.findByRole('group', { name: 'Sent files' });
  expect(card).toHaveTextContent("Couldn't check these files.");
  expect(within(card).getByRole('button', { name: 'Try again' })).toBeVisible();
});

it('a send_file step reads in words and shows its card', () => {
  expect(stepVerb('send_file', 'succeeded')).toBe('Sent files');
  expect(
    cardOf({
      kind: 'delivery',
      delivery_id: 'send-1a2b3c4d',
    } as Parameters<typeof cardOf>[0]),
  ).toEqual({ kind: 'delivery', deliveryId: 'send-1a2b3c4d' });
});
