import { useEffect, useRef } from 'react';
import { clientError } from '../../api/errors';
import type {
  SubscriptionOptionsSnapshot,
  SubscriptionOptionsReview,
} from '../../api/types';
import { Button, Field, Input, Skeleton } from '../../ui/primitives';
import {
  ProviderSettingsSession,
  useProviderSettingsValue,
} from './provider-settings-sessions';

export type {
  SubscriptionOptionsSnapshot,
  SubscriptionOptionsReview,
} from '../../api/types';
type Pending = { commandId: string; review: SubscriptionOptionsReview };
export class SubscriptionOptionsSession extends ProviderSettingsSession {
  constructor() {
    super('subscription_options');
  }
  hasRetained() {
    return (
      this.get('dirty', false) ||
      !!this.get('reviewed', null) ||
      this.retained()
    );
  }
}
export type SubscriptionOptionsProps = {
  collapsedAtRest?: boolean;
  session?: SubscriptionOptionsSession;
  load: (signal?: AbortSignal) => Promise<SubscriptionOptionsSnapshot>;
  review: (
    intent: Omit<
      SubscriptionOptionsReview,
      'action_digest' | 'reference_digest' | 'nonce'
    >,
    signal?: AbortSignal,
  ) => Promise<SubscriptionOptionsReview>;
  apply: (
    review: SubscriptionOptionsReview,
    commandId: string,
  ) => Promise<SubscriptionOptionsSnapshot>;
  receipt: (
    commandId: string,
    signal?: AbortSignal,
  ) => Promise<{
    command_id: string;
    status: 'completed' | 'rejected' | 'uncertain';
    published: boolean;
    options: SubscriptionOptionsSnapshot;
  }>;
  onSaved: (snapshot: SubscriptionOptionsSnapshot) => void;
};

