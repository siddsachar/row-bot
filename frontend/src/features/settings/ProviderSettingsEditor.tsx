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
  compact?: boolean;
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
  const readAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    const generation = ++epoch.current;
    const abort = session.beginRead(reload);
    const cleanup = () => {
      epoch.current += 1;
      if (!props.session) {
        abort?.abort();
        readAbort.current?.abort();
      }
    };
    if (!abort) return cleanup;
    setSnapshot(null);
    setSecret('');
    setPending(null);
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
    setOperation,
    setError,
    setNotice,
    setBusy,
  ]);

  const alive = (generation: number) =>
    session.active && (!!props.session || generation === epoch.current);

  function changed() {
    setNotice('');
    setError('');
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
  async function performDirect(next: Operation) {
    if (
      !snapshot ||
      locked ||
      effectPending.current ||
      (next === 'save' && !secret.trim()) ||
      (next === 'restore' && !snapshot.recovery_available)
    )
      return;
    const abort = session.read();
    const generation = epoch.current;
    setBusy('review');
    setError('');
    setNotice('');
    let reviewedSnapshot: ProviderSettingsView;
    try {
      reviewedSnapshot = await review(
        providerId,
        snapshot.revision,
        next,
        next === 'save' ? secret : undefined,
        abort.signal,
      );
      if (abort.signal.aborted || !alive(generation)) return;
      if (
        reviewedSnapshot.provider_id !== providerId ||
        reviewedSnapshot.revision !== snapshot.revision
      )
        throw { code: 'revision_conflict' };
      if (
        reviewedSnapshot.externally_managed ||
        (reviewedSnapshot.storage_unavailable && next !== 'restore')
      )
        throw { code: 'action_denied' };
    } catch (cause) {
      if (!abort.signal.aborted && alive(generation))
        setError(clientError(cause).message);
      return;
    } finally {
      session.finishRead(abort);
      if (alive(generation)) setBusy('');
    }
    if (!alive(generation)) return;
    const identity = crypto.randomUUID();
    const value = next === 'save' ? secret : undefined;
    effectPending.current = true;
    setPending(identity);
    setBusy('save');
    setSecret('');
    try {
      const result = await session.perform(
        [providerId, snapshot.revision, next, value, identity],
        () => apply(providerId, snapshot.revision, next, value, identity),
      );
      if (!alive(generation)) return;
      if (result.provider_id !== providerId)
        throw { code: 'operation_uncertain' };
      setSnapshot(result);
      setPending(null);
      session.resolved();
      setNotice(
        next === 'save'
          ? 'API key saved.'
          : next === 'clear'
            ? 'API key cleared.'
            : 'Previous local credential restored.',
      );
      if (generation === epoch.current) onSaved(result);
    } catch (cause) {
      setError(clientError(cause).message);
      setNotice(
        'The outcome is uncertain. Read the original receipt before another change.',
      );
    } finally {
      effectPending.current = false;
      setBusy('');
    }
  }
  if (props.compact)
    return (
      <section
        className="stack settings-provider-key-dialog"
        aria-label="Provider credential settings"
        aria-busy={!!busy}
      >
        <h2>{snapshot?.display_name ?? 'Provider'} API key</h2>
        {busy === 'load' && <Skeleton label="Loading API key status" />}
        {error && <p role="alert">{error}</p>}
        {notice && <p role="status">{notice}</p>}
        {snapshot && (
          <>
            <p>
              {snapshot.configured ? 'Connected' : 'Not connected'} ·{' '}
              {snapshot.source === 'keyring'
                ? 'Saved in keyring'
                : snapshot.source || 'No saved key'}
            </p>
            {snapshot.externally_managed ? (
              <p>
                This key is managed by the environment or server secret file.
              </p>
            ) : (
              <>
                <Field label="API key">
                  <Input
                    type="password"
                    autoComplete="new-password"
                    value={secret}
                    maxLength={16384}
                    disabled={locked}
                    onChange={(event) => setSecret(event.target.value)}
                  />
                </Field>
                <div className="actions">
                  <Button
                    disabled={locked || !secret.trim()}
                    onClick={() => void performDirect('save')}
                  >
                    {snapshot.configured ? 'Replace key' : 'Save key'}
                  </Button>
                  {snapshot.configured && (
                    <Button
                      disabled={locked}
                      onClick={() => void performDirect('clear')}
                    >
                      Clear key
                    </Button>
                  )}
                </div>
              </>
            )}
          </>
        )}
        <div className="actions">
          {pending && (
            <Button disabled={!!busy} onClick={() => void checkReceipt()}>
              Read original receipt
            </Button>
          )}
          <Button disabled={!!busy || !!pending} onClick={onCancel}>
            Close
          </Button>
        </div>
      </section>
    );
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
          <div className="actions">
            <Button
              disabled={
                locked ||
                (operation === 'save' && !secret.trim()) ||
                (operation === 'restore' && !snapshot.recovery_available)
              }
              onClick={() => void performDirect(operation)}
            >
              {operation === 'save'
                ? 'Save key'
                : operation === 'clear'
                  ? 'Disconnect key'
                  : 'Restore key'}
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
            onCancel();
          }}
        >
          Cancel
        </Button>
      </div>
    </section>
  );
}
