import { useEffect, useRef } from 'react';
import type { DefaultModelSnapshot } from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, Field, Input, Skeleton } from '../../ui/primitives';
import {
  ProviderSettingsSession,
  useProviderSettingsValue,
} from './provider-settings-sessions';

type Review = {
  settings_revision: string;
  provider_id: string;
  model_id: string;
  operation: 'provider.default_model.save';
  action_digest: string;
  nonce?: string;
};
type Pending = { commandId: string; review: Review };
export class DefaultModelSession extends ProviderSettingsSession {
  constructor() {
    super('default_model');
  }
  hasRetained() {
    return this.get('dirty', false) || this.retained();
  }
}
export type DefaultModelSettingsProps = {
  session?: DefaultModelSession;
  load: (signal?: AbortSignal) => Promise<DefaultModelSnapshot>;
  review: (
    revision: string,
    providerId: string,
    modelId: string,
    signal?: AbortSignal,
  ) => Promise<Review>;
  apply: (review: Review, commandId: string) => Promise<DefaultModelSnapshot>;
  receipt: (
    commandId: string,
    signal?: AbortSignal,
  ) => Promise<{
    status: 'completed' | 'rejected' | 'uncertain';
    selection: DefaultModelSnapshot;
  }>;
  onSaved: (snapshot: DefaultModelSnapshot) => void;
  onBrowseModels: () => void;
};

export default function DefaultModelSettings(props: DefaultModelSettingsProps) {
  const local = useRef<DefaultModelSession | null>(null);
  if (!local.current) local.current = new DefaultModelSession();
  const session = props.session ?? local.current;
  const [snapshot, setSnapshot] =
    useProviderSettingsValue<DefaultModelSnapshot | null>(
      session,
      'snapshot',
      null,
    );
  const [provider, setProvider] = useProviderSettingsValue(
    session,
    'provider',
    '',
  );
  const [model, setModel] = useProviderSettingsValue(session, 'model', '');
  const [dirty, setDirty] = useProviderSettingsValue(session, 'dirty', false);
  const [reviewed, setReviewed] = useProviderSettingsValue<Review | null>(
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
  function accept(value: DefaultModelSnapshot) {
    setSnapshot(value);
    setProvider(value.provider_id ?? '');
    setModel(value.model_id ?? '');
    setDirty(false);
    setReviewed(null);
  }
  async function load() {
    if (
      !session.active ||
      session.get('busy', '') ||
      session.get<Pending | null>('pending', null)
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
    if (
      !session.get<DefaultModelSnapshot | null>('snapshot', null) &&
      !session.get('busy', '')
    )
      void load();
    return () => {
      epoch.current += 1;
      if (!props.session) session.dispose();
    };
    // Callback changes must not reload a retained private intent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session]);
  async function review() {
    if (locked || !snapshot) return;
    const abort = session.read();
    setBusy('review');
    setReviewed(null);
    setError('');
    try {
      const value = await props.review(
        snapshot.revision,
        provider,
        model,
        abort.signal,
      );
      if (abort.signal.aborted || !session.active) return;
      if (
        value.settings_revision !== snapshot.revision ||
        value.provider_id !== provider ||
        value.model_id !== model ||
        value.operation !== 'provider.default_model.save'
      )
        throw { code: 'revision_conflict' };
      setReviewed(structuredClone(value));
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
      session.get<Pending | null>('pending', null)
    )
      return;
    const captured = session.get<Review | null>('reviewed', null);
    if (!captured) return;
    const original = {
      commandId: crypto.randomUUID(),
      review: structuredClone(captured),
    };
    const generation = epoch.current;
    setPending(original);
    setBusy('apply');
    setReviewed(null);
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
        'Global default saved for future work. No provider was started or unloaded.',
      );
      if (generation === epoch.current) props.onSaved(value);
    } catch (cause) {
      setError(clientError(cause).message);
      setNotice(
        'The original outcome is unconfirmed. Check its receipt; this request will not be sent again.',
      );
    } finally {
      setBusy('');
    }
  }
  async function receipt() {
    if (busy || !pending || !session.active) return;
    const abort = session.read();
    setBusy('receipt');
    setError('');
    try {
      const value = await props.receipt(pending.commandId, abort.signal);
      if (abort.signal.aborted || !session.active) return;
      if (value.status === 'uncertain')
        setNotice(
          'The original outcome remains unconfirmed. No request was replayed.',
        );
      else {
        setPending(null);
        session.resolved();
        accept(value.selection);
        setNotice(
          value.status === 'completed'
            ? 'The original save is confirmed.'
            : 'The original save was rejected. Review the current selection before trying again.',
        );
      }
    } catch (cause) {
      if (!abort.signal.aborted) setError(clientError(cause).message);
    } finally {
      session.finishRead(abort);
      setBusy('');
    }
  }
  return (
    <section
      className="stack"
      aria-label="Default chat model"
      aria-busy={!!busy}
    >
      <h2>Default chat model</h2>
      <p>
        This global choice applies to future work without a conversation
        override. Existing runs keep their captured model. Saving does not test
        readiness or start, unload or contact a provider.
      </p>
      {busy === 'load' && <Skeleton label="Loading saved default model" />}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {snapshot && (
        <p>
          Saved default:{' '}
          {snapshot.selection_ref ??
            (snapshot.saved_state === 'missing'
              ? 'No explicit saved choice'
              : 'Existing choice needs an explicit provider selection')}{' '}
          · Runtime readiness: unknown
        </p>
      )}
      <div className="field-row">
        <Field label="Default provider ID">
          <Input
            value={provider}
            maxLength={128}
            disabled={locked}
            onChange={(event) => {
              setProvider(event.target.value);
              setDirty(true);
              setReviewed(null);
            }}
          />
        </Field>
        <Field label="Default exact model ID">
          <Input
            value={model}
            maxLength={512}
            disabled={locked}
            onChange={(event) => {
              setModel(event.target.value);
              setDirty(true);
              setReviewed(null);
            }}
          />
        </Field>
      </div>
      <div className="actions">
        <Button onClick={props.onBrowseModels}>Browse saved models</Button>
        <Button
          disabled={locked || !snapshot || !provider || !model}
          onClick={() => void review()}
        >
          Review default model
        </Button>
        <Button disabled={locked || !reviewed} onClick={() => void confirm()}>
          Confirm default model
        </Button>
        <Button
          disabled={!pending || !!busy || !session.active}
          onClick={() => void receipt()}
        >
          Check original default receipt
        </Button>
        <Button
          disabled={locked || !dirty}
          onClick={() => {
            if (snapshot) accept(snapshot);
          }}
        >
          Discard unsent default
        </Button>
        <Button disabled={locked || dirty} onClick={() => void load()}>
          Reload saved default
        </Button>
      </div>
      {reviewed && (
        <p role="status">
          Reviewed global default: {reviewed.provider_id} / {reviewed.model_id}.
          Confirm to save this exact choice.
        </p>
      )}
    </section>
  );
}
