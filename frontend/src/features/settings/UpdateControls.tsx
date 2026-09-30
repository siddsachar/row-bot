import { useCallback, useEffect, useRef, useState } from 'react';
import { remindLaterAbout } from '../shell/AttentionIndicator';
import {
  ArrowUpCircle,
  Clock,
  Download,
  PackageCheck,
  RefreshCw,
  SkipForward,
} from 'lucide-react';
import type {
  UpdateCommand,
  UpdateInstallCommand,
  UpdateInstallStatus,
  UpdateReceipt,
  UpdateSnapshot,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Button, Disclosure, IconButton } from '../../ui/primitives';
import { absoluteTime, relativeTime } from '../../ui/format';
import { SettingsItem, StatusLine } from './anatomy';

type Owner = {
  load: (signal?: AbortSignal) => Promise<UpdateSnapshot>;
  send: (command: UpdateCommand) => Promise<UpdateReceipt>;
  startInstall?: (
    command: UpdateInstallCommand,
  ) => Promise<UpdateInstallStatus>;
  installStatus?: (commandId: string) => Promise<UpdateInstallStatus>;
  cancelInstall?: (commandId: string) => Promise<UpdateInstallStatus>;
};

const pendingKey = 'row-bot:updates:pending:v1';
const installKey = 'row-bot:updates:install:v1';

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

function savedInstall(): UpdateInstallCommand | null {
  try {
    const raw = sessionStorage.getItem(installKey);
    if (!raw || raw.length > 2048) return null;
    const value = JSON.parse(raw) as UpdateInstallCommand;
    return typeof value.command_id === 'string' &&
      typeof value.version === 'string'
      ? value
      : null;
  } catch {
    return null;
  }
}

