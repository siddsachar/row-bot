import { useEffect, useRef } from 'react';
import type {
  SubscriptionAccountsSnapshot,
  SubscriptionFlowSnapshot,
  SubscriptionActionReview,
} from '../../api/types';
import { Button, Field, Input, Select, Skeleton } from '../../ui/primitives';
import { clientError } from '../../api/errors';
import {
  ProviderSettingsSession,
  useProviderSettingsValue,
} from './provider-settings-sessions';

type Action =
  'start' | 'check' | 'submit' | 'disconnect' | 'restore' | 'import_token';
type Intent = {
  provider_id: SubscriptionFlowSnapshot['provider_id'];
  provider_revision: string;
  operation: Action;
  value?: string;
  flow_id?: string;
  server_epoch?: string;
};
type Reviewed = { intent: Intent; review: SubscriptionActionReview };
type Pending = Reviewed & { commandId: string };
type Result = {
  command_id: string;
  status: 'completed';
  accounts: SubscriptionAccountsSnapshot;
  flow?: SubscriptionFlowSnapshot;
};
export class SubscriptionAccountsSession extends ProviderSettingsSession {
  constructor() {
    super('subscription_accounts');
  }
  hasRetained() {
    const flow = this.get<SubscriptionFlowSnapshot | null>('flow', null);
    return (
      !!this.get('code', '') ||
      !!this.get('reviewed', null) ||
      this.retained() ||
      (!!flow &&
        (!flow.quiescent ||
          !['connected', 'cancelled', 'expired'].includes(flow.state)))
    );
  }
}
export type SubscriptionAccountsProps = {
  collapsedAtRest?: boolean;
  compact?: boolean;
  initialProvider?: SubscriptionFlowSnapshot['provider_id'];
  initialAction?: 'connect' | 'manage' | 'disconnect';
  onClose?: () => void;
  session?: SubscriptionAccountsSession;
  load: (signal?: AbortSignal) => Promise<SubscriptionAccountsSnapshot>;
  review: (
    intent: Intent,
    signal?: AbortSignal,
  ) => Promise<SubscriptionActionReview>;
  apply: (
    intent: Intent,
    review: SubscriptionActionReview,
    commandId: string,
  ) => Promise<Result>;
  readFlow: (
    identity: SubscriptionFlowSnapshot,
    signal?: AbortSignal,
  ) => Promise<SubscriptionFlowSnapshot>;
  cancel: (
    identity: SubscriptionFlowSnapshot,
    commandId: string,
  ) => Promise<Result>;
  cancelStart: (commandId: string) => Promise<SubscriptionFlowSnapshot>;
  receipt: (
    providerId: string,
    commandId: string,
    signal?: AbortSignal,
  ) => Promise<{
    command_id: string;
    status: 'completed' | 'rejected' | 'uncertain';
    published: boolean;
    accounts: SubscriptionAccountsSnapshot;
  }>;
  onSaved: (snapshot: SubscriptionAccountsSnapshot) => void;
};
const labels: Record<string, string> = {
  codex: 'ChatGPT / Codex',
  claude_subscription: 'Claude Subscription',
  xai_oauth: 'xAI Grok',
};
function safeLogin(value: string | null) {
  try {
    const url = new URL(value ?? '');
    return url.protocol === 'https:' && !url.username && !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}
export default function SubscriptionAccounts(props: SubscriptionAccountsProps) {
  const local = useRef<SubscriptionAccountsSession | null>(null);
  if (!local.current) local.current = new SubscriptionAccountsSession();
  const session = props.session ?? local.current;
  const [snapshot, setSnapshot] =
    useProviderSettingsValue<SubscriptionAccountsSnapshot | null>(
      session,
      'snapshot',
      null,
    );
  const [provider, setProvider] = useProviderSettingsValue<
    SubscriptionFlowSnapshot['provider_id']
  >(session, 'provider', 'codex');
  const [flow, setFlow] =
    useProviderSettingsValue<SubscriptionFlowSnapshot | null>(
      session,
      'flow',
      null,
    );
  const [code, setCode] = useProviderSettingsValue(session, 'code', '');
  const [reviewed, setReviewed] = useProviderSettingsValue<Reviewed | null>(
    session,
    'reviewed',
    null,
  );
  const [pending, setPending] = useProviderSettingsValue<Pending | null>(
    session,
    'pending',
    null,
  );
  const [busy, setBusy] = useProviderSettingsValue(session, 'busy', '');
  const [cancelling, setCancelling] = useProviderSettingsValue(
    session,
    'cancelling',
    false,
  );
  const [error, setError] = useProviderSettingsValue(session, 'error', '');
  const [notice, setNotice] = useProviderSettingsValue(session, 'notice', '');
  const epoch = useRef(0);
  const locked = !!busy || !!pending || cancelling || !session.active;
  const account = snapshot?.accounts.find(
    (item) => item.provider_id === provider,
  );
  const active =
    !!flow &&
    (!flow.quiescent ||
      !['connected', 'cancelled', 'expired'].includes(flow.state));
  const retained = session.hasRetained();
  async function load() {
    if (
      !session.active ||
      session.get('busy', '') ||
      session.get('pending', null) ||
      session.get('code', '')
    )
      return;
    const abort = session.read();
    setBusy('load');
    setError('');
    try {
      const value = await props.load(abort.signal);
      if (!abort.signal.aborted && session.active) {
        setSnapshot(value);
        setReviewed(null);
      }
    } catch (cause) {
      if (!abort.signal.aborted) setError(clientError(cause).message);
    } finally {
      session.finishRead(abort);
      setBusy('');
    }
  }
  useEffect(() => {
    epoch.current += 1;
    if (!session.get('snapshot', null) && !session.get('busy', '')) void load();
    return () => {
      epoch.current += 1;
      if (!props.session) session.dispose();
    };
    // Callback changes must not replace a retained private intent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session]);
  useEffect(() => {
    if (
      props.initialProvider &&
      !session.hasRetained() &&
      provider !== props.initialProvider
    ) {
      setFlow(null);
      setCode('');
      setProvider(props.initialProvider);
    }
  }, [
    props.initialProvider,
    provider,
    retained,
    setCode,
    setFlow,
    setProvider,
    session,
  ]);
  async function performDirect(operation: Action) {
    if (locked || !snapshot) return;
    const intent: Intent = {
      provider_id: provider,
      provider_revision: snapshot.revision,
      operation,
      ...(['submit', 'import_token'].includes(operation)
        ? { value: code }
        : {}),
      ...(['check', 'submit'].includes(operation) && flow
        ? { flow_id: flow.flow_id, server_epoch: flow.server_epoch }
        : {}),
    };
    if (
      intent.value !== undefined &&
      (!intent.value.trim() ||
        new TextEncoder().encode(intent.value).length > 16384)
    ) {
      setError('Enter a code or token of at most 16 KiB.');
      return;
    }
    const generation = epoch.current;
    setBusy('review');
    setError('');
    setNotice('');
    let review: SubscriptionActionReview;
    try {
      review = await props.review(structuredClone(intent));
      if (
        review.provider_id !== intent.provider_id ||
        review.provider_revision !== intent.provider_revision ||
        review.operation !== intent.operation ||
        (review.flow_id ?? undefined) !== intent.flow_id ||
        (review.server_epoch ?? undefined) !== intent.server_epoch
      )
        throw { code: 'revision_conflict' };
    } catch (cause) {
      setError(clientError(cause).message);
      setBusy('');
      return;
    }
    if (!session.active) return;
    const original: Pending = {
      intent,
      review,
      commandId: crypto.randomUUID(),
    };
    setPending(original);
    setBusy('apply');
    try {
      const result = await session.perform([original], () =>
        props.apply(
          structuredClone(intent),
          structuredClone(review),
          original.commandId,
        ),
      );
      if (!session.active) return;
      if (result.command_id !== original.commandId)
        throw { code: 'operation_uncertain' };
      setSnapshot(result.accounts);
      if (result.flow) setFlow(result.flow);
      setCode('');
      setPending(null);
      session.resolved();
      setNotice(
        `${labels[provider]} ${operation === 'start' ? 'sign-in started' : operation === 'disconnect' ? 'disconnected' : operation === 'import_token' ? 'setup token saved' : 'updated'}.`,
      );
      if (generation === epoch.current) props.onSaved(result.accounts);
    } catch (cause) {
      setError(clientError(cause).message);
      setNotice(
        'The outcome is uncertain. Read the original receipt before another action.',
      );
    } finally {
      setBusy('');
    }
  }
  async function readReceipt() {
    if (busy || !pending || !session.active) return;
    const abort = session.read();
    setBusy('receipt');
    setError('');
    try {
      const result = await props.receipt(
        pending.intent.provider_id,
        pending.commandId,
        abort.signal,
      );
      if (!session.active || abort.signal.aborted) return;
      if (result.command_id !== pending.commandId)
        throw { code: 'operation_uncertain' };
      setSnapshot(result.accounts);
      if (result.status === 'uncertain')
        setNotice(
          'The original outcome remains unconfirmed. No request was replayed.',
        );
      else {
        setPending(null);
        setCode('');
        session.resolved();
        setNotice(
          result.status === 'completed'
            ? 'The original action is confirmed.'
            : 'The original action was rejected. Review the current state before trying again.',
        );
      }
    } catch (cause) {
      if (!abort.signal.aborted) setError(clientError(cause).message);
    } finally {
      session.finishRead(abort);
      setBusy('');
    }
  }
  async function recover() {
    const original = session.get<Pending | null>('pending', null);
    if (!original || !session.active || session.get('busy', '')) return;
    const generation = epoch.current;
    setBusy('recovery');
    setError('');
    try {
      // Explicit same-command recovery is checked by the server against its
      // publication proof or retained tokens. Unknown exchanges are rejected.
      const result = await props.apply(
        structuredClone(original.intent),
        structuredClone(original.review),
        original.commandId,
      );
      if (!session.active) return;
      if (result.command_id !== original.commandId)
        throw { code: 'operation_uncertain' };
      setSnapshot(result.accounts);
      if (result.flow) setFlow(result.flow);
      setPending(null);
      setCode('');
      session.resolved();
      setNotice(
        'The original action is recovered. No authorization exchange was repeated.',
      );
      if (generation === epoch.current) props.onSaved(result.accounts);
    } catch (cause) {
      setError(clientError(cause).message);
      setNotice(
        'Recovery remains unconfirmed. The original request is retained.',
      );
    } finally {
      setBusy('');
    }
  }
  async function inspect() {
    if (!flow || busy || !session.active) return;
    const abort = session.read();
    setBusy('inspect');
    try {
      const current = await props.readFlow(structuredClone(flow), abort.signal);
      if (session.active && !abort.signal.aborted) {
        if (
          current.flow_id !== flow.flow_id ||
          current.server_epoch !== flow.server_epoch
        )
          throw { code: 'operation_uncertain' };
        setFlow(current);
      }
    } catch (cause) {
      if (!abort.signal.aborted) setError(clientError(cause).message);
    } finally {
      session.finishRead(abort);
      setBusy('');
    }
  }
  async function cancel() {
    const current = session.get<SubscriptionFlowSnapshot | null>('flow', null);
    const original = session.get<Pending | null>('pending', null);
    if (
      (!current && original?.intent.operation !== 'start') ||
      !session.active ||
      session.get('cancelling', false)
    )
      return;
    setCancelling(true);
    setReviewed(null);
    setError('');
    try {
      if (!current && original) {
        const stopped = await props.cancelStart(original.commandId);
        if (session.active) {
          if (stopped.provider_id !== original.intent.provider_id)
            throw { code: 'operation_uncertain' };
          setFlow(stopped);
          setNotice(
            stopped.state === 'connected'
              ? 'Sign-in completed before cancellation.'
              : stopped.quiescent
                ? 'Sign-in is cancelled.'
                : 'Cancellation requested. Waiting for the provider operation to finish.',
          );
        }
        return;
      }
      if (!current) return;
      const result = await props.cancel(
        structuredClone(current),
        crypto.randomUUID(),
      );
      if (session.active) {
        if (result.flow?.flow_id !== current.flow_id)
          throw { code: 'operation_uncertain' };
        setFlow(result.flow);
        setSnapshot(result.accounts);
        setCode('');
        setNotice(
          result.flow.state === 'connected'
            ? 'Sign-in completed before cancellation.'
            : result.flow.quiescent
              ? 'Sign-in is cancelled.'
              : 'Cancellation requested. Waiting for the provider operation to finish.',
        );
      }
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setCancelling(false);
    }
  }
  const login = safeLogin(flow?.authorization_url ?? null);
  const needsAttention =
    !!flow || !!code || !!reviewed || !!pending || !!error || !!notice;
  if (
    props.compact &&
    props.initialProvider &&
    provider !== props.initialProvider &&
    retained
  )
    return (
      <section
        className="stack settings-provider-account-dialog"
        aria-label="Current subscription sign-in"
      >
        <h2>{labels[provider]} sign-in in progress</h2>
        <p>
          Finish or cancel this sign-in before opening{' '}
          {labels[props.initialProvider]}.
        </p>
        {flow && <p>Sign-in: {flow.state.replaceAll('_', ' ')}</p>}
        {pending && (
          <Button disabled={!!busy} onClick={() => void readReceipt()}>
            Read original receipt
          </Button>
        )}
        {active && (
          <div className="actions">
            <Button
              disabled={locked}
              onClick={() =>
                void (provider === 'codex' ? performDirect('check') : inspect())
              }
            >
              {provider === 'codex' ? 'Check login' : 'Check sign-in'}
            </Button>
            <Button disabled={cancelling} onClick={() => void cancel()}>
              Cancel sign-in
            </Button>
          </div>
        )}
        <Button onClick={props.onClose}>Close</Button>
      </section>
    );
  if (props.compact)
    return (
      <section
        className="stack settings-provider-account-dialog"
        aria-label={`${labels[provider]} account`}
        aria-busy={!!busy}
      >
        <h2>{labels[provider]}</h2>
        {busy === 'load' && <Skeleton label="Loading account" />}
        {error && <p role="alert">{error}</p>}
        {notice && <p role="status">{notice}</p>}
        {account && (
          <p>
            {account.saved_state === 'saved'
              ? 'Connected'
              : account.saved_state === 'metadata_only'
                ? 'CLI login referenced'
                : 'Not connected'}
            {account.expires_at
              ? ` · Expires ${new Date(account.expires_at).toLocaleString()}`
              : ''}
          </p>
        )}
        {flow && (
          <div className="stack">
            <p>Sign-in: {flow.state.replaceAll('_', ' ')}</p>
            {login && (
              <a href={login} target="_blank" rel="noopener noreferrer">
                Open sign-in page
              </a>
            )}
            {flow.device_code && (
              <Field label="Device code">
                <Input readOnly value={flow.device_code} />
              </Field>
            )}
            {active && (
              <div className="actions">
                <Button
                  disabled={locked}
                  onClick={() =>
                    void (provider === 'codex'
                      ? performDirect('check')
                      : inspect())
                  }
                >
                  {provider === 'codex' ? 'Check login' : 'Check sign-in'}
                </Button>
                <Button disabled={cancelling} onClick={() => void cancel()}>
                  Cancel sign-in
                </Button>
              </div>
            )}
          </div>
        )}
        {active && provider !== 'codex' && (
          <Field label="Authorization code or callback URL">
            <Input
              type="password"
              value={code}
              disabled={locked}
              onChange={(event) => setCode(event.target.value)}
            />
          </Field>
        )}
        {!active &&
          provider === 'claude_subscription' &&
          props.initialAction === 'manage' && (
            <Field label="Claude setup token">
              <Input
                type="password"
                value={code}
                disabled={locked}
                onChange={(event) => setCode(event.target.value)}
              />
            </Field>
          )}
        <div className="actions">
          {!active && props.initialAction === 'connect' && (
            <Button
              disabled={locked}
              onClick={() => void performDirect('start')}
            >
              {account?.saved_state === 'saved' ? 'Reconnect' : 'Connect'}
            </Button>
          )}
          {active && provider !== 'codex' && (
            <Button
              disabled={locked || !code}
              onClick={() => void performDirect('submit')}
            >
              Connect with code
            </Button>
          )}
          {!active &&
            provider === 'claude_subscription' &&
            props.initialAction === 'manage' && (
              <Button
                disabled={locked || !code}
                onClick={() => void performDirect('import_token')}
              >
                Import setup token
              </Button>
            )}
          {!active &&
            props.initialAction === 'disconnect' &&
            account?.saved_state !== 'disconnected' && (
              <Button
                disabled={locked}
                onClick={() => void performDirect('disconnect')}
              >
                Disconnect
              </Button>
            )}
          {!active &&
            props.initialAction === 'manage' &&
            account?.has_recovery && (
              <Button
                disabled={locked}
                onClick={() => void performDirect('restore')}
              >
                Restore previous login
              </Button>
            )}
          {pending && (
            <Button disabled={!!busy} onClick={() => void readReceipt()}>
              Read original receipt
            </Button>
          )}
          <Button disabled={!!busy || !!pending} onClick={props.onClose}>
            Close
          </Button>
        </div>
      </section>
    );
  return (
    <details
      className="settings-provider-secondary"
      open={props.collapsedAtRest && !needsAttention ? undefined : true}
    >
      <summary>
        <span>
          <strong>Subscription accounts</strong>
          <small>Sign-in, disconnect, and recovery</small>
        </span>
      </summary>
      <section
        className="stack settings-provider-secondary-content"
        aria-label="Subscription accounts"
      >
        <p>
          Connect an existing subscription account. Saved account status does
          not test provider readiness.
        </p>
        {busy === 'load' && <Skeleton label="Loading subscription accounts" />}
        {error && <p role="alert">{error}</p>}
        {notice && <p role="status">{notice}</p>}
        <Field label="Subscription provider">
          <Select
            aria-label="Subscription provider"
            value={provider}
            disabled={locked || active || !!code}
            onChange={(event) => {
              setProvider(
                event.target.value as SubscriptionFlowSnapshot['provider_id'],
              );
              setReviewed(null);
              setFlow(null);
            }}
          >
            {Object.entries(labels).map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
        {account && (
          <p>
            Saved status: {account.saved_state.replaceAll('_', ' ')} ·
            Credential storage: {account.credential_storage} · Readiness:
            unknown
            {account.expires_at ? ` · Expires ${account.expires_at}` : ''}
          </p>
        )}
        {flow && (
          <div className="stack" role="group" aria-label="Current sign-in">
            <p>
              Sign-in: {flow.state}.{' '}
              {flow.quiescent
                ? 'No sign-in operation is running.'
                : 'A sign-in operation is still running.'}
            </p>
            {login && (
              <a href={login} target="_blank" rel="noopener noreferrer">
                Open {labels[provider]} sign-in
              </a>
            )}
            {flow.device_code && (
              <Field label="Device code">
                <Input
                  aria-label="Device code"
                  readOnly
                  value={flow.device_code}
                />
              </Field>
            )}
            {flow.expires_at && <p>Sign-in expires: {flow.expires_at}</p>}
            <div className="actions">
              <Button
                onClick={() => void inspect()}
                disabled={!!busy || !session.active}
              >
                Read sign-in status
              </Button>
              <Button
                onClick={() => void cancel()}
                disabled={cancelling || !active || !session.active}
              >
                Cancel sign-in
              </Button>
            </div>
          </div>
        )}
        {!flow && pending?.intent.operation === 'start' && (
          <Button
            disabled={cancelling || !session.active}
            onClick={() => void cancel()}
          >
            Cancel sign-in
          </Button>
        )}
        {(provider === 'claude_subscription' ||
          (provider === 'xai_oauth' && active)) && (
          <Field
            label={
              active
                ? 'Authorization code or callback URL'
                : 'Claude setup token'
            }
          >
            <Input
              aria-label={
                active
                  ? 'Authorization code or callback URL'
                  : 'Claude setup token'
              }
              type="password"
              autoComplete="off"
              value={code}
              disabled={locked}
              onChange={(event) => {
                setCode(event.target.value);
                setReviewed(null);
              }}
            />
          </Field>
        )}
        <div className="actions">
          <Button
            disabled={locked || active || !snapshot}
            onClick={() => void performDirect('start')}
          >
            Start sign-in
          </Button>
          {active && provider !== 'claude_subscription' && (
            <Button
              disabled={locked || flow?.state === 'uncertain'}
              onClick={() => void performDirect('check')}
            >
              Check login
            </Button>
          )}
          {active && provider !== 'codex' && (
            <Button
              disabled={locked || !code || flow?.state === 'uncertain'}
              onClick={() => void performDirect('submit')}
            >
              Connect with code
            </Button>
          )}
          {!active && provider === 'claude_subscription' && (
            <Button
              disabled={locked || !code}
              onClick={() => void performDirect('import_token')}
            >
              Import setup token
            </Button>
          )}
          <Button
            disabled={
              locked ||
              active ||
              account?.saved_state === 'disconnected' ||
              !account
            }
            onClick={() => void performDirect('disconnect')}
          >
            Disconnect
          </Button>
          <Button
            disabled={locked || active || !account?.has_recovery}
            onClick={() => void performDirect('restore')}
          >
            Restore account
          </Button>
          <Button disabled={locked || !!code} onClick={() => void load()}>
            Reload saved status
          </Button>
        </div>
        {pending && (
          <div className="actions">
            <Button
              disabled={!!busy || !session.active}
              onClick={() => void readReceipt()}
            >
              Read original account receipt
            </Button>
            <Button
              disabled={!!busy || !session.active}
              onClick={() => void recover()}
            >
              Recover original account action
            </Button>
          </div>
        )}
      </section>
    </details>
  );
}
