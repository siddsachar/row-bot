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
  PlanSignIn,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { usePlace } from '../../api/place';
import { safeExternalUrl } from '../../platform/types';
import { useRuntime } from '../../runtime';
import { Button } from '../../ui/primitives';
import SignInWait from '../apps/SignInWait';

type Account = 'google' | 'x' | 'microsoft';
type Owner = {
  load: (account: Account) => Promise<AccountAuthSnapshot>;
  send: (
    account: Account,
    command: AccountAuthCommand,
  ) => Promise<AccountAuthReceipt>;
  receipt: (account: Account, commandId: string) => Promise<AccountAuthReceipt>;
  cancel: (account: Account, commandId: string) => Promise<AccountAuthReceipt>;
  /** Opens the provider's sign-in page in this device's browser. */
  open?: (url: string) => void;
};

function pendingKey(account: Account) {
  return `row-bot:account-auth:${account}:pending:v1`;
}
function saved(account: Account): string {
  const value = sessionStorage.getItem(pendingKey(account)) ?? '';
  return /^[0-9a-f-]{36}$/.test(value) ? value : '';
}
const NAMES: Record<Account, string> = {
  google: 'Google',
  x: 'X',
  microsoft: 'Microsoft',
};

/** The provider's sign-in page from a receipt: an https address, or ''. */
function signInPage(receipt: AccountAuthReceipt): string {
  const url = safeExternalUrl(receipt.authorization_url ?? '');
  return receipt.phase === 'running' && url?.startsWith('https://') ? url : '';
}

/**
 * Microsoft's code while a sign-in from another device waits for it (F27):
 * the code, its page and how long it still works, as an app's sign-in shows
 * it. null for any other receipt.
 */
function signInCode(receipt: AccountAuthReceipt): PlanSignIn | null {
  if (receipt.phase !== 'running' || !receipt.device_code) return null;
  const page = safeExternalUrl(receipt.device_code.verification_uri);
  if (!page?.startsWith('https://')) return null;
  return {
    method: 'oauth_client',
    delivery: 'device',
    device: { ...receipt.device_code, verification_uri: page },
    expires_in: receipt.expires_in ?? null,
  };
}

/**
 * Microsoft's code, brought into view as it appears: it shows with the
 * account's row, and the person may have started it from the guide below.
 */
function CodeWait({ children }: { children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current?.scrollIntoView?.({ block: 'nearest' });
  }, []);
  return <div ref={box}>{children}</div>;
}

/**
 * Why this device can't start the account's sign-in, in plain words ('' when
 * it can). The desktop app on the computer Row-Bot runs on signs in through
 * that computer's browser; another device signs in through Row-Bot's one
 * public HTTPS address, which the provider must list.
 */
export function signInBlocked(snapshot: AccountAuthSnapshot | null): string {
  if (!snapshot || snapshot.sign_in !== 'unavailable') return '';
  switch (snapshot.sign_in_blocker) {
    case 'desktop_client':
      return 'Your Google client is a Desktop app, which returns only to the computer Row-Bot runs on. To sign in from this device, create a Web application client as the guide shows and choose its file. Check and Disconnect work from here.';
    case 'redirect_not_listed':
      return 'Your Google client doesn’t list the redirect address the guide shows. Add it to the client in Google Cloud, download the file again and choose it. Check and Disconnect work from here.';
    default:
      return 'Signing in from this device returns to Row-Bot’s public HTTPS address, and Row-Bot doesn’t have one yet: add it in Settings › Devices & remote access, or sign in from the Row-Bot desktop app on the computer it runs on. Check and Disconnect work from here.';
  }
}

/** A Google or X account's actions, for its row on the Accounts page (B263). */
export type AccountAuth = {
  snapshot: AccountAuthSnapshot | null;
  /**
   * Whether this device can start a sign-in (the server says how: the
   * desktop app here through this computer's browser, or this device's own
   * browser through Row-Bot's public address). False until the account loads.
   */
  canSignIn: boolean;
  /** Why it can't, in a sentence; '' when it can or isn't known yet. */
  blocked: string;
  /** An action is running or its outcome is still being checked. */
  locked: boolean;
  check: () => void;
  /** Sign in (again): in the browser here, or with a code from another device. */
  start: () => void;
  /** Asks to remove the local tokens (confirmed in `feedback`). */
  disconnect: () => void;
  /**
   * Asks to remove one X account (N7), confirmed in `feedback`: its numeric
   * ID ('' for the sign-in from before 5.3 not yet named) and its handle.
   */
  removeX: (account: { id: string; handle: string }) => void;
  importFile: (file: File | undefined) => void;
  /** Saves the person's own Microsoft app: its client ID and, optionally, its tenant. */
  saveApp: (clientId: string, tenant: string) => void;
  /** Confirmations, progress and outcomes, for under the row. */
  feedback: ReactNode;
};

/**
 * A Google or X account's reviewed sign-in actions: Check, sign in,
 * disconnect and the Google sign-in file. Nothing is refreshed or contacted
 * until one is chosen; a sign-in still running survives a reload. A sign-in
 * from another device opens the provider's page in this device's browser.
 */
