import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type {
  DataBackupReceipt,
  DataBackupSignIn,
  DataBackupState,
  DataRestoreReview,
} from '../../api/types';
import { setPlace } from '../../api/place';
import { DataBackup, type DataBackupOwner } from './DataBackup';

const idle: DataBackupState = {
  reveal_available: true,
  last_backup_at: null,
  last_backup_name: null,
  folder: 'Row-Bot › Backups',
  job: null,
  pending_restore: null,
  restore_result: null,
};

const review: DataRestoreReview = {
  review_id: 'b'.repeat(64),
  source_name: 'Row-Bot backup 2026-09-20 0900.zip',
  created_at: '2026-09-20T09:00:00',
  app_version: '4.9.0',
  files: 42,
  bytes: 5 * 1024 * 1024,
  left_out: ['Passwords and keys'],
  sign_in_again: [
    { kind: 'provider', name: 'openai' },
    { kind: 'account', name: 'Gmail' },
    { kind: 'webhooks', name: '1 webhook workflow' },
  ],
};

function receipt(
  state: DataBackupState,
  extra: Partial<DataBackupReceipt> = {},
): DataBackupReceipt {
  return {
    command_id: crypto.randomUUID(),
    status: 'completed',
    state,
    ...extra,
  };
}

function owner(first: DataBackupState = idle) {
  return {
    read: vi.fn(async () => first),
    send: vi.fn(async () => receipt(first)),
    pick: vi.fn(async () => 'grant-1' as string | null | 'unavailable'),
    download: vi.fn(async (_name: string) => null as string | null),
    upload: vi.fn(
      async (
        _file: File,
        _progress: (sent: number) => void,
        _signal: AbortSignal,
      ) => 'upload-1',
    ),
    discard: vi.fn(async (_upload: string) => undefined),
  } satisfies DataBackupOwner;
}

afterEach(() => {
  vi.useRealTimers();
  setPlace(null);
});

it('backs up in the background, then says where it went and offers the folder', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const fake = owner();
  const running: DataBackupState = {
    ...idle,
    job: {
      kind: 'backup',
      status: 'running',
      started_at: '2026-09-29T07:00:00',
    },
  };
  const done: DataBackupState = {
    ...idle,
    last_backup_at: new Date().toISOString(),
    last_backup_name: 'Row-Bot backup 2026-09-29 0700.zip',
    job: {
      kind: 'backup',
      status: 'completed',
      name: 'Row-Bot backup 2026-09-29 0700.zip',
      skipped: ['media/c/locked.png'],
    },
  };
  fake.send.mockResolvedValueOnce(receipt(running, { status: 'accepted' }));
  render(<DataBackup owner={fake} />);
  expect(await screen.findByText(/Last backup:/)).toHaveTextContent('never');
  fireEvent.click(screen.getByRole('button', { name: 'Back up now' }));
  expect(
    await screen.findByRole('button', { name: 'Backing up…' }),
  ).toBeDisabled();
  expect(fake.send).toHaveBeenCalledWith('backup', undefined);
  fake.read.mockResolvedValue(done);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(
    await screen.findByText(
      'Saved “Row-Bot backup 2026-09-29 0700.zip” in Row-Bot › Backups.',
      { exact: false },
    ),
  ).toBeVisible();
  expect(
    screen.getByText(
      /Left out a file that couldn't be read: media\/c\/locked\.png\./,
    ),
  ).toBeVisible();
  expect(screen.getByText(/Last backup:/)).not.toHaveTextContent('never');
  fake.send.mockResolvedValueOnce(receipt(done));
  fireEvent.click(screen.getByRole('button', { name: 'Show in folder' }));
  expect(fake.send).toHaveBeenLastCalledWith('reveal', undefined);
});

