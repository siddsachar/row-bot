import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type {
  UpdateCommand,
  UpdateReceipt,
  UpdateSnapshot,
} from '../../api/types';
import { UpdateControls } from './UpdateControls';

const engine: UpdateSnapshot['engine'] = {
  phase: 'idle',
  version: null,
  step: null,
  message: null,
  error: null,
  retryable: false,
  apply_when: null,
  full_installer_url: null,
  downloaded: 0,
  total: 0,
};

const snapshot: UpdateSnapshot = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  channel: 'stable',
  current_version: '1.0.0',
  last_check: null,
  last_success: null,
  skipped_versions: [],
  available: null,
  dev_install: false,
  mode: 'self',
  auto_check: true,
  auto_download: true,
  engine,
  previous_version: null,
  legacy_install: false,
  host: null,
  active_work: [],
  held_back: null,
  feed_url: null,
};

const release: NonNullable<UpdateSnapshot['available']> = {
  version: '2.0.0',
  channel: 'stable',
  published_at: '2026-09-23T12:00:00Z',
  notes: 'Synthetic changes',
  html_url: 'https://github.com/example/release',
  asset_size: 12,
  verified_manifest: true,
};

function receipt(
  command: UpdateCommand,
  next: UpdateSnapshot,
  status: UpdateReceipt['status'] = 'completed',
): UpdateReceipt {
  return {
    schema_version: 1,
    command_id: command.command_id,
    status,
    snapshot: next,
  };
}

function show(
  load: (signal?: AbortSignal) => Promise<UpdateSnapshot> = vi.fn(
    async () => snapshot,
  ),
  send: (command: UpdateCommand) => Promise<UpdateReceipt> = vi.fn(
    async (command) =>
      receipt(command, { ...snapshot, last_success: '2026-09-23T12:00:00Z' }),
  ),
) {
  render(<UpdateControls owner={{ load, send }} />);
  return { load, send };
}

beforeEach(() => sessionStorage.clear());

it('loads cached status without checking and checks from one explicit click', async () => {
  const { load, send } = show();
  expect(await screen.findByText('Row-Bot 1.0.0')).toBeVisible();
  expect(screen.getByText('Not checked yet.')).toBeVisible();
  expect(load).toHaveBeenCalledTimes(1);
  expect(send).not.toHaveBeenCalled();
  // One row: the version, when it was checked, and Check now (B258).
  expect(
    screen.getByRole('button', { name: 'Check for updates' }),
  ).toHaveTextContent('Check now');
  fireEvent.click(screen.getByRole('button', { name: 'Check for updates' }));
  expect(
    await screen.findByText('No update is available on this channel.'),
  ).toBeVisible();
  expect(send).toHaveBeenCalledWith(
    expect.objectContaining({
      action: 'check',
      expected_revision: snapshot.revision,
    }),
  );
});

it('checks once more on the new status when it moved on since the page read it', async () => {
  // The channel changed (or a check finished) after the page was read: Check
  // now still checks, on the status as it is now, without an error.
  const moved: UpdateSnapshot = {
    ...snapshot,
    revision: 'b'.repeat(64),
    channel: 'beta',
  };
  const load = vi.fn().mockResolvedValueOnce(snapshot).mockResolvedValue(moved);
  const send = vi
    .fn()
    .mockRejectedValueOnce({ code: 'update_changed', status: 409 })
    .mockImplementation(async (command: UpdateCommand) =>
      receipt(command, { ...moved, available: release }),
    );
  show(load, send);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Check for updates' }),
  );

  expect(await screen.findByText('Version 2.0.0 is available.')).toBeVisible();
  expect(send).toHaveBeenCalledTimes(2);
  expect(send).toHaveBeenLastCalledWith(
    expect.objectContaining({
      action: 'check',
      expected_revision: moved.revision,
    }),
  );
  expect(screen.queryByText(/Update status changed/)).toBeNull();
});

