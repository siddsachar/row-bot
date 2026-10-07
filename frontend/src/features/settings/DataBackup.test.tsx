import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type {
  DataBackupReceipt,
  DataBackupState,
  DataRestoreReview,
} from '../../api/types';
import { DataBackup, type DataBackupOwner } from './DataBackup';

const idle: DataBackupState = {
  local_owner: true,
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
  } satisfies DataBackupOwner;
}

afterEach(() => vi.useRealTimers());

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

  const browser = owner();
  render(<DataBackup owner={browser} canPick={false} />);
  expect(
    await screen.findByText('Open the Row-Bot desktop app to restore.'),
  ).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Restore from backup…' }),
  ).toBeNull();
});

it('another device sees that backups belong to this computer', async () => {
  const fake = owner({ ...idle, local_owner: false, folder: null });
  render(<DataBackup owner={fake} />);
  expect(
    await screen.findByText(
      /Backups are made and restored in the Row-Bot desktop app/,
    ),
  ).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Back up now' })).toBeNull();
});
