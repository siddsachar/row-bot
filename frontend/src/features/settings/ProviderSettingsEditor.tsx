import { useEffect, useRef } from 'react';
import {
  ProviderSettingsSession,
  useProviderSettingsValue,
} from './provider-settings-sessions';
import { clientError } from '../../api/errors';
import type { ProviderSettingsSnapshot } from '../../api/types';
import { Button, Field, Input, Select, Skeleton } from '../../ui/primitives';

type ProviderSettingsView = ProviderSettingsSnapshot;
type Operation = 'save' | 'clear' | 'restore';
export type ProviderSettingsEditorProps = {
  providerId: string;
  session?: ProviderSettingsSession;
  load: (
    providerId: string,
    signal?: AbortSignal,
  ) => Promise<ProviderSettingsView>;
  review: (
    providerId: string,
    revision: string,
    operation: Operation,
    value?: string,
    signal?: AbortSignal,
  ) => Promise<ProviderSettingsView>;
  apply: (
    providerId: string,
    revision: string,
    operation: Operation,
    value: string | undefined,
    commandId: string,
  ) => Promise<ProviderSettingsView>;
  receipt: (
    providerId: string,
    commandId: string,
    signal?: AbortSignal,
  ) => Promise<
    | ProviderSettingsView
    | { rejected: true; snapshot: ProviderSettingsView }
    | null
  >;
  onSaved: (snapshot: ProviderSettingsView) => void;
  onCancel: () => void;
};

