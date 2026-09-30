import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import type {
  AccountAuthCommand,
  AccountAuthReceipt,
  AccountAuthSnapshot,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Button } from '../../ui/primitives';

type Account = 'google' | 'x';
type Owner = {
  load: (account: Account) => Promise<AccountAuthSnapshot>;
  send: (
    account: Account,
    command: AccountAuthCommand,
  ) => Promise<AccountAuthReceipt>;
  receipt: (account: Account, commandId: string) => Promise<AccountAuthReceipt>;
  cancel: (account: Account, commandId: string) => Promise<AccountAuthReceipt>;
};

function pendingKey(account: Account) {
  return `row-bot:account-auth:${account}:pending:v1`;
}
function saved(account: Account): string {
  const value = sessionStorage.getItem(pendingKey(account)) ?? '';
  return /^[0-9a-f-]{36}$/.test(value) ? value : '';
}
const NAMES: Record<Account, string> = { google: 'Google', x: 'X' };

/** A Google or X account's actions, for its row on the Accounts page (B263). */
export type AccountAuth = {
  snapshot: AccountAuthSnapshot | null;
  /** Sign-in actions only run on the local owner device. */
  localOnly: boolean;
  /** An action is running or its outcome is still being checked. */
  locked: boolean;
  check: () => void;
  /** Sign in (again) in the browser. */
  start: () => void;
  /** Asks to remove the local tokens (confirmed in `feedback`). */
  disconnect: () => void;
  importFile: (file: File | undefined) => void;
  /** Confirmations, progress and outcomes, for under the row. */
  feedback: ReactNode;
};

/**
 * A Google or X account's reviewed sign-in actions: Check, sign in,
 * disconnect and the Google sign-in file. Nothing is refreshed or contacted
 * until one is chosen; a sign-in still running survives a reload.
 */
