import { useEffect, useRef } from 'react';
import { clientError } from '../../api/errors';
import type {
  SubscriptionProbeRequest,
  SubscriptionProbeResult,
  SubscriptionProbeReview,
  SubscriptionProbeSnapshot,
  SubscriptionProbeState,
} from '../../api/types';
import { Button, Field, Input, Select, Skeleton } from '../../ui/primitives';
import {
  ProviderSettingsSession,
  useProviderSettingsValue,
} from './provider-settings-sessions';

type Provider = SubscriptionProbeRequest['provider_id'];
type Kind = SubscriptionProbeRequest['kind'];
type Pending = { commandId: string; review: SubscriptionProbeReview };
const names: Record<Provider, string> = {
  codex: 'Codex',
  claude_subscription: 'Claude subscription',
  xai_oauth: 'xAI subscription',
};
const kinds: Record<Provider, Kind[]> = {
  codex: ['tokens'],
  claude_subscription: ['tokens', 'runtime'],
  xai_oauth: ['tokens', 'runtime', 'vision'],
};
const labels: Record<Kind, string> = {
  tokens: 'Stored credentials and expiry',
  runtime: 'Chat and tool compatibility',
  vision: 'Image understanding',
};
const flag = (value: boolean | null) =>
  value === null ? 'unknown' : value ? 'passed' : 'failed';

export class SubscriptionProbesSession extends ProviderSettingsSession {
  constructor() {
    super('subscription_probes');
  }
  hasRetained() {
    return this.get('dirty', false) || this.retained();
  }
}
export type SubscriptionProbesProps = {
  collapsedAtRest?: boolean;
  session?: SubscriptionProbesSession;
  load: (signal?: AbortSignal) => Promise<SubscriptionProbeSnapshot>;
  review: (
    intent: SubscriptionProbeRequest,
    signal?: AbortSignal,
  ) => Promise<SubscriptionProbeReview>;
  apply: (
    review: SubscriptionProbeReview,
    commandId: string,
  ) => Promise<SubscriptionProbeResult>;
  status: (
    commandId: string,
    signal?: AbortSignal,
  ) => Promise<SubscriptionProbeState | null>;
  cancel: (commandId: string) => Promise<SubscriptionProbeState>;
  receipt: (
    commandId: string,
    signal?: AbortSignal,
  ) => Promise<{
    command_id: string;
    provider_id: Provider;
    status: 'completed' | 'rejected' | 'uncertain';
    published: boolean;
  }>;
  onSaved: () => void;
  onBrowseModels?: () => void;
};