it('checks a picked backup, and restores it on restart only after confirming', async () => {
  const fake = owner();
  fake.send.mockResolvedValueOnce(receipt(idle, { review }));
  render(<DataBackup owner={fake} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Restore from backup…' }),
  );
  const group = await screen.findByRole('group', {
    name: 'Restore this backup?',
  });
  expect(fake.send).toHaveBeenCalledWith('inspect_restore', {
    file_grant: 'grant-1',
  });
  expect(group).toHaveTextContent('Row-Bot 4.9.0 · 42 files · 5.0 MB');
  const signIns = within(group).getByRole('list', { name: 'Sign in again' });
  expect(signIns).toHaveTextContent('OpenAI (provider)');
  expect(signIns).toHaveTextContent('Gmail account');
  expect(signIns).toHaveTextContent('1 webhook workflow: new webhook secrets');
  // Looking changes nothing; Cancel leaves no trace.
  fireEvent.click(within(group).getByRole('button', { name: 'Cancel' }));
  expect(
    screen.queryByRole('group', { name: 'Restore this backup?' }),
  ).toBeNull();
  expect(fake.send).toHaveBeenCalledTimes(1);

  fake.send.mockResolvedValueOnce(receipt(idle, { review }));
  fireEvent.click(screen.getByRole('button', { name: 'Restore from backup…' }));
  const again = await screen.findByRole('group', {
    name: 'Restore this backup?',
  });
  const pending: DataBackupState = {
    ...idle,
    pending_restore: {
      created_at: '2026-09-29T07:05:00',
      source_name: review.source_name,
      sign_in_again: review.sign_in_again,
    },
  };
  fake.send.mockResolvedValueOnce(receipt(pending, { status: 'accepted' }));
  fireEvent.click(
    within(again).getByRole('button', { name: 'Restore on restart' }),
  );
  expect(
    await screen.findByText(/will apply the next time Row-Bot starts\.$/),
  ).toBeVisible();
  expect(fake.send).toHaveBeenLastCalledWith('restore', {
    review_id: review.review_id,
  });
  expect(
    screen.getByRole('button', { name: 'Restore from backup…' }),
  ).toBeDisabled();
  fake.send.mockResolvedValueOnce(receipt(idle));
  fireEvent.click(screen.getByRole('button', { name: 'Cancel restore' }));
  expect(fake.send).toHaveBeenLastCalledWith('cancel_restore', undefined);
  expect(
    await screen.findByRole('button', { name: 'Restore from backup…' }),
  ).toBeEnabled();
});

it('after a restore, lists what to sign in to again until dismissed', async () => {
  const fake = owner({
    ...idle,
    restore_result: {
      status: 'applied',
      applied_at: '2026-09-29T08:00:00',
      source_created_at: '2026-09-20T09:00:00',
      kept_aside: 'before-restore-20260929-080000',
      sign_in_again: [{ kind: 'mcp', name: 'search' }],
    },
  });
  render(<DataBackup owner={fake} />);
  const result = await screen.findByText(/Restored the backup from/);
  const box = result.closest('[role="status"]') as HTMLElement;
  expect(box).toHaveTextContent('before-restore-20260929-080000');
  expect(
    within(box).getByRole('list', { name: 'Sign in again' }),
  ).toHaveTextContent('search (MCP server)');
  fireEvent.click(within(box).getByRole('button', { name: 'Done' }));
  expect(fake.send).toHaveBeenCalledWith('dismiss_result', undefined);
});

it('refused archives and other devices explain themselves', async () => {
  const fake = owner();
  fake.send.mockRejectedValueOnce({ code: 'backup_not_row_bot' });
  const view = render(<DataBackup owner={fake} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Restore from backup…' }),
  );
  expect(await screen.findByRole('alert')).toHaveTextContent(
    "That file isn't a Row-Bot backup.",
  );
  view.unmount();

  // The desktop app's picker couldn't open.
  const desktop = owner();
  desktop.pick.mockResolvedValueOnce('unavailable');
  render(<DataBackup owner={desktop} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Restore from backup…' }),
  );
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Restoring picks the backup file in the Row-Bot desktop app on the computer Row-Bot runs on.',
  );
  expect(desktop.send).not.toHaveBeenCalled();
});