export function UpdateControls({ owner }: { owner: Owner }) {
  const [snapshot, setSnapshot] = useState<UpdateSnapshot | null>(null);
  const [pending, setPending] = useState<UpdateCommand | null>(savedPending);
  const [installCommand, setInstallCommand] =
    useState<UpdateInstallCommand | null>(savedInstall);
  const [installStatus, setInstallStatus] =
    useState<UpdateInstallStatus | null>(null);
  const [installError, setInstallError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [deferred, setDeferred] = useState(false);
  const running = useRef(false);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      try {
        const value = await owner.load(signal);
        setSnapshot(value);
        if (savedInstall()?.version === value.current_version) {
          sessionStorage.removeItem(installKey);
          setInstallCommand(null);
          setInstallStatus(null);
        }
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
  useEffect(() => {
    if (!installCommand || !owner.installStatus) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const value = await owner.installStatus!(installCommand.command_id);
        if (stopped || value.command_id !== installCommand.command_id) return;
        setInstallStatus(value);
        setInstallError('');
        if (value.phase === 'downloading' || value.phase === 'cancel_requested')
          timer = setTimeout(() => void poll(), 1000);
      } catch (cause) {
        if (!stopped) setInstallError(clientError(cause).message);
      }
    };
    void poll();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [installCommand, owner]);

  async function startInstall(command: UpdateInstallCommand) {
    if (!owner.startInstall || !snapshot || busy || running.current) return;
    running.current = true;
    setBusy(true);
    setInstallError('');
    let reserved = false;
    try {
      sessionStorage.setItem(installKey, JSON.stringify(command));
      reserved = true;
      const result = await owner.startInstall(command);
      if (result.command_id !== command.command_id)
        throw new Error('Installer receipt did not match this action');
      setInstallStatus(result);
      setInstallCommand(command);
    } catch (cause) {
      const issue = clientError(cause);
      const rejected = [
        'update_changed',
        'update_unavailable',
        'update_install_busy',
        'invalid_update_command',
        'update_command_conflict',
      ].includes(issue.code);
      if (!reserved || rejected) {
        if (reserved) sessionStorage.removeItem(installKey);
        setInstallCommand(null);
        setInstallError(issue.message);
      } else {
        setInstallCommand(command);
        setInstallError(
          `${issue.message} Check the original installation before retrying.`,
        );
      }
    } finally {
      running.current = false;
      setBusy(false);
    }
  }

  async function cancelInstall() {
    if (!installCommand || !owner.cancelInstall || busy) return;
    setBusy(true);
    try {
      setInstallStatus(await owner.cancelInstall(installCommand.command_id));
      setInstallError('');
    } catch (cause) {
      setInstallError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  async function execute(command: UpdateCommand) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      sessionStorage.setItem(pendingKey, JSON.stringify(command));
      setPending(command);
      const receipt = await owner.send(command);
      if (receipt.command_id !== command.command_id)
        throw new Error('Update receipt did not match this action');
      sessionStorage.removeItem(pendingKey);
      setPending(null);
      setSnapshot(receipt.snapshot);
      setNotice(
        receipt.status === 'failed'
          ? 'The update check could not reach a verified release source. Try again later.'
          : command.action === 'skip'
            ? 'Update preference saved.'
            : // Check, or Show it again (which checks).
              receipt.snapshot.available
              ? `Version ${receipt.snapshot.available.version} is available.`
              : 'No update is available on this channel.',
      );
    } catch (cause) {
      const issue = clientError(cause);
      if (
        [
          'update_changed',
          'invalid_update_command',
          'update_command_conflict',
        ].includes(issue.code)
      ) {
        sessionStorage.removeItem(pendingKey);
        setPending(null);
      }
      setError(issue.message);
    } finally {
      running.current = false;
      setBusy(false);
    }
  }

  function send(action: UpdateCommand['action'], version = '') {
    if (!snapshot || pending || installCommand || busy) return;
    void execute({
      command_id: crypto.randomUUID(),
      expected_revision: snapshot.revision,
      action,
      version,
    });
  }

  const releaseUrl = (() => {
    try {
      const url = new URL(snapshot?.available?.html_url ?? '');
      return url.protocol === 'https:' && url.hostname === 'github.com'
        ? url.href
        : '';
    } catch {
      return '';
    }
  })();
  const locked = busy || !!pending || !!installCommand;
  const available = snapshot?.available ?? null;
  const skipped = snapshot?.skipped_versions ?? [];
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
            snapshot.dev_install ? (
              'Development checkout: installed-app updates are unavailable.'
            ) : notice ? (
              <span role="status">{notice}</span>
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
            skipped.length
              ? skipped.map((version) => (
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
                ))
              : undefined
          }
          control={
            snapshot.dev_install ? undefined : (
              <Button
                aria-label="Check for updates"
                disabled={locked}
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
          {installError && <p role="alert">{installError}</p>}
        </SettingsItem>
      )}
      {installCommand && (
        <SettingsItem
          label={
            installStatus?.message ??
            'Checking the original installation status.'
          }
          icon={<Download size={16} aria-hidden />}
          tone="accent"
          bind={false}
          control={
            <>
              {(installStatus?.phase === 'downloading' ||
                installStatus?.phase === 'cancel_requested') && (
                <Button
                  disabled={busy || installStatus.phase === 'cancel_requested'}
                  onClick={() => void cancelInstall()}
                >
                  Cancel download
                </Button>
              )}
              {(installStatus?.phase === 'cancelled' ||
                installStatus?.phase === 'failed') && (
                <Button
                  onClick={() => {
                    sessionStorage.removeItem(installKey);
                    setInstallCommand(null);
                    setInstallStatus(null);
                    void load();
                  }}
                >
                  Refresh release
                </Button>
              )}
              {!installStatus && (
                <Button
                  disabled={busy}
                  onClick={() => void startInstall(installCommand)}
                >
                  Retry original installation
                </Button>
              )}
            </>
          }
        >
          {installStatus && installStatus.total > 0 && (
            <progress
              aria-label="Update download"
              value={installStatus.downloaded}
              max={installStatus.total}
            >
              {Math.floor(
                (installStatus.downloaded / installStatus.total) * 100,
              )}
              %
            </progress>
          )}
        </SettingsItem>
      )}
      {available && !deferred && (
        <SettingsItem
          label={`Version ${available.version} is available`}
          icon={<ArrowUpCircle size={16} aria-hidden />}
          tone="2"
          bind={false}
          help={
            available.verified_manifest
              ? 'Release manifest includes an installer checksum.'
              : 'No verified installer checksum is available.'
          }
          control={
            <>
              {available.verified_manifest && owner.startInstall && (
                <Button
                  variant="primary"
                  aria-label={`Install version ${available.version}`}
                  disabled={locked}
                  onClick={() =>
                    void startInstall({
                      command_id: crypto.randomUUID(),
                      expected_revision: snapshot!.revision,
                      version: available.version,
                    })
                  }
                >
                  <Download size={14} aria-hidden />
                  Install
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
          <Disclosure
            className="settings-update-notes-disclosure"
            summary="What’s new"
          >
            <pre className="settings-update-notes">{available.notes}</pre>
            {releaseUrl && (
              <a href={releaseUrl} target="_blank" rel="noopener noreferrer">
                View release notes
              </a>
            )}
          </Disclosure>
        </SettingsItem>
      )}
      {available && deferred && (
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
    </div>
  );
}

export default function ConnectedUpdateControls() {
  const { controller } = useRuntime();
  const owner = useRef<Owner>({
    load: (signal) => controller.updates(signal),
    send: (command) => controller.updateCommand(command),
    startInstall: (command) => controller.startUpdateInstall(command),
    installStatus: (commandId) => controller.updateInstall(commandId),
    cancelInstall: (commandId) => controller.cancelUpdateInstall(commandId),
  });
  return <UpdateControls owner={owner.current} />;
}