export default function SubscriptionProbes(props: SubscriptionProbesProps) {
  const local = useRef<SubscriptionProbesSession | null>(null);
  if (!local.current) local.current = new SubscriptionProbesSession();
  const session = props.session ?? local.current;
  const [snapshot, setSnapshot] =
    useProviderSettingsValue<SubscriptionProbeSnapshot | null>(
      session,
      'snapshot',
      null,
    );
  const [provider, setProvider] = useProviderSettingsValue<Provider>(
    session,
    'provider',
    'codex',
  );
  const [kind, setKind] = useProviderSettingsValue<Kind>(
    session,
    'kind',
    'tokens',
  );
  const [model, setModel] = useProviderSettingsValue(session, 'model', '');
  const [dirty, setDirty] = useProviderSettingsValue(session, 'dirty', false);
  const [reviewed, setReviewed] =
    useProviderSettingsValue<SubscriptionProbeReview | null>(
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
  const [checking, setChecking] = useProviderSettingsValue(
    session,
    'checking',
    '',
  );
  const [state, setState] =
    useProviderSettingsValue<SubscriptionProbeState | null>(
      session,
      'state',
      null,
    );
  const [result, setResult] =
    useProviderSettingsValue<SubscriptionProbeResult | null>(
      session,
      'result',
      null,
    );
  const [error, setError] = useProviderSettingsValue(session, 'error', '');
  const [notice, setNotice] = useProviderSettingsValue(session, 'notice', '');
  const epoch = useRef(0);
  const locked = !!busy || !!checking || !!pending || !session.active;
  function changed() {
    setDirty(true);
    setReviewed(null);
    setError('');
  }
  async function load() {
    if (
      !session.active ||
      session.get('busy', '') ||
      session.get('pending', null) ||
      session.get('dirty', false) ||
      session.get('reviewed', null)
    )
      return;
    const abort = session.read();
    setBusy('load');
    setError('');
    try {
      const value = await props.load(abort.signal);
      if (!abort.signal.aborted && session.active) setSnapshot(value);
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
    // Callback replacements must not discard the retained original operation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session]);
  async function review() {
    if (!snapshot || locked) return;
    const intent: SubscriptionProbeRequest = {
      provider_id: provider,
      provider_revision: snapshot.revision,
      kind,
      model_ref: kind === 'tokens' ? null : model,
    };
    const abort = session.read();
    setBusy('review');
    setError('');
    setReviewed(null);
    let approved: SubscriptionProbeReview | null = null;
    try {
      const value = await props.review(intent, abort.signal);
      if (!session.active || abort.signal.aborted) return;
      if (
        value.provider_id !== intent.provider_id ||
        value.provider_revision !== intent.provider_revision ||
        value.kind !== intent.kind ||
        value.model_ref !== intent.model_ref
      )
        throw { code: 'revision_conflict' };
      approved = structuredClone(value);
    } catch (cause) {
      if (!abort.signal.aborted) setError(clientError(cause).message);
    } finally {
      session.finishRead(abort);
      setBusy('');
    }
    if (approved) await confirm(approved);
  }
  function verifyResult(value: SubscriptionProbeResult, original: Pending) {
    if (
      value.provider_id !== original.review.provider_id ||
      value.kind !== original.review.kind ||
      value.model_ref !== original.review.model_ref
    )
      throw { code: 'operation_uncertain' };
  }
  function settle(value: SubscriptionProbeResult | null) {
    setResult(value);
    setPending(null);
    setReviewed(null);
    setDirty(false);
    setSnapshot(null);
    session.resolved();
  }
  async function confirm(approved: SubscriptionProbeReview) {
    if (
      !session.active ||
      session.get('busy', '') ||
      session.get('pending', null)
    )
      return;
    const original = {
      commandId: crypto.randomUUID(),
      review: structuredClone(approved),
    };
    const generation = epoch.current;
    setPending(original);
    setReviewed(null);
    setState(null);
    setResult(null);
    setBusy('apply');
    setError('');
    try {
      const value = await session.perform([original], () =>
        props.apply(structuredClone(original.review), original.commandId),
      );
      if (!session.active) return;
      verifyResult(value, original);
      settle(value);
      setNotice(
        'The check completed. Reload saved checks before running another check.',
      );
      if (generation === epoch.current) props.onSaved();
    } catch (cause) {
      setError(clientError(cause).message);
      setNotice(
        'The original outcome is unconfirmed. Check its progress or receipt; it will not be replayed.',
      );
    } finally {
      setBusy('');
    }
  }
  async function inspect(action: 'status' | 'cancel' | 'receipt') {
    const original = session.get<Pending | null>('pending', null);
    if (
      !session.active ||
      !original ||
      session.get('checking', '') ||
      (action === 'receipt' && session.get('busy', ''))
    )
      return;
    const abort = session.read();
    setChecking(action);
    setError('');
    try {
      const receipt =
        action === 'receipt'
          ? await props.receipt(original.commandId, abort.signal)
          : null;
      if (!session.active || abort.signal.aborted) return;
      if (
        receipt &&
        (receipt.command_id !== original.commandId ||
          receipt.provider_id !== original.review.provider_id)
      )
        throw { code: 'operation_uncertain' };
      const value =
        action === 'cancel'
          ? await props.cancel(original.commandId)
          : await props.status(original.commandId, abort.signal);
      if (
        !session.active ||
        abort.signal.aborted ||
        session.get<Pending | null>('pending', null)?.commandId !==
          original.commandId
      )
        return;
      if (
        value &&
        (value.command_id !== original.commandId ||
          value.provider_id !== original.review.provider_id)
      )
        throw { code: 'operation_uncertain' };
      if (value?.result) verifyResult(value.result, original);
      setState(value);
      if (
        !session.get('busy', '') &&
        value?.quiescent &&
        ((value.state === 'completed' && value.result) ||
          (receipt && receipt.status !== 'uncertain'))
      ) {
        settle(value.result);
        setNotice(
          receipt?.status === 'rejected'
            ? 'The original check was rejected. Reload saved checks before trying again.'
            : 'The original check is confirmed and its work has stopped. Reload saved checks to continue.',
        );
      } else if (value && !value.quiescent) {
        setNotice(
          value.state === 'draining'
            ? 'Cancellation requested. Waiting for the original work to stop.'
            : 'The original check is still running.',
        );
      } else {
        setNotice(
          receipt?.published
            ? 'The saved result is confirmed. The original work has not yet been confirmed stopped.'
            : 'The original outcome remains unconfirmed. No provider request was replayed.',
        );
      }
    } catch (cause) {
      if (!abort.signal.aborted) setError(clientError(cause).message);
    } finally {
      session.finishRead(abort);
      setChecking('');
    }
  }
  const needsAttention =
    dirty ||
    !!reviewed ||
    !!pending ||
    !!state ||
    !!result ||
    !!error ||
    !!notice;
  return (
    <details
      className="settings-provider-secondary"
      open={props.collapsedAtRest && !needsAttention ? undefined : true}
    >
      <summary>
        <span>
          <strong>Subscription checks</strong>
          <small>Saved results and explicit readiness checks</small>
        </span>
      </summary>
      <section
        className="stack settings-provider-secondary-content"
        aria-label="Subscription checks"
        aria-busy={!!busy || !!checking}
      >
        <p>
          Saved results describe the last explicit check. They do not guarantee
          current provider readiness.
        </p>
        {busy === 'load' && (
          <Skeleton label="Loading saved subscription checks" />
        )}
        {error && <p role="alert">{error}</p>}
        {notice && <p role="status">{notice}</p>}
        {snapshot && (
          <ul>
            {snapshot.items.map((item) => (
              <li key={`${item.provider_id}:${item.kind}`}>
                {names[item.provider_id]} · {labels[item.kind]}: {item.status}
                {item.checked_at ? ` (${item.checked_at})` : ' (not checked)'}
                {item.model_ref && <p>{item.model_ref}</p>}
                {item.kind === 'runtime' && (
                  <p>
                    Chat: {flag(item.chat_ok)}. Tool request:{' '}
                    {flag(item.tool_calling)}. Tool round trip:{' '}
                    {flag(item.tool_round_trip)}.
                  </p>
                )}
                {item.kind === 'vision' && (
                  <p>Image understanding: {flag(item.vision_ok)}.</p>
                )}
              </li>
            ))}
          </ul>
        )}
        {result && (
          <p role="status">
            Last result: {names[result.provider_id]} · {labels[result.kind]}:{' '}
            {result.status}.
          </p>
        )}
        <div className="field-row">
          <Field label="Subscription provider">
            <Select
              aria-label="Subscription provider"
              value={provider}
              disabled={locked}
              onChange={(event) => {
                setProvider(event.target.value as Provider);
                setKind('tokens');
                setModel('');
                changed();
              }}
            >
              {Object.entries(names).map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Check type">
            <Select
              aria-label="Check type"
              value={kind}
              disabled={locked}
              onChange={(event) => {
                setKind(event.target.value as Kind);
                changed();
              }}
            >
              {kinds[provider].map((value) => (
                <option key={value} value={value}>
                  {labels[value]}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        {kind !== 'tokens' && (
          <Field label="Provider-qualified model reference">
            <Input
              aria-label="Provider-qualified model reference"
              value={model}
              maxLength={647}
              disabled={locked}
              onChange={(event) => {
                setModel(event.target.value);
                changed();
              }}
            />
          </Field>
        )}
        <p>
          {kind === 'tokens'
            ? 'This checks stored credentials and expiry only. It does not contact the provider or confirm remote readiness.'
            : kind === 'vision'
              ? 'Running this check sends a small synthetic image and prompt to this exact saved model. Provider usage may apply.'
              : 'Running this check sends synthetic chat and tool requests to this exact saved model. Provider usage may apply.'}
        </p>
        {state && (
          <p>
            Original work: {state.state}.{' '}
            {state.quiescent ? 'Stopped.' : 'Still active.'}
          </p>
        )}
        <div className="actions">
          {props.onBrowseModels && kind !== 'tokens' && (
            <Button disabled={locked} onClick={props.onBrowseModels}>
              Browse saved models
            </Button>
          )}
          <Button
            disabled={locked || !snapshot || (kind !== 'tokens' && !model)}
            onClick={() => void review()}
          >
            Run check
          </Button>
          <Button
            disabled={!pending || !!checking || !session.active}
            onClick={() => void inspect('status')}
          >
            Check original progress
          </Button>
          <Button
            disabled={
              !pending ||
              !!checking ||
              !session.active ||
              state?.quiescent === true
            }
            onClick={() => void inspect('cancel')}
          >
            Cancel original check
          </Button>
          <Button
            disabled={!pending || !!busy || !!checking || !session.active}
            onClick={() => void inspect('receipt')}
          >
            Read original check receipt
          </Button>
          <Button
            disabled={locked || (!dirty && !reviewed)}
            onClick={() => {
              setDirty(false);
              setReviewed(null);
              setModel('');
              setProvider('codex');
              setKind('tokens');
            }}
          >
            Discard unsent check
          </Button>
          <Button
            disabled={locked || dirty || !!reviewed}
            onClick={() => void load()}
          >
            Reload saved checks
          </Button>
        </div>
      </section>
    </details>
  );
}