export function useAccountAuth(
  account: Account,
  owner: Owner,
  onChanged?: () => void,
): AccountAuth {
  const place = usePlace();
  const changed = useRef(onChanged);
  useEffect(() => {
    changed.current = onChanged;
  });
  const [snapshot, setSnapshot] = useState<AccountAuthSnapshot | null>(null);
  const initialPending = useRef(saved(account));
  const [pending, setPending] = useState(initialPending.current);
  const [phase, setPhase] = useState<AccountAuthReceipt['phase'] | ''>('');
  /** The provider's page while a sign-in through Row-Bot's public address waits. */
  const [page, setPage] = useState('');
  /** Microsoft's code while a sign-in from another device waits. */
  const [code, setCode] = useState<PlanSignIn | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  /** The X account a confirmed disconnect removes (N7); the others stay. */
  const [removing, setRemoving] = useState<{
    id: string;
    handle: string;
  } | null>(null);
  const [missingReceipt, setMissingReceipt] = useState(false);
  const active = useRef(false);

  const load = useCallback(async () => {
    try {
      const result = await owner.load(account);
      setSnapshot(result);
      setError('');
    } catch (cause) {
      setError(clientError(cause).message);
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
        setPage(signInPage(result));
        setCode(signInCode(result));
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
    x_account = '',
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
      ...(x_account ? { x_account } : {}),
    };
    sessionStorage.setItem(pendingKey(account), command.command_id);
    setPending(command.command_id);
    try {
      const result = await owner.send(account, command);
      if (result.command_id !== command.command_id)
        throw new Error('Account receipt did not match this action');
      setSnapshot(result.snapshot);
      setPhase(result.phase);
      const url = signInPage(result);
      setPage(url);
      setCode(signInCode(result));
      // Its link below stays for a browser that blocks the page opening.
      if (url) owner.open?.(url);
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
          'account_callback_unavailable',
          'account_redirect_mismatch',
          'account_microsoft_app_invalid',
          'account_device_code_unavailable',
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
      setPage(signInPage(result));
      setCode(signInCode(result));
      setNotice(result.message);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  const name = NAMES[account];
  const quiet = !confirmDisconnect && !pending && !error && !notice;
  const feedback = quiet ? null : (
    <>
      {confirmDisconnect && (
        <div
          role="alertdialog"
          aria-label="Confirm local account disconnect"
          className="settings-account-confirm"
        >
          {removing ? (
            <p>
              Remove {removing.handle} from Row-Bot? Its sign-in is deleted from{' '}
              {place.noun}; your other X accounts stay. This doesn’t revoke
              access at X.
            </p>
          ) : (
            <p>
              Remove {name}’s sign-in from {place.noun}? You’ll need to sign in
              again. This doesn’t revoke access at {name}.
            </p>
          )}
          <div className="action-cluster">
            <Button
              variant="danger"
              className="small"
              onClick={() => {
                setConfirmDisconnect(false);
                setRemoving(null);
                void send('disconnect', '', removing?.id ?? '');
              }}
            >
              {removing ? `Remove ${removing.handle}` : 'Remove local tokens'}
            </Button>
            <Button
              variant="ghost"
              className="small"
              onClick={() => {
                setConfirmDisconnect(false);
                setRemoving(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
      {pending && code && (
        <CodeWait>
          <SignInWait
            name={name}
            signIn={code}
            busy={busy}
            stop={
              <Button disabled={busy} onClick={() => void cancel()}>
                Stop
              </Button>
            }
            onFinish={async () => undefined}
          />
        </CodeWait>
      )}
      {pending && !code && (
        <div role="status" className="action-cluster">
          <span>
            {phase === 'running' && page
              ? `Finish signing in to ${name} in the page that opened. This updates by itself.`
              : phase === 'running'
                ? `Finish signing in to ${name} in your browser.`
                : phase === 'cancel_requested'
                  ? 'Stopping the sign-in…'
                  : 'Checking what happened to the last action…'}
          </span>
          {phase === 'running' && page && (
            <a
              className="button small"
              href={page}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open {name}’s sign-in page
            </a>
          )}
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
                  setPage(signInPage(result));
                  setCode(signInCode(result));
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
      {notice && !code && <p role="status">{notice}</p>}
    </>
  );
  return {
    snapshot,
    canSignIn:
      snapshot?.sign_in === 'this_computer' ||
      snapshot?.sign_in === 'link' ||
      snapshot?.sign_in === 'device',
    blocked: signInBlocked(snapshot),
    locked: busy || Boolean(pending),
    check: () => void send('check'),
    start: () => void send('start'),
    disconnect: () => {
      setRemoving(null);
      setConfirmDisconnect(true);
    },
    removeX: (x) => {
      setRemoving(x);
      setConfirmDisconnect(true);
    },
    importFile: (file) => void importFile(file),
    saveApp: (clientId, tenant) =>
      void send(
        'import_credentials',
        JSON.stringify({ client_id: clientId.trim(), tenant: tenant.trim() }),
      ),
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
  const { controller, platform } = useRuntime();
  const owner = useRef<Owner>({
    load: (which) => controller.accountAuth(which),
    send: (which, command) => controller.accountAuthCommand(which, command),
    receipt: (which, commandId) =>
      controller.accountAuthReceipt(which, commandId),
    cancel: (which, commandId) =>
      controller.cancelAccountAuth(which, commandId),
    open: (url) => void platform.openExternal?.(url),
  });
  const auth = useAccountAuth(account, owner.current, onChanged);
  return children(auth);
}