it('tells the page when it last checked, after Check now too (B373)', async () => {
  const checked = vi.fn();
  const load = vi.fn(async () => ({
    ...snapshot,
    last_check: '2026-10-20T19:51:04Z',
  }));
  const send = vi.fn(async (command: UpdateCommand) =>
    receipt(command, {
      ...snapshot,
      last_check: '2026-10-21T07:30:03Z',
      last_success: '2026-10-21T07:30:03Z',
    }),
  );
  render(<UpdateControls owner={{ load, send }} onChecked={checked} />);
  await waitFor(() =>
    expect(checked).toHaveBeenLastCalledWith('2026-10-20T19:51:04Z'),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Check for updates' }));
  await waitFor(() =>
    expect(checked).toHaveBeenLastCalledWith('2026-10-21T07:30:03Z'),
  );
});

it('shows a release, skips it from one click and shows it again', async () => {
  const offered: UpdateSnapshot = {
    ...snapshot,
    auto_download: false,
    available: release,
  };
  const send = vi.fn(async (command: UpdateCommand) =>
    receipt(
      command,
      command.action === 'skip'
        ? { ...offered, available: null, skipped_versions: ['2.0.0'] }
        : offered,
    ),
  );
  show(
    vi.fn(async () => offered),
    send,
  );
  expect(await screen.findByText('Version 2.0.0 is available')).toBeVisible();
  // The notes wait behind "What's new" (B258).
  expect(screen.getByText('Synthetic changes')).not.toBeVisible();
  fireEvent.click(screen.getByText('What’s new'));
  expect(screen.getByText('Synthetic changes')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Skip version 2.0.0' }));
  // One line about the skipped release, only while it is held back (B261).
  expect(await screen.findByText('You skipped 2.0.0')).toBeVisible();
  expect(send).toHaveBeenCalledWith(
    expect.objectContaining({ action: 'skip', version: '2.0.0' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Show it again' }));
  expect(await screen.findByText('Version 2.0.0 is available')).toBeVisible();
  expect(screen.queryByText('You skipped 2.0.0')).toBeNull();
  expect(send).toHaveBeenLastCalledWith(
    expect.objectContaining({ action: 'clear_skipped' }),
  );
});

it('reports a failed check without claiming the installed version is current', async () => {
  show(
    vi.fn(async () => snapshot),
    vi.fn(async (command) => receipt(command, snapshot, 'failed')),
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Check for updates' }),
  );
  expect(
    await screen.findByText(/could not reach a verified release source/),
  ).toBeVisible();
  expect(
    screen.queryByText('No update is available on this channel.'),
  ).toBeNull();
});

it('keeps an uncertain action for explicit recovery after remount', async () => {
  const send = vi
    .fn()
    .mockRejectedValueOnce(new Error('unconfirmed'))
    .mockImplementationOnce(async (command) => receipt(command, snapshot));
  show(
    vi.fn(async () => snapshot),
    send,
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Check for updates' }),
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Check update action' }),
  );
  expect(
    await screen.findByText('No update is available on this channel.'),
  ).toBeVisible();
  expect(send.mock.calls[0][0].command_id).toBe(
    send.mock.calls[1][0].command_id,
  );
});

it('shows development installs as unavailable without making a check', async () => {
  const { send } = show(
    vi.fn(async () => ({
      ...snapshot,
      dev_install: true,
      mode: 'unavailable' as const,
    })),
  );
  expect(await screen.findByText(/Development checkout/)).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Check for updates' }),
  ).toBeNull();
  expect(
    screen.queryByRole('switch', { name: 'Download updates automatically' }),
  ).toBeNull();
  expect(send).not.toHaveBeenCalled();
});

it('downloads in the background, follows the progress and can cancel', async () => {
  const offered: UpdateSnapshot = {
    ...snapshot,
    auto_download: false,
    available: release,
  };
  const downloading: UpdateSnapshot = {
    ...offered,
    engine: {
      ...engine,
      phase: 'downloading',
      version: '2.0.0',
      step: 'code',
      downloaded: 3_000_000,
      total: 8_000_000,
    },
  };
  const load = vi
    .fn()
    .mockResolvedValueOnce(offered)
    .mockResolvedValue({
      ...downloading,
      engine: { ...downloading.engine, downloaded: 6_000_000 },
    });
  const send = vi.fn(async (command: UpdateCommand) =>
    receipt(command, command.action === 'download' ? downloading : offered),
  );
  show(load, send);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Download version 2.0.0' }),
  );
  expect(await screen.findByText('Downloading version 2.0.0')).toBeVisible();
  expect(send).toHaveBeenCalledWith(
    expect.objectContaining({ action: 'download', version: '2.0.0' }),
  );
  // The page follows the background download by itself.
  expect(
    await screen.findByText(/6\.0 MB of 8\.0 MB/, undefined, { timeout: 3000 }),
  ).toBeVisible();
  expect(
    screen.getByRole('progressbar', { name: 'Update download' }),
  ).toHaveAttribute('value', '6000000');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(send).toHaveBeenLastCalledWith(
    expect.objectContaining({ action: 'cancel' }),
  );
});

it('restarts into a ready update from one click when nothing is running', async () => {
  const ready: UpdateSnapshot = {
    ...snapshot,
    available: release,
    engine: { ...engine, phase: 'ready', version: '2.0.0' },
  };
  const send = vi.fn(async (command: UpdateCommand) =>
    receipt(command, {
      ...ready,
      engine: { ...ready.engine, phase: 'applying', message: 'Restarting…' },
    }),
  );
  show(
    vi.fn(async () => ready),
    send,
  );
  expect(await screen.findByText('Version 2.0.0 is ready')).toBeVisible();
  expect(screen.getByText(/installs the next time you quit/)).toBeVisible();
  // What's new sits with the ready version.
  expect(screen.getByText('What’s new')).toBeVisible();
  expect(screen.queryByText('Version 2.0.0 is available')).toBeNull();
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Restart now to install version 2.0.0',
    }),
  );
  expect(await screen.findByText('Installing version 2.0.0')).toBeVisible();
  expect(send).toHaveBeenCalledWith(
    expect.objectContaining({ action: 'apply', version: '2.0.0', when: 'now' }),
  );
});