export default function SubscriptionOptions(props: SubscriptionOptionsProps) {
  const local = useRef<SubscriptionOptionsSession | null>(null);
  if (!local.current) local.current = new SubscriptionOptionsSession();
  const session = props.session ?? local.current;
  const [snapshot, setSnapshot] =
    useProviderSettingsValue<SubscriptionOptionsSnapshot | null>(
      session,
      'snapshot',
      null,
    );
  const [client, setClient] = useProviderSettingsValue(session, 'client', '');
  const [dirty, setDirty] = useProviderSettingsValue(session, 'dirty', false);
  const [reviewed, setReviewed] =
    useProviderSettingsValue<SubscriptionOptionsReview | null>(
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
  const [error, setError] = useProviderSettingsValue(session, 'error', '');
  const [notice, setNotice] = useProviderSettingsValue(session, 'notice', '');
  const epoch = useRef(0);
  const locked = !!busy || !!pending || !session.active;
  function accept(value: SubscriptionOptionsSnapshot) {
    setSnapshot(value);
    setClient(value.xai_saved_client_id ?? '');
    setDirty(false);
    setReviewed(null);
  }
  async function load() {
    if (
      !session.active ||
      session.get('busy', '') ||
      session.get('dirty', false) ||
      session.get('pending', null)
    )
      return;
    const abort = session.read();
    setBusy('load');
    setError('');
    try {
      const value = await props.load(abort.signal);
      if (!abort.signal.aborted && session.active) accept(value);
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
    // Retained intent survives callback changes and panel remounts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session]);
  async function review(
    provider: SubscriptionOptionsReview['provider_id'],
    operation: SubscriptionOptionsReview['operation'],
  ) {
    if (!snapshot || locked) return;
    const intent = {
      provider_id: provider,
      provider_revision: snapshot.revision,
      operation,
      value: operation === 'client_id_save' ? client : null,
    };
    const abort = session.read();
    setBusy('review');
    setReviewed(null);
    setError('');
    try {
      const result = await props.review(intent, abort.signal);
      if (abort.signal.aborted || !session.active) return;
      if (
        result.provider_id !== provider ||
        result.provider_revision !== snapshot.revision ||
        result.operation !== operation ||
        result.value !== intent.value
      )
        throw { code: 'revision_conflict' };
      setReviewed(structuredClone(result));
    } catch (cause) {
      if (!abort.signal.aborted) setError(clientError(cause).message);
    } finally {
      session.finishRead(abort);
      setBusy('');
    }
  }
  async function confirm() {
    if (
      !session.active ||
      session.get('busy', '') ||
      session.get('pending', null)
    )
      return;
    const captured = session.get<SubscriptionOptionsReview | null>(
      'reviewed',
      null,
    );
    if (!captured) return;
    const original = {
      commandId: crypto.randomUUID(),
      review: structuredClone(captured),
    };
    const generation = epoch.current;
    setPending(original);
    setReviewed(null);
    setBusy('apply');
    setError('');
    try {
      const value = await session.perform([original], () =>
        props.apply(structuredClone(original.review), original.commandId),
      );
      if (!session.active) return;
      accept(value);
      setPending(null);
      session.resolved();
      setNotice(
        original.review.operation === 'reference'
          ? 'CLI reference saved as metadata only. No credentials were imported.'
          : 'OAuth client settings saved. Existing sign-in flows may need to be restarted.',
      );
      if (generation === epoch.current) props.onSaved(value);
    } catch (cause) {
      setError(clientError(cause).message);
      setNotice(
        'The original outcome is unconfirmed. Read its receipt before taking another action.',
      );
    } finally {
      setBusy('');
    }
  }
  async function receipt() {
    if (!pending || busy || !session.active) return;
    const abort = session.read();
    setBusy('receipt');
    setError('');
    try {
      const result = await props.receipt(pending.commandId, abort.signal);
      if (abort.signal.aborted || !session.active) return;
      if (result.command_id !== pending.commandId)
        throw { code: 'operation_uncertain' };
      if (result.status === 'uncertain')
        setNotice(
          'The original outcome remains unconfirmed. No operation was replayed.',
        );
      else {
        accept(result.options);
        setPending(null);
        session.resolved();
        setNotice(
          result.status === 'completed'
            ? 'The original option change is confirmed.'
            : 'The original option change was rejected. Review current settings before trying again.',
        );
      }
    } catch (cause) {
      if (!abort.signal.aborted) setError(clientError(cause).message);
    } finally {
      session.finishRead(abort);
      setBusy('');
    }
  }
  const needsAttention =
    dirty || !!reviewed || !!pending || !!error || !!notice;
  return (
    <details
      className="settings-provider-secondary"
      open={props.collapsedAtRest && !needsAttention ? undefined : true}
    >
      <summary>
        <span>
          <strong>Subscription account options</strong>
          <small>CLI references and OAuth client settings</small>
        </span>
      </summary>
      <section
        className="stack settings-provider-secondary-content"
        aria-label="Subscription account options"
        aria-busy={!!busy}
      >
        <p>
          Reference an existing CLI login as metadata only. This does not import
          its credentials or enable the subscription runtime.
        </p>
        {busy === 'load' && (
          <Skeleton label="Loading saved subscription options" />
        )}
        {error && <p role="alert">{error}</p>}
        {notice && <p role="status">{notice}</p>}
        {snapshot && (
          <>
            <ul>
              {snapshot.references.map((reference) => (
                <li key={reference.provider_id}>
                  {reference.provider_id === 'codex'
                    ? 'Codex CLI'
                    : 'Claude Code'}
                  :{' '}
                  {reference.metadata_saved
                    ? 'metadata reference saved'
                    : 'no saved metadata reference'}
                </li>
              ))}
            </ul>
            <p>
              xAI OAuth client source: {snapshot.xai_client_id_source}. Runtime
              readiness: unknown.
            </p>
          </>
        )}
        <div className="actions">
          <Button
            disabled={locked || !snapshot || dirty}
            onClick={() => void review('codex', 'reference')}
          >
            Review Codex CLI reference
          </Button>
          <Button
            disabled={locked || !snapshot || dirty}
            onClick={() => void review('claude_subscription', 'reference')}
          >
            Review Claude Code reference
          </Button>
        </div>
        <Field label="xAI OAuth client ID override">
          <Input
            aria-label="xAI OAuth client ID override"
            value={client}
            maxLength={512}
            disabled={locked}
            onChange={(event) => {
              setClient(event.target.value);
              setDirty(true);
              setReviewed(null);
            }}
          />
        </Field>
        <p>
          Use the built-in client unless you have your own xAI OAuth app. An
          environment override takes precedence over saved settings.
        </p>
        <div className="actions">
          <Button
            disabled={locked || !snapshot || !client}
            onClick={() => void review('xai_oauth', 'client_id_save')}
          >
            Review client ID override
          </Button>
          <Button
            disabled={locked || !snapshot}
            onClick={() => void review('xai_oauth', 'client_id_reset')}
          >
            Review reset to default
          </Button>
          <Button disabled={locked || !reviewed} onClick={() => void confirm()}>
            Confirm account option
          </Button>
          <Button
            disabled={!pending || !!busy || !session.active}
            onClick={() => void receipt()}
          >
            Read original option receipt
          </Button>
          <Button
            disabled={locked || (!dirty && !reviewed)}
            onClick={() => {
              if (snapshot) accept(snapshot);
            }}
          >
            Discard unsent option
          </Button>
          <Button
            disabled={locked || dirty || !!reviewed}
            onClick={() => void load()}
          >
            Reload saved account options
          </Button>
        </div>
        {reviewed && (
          <p role="status">
            Reviewed:{' '}
            {reviewed.operation === 'reference'
              ? `${reviewed.provider_id === 'codex' ? 'Codex CLI' : 'Claude Code'} metadata reference`
              : reviewed.operation === 'client_id_save'
                ? `xAI client ID ${reviewed.value}`
                : 'Reset xAI client ID to default'}
            . Confirm to apply this exact option.
          </p>
        )}
      </section>
    </details>
  );
}