export function useAccountAuth(
  account: Account,
  owner: Owner,
  onChanged?: () => void,
): AccountAuth {
  const changed = useRef(onChanged);
  useEffect(() => {
    changed.current = onChanged;
  });
  const [snapshot, setSnapshot] = useState<AccountAuthSnapshot | null>(null);
  const initialPending = useRef(saved(account));
  const [pending, setPending] = useState(initialPending.current);
  const [phase, setPhase] = useState<AccountAuthReceipt['phase'] | ''>('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [localOnly, setLocalOnly] = useState(false);
  const [missingReceipt, setMissingReceipt] = useState(false);
  const active = useRef(false);

  const load = useCallback(async () => {
    try {
      const result = await owner.load(account);
      setSnapshot(result);
      setError('');
    } catch (cause) {
      const issue = clientError(cause);
      if (issue.code === 'action_denied' || issue.code === 'origin_rejected')
        setLocalOnly(true);
      else setError(issue.message);
    }
  }, [account, owner]);

  useEffect(() => {
    if (!initialPending.current) void load();
  }, [load]);
  useEffect(() => {
    if (!pending || (phase === '' && pending !== initialPending.current))
      return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const result = await owner.receipt(account, pending);
        if (stopped || result.command_id !== pending) return;
        setSnapshot(result.snapshot);
        setPhase(result.phase);
        setNotice(result.message);
        setError('');
        if (result.phase === 'running' || result.phase === 'cancel_requested') {
          timer = setTimeout(() => void poll(), 1000);
        } else {
          sessionStorage.removeItem(pendingKey(account));
          setPending('');
          changed.current?.();
        }
      } catch (cause) {
        if (!stopped) {
          const issue = clientError(cause);
          setMissingReceipt(issue.code === 'account_receipt_missing');
          setError(
            `${issue.message} Check the saved account state before another sign-in.`,
          );
        }
      }
    };
    void poll();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [account, owner, pending, phase]);

  async function send(
    action: AccountAuthCommand['action'],
    credentials_json = '',
  ) {
    if (!snapshot || pending || busy || active.current) return;
    active.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    setPhase('');
    const command: AccountAuthCommand = {
      command_id: crypto.randomUUID(),
      account,
      expected_revision: snapshot.revision,
      action,
      confirmed: action === 'disconnect',
      credentials_json,
    };
    sessionStorage.setItem(pendingKey(account), command.command_id);
    setPending(command.command_id);
    try {
      const result = await owner.send(account, command);
      if (result.command_id !== command.command_id)
        throw new Error('Account receipt did not match this action');
      setSnapshot(result.snapshot);
      setPhase(result.phase);
      setNotice(result.message);
      if (result.phase !== 'running' && result.phase !== 'cancel_requested') {
        sessionStorage.removeItem(pendingKey(account));
        setPending('');
        changed.current?.();
      }
    } catch (cause) {
      const issue = clientError(cause);
      if (
        [
          'account_changed',
          'invalid_account_command',
          'account_credentials_invalid',
          'account_credentials_required',
          'account_confirmation_required',
        ].includes(issue.code)
      ) {
        sessionStorage.removeItem(pendingKey(account));
        setPending('');
      }
      setError(issue.message);
    } finally {
      active.current = false;
      setBusy(false);
    }
  }

  async function importFile(file: File | undefined) {
    if (!file) return;
    if (file.size > 65536) {
      setError('Choose a Google sign-in file smaller than 64 KiB.');
      return;
    }
    try {
      await send('import_credentials', await file.text());
    } catch (cause) {
      setError(clientError(cause).message);
    }
  }

  async function cancel() {
    if (!pending || busy) return;
    setBusy(true);
    try {
      const result = await owner.cancel(account, pending);
      setPhase(result.phase);
      setNotice(result.message);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  const name = NAMES[account];
  const quiet = !confirmDisconnect && !pending && !error && !notice;
  const feedback = localOnly ? (
    <p className="muted">
      Account sign-in is available on the local owner device.
    </p>
  ) : quiet ? null : (
    <>
      {confirmDisconnect && (
        <div
          role="alertdialog"
          aria-label="Confirm local account disconnect"
          className="settings-account-confirm"
        >
          <p>
            Remove {name}’s sign-in from this computer? You’ll need to sign in
            again. This doesn’t revoke access at {name}.
          </p>
          <div className="action-cluster">
            <Button
              variant="danger"
              className="small"
              onClick={() => {
                setConfirmDisconnect(false);
                void send('disconnect');
              }}
            >
              Remove local tokens
            </Button>
            <Button
              variant="ghost"
              className="small"
              onClick={() => setConfirmDisconnect(false)}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
      {pending && (
        <div role="status" className="action-cluster">
          <span>
            {phase === 'running'
              ? `Finish signing in to ${name} in your browser.`
              : phase === 'cancel_requested'
                ? 'Stopping the sign-in…'
                : 'Checking what happened to the last action…'}
          </span>
          {(phase === 'running' || phase === 'cancel_requested') && (
            <Button
              className="small"
              disabled={busy || phase === 'cancel_requested'}
              onClick={() => void cancel()}
            >
              Cancel sign-in
            </Button>
          )}
          <Button
            variant="ghost"
            className="small"
            disabled={busy}
            onClick={() =>
              void owner.receipt(account, pending).then(
                (result) => {
                  setSnapshot(result.snapshot);
                  setPhase(result.phase);
                  setNotice(result.message);
                  setMissingReceipt(false);
                  if (!['running', 'cancel_requested'].includes(result.phase)) {
                    sessionStorage.removeItem(pendingKey(account));
                    setPending('');
                    changed.current?.();
                  }
                },
                (cause) => {
                  const issue = clientError(cause);
                  setMissingReceipt(issue.code === 'account_receipt_missing');
                  setError(issue.message);
                },
              )
            }
          >
            Check original action
          </Button>
          {missingReceipt && (
            <Button
              variant="ghost"
              className="small"
              onClick={() => {
                sessionStorage.removeItem(pendingKey(account));
                setPending('');
                setMissingReceipt(false);
                void load();
              }}
            >
              I checked account state; start again
            </Button>
          )}
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
    </>
  );
  return {
    snapshot,
    localOnly,
    locked: busy || Boolean(pending),
    check: () => void send('check'),
    start: () => void send('start'),
    disconnect: () => setConfirmDisconnect(true),
    importFile: (file) => void importFile(file),
    feedback,
  };
}

/** The account's actions through this app's controller, for its row. */
export default function ConnectedAccountAuth({
  account,
  onChanged,
  children,
}: {
  account: Account;
  /** An action finished: the page reloads what it shows (B263). */
  onChanged?: () => void;
  children: (auth: AccountAuth) => ReactNode;
}) {
  const { controller } = useRuntime();
  const owner = useRef<Owner>({
    load: (which) => controller.accountAuth(which),
    send: (which, command) => controller.accountAuthCommand(which, command),
    receipt: (which, commandId) =>
      controller.accountAuthReceipt(which, commandId),
    cancel: (which, commandId) =>
      controller.cancelAccountAuth(which, commandId),
  });
  const auth = useAccountAuth(account, owner.current, onChanged);
  return children(auth);
}
