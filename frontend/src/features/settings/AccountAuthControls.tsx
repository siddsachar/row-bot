import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle2, LogIn, LogOut, RotateCcw } from 'lucide-react';
import type {
  AccountAuthCommand,
  AccountAuthReceipt,
  AccountAuthSnapshot,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Button, CompactAction } from '../../ui/primitives';

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

export function AccountAuthControls({
  account,
  owner,
}: {
  account: Account;
  owner: Owner;
}) {
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
      setError('Choose a Google OAuth client JSON file smaller than 64 KiB.');
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

  if (localOnly)
    return (
      <p className="muted">
        Account sign-in is available on the local owner device.
      </p>
    );
  return (
    <div
      className="stack"
      aria-label={`${account === 'google' ? 'Google' : 'X'} authentication`}
    >
      {snapshot && (
        <>
          <p role="status">
            {snapshot.state.replaceAll('_', ' ')} · {snapshot.token_files} local
            token {snapshot.token_files === 1 ? 'file' : 'files'}
          </p>
          {account === 'google' && (
            <label className="stack">
              Google OAuth client JSON
              <input
                type="file"
                accept=".json,application/json"
                disabled={busy || !!pending}
                onChange={(event) => {
                  void importFile(event.target.files?.[0]);
                  event.target.value = '';
                }}
              />
            </label>
          )}
          <div className="actions">
            <CompactAction
              label={`Check ${account === 'google' ? 'Google' : 'X'} account token`}
              disabled={busy || !!pending}
              onClick={() => void send('check')}
            >
              <CheckCircle2 size={17} aria-hidden />
            </CompactAction>
            <CompactAction
              label={`${snapshot.token_files ? 'Reauthenticate' : 'Authenticate'} ${account === 'google' ? 'Google' : 'X'}`}
              disabled={!snapshot.configured || busy || !!pending}
              onClick={() => void send('start')}
            >
              <LogIn size={17} aria-hidden />
            </CompactAction>
            {!!snapshot.token_files && (
              <CompactAction
                label={`Disconnect ${account === 'google' ? 'Google' : 'X'} locally`}
                disabled={busy || !!pending}
                onClick={() => setConfirmDisconnect(true)}
              >
                <LogOut size={17} aria-hidden />
              </CompactAction>
            )}
            <CompactAction
              label="Refresh saved account state"
              disabled={busy}
              onClick={() => void load()}
            >
              <RotateCcw size={17} aria-hidden />
            </CompactAction>
          </div>
        </>
      )}
      {confirmDisconnect && (
        <div
          role="alertdialog"
          aria-label="Confirm local account disconnect"
          className="surface stack"
        >
          <p>
            Remove this account’s local tokens? You will need to authenticate
            again. This does not revoke authorization at the provider.
          </p>
          <div className="actions">
            <Button
              onClick={() => {
                setConfirmDisconnect(false);
                void send('disconnect');
              }}
            >
              Remove local tokens
            </Button>
            <Button variant="ghost" onClick={() => setConfirmDisconnect(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {pending && (
        <div role="status" className="actions">
          <span>Account action {phase || 'pending'}.</span>
          {(phase === 'running' || phase === 'cancel_requested') && (
            <Button
              disabled={busy || phase === 'cancel_requested'}
              onClick={() => void cancel()}
            >
              Cancel sign-in
            </Button>
          )}
          <Button
            variant="ghost"
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
      {!snapshot && !error && <p role="status">Loading saved account state…</p>}
    </div>
  );
}

export default function ConnectedAccountAuthControls({
  account,
}: {
  account: Account;
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
  return <AccountAuthControls account={account} owner={owner.current} />;
}
