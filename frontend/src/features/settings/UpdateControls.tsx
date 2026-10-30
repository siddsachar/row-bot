import { useCallback, useEffect, useRef, useState } from 'react';
import { remindLaterAbout } from '../shell/AttentionIndicator';
import {
  ArrowUpCircle,
  CheckCircle2,
  Clock,
  Download,
  History,
  PackageCheck,
  RefreshCw,
  RotateCcw,
  Server,
  ShieldAlert,
  SkipForward,
  TriangleAlert,
} from 'lucide-react';
import type {
  UpdateCommand,
  UpdateReceipt,
  UpdateSnapshot,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useOnThisComputer, useRuntime } from '../../runtime';
import { Button, Disclosure, IconButton, Toggle } from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';
import { absoluteTime, relativeTime } from '../../ui/format';
import { SettingsItem, StatusLine } from './anatomy';

type Owner = {
  load: (signal?: AbortSignal) => Promise<UpdateSnapshot>;
  send: (command: UpdateCommand) => Promise<UpdateReceipt>;
};

type Action = UpdateCommand['action'];
type Extra = Partial<Pick<UpdateCommand, 'when' | 'force' | 'enabled'>>;

const pendingKey = 'row-bot:updates:pending:v1';
const POLL_MS = 1000;
const MOVING = new Set(['checking', 'downloading', 'staging', 'applying']);
// Commands that mean the same whatever the status moved to since it was read.
const RESENDABLE = new Set([
  'check',
  'clear_skipped',
  'set_auto_check',
  'set_auto_download',
]);

const STEPS: Record<string, string> = {
  manifest: 'Reading the release',
  code: 'Downloading',
  libraries: 'Downloading libraries',
  clone: 'Copying the runtime',
  unpack: 'Unpacking',
  compile: 'Compiling',
  self_test: 'Testing the new version',
  finish: 'Finishing',
};

const HOST_STATES: Record<string, string> = {
  unknown: 'The host hasn’t reported its update status yet.',
  idle: 'Up to date, as far as the host knows.',
  checking: 'The host is checking for updates.',
  downloading: 'The host is downloading an update.',
  ready: 'The host has an update ready.',
  applying: 'The host is updating Row-Bot.',
  failed: 'The host’s last update failed.',
  paused: 'Updates are paused on the host.',
};

const HOST_ACTIONS: Record<string, string> = {
  check: 'a check',
  update_now: 'an update',
  skip: 'a skip',
  pause: 'a pause',
  resume: 'updates to resume',
};

function savedPending(): UpdateCommand | null {
  try {
    const raw = sessionStorage.getItem(pendingKey);
    if (!raw || raw.length > 2048) return null;
    const value = JSON.parse(raw) as UpdateCommand;
    return typeof value.command_id === 'string' &&
      typeof value.action === 'string'
      ? value
      : null;
  } catch {
    return null;
  }
}

function megabytes(bytes: number) {
  return `${(bytes / 1_000_000).toFixed(bytes < 10_000_000 ? 1 : 0)} MB`;
}

function githubUrl(value: string | null | undefined) {
  try {
    const url = new URL(value ?? '');
    return url.protocol === 'https:' &&
      (url.hostname === 'github.com' ||
        url.hostname.endsWith('.githubusercontent.com'))
      ? url.href
      : '';
  } catch {
    return '';
  }
}

function noticeFor(receipt: UpdateReceipt, command: UpdateCommand) {
  const next = receipt.snapshot;
  if (command.action === 'remove_legacy' && receipt.status === 'requested')
    return 'Approve the Windows prompt to remove the earlier Row-Bot.';
  if (receipt.status === 'requested')
    return 'Sent to the host. It applies the request on its own schedule.';
  if (receipt.status === 'failed')
    return command.action === 'check' || command.action === 'clear_skipped'
      ? 'The update check could not reach a verified release source. Try again later.'
      : 'That didn’t work. Refresh the update status and try again.';
  switch (command.action) {
    case 'check':
    case 'clear_skipped':
      if (
        next.engine.phase === 'downloading' ||
        next.engine.phase === 'staging'
      )
        return `Version ${next.engine.version} is downloading in the background.`;
      return next.available
        ? `Version ${next.available.version} is available.`
        : 'No update is available on this channel.';
    case 'skip':
      return 'Update preference saved.';
    case 'set_auto_check':
      return command.enabled
        ? 'Row-Bot checks for updates by itself.'
        : 'Row-Bot checks for updates only when you ask.';
    case 'set_auto_download':
      return command.enabled
        ? 'Updates download in the background.'
        : 'Updates download only when you choose.';
    case 'apply':
      return command.when === 'when_idle'
        ? 'Row-Bot restarts to update when it is idle.'
        : command.when === 'on_quit'
          ? 'The update installs the next time you quit Row-Bot.'
          : 'Restarting…';
    case 'remove_legacy':
      return 'Approve the Windows prompt to remove the earlier Row-Bot.';
    default:
      return '';
  }
}

