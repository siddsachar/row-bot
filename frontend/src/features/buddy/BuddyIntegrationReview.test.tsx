import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import BuddyPanel, {
  createBuddyPanelSession,
  type BuddyPanelTransport,
  type BuddyReceipt,
} from './BuddyPanel';
import type { BuddyPack, BuddySnapshot } from '../shell/BuddyControls';
import type { HatchResult } from '../shell/BuddyHatch';

const snapshot: BuddySnapshot = {
  schema_version: 1,
  revision: 'revision',
  placement: 'docked',
  native_placement_retained: true,
  preferences: {
    visible: true,
    collapsed: false,
    display_name: 'Buddy',
    personality: 'warm_mystical',
    bubble_verbosity: 'normal',
    animation_intensity: 'quiet',
    pack_id: 'glyph',
  },
  status: {
    mood: 'curious',
    animation: 'idle',
    energy: 50,
    focus: 0,
    alert: 0,
    event_id: 1,
    label: 'Ready',
  },
};
const pack: BuddyPack = {
  id: 'glyph',
  name: 'Glyph',
  revision: 'pack',
  runtime: 'rive',
  generated: false,
  available: true,
  assets: [],
  animation_map: {},
};
const request = {
  action: 'full' as const,
  prompt: 'Synthetic look',
  config_revision: 'revision',
};
const result = (id: string): HatchResult => ({
  schema_version: 1,
  command_id: id,
  job_id: 'buddy-hatch-' + id,
  status: 'running',
  stage: 'motion_provider_started',
  pack_id: null,
  selected: false,
  completed_clips: 1,
  total_clips: 6,
  code: '',
  retained_copy: true,
  has_still: true,
});
function api(): BuddyPanelTransport {
  return {
    snapshot: vi.fn(async () => snapshot),
    packs: vi.fn(async () => ({
      revision: 'catalog',
      total: 1,
      packs: [pack],
      next_cursor: null,
    })),
    pack: vi.fn(async () => pack),
    review: vi.fn(async (r) => ({
      review_id: 'review',
      action: r.action,
      config_revision: r.config_revision,
      image_model: 'fixture/image',
      video_model: 'fixture/video',
      provider_calls: 7,
    })),
    execute: vi.fn(async (c) => ({
      command_id: c.command_id,
      status: 'completed',
      buddy_revision: 'revision',
    })),
    receipt: vi.fn(async (id) => ({ command_id: id, status: 'completed' })),
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
afterEach(() => vi.useRealTimers());

it('reuses bounded settled command capacity rather than permanently blocking the 33rd preference save', async () => {
  const transport = api();
  let next = 0;
  const session = createBuddyPanelSession(
    transport,
    () => {},
    () => 'command-' + ++next,
  );
  try {
    for (let n = 0; n < 33; n += 1)
      await session.save({ visible: n % 2 === 0 }, 'revision');
    expect(transport.execute).toHaveBeenCalledTimes(33);
    expect(session.getSnapshot().pending).toBe(false);
  } finally {
    session.purge();
  }
});

it('dismisses the owned review as well as its visible card so a clean session is evictable', async () => {
  const transport = api();
  const session = createBuddyPanelSession(transport, () => {});
  render(
    <BuddyPanel
      session={session}
      scopeKey="conversation"
      settingsOpen
      currentRunId={null}
      onSettings={() => {}}
      stop={async () => {}}
    />,
  );
  await screen.findByLabelText('Describe your Buddy');
  fireEvent.change(screen.getByLabelText('Describe your Buddy'), {
    target: { value: 'Synthetic look' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: /Review Generate full Buddy/i }),
  );
  await screen.findByRole('button', { name: 'Dismiss review' });
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss review' }));
  fireEvent.change(screen.getByLabelText('Describe your Buddy'), {
    target: { value: '' },
  });
  await waitFor(() =>
    expect(
      screen.queryByRole('button', { name: 'Dismiss review' }),
    ).not.toBeInTheDocument(),
  );
  expect(session.hasRetained()).toBe(false);
  expect(transport.execute).not.toHaveBeenCalled();
  act(() => session.purge());
});

it('keeps exact worker cancellation usable while a passive original-status read is blocked', async () => {
  const transport = api();
  const pending = deferred<BuddyReceipt>();
  let next = 0;
  transport.execute = vi.fn(async (c) =>
    c.type === 'buddy.cancel'
      ? {
          command_id: c.command_id,
          status: 'completed',
          cancel_requested: true,
        }
      : {
          command_id: c.command_id,
          status: 'accepted',
          hatch: result(c.command_id),
        },
  );
  const session = createBuddyPanelSession(
    transport,
    () => {},
    () => 'command-' + ++next,
  );
  await session.review(request);
  await session.confirm('review');
  transport.receipt = vi.fn(() => pending.promise);
  const reading = session.refresh('command-1');
  await Promise.resolve();
  const stop = session.cancel('buddy-hatch-command-1');
  try {
    await expect(stop).resolves.toBeUndefined();
    expect(transport.execute).toHaveBeenLastCalledWith(
      expect.objectContaining({
        type: 'buddy.cancel',
        payload: {
          source_command_id: 'command-1',
          job_id: 'buddy-hatch-command-1',
        },
      }),
    );
  } finally {
    pending.resolve({
      command_id: 'command-1',
      status: 'accepted',
      hatch: result('command-1'),
    });
    await reading;
    session.purge();
  }
});

it('discards a late initial snapshot after authentication purge without further pack or effect reads', async () => {
  const transport = api();
  const pending = deferred<BuddySnapshot>();
  transport.snapshot = vi.fn(() => pending.promise);
  const session = createBuddyPanelSession(transport, () => {});
  const loading = session.load();
  await Promise.resolve();
  session.purge();
  pending.resolve(snapshot);
  await expect(loading).rejects.toThrow('authentication_required');
  expect(session.getSnapshot()).toMatchObject({
    revoked: true,
    snapshot: null,
    selectedPack: null,
    pending: false,
  });
  expect(transport.pack).not.toHaveBeenCalled();
  expect(transport.execute).not.toHaveBeenCalled();
});

it('keeps the visible Stop Hatch enabled during a blocked refresh and coalesces exact concurrent Stop requests', async () => {
  const transport = api();
  const reading = deferred<BuddyReceipt>();
  const cancelling = deferred<BuddyReceipt>();
  let next = 0;
  transport.execute = vi.fn(async (c) =>
    c.type === 'buddy.cancel'
      ? cancelling.promise
      : {
          command_id: c.command_id,
          status: 'accepted',
          hatch: result(c.command_id),
        },
  );
  const session = createBuddyPanelSession(
    transport,
    () => {},
    () => 'command-' + ++next,
  );
  await session.load();
  await session.review(request);
  await session.confirm('review');
  transport.receipt = vi.fn(() => reading.promise);
  const view = render(
    <BuddyPanel
      session={session}
      scopeKey="conversation"
      settingsOpen
      currentRunId={null}
      onSettings={() => {}}
      stop={async () => {}}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Refresh Hatch status' }));
  const button = screen.getByRole('button', { name: 'Stop Hatch' });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  await waitFor(() => expect(transport.execute).toHaveBeenCalledTimes(2));
  const same = session.cancel('buddy-hatch-command-1');
  await expect(session.cancel('buddy-hatch-foreign')).rejects.toThrow(
    'hatch_job_unavailable',
  );
  expect(button).toBeDisabled();
  await act(async () => {
    cancelling.resolve({
      command_id: 'command-2',
      status: 'completed',
      cancel_requested: true,
    });
    await same;
    reading.resolve({
      command_id: 'command-1',
      status: 'accepted',
      hatch: result('command-1'),
    });
  });
  expect(transport.execute).toHaveBeenCalledTimes(2);
  view.unmount();
  session.purge();
});

it('retains a lost Stop receipt after generation completes and reconciles both originals without effects', async () => {
  const transport = api();
  let next = 0;
  transport.execute = vi.fn(async (c) => {
    if (c.type === 'buddy.cancel') throw new Error('lost Stop response');
    return {
      command_id: c.command_id,
      status: 'accepted',
      hatch: result(c.command_id),
    };
  });
  const session = createBuddyPanelSession(
    transport,
    () => {},
    () => 'command-' + ++next,
  );
  await session.review(request);
  await session.confirm('review');
  await expect(session.cancel('buddy-hatch-command-1')).rejects.toThrow(
    'lost Stop response',
  );
  transport.receipt = vi.fn(async (id) =>
    id === 'command-1'
      ? {
          command_id: id,
          status: 'completed',
          hatch: { ...result(id), status: 'completed' },
        }
      : { command_id: id, status: 'completed', cancel_requested: true },
  );
  await session.refresh('command-1');
  expect(session.getSnapshot().pending).toBe(true);
  await session.recover();
  expect(transport.receipt).toHaveBeenCalledWith('command-2');
  expect(transport.execute).toHaveBeenCalledTimes(2);
  expect(session.getSnapshot().pending).toBe(false);
  session.purge();
});