it('another device backs up too, without the file manager of the computer it runs on', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const away: DataBackupState = { ...idle, reveal_available: false };
  const running: DataBackupState = {
    ...away,
    job: {
      kind: 'backup',
      status: 'running',
      started_at: '2026-10-11T09:00:00',
    },
  };
  const done: DataBackupState = {
    ...away,
    last_backup_at: new Date().toISOString(),
    last_backup_name: 'Row-Bot backup 2026-10-11 0900.zip',
    job: {
      kind: 'backup',
      status: 'completed',
      name: 'Row-Bot backup 2026-10-11 0900.zip',
    },
  };
  const fake = owner(away);
  fake.send.mockResolvedValueOnce(receipt(running, { status: 'accepted' }));
  render(<DataBackup owner={fake} canPick={false} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Back up now' }));
  expect(
    await screen.findByRole('button', { name: 'Backing up…' }),
  ).toBeDisabled();
  expect(fake.send).toHaveBeenCalledWith('backup', undefined);
  fake.read.mockResolvedValue(done);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(
    await screen.findByText(
      'Saved “Row-Bot backup 2026-10-11 0900.zip” in Row-Bot › Backups.',
      { exact: false },
    ),
  ).toBeVisible();
  // Show in folder opens the file manager on that computer's screen; this
  // device downloads the backup instead.
  expect(screen.queryByRole('button', { name: 'Show in folder' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Download' })).toBeEnabled();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

const away: DataBackupState = {
  ...idle,
  reveal_available: false,
  last_backup_at: '2026-10-11T09:00:00',
  last_backup_name: 'Row-Bot backup 2026-10-11 0900.zip',
};

it('downloads the last backup by its name where the folder is out of reach', async () => {
  const fake = owner(away);
  const saving = deferred<string | null>();
  fake.download.mockReturnValueOnce(saving.promise);
  render(<DataBackup owner={fake} canPick={false} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Download' }));
  expect(fake.download).toHaveBeenCalledWith(
    'Row-Bot backup 2026-10-11 0900.zip',
  );
  expect(screen.getByRole('button', { name: 'Downloading…' })).toBeDisabled();
  await act(async () => saving.resolve(null));
  expect(screen.getByRole('button', { name: 'Download' })).toBeEnabled();
  expect(screen.queryByRole('alert')).toBeNull();
  // The browser refused to save without a click: it says what to do.
  fake.download.mockResolvedValueOnce('user_gesture_required');
  fireEvent.click(screen.getByRole('button', { name: 'Download' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Click Download again to save the backup.',
  );
});

it('a backup gone since it was shown is said so, and the page catches up', async () => {
  const fake = owner(away);
  fake.download.mockRejectedValueOnce({ code: 'backup_unavailable' });
  render(<DataBackup owner={fake} canPick={false} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Download' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    "That backup isn't in the Backups folder any more.",
  );
  expect(fake.read).toHaveBeenCalledTimes(2);
});

it('the computer Row-Bot runs on shows the folder rather than downloading', async () => {
  render(<DataBackup owner={owner({ ...away, reveal_available: true })} />);
  expect(await screen.findByText(/Last backup:/)).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Download' })).toBeNull();
});

it('another device sends a backup, then restores it on restart only after confirming', async () => {
  const fake = owner();
  const sending = deferred<string>();
  fake.upload.mockImplementationOnce(async (_file, progress) => {
    progress(1024 * 1024);
    return sending.promise;
  });
  render(<DataBackup owner={fake} canPick={false} />);
  expect(
    await screen.findByText(
      /Sends the backup file to Row-Bot and checks it first\./,
    ),
  ).toBeVisible();
  const file = new File([new Uint8Array(4 * 1024 * 1024)], 'Phone backup.zip', {
    type: 'application/zip',
  });
  fireEvent.change(screen.getByLabelText('Backup file to restore'), {
    target: { files: [file] },
  });
  expect(
    await screen.findByText(/Sending the backup… 25% of 4\.0 MB/),
  ).toBeVisible();
  expect(fake.upload.mock.calls[0][0]).toBe(file);
  expect(fake.send).not.toHaveBeenCalled();

  fake.send.mockResolvedValueOnce(
    receipt(idle, { review: { ...review, source_name: 'Phone backup.zip' } }),
  );
  await act(async () => sending.resolve('upload-7'));
  const group = await screen.findByRole('group', {
    name: 'Restore this backup?',
  });
  expect(fake.send).toHaveBeenCalledWith('inspect_restore', {
    upload_id: 'upload-7',
  });
  expect(group).toHaveTextContent('Restore “Phone backup.zip”?');
  const pending: DataBackupState = {
    ...idle,
    pending_restore: {
      created_at: '2026-10-11T09:05:00',
      source_name: 'Phone backup.zip',
      sign_in_again: [],
    },
  };
  fake.send.mockResolvedValueOnce(receipt(pending, { status: 'accepted' }));
  fireEvent.click(
    within(group).getByRole('button', { name: 'Restore on restart' }),
  );
  expect(
    await screen.findByText(/will apply the next time Row-Bot starts\.$/),
  ).toBeVisible();
  expect(fake.send).toHaveBeenLastCalledWith('restore', {
    review_id: review.review_id,
  });
  // Row-Bot deletes a staged archive itself.
  expect(fake.discard).not.toHaveBeenCalled();
});

it('a stopped, refused or dismissed backup that was sent is let go', async () => {
  const fake = owner();
  render(<DataBackup owner={fake} canPick={false} />);
  const choose = async (name: string) => {
    fireEvent.change(await screen.findByLabelText('Backup file to restore'), {
      target: { files: [new File(['zip'], name)] },
    });
  };
  // Stopped while sending: no complaint, and nothing to check.
  fake.upload.mockImplementationOnce(
    (_file, _progress, signal) =>
      new Promise((_resolve, reject) =>
        signal.addEventListener('abort', () =>
          reject(new DOMException('Cancelled', 'AbortError')),
        ),
      ),
  );
  await choose('first.zip');
  fireEvent.click(await screen.findByRole('button', { name: 'Stop' }));
  expect(
    await screen.findByRole('button', { name: 'Restore from backup…' }),
  ).toBeEnabled();
  expect(fake.upload.mock.calls[0][2].aborted).toBe(true);
  expect(screen.queryByRole('alert')).toBeNull();
  expect(fake.send).not.toHaveBeenCalled();

  // Not a Row-Bot backup: said so, and let go.
  fake.upload.mockResolvedValueOnce('upload-2');
  fake.send.mockRejectedValueOnce({ code: 'backup_not_row_bot' });
  await choose('photos.zip');
  expect(await screen.findByRole('alert')).toHaveTextContent(
    "That file isn't a Row-Bot backup.",
  );
  expect(fake.discard).toHaveBeenLastCalledWith('upload-2');

  // Checked, then not wanted.
  fake.upload.mockResolvedValueOnce('upload-3');
  fake.send.mockResolvedValueOnce(receipt(idle, { review }));
  await choose('old.zip');
  const group = await screen.findByRole('group', {
    name: 'Restore this backup?',
  });
  fireEvent.click(within(group).getByRole('button', { name: 'Cancel' }));
  expect(fake.discard).toHaveBeenLastCalledWith('upload-3');

  // Too large for Row-Bot to restore: said before anything is checked.
  fake.upload.mockRejectedValueOnce({ code: 'backup_too_large' });
  await choose('huge.zip');
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'That backup is larger than Row-Bot can restore.',
  );
  expect(fake.send).toHaveBeenCalledTimes(2);
});

const MOVED = 'Row-Bot backup 2026-10-31 1100.zip';
/** A hosted Row-Bot's Move: the backup runs, then its own manifest says what moves. */
function moving(
  signIns: DataBackupSignIn[] = [{ kind: 'provider', name: 'openai' }],
) {
  const away: DataBackupState = { ...idle, reveal_available: false };
  const fake = owner(away);
  fake.send.mockResolvedValueOnce(
    receipt(
      {
        ...away,
        job: {
          kind: 'backup',
          status: 'running',
          started_at: '2026-10-31T11:00:00',
        },
      },
      { status: 'accepted' },
    ),
  );
  const done: DataBackupState = {
    ...away,
    last_backup_at: '2026-10-31T11:00:00',
    last_backup_name: MOVED,
    job: {
      kind: 'backup',
      status: 'completed',
      name: MOVED,
      left_out: [
        'API keys, sign-in tokens and passwords',
        'Caches, logs and downloaded runtimes',
      ],
      sign_in_again: signIns,
    },
  };
  return { fake, done };
}

it('a hosted Row-Bot moves to a computer: a fresh backup downloads, with what it leaves out and what to sign in to again (U9)', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  setPlace({ kind: 'hosted', host_name: 'PikaPods' });
  const { fake, done } = moving();
  render(<DataBackup owner={fake} canPick={false} />);
  expect(await screen.findByText('Move Row-Bot')).toBeVisible();
  expect(
    screen.getByText(
      'Download a backup, then install Row-Bot on your computer, open Settings › Data and choose Restore from backup.',
    ),
  ).toBeVisible();
  expect(
    screen.getByText(/A backup from Row-Bot on your computer works too\./),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Download backup' }));
  expect(fake.send).toHaveBeenCalledWith('backup', undefined);
  await screen.findAllByRole('button', { name: 'Backing up…' });
  fake.read.mockResolvedValue(done);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(fake.download).toHaveBeenCalledWith(MOVED);
  const moved = await screen.findByText(`Downloaded “${MOVED}”.`);
  const status = moved.closest('[role="status"]') as HTMLElement;
  expect(status).toHaveTextContent(
    'Not in the backup: API keys, sign-in tokens and passwords · Caches, logs and downloaded runtimes',
  );
  expect(status).toHaveTextContent('After moving, set these up again:');
  expect(
    within(screen.getByRole('list', { name: 'Sign in again' })).getByText(
      'OpenAI (provider)',
    ),
  ).toBeVisible();
});

it('a backup made for moving waits for a click when the browser won’t save it by itself', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  setPlace({ kind: 'hosted', host_name: 'PikaPods' });
  const { fake, done } = moving();
  fake.download.mockResolvedValueOnce('user_gesture_required');
  render(<DataBackup owner={fake} canPick={false} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Download backup' }),
  );
  await screen.findAllByRole('button', { name: 'Backing up…' });
  fake.read.mockResolvedValue(done);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(
    await screen.findByText(
      `“${MOVED}” is ready. Choose Download backup to save it.`,
    ),
  ).toBeVisible();
  expect(screen.queryByRole('alert')).toBeNull();
  // The click saves that backup; it doesn't make another.
  fireEvent.click(screen.getByRole('button', { name: 'Download backup' }));
  expect(await screen.findByText(`Downloaded “${MOVED}”.`)).toBeVisible();
  expect(fake.download).toHaveBeenLastCalledWith(MOVED);
  expect(fake.send).toHaveBeenCalledTimes(1);
});

it('the desktop has no Move row, and its restore takes a hosted Row-Bot’s backup', async () => {
  render(<DataBackup owner={owner()} />);
  expect(await screen.findByText('Restore from backup')).toBeVisible();
  expect(
    screen.getByText(
      'Checks the backup first. A backup from a hosted Row-Bot works too. Your current profile is kept aside, and nothing changes until Row-Bot restarts.',
    ),
  ).toBeVisible();
  expect(screen.queryByText('Move Row-Bot')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Download backup' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Back up now' })).toHaveClass(
    'primary',
  );
});

it('the Move row lists exactly what the backup says to sign in to again, and nothing when it says nothing', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  setPlace({ kind: 'hosted', host_name: 'PikaPods' });
  const move = async (signIns: DataBackupSignIn[]) => {
    const { fake, done } = moving(signIns);
    const view = render(<DataBackup owner={fake} canPick={false} />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Download backup' }),
    );
    await screen.findAllByRole('button', { name: 'Backing up…' });
    fake.read.mockResolvedValue(done);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    await screen.findByText(`Downloaded “${MOVED}”.`);
    return view;
  };

  const nothing = await move([]);
  expect(screen.queryByText('After moving, set these up again:')).toBeNull();
  expect(screen.queryByRole('list', { name: 'Sign in again' })).toBeNull();
  nothing.unmount();

  await move([
    { kind: 'provider', name: 'anthropic' },
    { kind: 'mcp', name: 'Notes server' },
  ]);
  expect(screen.getByText('After moving, set these up again:')).toBeVisible();
  const items = within(
    screen.getByRole('list', { name: 'Sign in again' }),
  ).getAllByRole('listitem');
  expect(items.map((item) => item.textContent)).toEqual([
    'Anthropic (provider)',
    'Notes server (MCP server)',
  ]);
});
