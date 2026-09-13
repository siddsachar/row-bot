import { useEffect, useRef, useSyncExternalStore } from 'react';
import type {
  ProviderConfigurationPage,
  ProviderEndpointFields,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, Field, Input, Select, Skeleton } from '../../ui/primitives';

type Operation =
  | 'provider.endpoint.create'
  | 'provider.endpoint.save'
  | 'provider.endpoint.delete'
  | 'provider.endpoint.probe'
  | 'provider.endpoint.refresh'
  | 'provider.model.pin'
  | 'provider.model.unpin';
type Fields =
  | ProviderEndpointFields
  | { endpoint_id: string }
  | { provider_id: string; model_id: string; surface: string };
type Review = {
  configuration_revision: string;
  operation: string;
  action_digest: string;
  nonce?: string;
};
type Pending = {
  commandId: string;
  operation: Operation;
  revision: string;
  fields: Fields;
  review: Review;
};
const blank = (): ProviderEndpointFields => ({
  endpoint_id: '',
  display_name: '',
  base_url: '',
  profile: 'generic_openai',
  execution_location: 'local',
  enabled: true,
  auth_required: false,
  vision_mode: 'auto',
  tool_mode: 'auto',
  context_window: null,
  reasoning_mode: 'auto',
  thinking_budget: null,
  supports_reasoning_content: false,
  supports_reasoning_replay: false,
  extra_body_json: '{}',
});
type State = {
  page: ProviderConfigurationPage | null;
  query: string;
  fields: ProviderEndpointFields;
  revision: string;
  existing: boolean;
  editing: boolean;
  dirty: boolean;
  operation: Operation;
  provider: string;
  model: string;
  surface: string;
  busy: string;
  error: string;
  notice: string;
  reviewed: Review | null;
  pending: Pending | null;
  active: boolean;
};
const initial = (): State => ({
  page: null,
  query: '',
  fields: blank(),
  revision: '',
  existing: false,
  editing: false,
  dirty: false,
  operation: 'provider.endpoint.save',
  provider: '',
  model: '',
  surface: 'chat',
  busy: '',
  error: '',
  notice: '',
  reviewed: null,
  pending: null,
  active: true,
});

/** One bounded private configuration editor, retained by the authenticated runtime. */
export class ProviderConfigurationSession {
  private state = initial();
  private listeners = new Set<() => void>();
  private reads = new Set<AbortController>();
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  update(patch: Partial<State>) {
    if (!this.state.active) return;
    const candidate = { ...this.state, ...patch };
    if (JSON.stringify(candidate).length > 512 * 1024) {
      this.state = {
        ...this.state,
        error: 'This configuration exceeds the supported editor size.',
      };
    } else this.state = candidate;
    this.listeners.forEach((listener) => listener());
  }
  read() {
    const abort = new AbortController();
    this.reads.add(abort);
    return abort;
  }
  finishRead(abort: AbortController) {
    this.reads.delete(abort);
  }
  hasRetained() {
    return !!(this.state.dirty || this.state.pending || this.state.busy);
  }
  dispose() {
    this.reads.forEach((read) => read.abort());
    this.reads.clear();
    this.state = { ...initial(), active: false };
    this.listeners.forEach((listener) => listener());
  }
}

export type ProviderConfigurationProps = {
  session?: ProviderConfigurationSession;
  load: (
    query: string,
    cursor?: string,
    signal?: AbortSignal,
  ) => Promise<ProviderConfigurationPage>;
  review: (
    operation: Operation,
    revision: string,
    fields: Fields,
    signal?: AbortSignal,
  ) => Promise<Review>;
  apply: (
    operation: Operation,
    revision: string,
    fields: Fields,
    commandId: string,
    review: Review,
  ) => Promise<{ configuration_revision: string }>;
  receipt: (
    commandId: string,
    signal?: AbortSignal,
  ) => Promise<'completed' | 'rejected' | 'uncertain'>;
  onSaved: () => void;
  onCredentials: () => void;
};