it('warns before a restart would interrupt work and offers when idle or on quit', async () => {
  const ready: UpdateSnapshot = {
    ...snapshot,
    engine: { ...engine, phase: 'ready', version: '2.0.0' },
    active_work: ['An agent is working', 'A workflow is running'],
  };
  const send = vi.fn(async (command: UpdateCommand) =>
    receipt(command, {
      ...ready,
      engine: { ...ready.engine, apply_when: command.when ?? null },
    }),
  );
  show(
    vi.fn(async () => ready),
    send,
  );
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Restart now to install version 2.0.0',
    }),
  );
  const dialog = await screen.findByRole('dialog', { name: 'Restart now?' });
  expect(within(dialog).getByText('An agent is working')).toBeVisible();
  expect(send).not.toHaveBeenCalled();
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Restart when idle' }),
  );
  expect(send).toHaveBeenCalledWith(
    expect.objectContaining({ action: 'apply', when: 'when_idle' }),
  );
  expect(
    await screen.findByText(
      'Row-Bot restarts to install it as soon as nothing is running.',
    ),
  ).toBeVisible();

  fireEvent.click(
    screen.getByRole('button', {
      name: 'Restart now to install version 2.0.0',
    }),
  );
  fireEvent.click(
    within(await screen.findByRole('dialog')).getByRole('button', {
      name: 'Restart anyway',
    }),
  );
  expect(send).toHaveBeenLastCalledWith(
    expect.objectContaining({ action: 'apply', when: 'now', force: true }),
  );
});

it('turns background downloads off and on from Settings', async () => {
  const send = vi.fn(async (command: UpdateCommand) =>
    receipt(command, { ...snapshot, auto_download: command.enabled ?? true }),
  );
  show(
    vi.fn(async () => snapshot),
    send,
  );
  const toggle = await screen.findByRole('switch', {
    name: 'Download updates automatically',
  });
  expect(toggle).toBeChecked();
  fireEvent.click(toggle);
  expect(send).toHaveBeenCalledWith(
    expect.objectContaining({ action: 'set_auto_download', enabled: false }),
  );
  expect(
    await screen.findByText('Updates download only when you choose.'),
  ).toBeVisible();
  expect(
    screen.getByRole('switch', { name: 'Download updates automatically' }),
  ).not.toBeChecked();
});

