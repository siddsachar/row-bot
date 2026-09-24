import { useCallback, useEffect, useRef, useState } from 'react';
import { Download, RotateCcw, SkipForward } from 'lucide-react';
import type {
  UpdateCommand,
  UpdateInstallCommand,
  UpdateInstallStatus,
  UpdateReceipt,
  UpdateSnapshot,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import {
  Button,
  CompactAction,
  ErrorState,
  Skeleton,
} from '../../ui/primitives';

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
          : command.action === 'check'
            ? receipt.snapshot.available
              ? `Version ${receipt.snapshot.available.version} is available.`
              : 'No update is available on this channel.'
            : 'Update preference saved.',
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
  return (
    <section className="stack" aria-label="Update controls">
      {loading && <Skeleton label="Loading cached update state" />}
      {error && (
        <ErrorState
          title="Update action needs attention"
          action={
            <Button onClick={() => void load()}>Refresh update state</Button>
          }
        >
          {error}
        </ErrorState>
      )}
      {pending && (
        <div role="status" className="surface stack">
          <p>Check the original update action before starting another.</p>
          <Button disabled={busy} onClick={() => void execute(pending)}>
            Check update action
          </Button>
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      {installError && <p role="alert">{installError}</p>}
      {installCommand && (
        <div
          className="surface stack"
          role="status"
          aria-label="Update installation"
        >
          <p>
            {installStatus?.message ??
              'Checking the original installation status.'}
          </p>
          {installStatus && installStatus.total > 0 && (
            <progress
              value={installStatus.downloaded}
              max={installStatus.total}
            >
              {Math.floor(
                (installStatus.downloaded / installStatus.total) * 100,
              )}
              %
            </progress>
          )}
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
        </div>
      )}
      {snapshot && (
        <>
          <p>
            Current version: {snapshot.current_version} · Channel:{' '}
            {snapshot.channel}
          </p>
          {snapshot.dev_install ? (
            <p>Development checkout: installed-app updates are unavailable.</p>
          ) : (
            <CompactAction
              label="Check for updates"
              disabled={busy || !!pending || !!installCommand}
              onClick={() => send('check')}
            >
              <RotateCcw size={17} aria-hidden />
            </CompactAction>
          )}
          {snapshot.available && !deferred && (
            <div className="surface stack" aria-label="Available update">
              <h3>Version {snapshot.available.version} is available</h3>
              <p>
                {snapshot.available.verified_manifest
                  ? 'Release manifest includes an installer checksum.'
                  : 'No verified installer checksum is available.'}
              </p>
              <pre className="settings-update-notes">
                {snapshot.available.notes}
              </pre>
              {releaseUrl && (
                <a href={releaseUrl} target="_blank" rel="noopener noreferrer">
                  View release notes
                </a>
              )}
              <div className="actions">
                {snapshot.available.verified_manifest && owner.startInstall && (
                  <CompactAction
                    label={`Install version ${snapshot.available.version}`}
                    disabled={busy || !!pending || !!installCommand}
                    onClick={() =>
                      void startInstall({
                        command_id: crypto.randomUUID(),
                        expected_revision: snapshot.revision,
                        version: snapshot.available!.version,
                      })
                    }
                  >
                    <Download size={17} aria-hidden />
                  </CompactAction>
                )}
                <CompactAction
                  label={`Skip version ${snapshot.available.version}`}
                  disabled={busy || !!pending || !!installCommand}
                  onClick={() => send('skip', snapshot.available!.version)}
                >
                  <SkipForward size={17} aria-hidden />
                </CompactAction>
                <Button variant="ghost" onClick={() => setDeferred(true)}>
                  Later
                </Button>
              </div>
            </div>
          )}
          {snapshot.available && deferred && (
            <Button variant="ghost" onClick={() => setDeferred(false)}>
              Show version {snapshot.available.version}
            </Button>
          )}
          {!!snapshot.skipped_versions.length && (
            <div className="actions">
              <span>Skipped: {snapshot.skipped_versions.join(', ')}</span>
              <CompactAction
                label="Clear skipped versions"
                disabled={busy || !!pending || !!installCommand}
                onClick={() => send('clear_skipped')}
              >
                <Download size={17} aria-hidden />
              </CompactAction>
            </div>
          )}
        </>
      )}
    </section>
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