export function UpdateControls({
  owner,
  onThisComputer = true,
  onChecked,
}: {
  owner: Owner;
  /** The desktop app on the computer Row-Bot runs on (#416). */
  onThisComputer?: boolean;
  /** Told the time of each check these controls see, for the page header. */
  onChecked?: (lastCheck: string | null) => void;
}) {
  const [snapshot, setSnapshot] = useState<UpdateSnapshot | null>(null);
  const [pending, setPending] = useState<UpdateCommand | null>(savedPending);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [deferred, setDeferred] = useState(false);
  const [guard, setGuard] = useState(false);
  const [repair, setRepair] = useState(false);
  const running = useRef(false);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      try {
        const value = await owner.load(signal);
        setSnapshot(value);
        setError('');
      } catch (cause) {
        if (!signal?.aborted) setError(clientError(cause).message);
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [owner],
  );
  useEffect(() => {
    const abort = new AbortController();
    void load(abort.signal);
    return () => abort.abort();
  }, [load]);
  const lastCheck = snapshot ? snapshot.last_check : undefined;
  useEffect(() => {
    if (lastCheck !== undefined) onChecked?.(lastCheck);
  }, [lastCheck, onChecked]);
  const phase = snapshot?.engine.phase ?? 'idle';
  // A command's notice ("…is downloading in the background") is about the
  // moment it ran: once the update moves on, the controls say where it is.
  useEffect(() => setNotice(''), [phase]);
  // Background work moves on its own: follow it while it does. A restart
  // takes the server away for a few seconds, so a failed read tries again.
  useEffect(() => {
    if (!MOVING.has(phase)) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = (delay: number) => {
      timer = setTimeout(() => {
        owner.load(abort.signal).then(
          (value) => {
            if (!abort.signal.aborted) setSnapshot(value);
          },
          () => {
            if (!abort.signal.aborted) read(Math.min(delay * 2, 10_000));
          },
        );
      }, delay);
    };
    read(POLL_MS);
    return () => {
      if (timer) clearTimeout(timer);
      abort.abort();
    };
  }, [phase, snapshot, owner]);

  async function execute(command: UpdateCommand) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      sessionStorage.setItem(pendingKey, JSON.stringify(command));
      setPending(command);
      let receipt: UpdateReceipt;
      try {
        receipt = await owner.send(command);
      } catch (cause) {
        // The status moved on since the page read it (a check finished, the
        // channel changed): a check or a switch means the same now, so read it
        // again and send it once more instead of asking the person to.
        if (
          clientError(cause).code !== 'update_changed' ||
          !RESENDABLE.has(command.action)
        )
          throw cause;
        const fresh = await owner.load();
        setSnapshot(fresh);
        command = {
          ...command,
          command_id: crypto.randomUUID(),
          expected_revision: fresh.revision,
        };
        sessionStorage.setItem(pendingKey, JSON.stringify(command));
        setPending(command);
        receipt = await owner.send(command);
      }
      if (receipt.command_id !== command.command_id)
        throw new Error('Update receipt did not match this action');
      sessionStorage.removeItem(pendingKey);
      setPending(null);
      setSnapshot(receipt.snapshot);
      setNotice(noticeFor(receipt, command));
    } catch (cause) {
      const issue = clientError(cause);
      if (
        [
          'update_changed',
          'invalid_update_command',
          'update_command_conflict',
          'update_not_ready',
          'update_restart_busy',
          'update_busy',
          'update_no_previous',
          'update_unavailable',
        ].includes(issue.code)
      ) {
        sessionStorage.removeItem(pendingKey);
        setPending(null);
      }
      if (issue.code === 'update_restart_busy') {
        // Work started since the page was read: ask again.
        setGuard(true);
        void load();
      }
      setError(issue.message);
    } finally {
      running.current = false;
      setBusy(false);
    }
  }

  function send(action: Action, version = '', extra: Extra = {}) {
    if (!snapshot || pending || busy) return;
    void execute({
      command_id: crypto.randomUUID(),
      expected_revision: snapshot.revision,
      action,
      version,
      ...extra,
    });
  }

  const locked = busy || !!pending;
  const available = snapshot?.available ?? null;
  const skipped = snapshot?.skipped_versions ?? [];
  const engine = snapshot?.engine;
  const external = snapshot?.mode === 'external';
  const self = snapshot?.mode === 'self';
  const readyVersion = engine?.phase === 'ready' ? engine.version : null;
  const work = snapshot?.active_work ?? [];
  const notesFor = (version: string | null | undefined) =>
    available && version === available.version ? available : null;

  function restartNow() {
    if (!readyVersion) return;
    if (work.length) {
      setGuard(true);
      return;
    }
    send('apply', readyVersion, { when: 'now' });
  }

  function whatsNew(release: NonNullable<UpdateSnapshot['available']>) {
    const releaseUrl = githubUrl(release.html_url);
    return (
      <Disclosure
        className="settings-update-notes-disclosure"
        summary="What’s new"
      >
        <pre className="settings-update-notes">{release.notes}</pre>
        {releaseUrl && (
          <a href={releaseUrl} target="_blank" rel="noopener noreferrer">
            View release notes
          </a>
        )}
      </Disclosure>
    );
  }

  const engineRow = (() => {
    if (!snapshot || !engine || !self) return null;
    const version = engine.version ?? '';
    switch (engine.phase) {
      case 'downloading':
      case 'staging': {
        const step = STEPS[engine.step ?? ''] ?? 'Preparing';
        const percent =
          engine.total > 0
            ? Math.min(
                100,
                Math.floor((engine.downloaded / engine.total) * 100),
              )
            : null;
        return (
          <SettingsItem
            label={`${engine.phase === 'staging' ? 'Preparing' : 'Downloading'} version ${version}`}
            icon={<Download size={16} aria-hidden />}
            tone="accent"
            bind={false}
            help={
              engine.phase === 'downloading' && engine.total > 0
                ? `${step}: ${megabytes(engine.downloaded)} of ${megabytes(engine.total)}. Only what changed is downloaded.`
                : `${step}… Row-Bot keeps working while it prepares the update.`
            }
            control={
              <Button disabled={locked} onClick={() => send('cancel')}>
                Cancel
              </Button>
            }
          >
            {engine.phase === 'downloading' && percent !== null && (
              <progress
                aria-label="Update download"
                value={engine.downloaded}
                max={engine.total}
              >
                {percent}%
              </progress>
            )}
          </SettingsItem>
        );
      }
      case 'ready': {
        const release = notesFor(version);
        return (
          <SettingsItem
            label={`Version ${version} is ready`}
            icon={<ArrowUpCircle size={16} aria-hidden />}
            tone="accent"
            bind={false}
            help={
              engine.apply_when === 'when_idle'
                ? 'Row-Bot restarts to install it as soon as nothing is running.'
                : engine.apply_when === 'on_quit'
                  ? 'It installs the next time you quit Row-Bot.'
                  : 'Restart now to install it, or it installs the next time you quit Row-Bot.'
            }
            status={
              work.length ? (
                <StatusLine tone="warning">{work.join(' · ')}</StatusLine>
              ) : undefined
            }
            control={
              <Button
                variant="primary"
                aria-label={`Restart now to install version ${version}`}
                disabled={locked}
                onClick={restartNow}
              >
                <RefreshCw size={14} aria-hidden />
                Restart now
              </Button>
            }
          >
            {release && whatsNew(release)}
          </SettingsItem>
        );
      }
      case 'applying':
        return (
          <SettingsItem
            label={`Installing version ${version}`}
            icon={<RefreshCw size={16} aria-hidden />}
            tone="accent"
            bind={false}
            help={<span role="status">{engine.message || 'Restarting…'}</span>}
          />
        );
      case 'verified':
        return (
          <SettingsItem
            label={`Updated to ${version}`}
            icon={<CheckCircle2 size={16} aria-hidden />}
            tone="accent"
            bind={false}
            help={
              engine.message ||
              'The update installed and Row-Bot started normally.'
            }
          />
        );
      case 'failed': {
        const installer =
          engine.error === 'full_installer'
            ? githubUrl(engine.full_installer_url)
            : '';
        // It didn't start, so the launcher went back: offered again only
        // when the person asks for it.
        const again =
          engine.error === 'rolled_back' &&
          available?.version === version &&
          available.verified_manifest;
        return (
          <SettingsItem
            label={
              engine.error === 'full_installer'
                ? `Version ${version} needs the full installer`
                : engine.error === 'rolled_back'
                  ? `Version ${version} didn’t start`
                  : engine.error === 'installed'
                    ? `Version ${version} is already installed`
                    : `Version ${version} could not be prepared`
            }
            icon={<TriangleAlert size={16} aria-hidden />}
            tone="2"
            bind={false}
            help={
              <span role="alert">
                {engine.message || 'Preparing the update failed.'}
                {engine.error === 'full_installer' &&
                  ' It changes Row-Bot’s runtime, so it can’t be prepared in the background.'}
              </span>
            }
            control={
              <>
                {installer && (
                  <a
                    className="button primary"
                    href={installer}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <Download size={14} aria-hidden />
                    Download the full installer
                  </a>
                )}
                {engine.retryable && available?.version === version && (
                  <Button
                    disabled={locked}
                    onClick={() => send('retry', version)}
                  >
                    Try again
                  </Button>
                )}
                {again && (
                  <Button
                    aria-label={`Download version ${version} again`}
                    disabled={locked}
                    onClick={() => send('download', version)}
                  >
                    <Download size={14} aria-hidden />
                    Download again
                  </Button>
                )}
              </>
            }
          />
        );
      }
      default:
        return null;
    }
  })();

  const host = snapshot?.host ?? null;
  const hostRow =
    external && host?.state === 'unknown' ? (
      <SettingsItem
        label="Updated by this computer’s host"
        icon={<Server size={16} aria-hidden />}
        tone="neutral"
        bind={false}
        help="This Row-Bot doesn’t update itself. For Docker, pull a newer image and start the container again."
      />
    ) : external && host ? (
      <SettingsItem
        label="Updated by this computer’s host"
        icon={<Server size={16} aria-hidden />}
        tone="neutral"
        bind={false}
        help={host.message || HOST_STATES[host.state]}
        status={
          <>
            {host.available_version && (
              <StatusLine>
                Version {host.available_version} is available
              </StatusLine>
            )}
            {host.request && (
              <StatusLine tone="info" pulse>
                Waiting for the host to act on{' '}
                {HOST_ACTIONS[host.request.action]}
              </StatusLine>
            )}
            {host.updated_at && (
              <StatusLine>
                Reported{' '}
                <time
                  dateTime={host.updated_at}
                  title={absoluteTime(host.updated_at)}
                >
                  {relativeTime(host.updated_at)}
                </time>
              </StatusLine>
            )}
          </>
        }
        control={
          <>
            {host.available_version && (
              <Button
                variant="primary"
                disabled={locked}
                onClick={() =>
                  send('apply', host.available_version ?? '', { when: 'now' })
                }
              >
                Update now
              </Button>
            )}
            {host.available_version && (
              <IconButton
                label={`Skip version ${host.available_version}`}
                disabled={locked}
                onClick={() => send('skip', host.available_version ?? '')}
              >
                <SkipForward size={16} aria-hidden />
              </IconButton>
            )}
            <Button
              disabled={locked}
              onClick={() => send(host.state === 'paused' ? 'resume' : 'pause')}
            >
              {host.state === 'paused' ? 'Resume updates' : 'Pause updates'}
            </Button>
          </>
        }
      >
        {githubUrl(host.notes_url) && (
          <a
            href={githubUrl(host.notes_url)}
            target="_blank"
            rel="noopener noreferrer"
          >
            What’s new
          </a>
        )}
      </SettingsItem>
    ) : null;

  const heldBack =
    available && snapshot?.held_back?.version === available.version
      ? snapshot.held_back
      : null;
  const offered =
    available &&
    self &&
    !(
      engine &&
      engine.version === available.version &&
      ['downloading', 'staging', 'ready', 'applying', 'failed'].includes(
        engine.phase,
      )
    );

  return (
    <div className="settings-update-controls" aria-busy={busy || loading}>
      {loading && !snapshot && (
        <SettingsItem label="Row-Bot" help="Reading the saved update state…" />
      )}
      {error && (
        <SettingsItem
          label="Update action needs attention"
          help={<span role="alert">{error}</span>}
          control={
            <Button onClick={() => void load()}>Refresh update state</Button>
          }
        />
      )}
      {snapshot && (
        <SettingsItem
          label={`Row-Bot ${snapshot.current_version}`}
          icon={<PackageCheck size={16} aria-hidden />}
          tone="accent"
          bind={false}
          help={
            snapshot.mode === 'unavailable' ? (
              'Development checkout: installed-app updates are unavailable.'
            ) : notice ? (
              <span role="status">{notice}</span>
            ) : phase === 'checking' ? (
              <span role="status">Checking for updates…</span>
            ) : external ? (
              'This Row-Bot’s host installs its updates.'
            ) : snapshot.last_check ? (
              <>
                Checked{' '}
                <time
                  dateTime={snapshot.last_check}
                  title={absoluteTime(snapshot.last_check)}
                >
                  {relativeTime(snapshot.last_check)}
                </time>
              </>
            ) : (
              'Not checked yet.'
            )
          }
          status={
            snapshot.feed_url || (skipped.length && self) ? (
              <>
                {snapshot.feed_url && (
                  <StatusLine tone="warning">
                    Testing: updates come from {snapshot.feed_url}, not GitHub
                  </StatusLine>
                )}
                {self &&
                  skipped.map((version) => (
                    // The server reports only the skip holding back the
                    // release on offer (B261).
                    <StatusLine
                      key={version}
                      action={
                        <Button
                          variant="ghost"
                          className="settings-link"
                          disabled={locked}
                          onClick={() => send('clear_skipped')}
                        >
                          Show it again
                        </Button>
                      }
                    >
                      You skipped {version}
                    </StatusLine>
                  ))}
              </>
            ) : undefined
          }
          control={
            snapshot.mode === 'unavailable' ? undefined : (
              <Button
                aria-label="Check for updates"
                disabled={locked || phase === 'checking'}
                onClick={() => send('check')}
              >
                <RefreshCw size={14} aria-hidden />
                Check now
              </Button>
            )
          }
        >
          {pending && (
            <div role="status" className="settings-update-recovery">
              <p>Check the original update action before starting another.</p>
              <Button disabled={busy} onClick={() => void execute(pending)}>
                Check update action
              </Button>
            </div>
          )}
        </SettingsItem>
      )}
      {hostRow}
      {engineRow}
      {offered && !deferred && (
        <SettingsItem
          label={`Version ${available.version} is available`}
          icon={<ArrowUpCircle size={16} aria-hidden />}
          tone="2"
          bind={false}
          help={
            !available.verified_manifest
              ? 'No verified release checksum is available, so it can’t be downloaded.'
              : heldBack?.reason === 'rolled_back'
                ? 'It didn’t start last time, so Row-Bot went back. It won’t download by itself; download it again only if you want to retry.'
                : heldBack
                  ? 'You went back from this version with Repair, so it won’t download by itself.'
                  : snapshot!.auto_download
                    ? 'It downloads in the background. Only what changed is downloaded.'
                    : 'Download it in the background; nothing changes until Row-Bot restarts.'
          }
          control={
            <>
              {available.verified_manifest && (
                <Button
                  variant="primary"
                  aria-label={`Download version ${available.version}`}
                  disabled={locked}
                  onClick={() => send('download', available.version)}
                >
                  <Download size={14} aria-hidden />
                  Download
                </Button>
              )}
              <IconButton
                label={`Skip version ${available.version}`}
                disabled={locked}
                onClick={() => send('skip', available.version)}
              >
                <SkipForward size={16} aria-hidden />
              </IconButton>
              <IconButton
                label="Remind me later"
                onClick={() => {
                  // The sidebar indicator leaves it out for a day, on this
                  // device (parity row 12).
                  remindLaterAbout(available.version);
                  setDeferred(true);
                }}
              >
                <Clock size={16} aria-hidden />
              </IconButton>
            </>
          }
        >
          {whatsNew(available)}
        </SettingsItem>
      )}
      {offered && deferred && (
        <SettingsItem
          label={`Version ${available.version} is available`}
          help="Hidden until tomorrow on this device."
          control={
            <Button variant="ghost" onClick={() => setDeferred(false)}>
              Show version {available.version}
            </Button>
          }
        />
      )}
      {snapshot && self && (
        <>
          <SettingsItem
            label="Check for updates automatically"
            help="Row-Bot looks for a new release a few times a day."
            control={
              <Toggle
                label="Check for updates automatically"
                checked={snapshot.auto_check}
                disabled={locked}
                onChange={(event) =>
                  send('set_auto_check', '', {
                    enabled: event.currentTarget.checked,
                  })
                }
              />
            }
          />
          <SettingsItem
            label="Download updates automatically"
            help="Downloads only what changed and prepares it next to this version. Nothing changes until Row-Bot restarts or you quit it."
            control={
              <Toggle
                label="Download updates automatically"
                checked={snapshot.auto_download}
                disabled={locked}
                onChange={(event) =>
                  send('set_auto_download', '', {
                    enabled: event.currentTarget.checked,
                  })
                }
              />
            }
          />
        </>
      )}
      {snapshot?.previous_version && self && (
        <SettingsItem
          label="Repair"
          icon={<History size={16} aria-hidden />}
          help={`If this version misbehaves, go back to Row-Bot ${snapshot.previous_version}. Your data stays as it is.`}
          control={
            <Button
              aria-label={`Go back to ${snapshot.previous_version}`}
              disabled={locked}
              onClick={() => setRepair(true)}
            >
              <RotateCcw size={14} aria-hidden />
              Go back to {snapshot.previous_version}
            </Button>
          }
        />
      )}
      {snapshot?.legacy_install && (
        <SettingsItem
          label="An earlier Row-Bot is still installed for all users"
          icon={<ShieldAlert size={16} aria-hidden />}
          help={
            onThisComputer
              ? 'Removing it needs administrator approval. Your data is kept.'
              : 'Removing it asks for administrator approval on the computer Row-Bot runs on, so it’s done in the Row-Bot desktop app there.'
          }
          control={
            // Windows' prompt appears on that computer's own screen.
            onThisComputer ? (
              <Button
                aria-label="Remove the old Row-Bot"
                disabled={locked}
                onClick={() => send('remove_legacy')}
              >
                Remove the old Row-Bot
              </Button>
            ) : undefined
          }
        />
      )}
      <ModalTask
        open={guard && !!readyVersion}
        onOpenChange={setGuard}
        title="Restart now?"
        description="Restarting stops what Row-Bot is doing right now."
      >
        <ul className="settings-update-work">
          {work.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
        <div className="action-cluster">
          <Button
            variant="primary"
            data-initial-focus
            onClick={() => {
              setGuard(false);
              send('apply', readyVersion ?? '', { when: 'when_idle' });
            }}
          >
            Restart when idle
          </Button>
          <Button
            onClick={() => {
              setGuard(false);
              send('apply', readyVersion ?? '', { when: 'on_quit' });
            }}
          >
            When I quit
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              setGuard(false);
              send('apply', readyVersion ?? '', { when: 'now', force: true });
            }}
          >
            Restart anyway
          </Button>
        </div>
      </ModalTask>
      <ModalTask
        open={repair && !!snapshot?.previous_version}
        onOpenChange={setRepair}
        title={`Go back to ${snapshot?.previous_version ?? ''}?`}
        description="Row-Bot restarts into the earlier version. You can update again later."
      >
        <div className="action-cluster">
          <Button
            variant="primary"
            data-initial-focus
            onClick={() => {
              setRepair(false);
              send('rollback', snapshot?.previous_version ?? '');
            }}
          >
            Go back and restart
          </Button>
          <Button onClick={() => setRepair(false)}>Cancel</Button>
        </div>
      </ModalTask>
    </div>
  );
}

export default function ConnectedUpdateControls({
  onChecked,
}: {
  onChecked?: (lastCheck: string | null) => void;
}) {
  const { controller } = useRuntime();
  const onThisComputer = useOnThisComputer();
  const owner = useRef<Owner>({
    load: (signal) => controller.updates(signal),
    send: (command) => controller.updateCommand(command),
  });
  return (
    <UpdateControls
      owner={owner.current}
      onThisComputer={onThisComputer}
      onChecked={onChecked}
    />
  );
}