it('offers the full installer when an update cannot be prepared in the background', async () => {
  show(
    vi.fn(async () => ({
      ...snapshot,
      available: release,
      engine: {
        ...engine,
        phase: 'failed' as const,
        version: '2.0.0',
        error: 'full_installer',
        message: 'This update needs the full installer.',
        full_installer_url:
          'https://github.com/example/row-bot/releases/download/v2.0.0/Row-Bot-2.0.0-Windows-x64.exe',
      },
    })),
  );
  const link = await screen.findByRole('link', {
    name: 'Download the full installer',
  });
  expect(link).toHaveAttribute(
    'href',
    'https://github.com/example/row-bot/releases/download/v2.0.0/Row-Bot-2.0.0-Windows-x64.exe',
  );
  expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
});

it('retries a failed preparation and repairs by going back a version', async () => {
  const failed: UpdateSnapshot = {
    ...snapshot,
    available: release,
    previous_version: '0.9.0',
    engine: {
      ...engine,
      phase: 'failed',
      version: '2.0.0',
      error: 'download',
      retryable: true,
      message: 'The download stopped. Row-Bot will try again.',
    },
  };
  const send = vi.fn(async (command: UpdateCommand) =>
    receipt(command, failed),
  );
  show(
    vi.fn(async () => failed),
    send,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
  expect(send).toHaveBeenCalledWith(
    expect.objectContaining({ action: 'retry', version: '2.0.0' }),
  );
  const goBack = screen.getByRole('button', { name: 'Go back to 0.9.0' });
  await waitFor(() => expect(goBack).toBeEnabled());
  fireEvent.click(goBack);
  const dialog = await screen.findByRole('dialog', {
    name: 'Go back to 0.9.0?',
  });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Go back and restart' }),
  );
  expect(send).toHaveBeenLastCalledWith(
    expect.objectContaining({ action: 'rollback', version: '0.9.0' }),
  );
});

it('shows the host’s status and sends requests when the host applies updates', async () => {
  const external: UpdateSnapshot = {
    ...snapshot,
    mode: 'external',
    host: {
      state: 'ready',
      current_version: '1.0.0',
      available_version: '2.0.0',
      message: 'Ready to update',
      notes_url: null,
      updated_at: '2026-10-11T12:00:00Z',
      request: null,
    },
  };
  const send = vi.fn(async (command: UpdateCommand) =>
    receipt(
      command,
      {
        ...external,
        host: {
          ...external.host!,
          request: {
            action: 'update_now',
            requested_at: '2026-10-11T12:01:00Z',
          },
        },
      },
      'requested',
    ),
  );
  show(
    vi.fn(async () => external),
    send,
  );
  expect(await screen.findByText('Ready to update')).toBeVisible();
  expect(screen.getByText('Updated by your host')).toBeVisible();
  expect(
    screen.queryByRole('switch', { name: 'Download updates automatically' }),
  ).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Update now' }));
  expect(send).toHaveBeenCalledWith(
    expect.objectContaining({ action: 'apply', version: '2.0.0', when: 'now' }),
  );
  expect(
    await screen.findByText('Waiting for the host to act on an update'),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Pause updates' })).toBeVisible();
});

it('offers to remove an earlier all-users install once', async () => {
  // Windows' prompt waits for the person: the server answers "requested".
  const send = vi.fn(async (command: UpdateCommand) =>
    receipt(command, { ...snapshot, legacy_install: true }, 'requested'),
  );
  show(
    vi.fn(async () => ({ ...snapshot, legacy_install: true })),
    send,
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Remove the old Row-Bot' }),
  );
  expect(send).toHaveBeenCalledWith(
    expect.objectContaining({ action: 'remove_legacy' }),
  );
  expect(
    await screen.findByText(
      'Approve the Windows prompt to remove the earlier Row-Bot.',
    ),
  ).toBeVisible();
});

const container: UpdateSnapshot = {
  ...snapshot,
  mode: 'external',
  host: {
    state: 'unknown',
    current_version: null,
    available_version: null,
    message: null,
    notes_url: null,
    updated_at: null,
    request: null,
  },
};

it('explains a container that no host updater reports for', async () => {
  show(vi.fn(async () => container));
  expect(await screen.findByText('Updated by your host')).toBeVisible();
  expect(
    screen.getByText(
      'This Row-Bot doesn’t update itself. For Docker, pull a newer image and start the container again.',
    ),
  ).toBeVisible();
  expect(
    screen.getByText('This Row-Bot’s host installs its updates.'),
  ).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Pause updates' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Update now' })).toBeNull();
});