export default function ProviderConfiguration(
  props: ProviderConfigurationProps,
) {
  const local = useRef<ProviderConfigurationSession | null>(null);
  if (!local.current) local.current = new ProviderConfigurationSession();
  const session = props.session ?? local.current;
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const epoch = useRef(0);
  const { page, fields, pending, operation } = state;
  const locked = !!state.busy || !!pending || !state.active;
  const modelAction = operation.startsWith('provider.model.');
  const networkAction =
    operation === 'provider.endpoint.probe' ||
    operation === 'provider.endpoint.refresh';
  const payload = (): Fields =>
    modelAction
      ? {
          provider_id: state.provider,
          model_id: state.model,
          surface: state.surface,
        }
      : operation === 'provider.endpoint.save' ||
          operation === 'provider.endpoint.create'
        ? structuredClone(fields)
        : { endpoint_id: fields.endpoint_id };

  async function load(cursor?: string) {
    if (
      !session.getSnapshot().active ||
      session.getSnapshot().busy ||
      session.getSnapshot().pending
    )
      return;
    const abort = session.read();
    session.update({ busy: 'load', error: '' });
    try {
      const result = await props.load(
        session.getSnapshot().query,
        cursor,
        abort.signal,
      );
      if (abort.signal.aborted || !session.getSnapshot().active) return;
      if (cursor && result.revision !== session.getSnapshot().page?.revision)
        throw { code: 'cursor_expired' };
      session.update({ page: result });
    } catch (cause) {
      if (!abort.signal.aborted)
        session.update({ error: clientError(cause).message });
    } finally {
      session.finishRead(abort);
      session.update({ busy: '' });
    }
  }
  useEffect(() => {
    epoch.current += 1;
    if (!session.getSnapshot().page && !session.getSnapshot().busy) void load();
    return () => {
      epoch.current += 1;
      if (!props.session) session.dispose();
    };
    // The retained owner survives presentation remounts; callback changes never reload drafts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session]);

  function changed(patch: Partial<State>) {
    if (!locked)
      session.update({
        ...patch,
        dirty: true,
        reviewed: null,
        error: '',
        notice: '',
      });
  }
  function field<K extends keyof ProviderEndpointFields>(
    name: K,
    value: ProviderEndpointFields[K],
  ) {
    changed({ fields: { ...fields, [name]: value } });
  }
  async function review() {
    if (locked || !page) return;
    const abort = session.read(),
      captured = payload(),
      revision = state.revision || page.revision;
    session.update({ busy: 'review', reviewed: null, error: '' });
    try {
      const result = await props.review(
        operation,
        revision,
        captured,
        abort.signal,
      );
      if (abort.signal.aborted || !session.getSnapshot().active) return;
      if (
        result.operation !== operation ||
        result.configuration_revision !== revision
      )
        throw { code: 'revision_conflict' };
      session.update({ reviewed: result });
    } catch (cause) {
      if (!abort.signal.aborted)
        session.update({ error: clientError(cause).message });
    } finally {
      session.finishRead(abort);
      session.update({ busy: '' });
    }
  }
  async function confirm() {
    const current = session.getSnapshot();
    if (current.busy || current.pending || !current.reviewed || !current.active)
      return;
    const original: Pending = {
      commandId: crypto.randomUUID(),
      operation,
      revision: current.reviewed.configuration_revision,
      fields: payload(),
      review: structuredClone(current.reviewed),
    };
    const generation = epoch.current;
    session.update({
      pending: original,
      busy: 'apply',
      reviewed: null,
      error: '',
    });
    try {
      await props.apply(
        original.operation,
        original.revision,
        structuredClone(original.fields),
        original.commandId,
        structuredClone(original.review),
      );
      if (!session.getSnapshot().active) return;
      session.update({
        pending: null,
        dirty: false,
        editing: false,
        notice: networkAction
          ? 'Endpoint check completed. Reload saved status for its result.'
          : 'Configuration saved. No model was started.',
        page: null,
      });
      if (generation === epoch.current) props.onSaved();
    } catch (cause) {
      session.update({
        error: clientError(cause).message,
        notice:
          'The original outcome is unconfirmed. Check its receipt; it will not be sent again.',
      });
    } finally {
      session.update({ busy: '' });
    }
  }
  async function receipt() {
    if (!pending || state.busy) return;
    const abort = session.read();
    session.update({ busy: 'receipt', error: '' });
    try {
      const outcome = await props.receipt(pending.commandId, abort.signal);
      if (abort.signal.aborted) return;
      if (outcome === 'uncertain')
        session.update({
          notice:
            'The original outcome is still unconfirmed. No request was replayed.',
        });
      else
        session.update({
          pending: null,
          reviewed: null,
          dirty: outcome === 'rejected',
          notice:
            outcome === 'completed'
              ? 'The original change is confirmed. Reload saved status.'
              : 'The original change was rejected. Reload saved status before a fresh review.',
        });
    } catch (cause) {
      if (!abort.signal.aborted)
        session.update({ error: clientError(cause).message });
    } finally {
      session.finishRead(abort);
      session.update({ busy: '' });
    }
  }
  return (
    <section
      className="stack"
      aria-label="Provider configuration"
      aria-busy={!!state.busy}
    >
      <h2>Endpoints and model pickers</h2>
      <p>
        These settings are global. Saving does not start a model or change a
        conversation profile. Readiness shown here is saved evidence.
      </p>
      {state.error && <p role="alert">{state.error}</p>}
      {state.notice && <p role="status">{state.notice}</p>}
      {state.busy === 'load' && <Skeleton label="Loading saved endpoints" />}
      <div className="field-row">
        <Field label="Search saved endpoints">
          <Input
            value={state.query}
            maxLength={256}
            disabled={locked}
            onChange={(event) => session.update({ query: event.target.value })}
          />
        </Field>
        <Button disabled={locked} onClick={() => void load()}>
          Search
        </Button>
      </div>
      {page && (
        <>
          <p>
            {page.total} saved endpoints · showing {page.items.length} on this
            page
          </p>
          <ul className="stack">
            {page.items.map((item) => (
              <li key={item.provider_id}>
                <div className="actions">
                  <span>
                    {item.fields.display_name} ·{' '}
                    {item.fields.enabled ? 'Enabled' : 'Disabled'} · Saved
                    probe: {item.probe_state} · Models:{' '}
                    {item.model_count ?? 'unknown'}
                  </span>
                  <Button
                    disabled={locked || state.dirty}
                    onClick={() =>
                      session.update({
                        fields: structuredClone(item.fields),
                        revision: page.revision,
                        existing: true,
                        editing: true,
                        operation: 'provider.endpoint.save',
                        reviewed: null,
                      })
                    }
                  >
                    Edit {item.fields.display_name}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
          {page.next_cursor && (
            <Button
              disabled={locked}
              onClick={() => void load(page.next_cursor!)}
            >
              Next endpoint page
            </Button>
          )}
        </>
      )}
      <div className="actions">
        <Button
          disabled={locked || state.dirty || !page}
          onClick={() =>
            session.update({
              fields: blank(),
              revision: page!.revision,
              existing: false,
              editing: true,
              operation: 'provider.endpoint.create',
              reviewed: null,
            })
          }
        >
          New endpoint
        </Button>
        <Button
          disabled={locked || state.dirty || !page}
          onClick={() =>
            session.update({
              revision: page!.revision,
              editing: true,
              operation: 'provider.model.pin',
              reviewed: null,
            })
          }
        >
          Model picker settings
        </Button>
        <Button onClick={props.onCredentials}>Manage credentials</Button>
      </div>
      {state.editing && (
        <>
          <Field label="Configuration action">
            <Select
              disabled={locked}
              value={operation}
              onChange={(event) =>
                changed({ operation: event.target.value as Operation })
              }
            >
              {modelAction ? (
                <>
                  <option value="provider.model.pin">
                    Pin model to picker
                  </option>
                  <option value="provider.model.unpin">
                    Remove model from picker
                  </option>
                </>
              ) : (
                <>
                  <option
                    value={
                      state.existing
                        ? 'provider.endpoint.save'
                        : 'provider.endpoint.create'
                    }
                  >
                    {state.existing ? 'Save endpoint' : 'Create endpoint'}
                  </option>
                  <option
                    value="provider.endpoint.delete"
                    disabled={!state.existing}
                  >
                    Remove endpoint
                  </option>
                  <option
                    value="provider.endpoint.refresh"
                    disabled={!state.existing}
                  >
                    Refresh endpoint models
                  </option>
                  <option
                    value="provider.endpoint.probe"
                    disabled={!state.existing}
                  >
                    Probe endpoint readiness
                  </option>
                </>
              )}
            </Select>
          </Field>
          {modelAction ? (
            <>
              <Field label="Provider ID">
                <Input
                  disabled={locked}
                  maxLength={128}
                  value={state.provider}
                  onChange={(event) =>
                    changed({ provider: event.target.value })
                  }
                />
              </Field>
              <Field label="Exact model ID">
                <Input
                  disabled={locked}
                  maxLength={512}
                  value={state.model}
                  onChange={(event) => changed({ model: event.target.value })}
                />
              </Field>
              <Field label="Picker">
                <Select
                  disabled={locked}
                  value={state.surface}
                  onChange={(event) => changed({ surface: event.target.value })}
                >
                  {['chat', 'vision', 'image', 'video', 'voice'].map(
                    (surface) => (
                      <option key={surface}>{surface}</option>
                    ),
                  )}
                </Select>
              </Field>
              <p>
                Provider identity is preserved. Pinning does not select a
                default or prove runtime readiness.
              </p>
            </>
          ) : (
            <>
              <Field label="Endpoint ID">
                <Input
                  value={fields.endpoint_id}
                  disabled={locked || state.existing}
                  maxLength={64}
                  onChange={(event) => field('endpoint_id', event.target.value)}
                />
              </Field>
              <Field label="Display name">
                <Input
                  value={fields.display_name}
                  disabled={
                    locked ||
                    (operation !== 'provider.endpoint.save' &&
                      operation !== 'provider.endpoint.create')
                  }
                  maxLength={160}
                  onChange={(event) =>
                    field('display_name', event.target.value)
                  }
                />
              </Field>
              <Field label="Base URL">
                <Input
                  value={fields.base_url}
                  disabled={
                    locked ||
                    (operation !== 'provider.endpoint.save' &&
                      operation !== 'provider.endpoint.create')
                  }
                  maxLength={2048}
                  onChange={(event) => field('base_url', event.target.value)}
                />
              </Field>
              {(operation === 'provider.endpoint.save' ||
                operation === 'provider.endpoint.create') && (
                <>
                  <div className="field-row">
                    <Field label="Endpoint profile">
                      <Select
                        disabled={locked || state.existing}
                        value={fields.profile}
                        onChange={(event) =>
                          field('profile', event.target.value)
                        }
                      >
                        {page?.profiles.map((profile) => (
                          <option key={profile}>{profile}</option>
                        ))}
                      </Select>
                    </Field>
                    <Field label="Execution location">
                      <Select
                        disabled={locked || state.existing}
                        value={fields.execution_location}
                        onChange={(event) =>
                          field(
                            'execution_location',
                            event.target
                              .value as ProviderEndpointFields['execution_location'],
                          )
                        }
                      >
                        <option value="local">Local/private</option>
                        <option value="remote">Remote/proxy</option>
                      </Select>
                    </Field>
                  </div>
                  <label className="actions">
                    <input
                      type="checkbox"
                      checked={fields.enabled}
                      disabled={locked}
                      onChange={(event) =>
                        field('enabled', event.target.checked)
                      }
                    />
                    Enable this endpoint
                  </label>
                  <label className="actions">
                    <input
                      type="checkbox"
                      checked={fields.auth_required}
                      disabled={locked}
                      onChange={(event) =>
                        field('auth_required', event.target.checked)
                      }
                    />
                    Require an existing saved credential
                  </label>
                  <p>
                    Credential bytes are managed separately. This form never
                    replaces or deletes them.
                  </p>
                  <details>
                    <summary>Advanced capabilities and reasoning</summary>
                    <div className="stack">
                      {(
                        ['vision_mode', 'tool_mode', 'reasoning_mode'] as const
                      ).map((key) => (
                        <Field
                          key={key}
                          label={
                            {
                              vision_mode: 'Vision input',
                              tool_mode: 'Tool calling',
                              reasoning_mode: 'Reasoning mode',
                            }[key]
                          }
                        >
                          <Select
                            disabled={locked}
                            value={fields[key]}
                            onChange={(event) =>
                              field(
                                key,
                                event.target
                                  .value as ProviderEndpointFields[typeof key],
                              )
                            }
                          >
                            {['auto', 'on', 'off'].map((mode) => (
                              <option key={mode}>{mode}</option>
                            ))}
                          </Select>
                        </Field>
                      ))}
                      {(['context_window', 'thinking_budget'] as const).map(
                        (key) => (
                          <Field
                            key={key}
                            label={
                              key === 'context_window'
                                ? 'Native context limit'
                                : 'Thinking budget'
                            }
                          >
                            <Input
                              type="number"
                              min={1}
                              max={10000000}
                              disabled={locked}
                              value={fields[key] ?? ''}
                              onChange={(event) =>
                                field(
                                  key,
                                  event.target.value === ''
                                    ? null
                                    : Number(event.target.value),
                                )
                              }
                            />
                          </Field>
                        ),
                      )}
                      {(
                        [
                          'supports_reasoning_content',
                          'supports_reasoning_replay',
                        ] as const
                      ).map((key) => (
                        <label className="actions" key={key}>
                          <input
                            type="checkbox"
                            checked={fields[key]}
                            disabled={locked}
                            onChange={(event) =>
                              field(key, event.target.checked)
                            }
                          />
                          {key === 'supports_reasoning_content'
                            ? 'Endpoint returns reasoning content'
                            : 'Replay preserved reasoning'}
                        </label>
                      ))}
                      <Field label="Credential-free extra request JSON">
                        <textarea
                          className="input"
                          value={fields.extra_body_json}
                          maxLength={16384}
                          disabled={locked}
                          onChange={(event) =>
                            field('extra_body_json', event.target.value)
                          }
                        />
                      </Field>
                    </div>
                  </details>
                </>
              )}
              {operation === 'provider.endpoint.delete' && (
                <p>
                  Removes this endpoint and its exact model-picker pins.
                  Credential recovery bytes and global model selections are
                  retained; unavailable selections require an explicit new
                  choice.
                </p>
              )}
              {networkAction && (
                <p>
                  This explicit action contacts the saved endpoint and may
                  consume provider quota. It sends synthetic probe content,
                  never conversation history. Changing this form does not probe
                  automatically.
                </p>
              )}
            </>
          )}
          {state.reviewed && (
            <p role="status">
              Review complete. Confirm the displayed action and target.
            </p>
          )}
          <div className="actions">
            <Button disabled={locked} onClick={() => void review()}>
              Review configuration
            </Button>
            <Button
              variant="primary"
              disabled={locked || !state.reviewed}
              onClick={() => void confirm()}
            >
              Confirm configuration
            </Button>
            <Button
              disabled={locked}
              onClick={() =>
                session.update({
                  editing: false,
                  dirty: false,
                  reviewed: null,
                  fields: blank(),
                })
              }
            >
              Discard unsent changes
            </Button>
          </div>
        </>
      )}
      {pending && (
        <Button disabled={!!state.busy} onClick={() => void receipt()}>
          Check original configuration receipt
        </Button>
      )}
      <Button disabled={locked || state.dirty} onClick={() => void load()}>
        Reload saved configuration
      </Button>
    </section>
  );
}
