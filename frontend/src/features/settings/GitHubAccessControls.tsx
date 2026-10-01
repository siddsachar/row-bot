import { useEffect, useRef, useState, type ReactNode } from 'react';
import type {
  GitHubAccessCommand,
  GitHubAccessReceipt,
  GitHubAccessSnapshot,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Button } from '../../ui/primitives';

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

const STARTED =
  'Finish signing in to GitHub in the window that opened, then check GitHub.';

/** GitHub's actions, for its row on the Accounts page (B263). */
export type GitHubAccess = {
  snapshot: GitHubAccessSnapshot | null;
  /** GitHub actions only run on the local owner device. */
  localOnly: boolean;
  /** An action is running or its outcome is still being checked. */
  locked: boolean;
  send: (action: GitHubAccessCommand['action']) => void;
  /** Progress and outcomes, for under the row. */
  feedback: ReactNode;
};

/**
 * GitHub's reviewed actions: Check, sign in or refresh through the GitHub
 * CLI, and public access. Nothing is contacted until one is chosen.
 */
export function useGitHubAccess(
  owner: Owner,
  onChanged?: () => void,
): GitHubAccess {
  const changed = useRef(onChanged);
  useEffect(() => {
    changed.current = onChanged;
  });
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
            result.phase === 'started' ? STARTED : 'GitHub access checked.',
          );
          sessionStorage.removeItem(pendingKey);
          setPending(null);
          changed.current?.();
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
      changed.current?.();
      setError('');
      setNotice(
        result.phase === 'started' ? STARTED : 'GitHub access checked.',
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
      changed.current?.();
      setNotice(
        result.phase === 'started'
          ? STARTED
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

  const quiet = !pending && !error && !notice;
  const feedback = localOnly ? (
    <p className="muted">
      GitHub account actions are available on the local owner device.
    </p>
  ) : quiet ? null : (
    <>
      {pending && (
        <div role="status" className="action-cluster">
          <span>Checking what happened to the last GitHub action.</span>
          <Button
            className="small"
            onClick={() => void recover()}
            disabled={busy}
          >
            Check original action
          </Button>
        </div>
      )}
      {error && (
        <p role="alert">
          {error}{' '}
          <Button className="settings-link" onClick={() => void refresh()}>
            Refresh status
          </Button>
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
    </>
  );
  return {
    snapshot,
    localOnly,
    locked: busy || Boolean(pending),
    send: (action) => void send(action),
    feedback,
  };
}

/** GitHub's actions through this app's controller, for its row. */
export default function ConnectedGitHubAccess({
  onChanged,
  children,
}: {
  /** An action finished: the page reloads what it shows (B263). */
  onChanged?: () => void;
  children: (access: GitHubAccess) => ReactNode;
}) {
  const { controller } = useRuntime();
  const owner = useRef<Owner>({
    load: () => controller.githubAccess(),
    send: (command) => controller.githubAccessCommand(command),
    receipt: (commandId) => controller.githubAccessReceipt(commandId),
  });
  const access = useGitHubAccess(owner.current, onChanged);
  return children(access);
}
