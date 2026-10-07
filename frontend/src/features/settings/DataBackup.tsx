import { useCallback, useEffect, useRef, useState } from 'react';
import { ArchiveRestore, FolderOpen, HardDriveDownload } from 'lucide-react';
import type {
  DataBackupCommand,
  DataBackupReceipt,
  DataBackupSignIn,
  DataBackupState,
  DataRestoreReview,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Button } from '../../ui/primitives';
import { humanizeToken } from '../../ui/format';
import { When } from '../../ui/When';

type Action = DataBackupCommand['action'];

export type DataBackupOwner = {
  read: () => Promise<DataBackupState>;
  send: (
    action: Action,
    extra?: { file_grant?: string; review_id?: string },
  ) => Promise<DataBackupReceipt>;
  /** The desktop pick of one backup archive, or null (cancelled or no desktop). */
  pick: () => Promise<string | null | 'unavailable'>;
};

const POLL_MS = 1000;

const JOB_FAILED: Record<string, string> = {
  backup_failed: "The backup didn't finish. Try again.",
  restore_failed:
    "The restore couldn't be prepared. Your profile is unchanged.",
};

function signInLine(item: DataBackupSignIn) {
  if (item.kind === 'provider') return `${humanizeToken(item.name)} (provider)`;
  if (item.kind === 'account') return `${item.name} account`;
  if (item.kind === 'channel') return `${item.name} channel`;
  if (item.kind === 'mcp') return `${item.name} (MCP server)`;
  return `${item.name}: new webhook secrets`;
}

function SignInList({ items }: { items: DataBackupSignIn[] }) {
  if (!items.length) return null;
  return (
    <ul className="data-backup-signins" aria-label="Sign in again">
      {items.map((item) => (
        <li key={`${item.kind}:${item.name}`}>{signInLine(item)}</li>
      ))}
    </ul>
  );
}