it('names a known host that applies updates and offers nothing to press (H3, U6)', async () => {
  const notes = 'https://github.com/example/row-bot/releases/tag/v1.0.0';
  const load = vi.fn(async () => ({
    ...container,
    host: { ...container.host!, notes_url: notes },
  }));
  const send = vi.fn();
  render(<UpdateControls owner={{ load, send }} hostName="PikaPods" />);
  expect(await screen.findByText('Updated by PikaPods')).toBeVisible();
  expect(
    screen.getByText(
      'They test each release first, so new versions usually arrive within a week.',
    ),
  ).toBeVisible();
  expect(
    screen.getByRole('link', { name: 'What’s new in Row-Bot 1.0.0' }),
  ).toHaveAttribute('href', notes);
  // No check, install, skip or pause: the host does all of it.
  expect(screen.queryAllByRole('button')).toEqual([]);
  expect(screen.queryByText(/Docker/)).toBeNull();
  expect(send).not.toHaveBeenCalled();
});

it('reports an update that did not start once and downloads it again only when asked', async () => {
  const rolledBack: UpdateSnapshot = {
    ...snapshot,
    available: release,
    held_back: { version: '2.0.0', reason: 'rolled_back' },
    engine: {
      ...engine,
      phase: 'failed',
      version: '2.0.0',
      error: 'rolled_back',
      message:
        'The new version didn’t start, so Row-Bot went back to the one that works.',
    },
  };
  const send = vi.fn(async (command: UpdateCommand) =>
    receipt(command, {
      ...rolledBack,
      engine: { ...engine, phase: 'downloading', version: '2.0.0' },
    }),
  );
  show(
    vi.fn(async () => rolledBack),
    send,
  );
  expect(await screen.findByText('Version 2.0.0 didn’t start')).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Try again' }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Download version 2.0.0 again' }),
  );
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith(
      expect.objectContaining({ action: 'download', version: '2.0.0' }),
    ),
  );
});

it('says why a version Row-Bot went back from is not downloaded by itself', async () => {
  show(
    vi.fn(async () => ({
      ...snapshot,
      available: release,
      held_back: { version: '2.0.0', reason: 'repair' as const },
    })),
  );
  expect(
    await screen.findByText(
      'You went back from this version with Repair, so it won’t download by itself.',
    ),
  ).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Download version 2.0.0' }),
  ).toBeVisible();
});

it('shows when updates come from a test feed instead of GitHub', async () => {
  show(
    vi.fn(async () => ({ ...snapshot, feed_url: 'http://127.0.0.1:8765/' })),
  );
  expect(
    await screen.findByText(
      'Testing: updates come from http://127.0.0.1:8765/, not GitHub',
    ),
  ).toBeVisible();
});

it('leaves removing the earlier install to the desktop app on that computer', async () => {
  const send = vi.fn();
  render(
    <UpdateControls
      owner={{
        load: async () => ({ ...snapshot, legacy_install: true }),
        send,
      }}
      onThisComputer={false}
    />,
  );
  expect(
    await screen.findByText(/so it’s done in the Row-Bot desktop app there/),
  ).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Remove the old Row-Bot' }),
  ).toBeNull();
  expect(send).not.toHaveBeenCalled();
});
