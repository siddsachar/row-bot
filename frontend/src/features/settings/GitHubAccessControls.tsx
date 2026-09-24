import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, LogIn, RefreshCcw, Globe2 } from 'lucide-react';
import type {
  GitHubAccessCommand,
  GitHubAccessReceipt,
  GitHubAccessSnapshot,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Button, CompactAction } from '../../ui/primitives';

type Owner = {
  load: () => Promise<GitHubAccessSnapshot>;
  send: (command: GitHubAccessCommand) => Promise<GitHubAccessReceipt>;
  receipt: (commandId: string) => Promise<GitHubAccessReceipt>;
};
const pendingKey = 'row-bot:github-access:pending:v1';

function saved(): GitHubAccessCommand | null {
  try {
    const raw = sessionStorage.getItem(pendingKey);
    if (!raw || raw.length > 2048) return null;
    const value = JSON.parse(raw) as GitHubAccessCommand;
    return typeof value.command_id === 'string' &&
      typeof value.action === 'string'
      ? value
      : null;
  } catch {
    return null;
  }
}

export function GitHubAccessControls({ owner }: { owner: Owner }) {
  const [snapshot, setSnapshot] = useState<GitHubAccessSnapshot | null>(null);
  const [pending, setPending] = useState<GitHubAccessCommand | null>(saved);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [localOnly, setLocalOnly] = useState(false);
  const running = useRef(false);

  useEffect(() => {
    let cancelled = false;
    const original = saved();
    if (!original)
      void owner.load().then(
        (value) => {
          if (!cancelled) setSnapshot(value);
        },
        (cause) => {
          if (cancelled) return;
          const issue = clientError(cause);
          if (
            issue.code === 'action_denied' ||
            issue.code === 'origin_rejected'
          )
            setLocalOnly(true);
          else setError(issue.message);
        },
      );
    if (original) {
      void owner.receipt(original.command_id).then(
        (result) => {
          if (cancelled) return;
          setSnapshot(result.snapshot);
          setNotice(
            result.phase === 'started'
              ? 'Complete GitHub CLI authentication on this computer, then check GitHub.'
              : 'GitHub access checked.',
          );
          sessionStorage.removeItem(pendingKey);
          setPending(null);
        },
        (cause) => {
          if (!cancelled)
            setError(
              `${clientError(cause).message} Check the original action before retrying.`,
            );
        },
      );
    }
    return () => {
      cancelled = true;
    };
  }, [owner]);

  async function refresh() {
    if (busy) return;
    try {
      setSnapshot(await owner.load());
      setError('');
    } catch (cause) {
      setError(clientError(cause).message);
    }
  }

  async function recover() {
    if (!pending || busy) return;
    setBusy(true);
    try {
      const result = await owner.receipt(pending.command_id);
      setSnapshot(result.snapshot);
      sessionStorage.removeItem(pendingKey);
      setPending(null);
      setError('');
      setNotice(
        result.phase === 'started'
          ? 'Complete GitHub CLI authentication on this computer, then check GitHub.'
          : 'GitHub access checked.',
      );
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  async function send(action: GitHubAccessCommand['action']) {
    if (!snapshot || pending || busy || running.current) return;
    const command: GitHubAccessCommand = {
      command_id: crypto.randomUUID(),
      expected_revision: snapshot.revision,
      action,
    };
    running.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    sessionStorage.setItem(pendingKey, JSON.stringify(command));
    setPending(command);
    try {
      const result = await owner.send(command);
      if (result.command_id !== command.command_id)
        throw new Error('GitHub action receipt did not match this command');
      sessionStorage.removeItem(pendingKey);
      setPending(null);
      setSnapshot(result.snapshot);
      setNotice(
        result.phase === 'started'
          ? 'Complete GitHub CLI authentication on this computer, then check GitHub.'
          : action === 'anonymous'
            ? 'Public sources will use anonymous access when needed.'
            : 'GitHub access checked.',
      );
    } catch (cause) {
      const issue = clientError(cause);
      if (
        [
          'account_changed',
          'invalid_account_command',
          'github_cli_missing',
          'github_cli_host_terminal_required',
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

  if (localOnly)
    return (
      <p className="muted">
        GitHub account actions are available on the local owner device.
      </p>
    );
  return (
    <div className="stack" aria-label="GitHub account access">
      {snapshot && (
        <>
          <p role="status">
            {snapshot.connected
              ? 'Connected'
              : snapshot.anonymous_ok
                ? 'Anonymous public access'
                : snapshot.state.replaceAll('_', ' ')}{' '}
            ·{' '}
            {snapshot.credential_source === 'none'
              ? 'No saved token'
              : snapshot.credential_source.replaceAll('_', ' ')}
          </p>
          {snapshot.remaining != null && (
            <p>API requests remaining: {snapshot.remaining}</p>
          )}
          <div className="actions">
            <CompactAction
              label="Check GitHub access"
              disabled={busy || !!pending}
              onClick={() => void send('check')}
            >
              <CheckCircle2 size={17} aria-hidden />
            </CompactAction>
            {snapshot.cli_installed && (
              <>
                <CompactAction
                  label="Connect GitHub CLI"
                  disabled={busy || !!pending}
                  onClick={() => void send('cli_login')}
                >
                  <LogIn size={17} aria-hidden />
                </CompactAction>
                <CompactAction
                  label="Refresh GitHub CLI authentication"
                  disabled={busy || !!pending}
                  onClick={() => void send('cli_refresh')}
                >
                  <RefreshCcw size={17} aria-hidden />
                </CompactAction>
              </>
            )}
            <CompactAction
              label="Use anonymous public access"
              disabled={busy || !!pending}
              onClick={() => void send('anonymous')}
            >
              <Globe2 size={17} aria-hidden />
            </CompactAction>
          </div>
          {!snapshot.cli_installed && (
            <p className="muted">
              GitHub CLI is unavailable on this computer. A saved token can
              still be checked above.
            </p>
          )}
        </>
      )}
      {pending && (
        <div role="status" className="actions">
          <span>Checking the original GitHub action.</span>
          <Button onClick={() => void recover()} disabled={busy}>
            Check original action
          </Button>
        </div>
      )}
      {error && (
        <p role="alert">
          {error}{' '}
          <Button variant="ghost" onClick={() => void refresh()}>
            Refresh status
          </Button>
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
    </div>
  );
}

export default function ConnectedGitHubAccessControls() {
  const { controller } = useRuntime();
  const owner = useRef<Owner>({
    load: () => controller.githubAccess(),
    send: (command) => controller.githubAccessCommand(command),
    receipt: (commandId) => controller.githubAccessReceipt(commandId),
  });
  return <GitHubAccessControls owner={owner.current} />;
}
