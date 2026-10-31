import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArchiveRestore,
  Download,
  FolderOpen,
  HardDriveDownload,
} from 'lucide-react';
import type {
  DataBackupCommand,
  DataBackupJob,
  DataBackupReceipt,
  DataBackupSignIn,
  DataBackupState,
  DataRestoreReview,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { usePlace } from '../../api/place';
import { saveBrowserDownload } from '../../platform/download';
import { useRuntime } from '../../runtime';
import { Button } from '../../ui/primitives';
import { humanizeToken } from '../../ui/format';
import { When } from '../../ui/When';

type Action = DataBackupCommand['action'];
type Extra = { file_grant?: string; review_id?: string; upload_id?: string };

export type DataBackupOwner = {
  read: () => Promise<DataBackupState>;
  send: (action: Action, extra?: Extra) => Promise<DataBackupReceipt>;
  /** The desktop pick of one backup archive, or null (cancelled or no desktop). */
  pick: () => Promise<string | null | 'unavailable'>;
  /** Save the named last backup on this device: null once saved, else why not. */
  download: (name: string) => Promise<string | null>;
  /** Send an archive to restore from (no desktop picker); its upload id. */
  upload: (
    file: File,
    progress: (sent: number) => void,
    signal: AbortSignal,
  ) => Promise<string>;
  /** Let a sent archive go when its restore isn't confirmed. */
  discard: (upload: string) => Promise<unknown>;
};

const POLL_MS = 1000;

const JOB_FAILED: Record<string, string> = {
  backup_failed: "The backup didn't finish. Try again.",
  restore_failed:
    "The restore couldn't be prepared. Your profile is unchanged.",
};

const DOWNLOAD_FAILED: Record<string, string> = {
  user_gesture_required: 'Click Download again to save the backup.',
  desktop_download_unavailable:
    'Open Row-Bot in a web browser to download the backup.',
  operation_failed: "The backup couldn't be downloaded. Try again.",
};

const UPLOAD_FAILED: Record<string, string> = {
  upload_expired: 'Sending the backup took too long. Choose it again.',
  upload_incomplete: "The backup didn't finish sending. Choose it again.",
};

function problem(cause: unknown, words: Record<string, string> = {}) {
  const error = clientError(cause);
  return words[error.code] ?? error.message;
}

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

/** The desktop app picks the file itself, when its picker can't open. */
const RESTORE_IN_DESKTOP_APP =
  'Restoring picks the backup file in the Row-Bot desktop app on the computer Row-Bot runs on.';

/**
 * Settings › Data: Back up now, and Restore from backup on the next start,
 * from any signed-in device. A device that can't open the Backups folder
 * downloads the backup, and sends the file it restores from. On a hosted
 * Row-Bot, Move Row-Bot makes a fresh backup and downloads it, then says what
 * that backup's own manifest leaves out and what to sign in to again (U9).
 */
export function DataBackup({
  owner,
  canPick = true,
}: {
  owner: DataBackupOwner;
  /** The desktop app picks a backup file to restore; other devices send one. */
  canPick?: boolean;
}) {
  const [state, setState] = useState<DataBackupState | null>(null);
  const [review, setReview] = useState<DataRestoreReview | null>(null);
  const [busy, setBusy] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [sending, setSending] = useState<{ sent: number; size: number } | null>(
    null,
  );
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  // Move Row-Bot (hosted): the backup made for moving, and whether it was saved.
  const [moved, setMoved] = useState<DataBackupJob | null>(null);
  const [saved, setSaved] = useState(false);
  const hosted = usePlace().kind === 'hosted';
  const alive = useRef(true);
  const watched = useRef<string | null>(null);
  const moving = useRef(false);
  const files = useRef<HTMLInputElement>(null);
  const transfer = useRef<AbortController | null>(null);
  // The archive this page sent, until its restore is confirmed or let go.
  const uploaded = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await owner.read();
      if (!alive.current) return;
      setState(next);
      const job = next.job;
      if (job && job.status !== 'running' && watched.current === job.kind) {
        watched.current = null;
        const forMove = moving.current && job.kind === 'backup';
        moving.current = false;
        if (job.status === 'failed')
          setError(
            (job.code &&
              (JOB_FAILED[job.code] ??
                clientError({ code: job.code }).message)) ||
              JOB_FAILED[`${job.kind}_failed`],
          );
        else if (forMove) {
          // Saved at once while the click still counts (a browser saves only
          // just after one); otherwise Download backup saves it.
          setMoved(job);
          setSaved(false);
          if (job.name)
            void owner.download(job.name).then(
              (reason) => alive.current && setSaved(reason === null),
              () => undefined,
            );
        } else if (job.kind === 'backup')
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

  const forget = useCallback(() => {
    const upload = uploaded.current;
    uploaded.current = null;
    if (upload) void owner.discard(upload).catch(() => undefined);
  }, [owner]);

  useEffect(() => {
    alive.current = true;
    void refresh();
    return () => {
      alive.current = false;
      transfer.current?.abort();
      forget();
    };
  }, [refresh, forget]);

  const running = state?.job?.status === 'running';
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, refresh]);

  async function send(action: Action, extra?: Extra) {
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

  async function download(name = state?.last_backup_name) {
    if (!name) return;
    setError('');
    setDownloading(true);
    try {
      // Called straight from the click: the browser saves only for a gesture.
      const reason = await owner.download(name);
      if (!alive.current) return;
      if (reason)
        setError(DOWNLOAD_FAILED[reason] ?? DOWNLOAD_FAILED.operation_failed);
      else if (name === moved?.name) setSaved(true);
    } catch (cause) {
      if (!alive.current) return;
      setError(problem(cause));
      // Gone, or a newer backup since: show the one there is now.
      if (clientError(cause).code === 'backup_unavailable') void refresh();
    } finally {
      if (alive.current) setDownloading(false);
    }
  }

  /** Move Row-Bot: save the backup made for moving, or make a fresh one. */
  function moveOut() {
    if (moved?.name && !saved) {
      void download(moved.name);
      return;
    }
    moving.current = true;
    void send('backup').then((receipt) => {
      if (receipt?.status !== 'accepted') moving.current = false;
    });
  }

  async function chooseBackup() {
    setError('');
    setNotice('');
    const picked = await owner.pick();
    if (!alive.current || picked === null) return;
    if (picked === 'unavailable') {
      setError(RESTORE_IN_DESKTOP_APP);
      return;
    }
    const receipt = await send('inspect_restore', { file_grant: picked });
    if (receipt?.review) setReview(receipt.review);
  }

  async function sendBackup(file: File) {
    setError('');
    setNotice('');
    setReview(null);
    forget();
    const control = new AbortController();
    transfer.current = control;
    setSending({ sent: 0, size: file.size });
    let upload: string;
    try {
      upload = await owner.upload(
        file,
        (sent) => {
          if (alive.current) setSending({ sent, size: file.size });
        },
        control.signal,
      );
    } catch (cause) {
      if (alive.current && !control.signal.aborted)
        setError(problem(cause, UPLOAD_FAILED));
      return;
    } finally {
      if (transfer.current === control) transfer.current = null;
      if (alive.current) setSending(null);
    }
    uploaded.current = upload;
    if (!alive.current) {
      forget();
      return;
    }
    // Checked exactly as a picked file is; refused files are let go.
    const receipt = await send('inspect_restore', { upload_id: upload });
    if (receipt?.review) setReview(receipt.review);
    else forget();
  }

  function cancelReview() {
    setReview(null);
    forget();
  }

  async function confirmRestore() {
    if (!review) return;
    // The pending line below says what happens next.
    const receipt = await send('restore', { review_id: review.review_id });
    setReview(null);
    // Once staged, Row-Bot deletes the sent archive itself.
    if (receipt) uploaded.current = null;
    else forget();
  }

  if (!state) return <p className="muted">Checking backups…</p>;

  const pending = state.pending_restore;
  const result = state.restore_result;
  const locked = busy || running;
  const percent = sending
    ? Math.floor((sending.sent / Math.max(1, sending.size)) * 100)
    : 0;
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
      {hosted && (
        <div className="settings-inline-row data-backup-row">
          <div>
            <strong>Move Row-Bot</strong>
            <p>
              Download a backup, then install Row-Bot on your computer, open
              Settings › Data and choose Restore from backup.
            </p>
            {moved && (
              <div className="data-backup-moved" role="status">
                <p>
                  {saved
                    ? `Downloaded “${moved.name}”.`
                    : `“${moved.name}” is ready. Choose Download backup to save it.`}
                </p>
                {Boolean(moved.left_out?.length) && (
                  <p>Not in the backup: {moved.left_out!.join(' · ')}</p>
                )}
                {Boolean(moved.sign_in_again?.length) && (
                  <>
                    <p>After moving, set these up again:</p>
                    <SignInList items={moved.sign_in_again!} />
                  </>
                )}
              </div>
            )}
          </div>
          <Button
            variant="primary"
            disabled={locked || downloading}
            onClick={moveOut}
          >
            <Download size={16} aria-hidden />
            {running && state.job?.kind === 'backup'
              ? 'Backing up…'
              : downloading
                ? 'Downloading…'
                : 'Download backup'}
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
            {/* Show in folder needs that computer's screen; other devices download it. */}
            {!state.reveal_available && state.last_backup_name && (
              <Button
                variant="ghost"
                disabled={downloading}
                onClick={() => void download()}
              >
                <Download size={14} aria-hidden />
                {downloading ? 'Downloading…' : 'Download'}
              </Button>
            )}
          </p>
        </div>
        <Button
          variant={hosted ? 'secondary' : 'primary'}
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
          {notice.startsWith('Saved') &&
            state.last_backup_name &&
            // The file manager opens on the computer Row-Bot runs on.
            state.reveal_available && (
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
            {canPick
              ? 'Checks the backup first.'
              : 'Sends the backup file to Row-Bot and checks it first.'}{' '}
            {hosted
              ? 'A backup from Row-Bot on your computer works too.'
              : 'A backup from a hosted Row-Bot works too.'}{' '}
            Your current profile is kept aside, and nothing changes until
            Row-Bot restarts.
          </p>
          {sending && (
            <p className="muted" role="status">
              Sending the backup… {percent}% of {size(sending.size)}
            </p>
          )}
        </div>
        {sending ? (
          <Button onClick={() => transfer.current?.abort()}>Stop</Button>
        ) : (
          <Button
            disabled={locked || Boolean(pending)}
            onClick={() =>
              canPick ? void chooseBackup() : files.current?.click()
            }
          >
            <ArchiveRestore size={16} aria-hidden />
            {running && state.job?.kind === 'restore'
              ? 'Preparing…'
              : 'Restore from backup…'}
          </Button>
        )}
        {!canPick && (
          <input
            ref={files}
            type="file"
            accept=".zip,application/zip"
            hidden
            aria-label="Backup file to restore"
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = '';
              if (file) void sendBackup(file);
            }}
          />
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
            <Button disabled={locked} onClick={cancelReview}>
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
    download: async (name) => {
      // The server's own refusal says more than "the download failed".
      let refused: unknown;
      const saved = await saveBrowserDownload(async () => {
        try {
          return await controller.downloadDataBackup(name);
        } catch (cause) {
          refused = cause;
          throw cause;
        }
      }, name);
      if (refused !== undefined) throw refused;
      return saved.status === 'unavailable' ? saved.reason : null;
    },
    upload: (file, progress, signal) =>
      controller.uploadDataBackup(file, progress, signal),
    discard: (upload) => controller.cancelDataBackupUpload(upload),
  });
  return <DataBackup owner={owner.current} canPick={desktop} />;
}