export default function ProviderSettingsEditor(
  props: ProviderSettingsEditorProps,
) {
  const { providerId, load, review, apply, receipt, onSaved, onCancel } = props;
  const local = useRef<ProviderSettingsSession | null>(null);
  if (!local.current || local.current.providerId !== providerId)
    local.current = new ProviderSettingsSession(providerId);
  const session = props.session ?? local.current;
  const [snapshot, setSnapshot] =
    useProviderSettingsValue<ProviderSettingsView | null>(
      session,
      'snapshot',
      null,
    );
  const [operation, setOperation] = useProviderSettingsValue<Operation>(
    session,
    'operation',
    'save',
  );
  const [secret, setSecret] = useProviderSettingsValue(session, 'secret', '');
  const [reviewed, setReviewed] = useProviderSettingsValue(
    session,
    'reviewed',
    false,
  );
  const [busy, setBusy] = useProviderSettingsValue(session, 'busy', 'load');
  const [error, setError] = useProviderSettingsValue(session, 'error', '');
  const [notice, setNotice] = useProviderSettingsValue(session, 'notice', '');
  const [pending, setPending] = useProviderSettingsValue<string | null>(
    session,
    'pending',
    null,
  );
  const [reload, setReload] = useProviderSettingsValue(session, 'reload', 0);
  const epoch = useRef(0);
  const effectPending = useRef(false);
  const reviewAbort = useRef<AbortController | null>(null);
  const readAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    const generation = ++epoch.current;
    const abort = session.beginRead(reload);
    const cleanup = () => {
      epoch.current += 1;
      if (!props.session) {
        abort?.abort();
        reviewAbort.current?.abort();
        readAbort.current?.abort();
      }
    };
    if (!abort) return cleanup;
    setSnapshot(null);
    setSecret('');
    setPending(null);
    setReviewed(false);
    setOperation('save');
    setError('');
    setNotice('');
    setBusy('load');
    load(providerId, abort.signal)
      .then(
        (result) => {
          if (
            abort.signal.aborted ||
            !session.active ||
            (!props.session && generation !== epoch.current)
          )
            return;
          if (result.provider_id !== providerId)
            setError('The provider response did not match this request.');
          else setSnapshot(result);
          setBusy('');
        },
        (cause: unknown) => {
          if (
            abort.signal.aborted ||
            !session.active ||
            (!props.session && generation !== epoch.current)
          )
            return;
          setError(clientError(cause).message);
          setBusy('');
        },
      )
      .finally(() => session.finishRead(abort));
    return cleanup;
  }, [
    providerId,
    load,
    reload,
    session,
    props.session,
    setSnapshot,
    setSecret,
    setPending,
    setReviewed,
    setOperation,
    setError,
    setNotice,
    setBusy,
  ]);

  const alive = (generation: number) =>
    session.active && (!!props.session || generation === epoch.current);

  function changed() {
    reviewAbort.current?.abort();
    setReviewed(false);
    setNotice('');
    setError('');
  }
  async function reviewChange() {
    if (!snapshot || effectPending.current || busy || pending) return;
    const abort = session.read();
    reviewAbort.current?.abort();
    reviewAbort.current = abort;
    const generation = epoch.current;
    setBusy('review');
    setReviewed(false);
    setError('');
    try {
      const result = await review(
        providerId,
        snapshot.revision,
        operation,
        operation === 'save' ? secret : undefined,
        abort.signal,
      );
      if (abort.signal.aborted || !alive(generation)) return;
      if (
        result.provider_id !== providerId ||
        result.revision !== snapshot.revision
      )
        throw { code: 'revision_conflict' };
      if (
        result.externally_managed ||
        (result.storage_unavailable && operation !== 'restore')
      )
        throw { code: 'action_denied' };
      setReviewed(true);
    } catch (cause) {
      if (!abort.signal.aborted && alive(generation))
        setError(clientError(cause).message);
    } finally {
      session.finishRead(abort);
      if (!abort.signal.aborted && alive(generation)) setBusy('');
    }
  }
  async function confirm() {
    if (!snapshot || !reviewed || busy || pending || effectPending.current)
      return;
    effectPending.current = true;
    const generation = epoch.current;
    const identity = crypto.randomUUID();
    const value = operation === 'save' ? secret : undefined;
    setSecret('');
    setPending(identity);
    setBusy('save');
    setReviewed(false);
    setError('');
    try {
      const result = await session.perform(
        [providerId, snapshot.revision, operation, value, identity],
        () => apply(providerId, snapshot.revision, operation, value, identity),
      );
      if (!alive(generation)) return;
      if (result.provider_id !== providerId)
        throw { code: 'operation_uncertain' };
      setSnapshot(result);
      setPending(null);
      session.resolved();
      setNotice('Saved locally. Provider connectivity has not been tested.');
      if (generation === epoch.current) onSaved(result);
    } catch (cause) {
      if (alive(generation)) {
        setError(clientError(cause).message);
        setNotice(
          'The original outcome is unconfirmed. Check its receipt before starting another reviewed change.',
        );
      }
    } finally {
      effectPending.current = false;
      if (alive(generation)) setBusy('');
    }
  }
  async function checkReceipt() {
    if (!pending || busy || effectPending.current) return;
    const abort = session.read();
    readAbort.current?.abort();
    readAbort.current = abort;
    const generation = epoch.current;
    setBusy('receipt');
    setError('');
    try {
      const result = await receipt(providerId, pending, abort.signal);
      if (abort.signal.aborted || !alive(generation)) return;
      if (result) {
        const rejected = 'rejected' in result;
        const current = rejected ? result.snapshot : result;
        if (current.provider_id !== providerId)
          throw { code: 'operation_uncertain' };
        setSnapshot(current);
        setPending(null);
        session.resolved();
        setNotice(
          rejected
            ? 'The original change was rejected. Review the current saved status before trying again.'
            : 'The original change is confirmed. Provider connectivity has not been tested.',
        );
        if (!rejected && generation === epoch.current) onSaved(current);
      } else
        setNotice(
          'No completed receipt is available. The original change has not been sent again.',
        );
    } catch (cause) {
      if (!abort.signal.aborted && alive(generation))
        setError(clientError(cause).message);
    } finally {
      session.finishRead(abort);
      if (!abort.signal.aborted && alive(generation)) setBusy('');
    }
  }
  const locked =
    !session.active ||
    !!busy ||
    !!pending ||
    !!snapshot?.externally_managed ||
    (!!snapshot?.storage_unavailable && operation !== 'restore');
  return (
    <section
      className="stack"
      aria-label="Provider credential settings"
      aria-busy={!!busy}
    >
      <h2>{snapshot?.display_name ?? 'Provider'} credentials</h2>
      <p>
        Credentials are write-only. Saving verifies local secure storage; it
        does not contact the provider or refresh models.
      </p>
      {busy === 'load' && <Skeleton label="Loading saved credential status" />}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {snapshot && (
        <>
          <p>
            {snapshot.configured
              ? 'Credential configured'
              : 'No active saved credential'}{' '}
            · Source: {snapshot.source}
          </p>
          {snapshot.externally_managed && (
            <p role="status">
              This credential is managed by the environment or a secret file.
              Update it through that existing owner.
            </p>
          )}
          {snapshot.storage_unavailable && (
            <p role="status">
              Secure storage is unavailable. Existing credentials are retained.
            </p>
          )}
          <Field label="Credential action">
            <Select
              value={operation}
              disabled={!!busy || !!pending || !!snapshot.externally_managed}
              onChange={(event) => {
                changed();
                setSecret('');
                setOperation(event.target.value as Operation);
              }}
            >
              <option value="save">Replace saved API key</option>
              <option value="clear">Disconnect saved API key</option>
              <option value="restore" disabled={!snapshot.recovery_available}>
                Restore previous local credential
              </option>
            </Select>
          </Field>
          {operation === 'save' && (
            <Field label="New API key">
              <Input
                type="password"
                autoComplete="new-password"
                spellCheck={false}
                maxLength={16384}
                value={secret}
                disabled={locked}
                onChange={(event) => {
                  changed();
                  setSecret(event.target.value);
                }}
              />
            </Field>
          )}
          <p>
            The previous local credential is retained for explicit recovery.
            Disconnecting stops its use without deleting retained recovery
            bytes.
          </p>
          {reviewed && (
            <p role="status">
              Review complete:{' '}
              {operation === 'save'
                ? 'replace the saved API key'
                : operation === 'clear'
                  ? 'disconnect the saved API key'
                  : 'restore the previous local credential'}{' '}
              for {snapshot.display_name}.
            </p>
          )}
          <div className="actions">
            <Button
              disabled={
                locked ||
                (operation === 'save' && !secret.trim()) ||
                (operation === 'restore' && !snapshot.recovery_available)
              }
              onClick={() => void reviewChange()}
            >
              Review change
            </Button>
            <Button
              variant="primary"
              disabled={locked || !reviewed}
              onClick={() => void confirm()}
            >
              Confirm{' '}
              {operation === 'save'
                ? 'replacement'
                : operation === 'clear'
                  ? 'disconnect'
                  : 'restore'}
            </Button>
            {pending && (
              <Button disabled={!!busy} onClick={() => void checkReceipt()}>
                Check original receipt
              </Button>
            )}
          </div>
        </>
      )}
      <div className="actions">
        <Button
          disabled={!!busy || !!pending}
          onClick={() => {
            setSecret('');
            setReload((value) => value + 1);
          }}
        >
          Reload saved status
        </Button>
        <Button
          disabled={!!busy}
          onClick={() => {
            setSecret('');
            setReviewed(false);
            onCancel();
          }}
        >
          Cancel
        </Button>
      </div>
    </section>
  );
}