function size(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

/** Settings › Data: Back up now, and Restore from backup on the next start. */
export function DataBackup({
  owner,
  canPick = true,
}: {
  owner: DataBackupOwner;
  /** Only the desktop app can pick a backup file to restore. */
  canPick?: boolean;
}) {
  const [state, setState] = useState<DataBackupState | null>(null);
  const [review, setReview] = useState<DataRestoreReview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const alive = useRef(true);
  const watched = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await owner.read();
      if (!alive.current) return;
      setState(next);
      const job = next.job;
      if (job && job.status !== 'running' && watched.current === job.kind) {
        watched.current = null;
        if (job.status === 'failed')
          setError(
            (job.code &&
              (JOB_FAILED[job.code] ??
                clientError({ code: job.code }).message)) ||
              JOB_FAILED[`${job.kind}_failed`],
          );
        else if (job.kind === 'backup')
          setNotice(
            `Saved “${job.name ?? next.last_backup_name}” in ${next.folder}.` +
              (job.skipped?.length
                ? ` Left out ${job.skipped.length === 1 ? 'a file' : `${job.skipped.length} files`} that couldn't be read: ${job.skipped.join(', ')}.`
                : ''),
          );
      }
    } catch (cause) {
      if (alive.current) setError(clientError(cause).message);
    }
  }, [owner]);

  useEffect(() => {
    alive.current = true;
    void refresh();
    return () => {
      alive.current = false;
    };
  }, [refresh]);

  const running = state?.job?.status === 'running';
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, refresh]);

  async function send(
    action: Action,
    extra?: { file_grant?: string; review_id?: string },
  ) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const receipt = await owner.send(action, extra);
      if (!alive.current) return null;
      setState(receipt.state);
      if (receipt.status === 'accepted')
        watched.current = action === 'backup' ? 'backup' : 'restore';
      return receipt;
    } catch (cause) {
      if (alive.current) setError(clientError(cause).message);
      return null;
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  async function chooseBackup() {
    setError('');
    setNotice('');
    const picked = await owner.pick();
    if (!alive.current || picked === null) return;
    if (picked === 'unavailable') {
      setError('Restoring needs the Row-Bot desktop app on this computer.');
      return;
    }
    const receipt = await send('inspect_restore', { file_grant: picked });
    if (receipt?.review) setReview(receipt.review);
  }

  async function confirmRestore() {
    if (!review) return;
    // The pending line below says what happens next.
    await send('restore', { review_id: review.review_id });
    setReview(null);
  }

  if (!state) return <p className="muted">Checking backups…</p>;
  if (!state.local_owner)
    return (
      <p className="muted">
        Backups are made and restored in the Row-Bot desktop app on this
        computer.
      </p>
    );

  const pending = state.pending_restore;
  const result = state.restore_result;
  const locked = busy || running;
  return (
    <div className="data-backup stack">
      {result && (
        <div
          className={`data-backup-result ${result.status === 'failed' ? 'is-danger' : ''}`}
          role="status"
        >
          {result.status === 'applied' ? (
            <>
              <strong>
                Restored the backup from{' '}
                <When value={result.source_created_at} fallback="earlier" />.
              </strong>
              {result.kept_aside && (
                <p>
                  Your previous profile is kept in the “{result.kept_aside}”
                  folder inside Row-Bot’s data folder.
                </p>
              )}
              {result.sign_in_again.length > 0 && (
                <p>
                  Backups never hold passwords or sign-ins. Set these up again:
                </p>
              )}
              <SignInList items={result.sign_in_again} />
            </>
          ) : (
            <strong>
              The restore didn’t finish, so Row-Bot kept your profile as it was.
            </strong>
          )}
          <Button disabled={locked} onClick={() => void send('dismiss_result')}>
            Done
          </Button>
        </div>
      )}
      <div className="settings-inline-row data-backup-row">
        <div>
          <strong>Back up now</strong>
          <p>
            Saves conversations, memories, workflows, designs and settings to
            the {state.folder} folder. Passwords, keys, sign-ins, caches and
            logs are left out.
          </p>
          <p className="muted">
            Last backup:{' '}
            {state.last_backup_at ? (
              <When value={state.last_backup_at} />
            ) : (
              'never'
            )}
          </p>
        </div>
        <Button
          variant="primary"
          disabled={locked}
          onClick={() => void send('backup')}
        >
          <HardDriveDownload size={16} aria-hidden />
          {running && state.job?.kind === 'backup'
            ? 'Backing up…'
            : 'Back up now'}
        </Button>
      </div>
      {notice && (
        <p className="settings-saved-note" role="status">
          {notice}
          {notice.startsWith('Saved') && state.last_backup_name && (
            <Button
              variant="ghost"
              disabled={locked}
              onClick={() => void send('reveal')}
            >
              <FolderOpen size={14} aria-hidden />
              Show in folder
            </Button>
          )}
        </p>
      )}
      <div className="settings-inline-row data-backup-row">
        <div>
          <strong>Restore from backup</strong>
          <p>
            Checks the backup first. Your current profile is kept aside, and
            nothing changes until Row-Bot restarts.
          </p>
        </div>
        {canPick ? (
          <Button
            disabled={locked || Boolean(pending)}
            onClick={() => void chooseBackup()}
          >
            <ArchiveRestore size={16} aria-hidden />
            {running && state.job?.kind === 'restore'
              ? 'Preparing…'
              : 'Restore from backup…'}
          </Button>
        ) : (
          <p className="muted">Open the Row-Bot desktop app to restore.</p>
        )}
      </div>
      {review && (
        <div
          className="data-backup-review"
          role="group"
          aria-label="Restore this backup?"
        >
          <strong>Restore “{review.source_name}”?</strong>
          <p>
            Made{' '}
            <When value={review.created_at} fallback="at an unknown time" /> by
            Row-Bot {review.app_version} · {review.files} files ·{' '}
            {size(review.bytes)}
          </p>
          <p>
            When Row-Bot next starts, this replaces your conversations,
            memories, workflows and settings. Your current profile is moved
            aside first, so it can be brought back.
          </p>
          {review.sign_in_again.length > 0 && (
            <p>Afterwards, set these up again:</p>
          )}
          <SignInList items={review.sign_in_again} />
          <div className="button-row">
            <Button
              variant="danger"
              disabled={locked}
              onClick={() => void confirmRestore()}
            >
              Restore on restart
            </Button>
            <Button disabled={locked} onClick={() => setReview(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {pending && (
        <div className="data-backup-pending" role="status">
          <p>
            A restore from “{pending.source_name}” will apply the next time
            Row-Bot starts.
          </p>
          <Button disabled={locked} onClick={() => void send('cancel_restore')}>
            Cancel restore
          </Button>
        </div>
      )}
      {error && (
        <p className="settings-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export default function ConnectedDataBackup() {
  const { controller, platform } = useRuntime();
  const [desktop, setDesktop] = useState(false);
  useEffect(() => {
    let live = true;
    void platform
      .discover()
      .then((value) => {
        if (live)
          setDesktop(value.status === 'ok' && value.value.kind === 'pywebview');
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [platform]);
  const owner = useRef<DataBackupOwner>({
    read: () => controller.dataBackup() as Promise<DataBackupState>,
    send: (action, extra) =>
      controller.executeDataBackup({
        command_id: crypto.randomUUID(),
        action,
        ...(extra ?? {}),
      }),
    pick: async () => {
      const picked = await platform.selectFile(undefined, {
        intentId: crypto.randomUUID(),
        intent: 'restore_backup',
        conversationId: null,
        destination: 'data-restore',
      });
      if (picked.status === 'cancelled') return null;
      if (picked.status !== 'ok') return 'unavailable';
      const value = picked.value as { kind?: string; reference?: string };
      if (value.kind !== 'file' || !value.reference) return 'unavailable';
      return value.reference;
    },
  });
  return <DataBackup owner={owner.current} canPick={desktop} />;
}
