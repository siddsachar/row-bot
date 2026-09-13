import { useEffect, useSyncExternalStore } from 'react';
import { Button, Field, Input, Select } from '../../ui/primitives';

export type PluginCapability = { available: boolean; code: string | null };
export type PluginCatalogItem = {
  plugin_id: string;
  name: string;
  version: string;
  description: string;
  source: 'installed' | 'marketplace';
  installed: boolean;
  enabled: boolean;
  setup_complete: boolean;
  health: string;
  update_version: string | null;
  permissions: string[];
  provides: Record<string, number>;
  manifest_revision: string | null;
  capabilities: Record<string, PluginCapability>;
};
export type PluginCatalogPage = {
  schema_version: 1;
  revision: string;
  availability: string;
  items: PluginCatalogItem[];
  total: number;
  next_cursor: string | null;
};
export type PluginField = {
  name: string;
  label: string;
  type: string;
  required: boolean;
  options: string[];
  configured: boolean;
  value?: unknown;
  minimum?: number | null;
  maximum?: number | null;
};
export type PluginDetail = {
  schema_version: 1;
  plugin_id: string;
  revision: string;
  name: string;
  version: string;
  description: string;
  enabled: boolean;
  settings: PluginField[];
  secrets: PluginField[];
  health: { status: string; checks: { label: string; status: string }[] };
  permissions: string[];
  capabilities: Record<string, PluginCapability>;
};
export type PluginAction =
  | 'plugin.enable'
  | 'plugin.disable'
  | 'plugin.configure'
  | 'plugin.install'
  | 'plugin.update'
  | 'plugin.remove';
export type PluginReview = {
  schema_version: 1;
  plugin_id: string;
  action: PluginAction;
  revision: string;
  action_digest: string;
  changes: Record<string, unknown>;
  disclosures: string[];
  review_id: string;
};
export type PluginCommand = {
  command_id: string;
  type: PluginAction;
  payload: Record<string, unknown> & {
    plugin_id: string;
    revision: string;
    action_digest: string;
  };
};
export type PluginReceipt = {
  command_id: string;
  status: string;
  code?: string | null;
  plugin?: {
    plugin_id: string;
    action: PluginAction;
    enabled?: boolean | null;
    revision?: string | null;
  } | null;
};

type Attempt = { command: PluginCommand; review: PluginReview };
type State = {
  active: boolean;
  page: PluginCatalogPage | null;
  selected: PluginDetail | null;
  query: string;
  source: 'all' | 'installed' | 'marketplace';
  settings: Record<string, unknown>;
  secrets: Record<string, string | null | undefined>;
  reviewed: Attempt | null;
  pending: Attempt | null;
  busy: string;
  message: string;
};

export function createPluginSettingsSession() {
  let state: State = {
    active: true,
    page: null,
    selected: null,
    query: '',
    source: 'all',
    settings: {},
    secrets: {},
    reviewed: null,
    pending: null,
    busy: '',
    message: '',
  };
  const listeners = new Set<() => void>();
  const reads = new Set<AbortController>();
  const update = (patch: Partial<State>) => {
    if (!state.active) return;
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  };
  return {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    update,
    beginRead: () => {
      const controller = new AbortController();
      if (state.active) reads.add(controller);
      else controller.abort();
      return controller;
    },
    endRead: (controller: AbortController) => reads.delete(controller),
    hasRetained: () =>
      state.active &&
      Boolean(
        state.pending ||
        state.reviewed ||
        Object.keys(state.settings).length ||
        Object.keys(state.secrets).length,
      ),
    dispose: () => {
      reads.forEach((controller) => controller.abort());
      reads.clear();
      state = {
        active: false,
        page: null,
        selected: null,
        query: '',
        source: 'all',
        settings: {},
        secrets: {},
        reviewed: null,
        pending: null,
        busy: '',
        message: 'Sign in again to manage plugins.',
      };
      listeners.forEach((listener) => listener());
    },
  };
}
export type PluginSettingsSession = ReturnType<
  typeof createPluginSettingsSession
>;
export type PluginSettingsProps = {
  session: PluginSettingsSession;
  load: (
    query: {
      query: string;
      source: 'all' | 'installed' | 'marketplace';
      cursor?: string;
    },
    signal: AbortSignal,
  ) => Promise<PluginCatalogPage>;
  open: (pluginId: string, signal: AbortSignal) => Promise<PluginDetail>;
  review: (
    action: PluginAction,
    payload: Record<string, unknown>,
    signal: AbortSignal,
  ) => Promise<PluginReview>;
  execute: (
    command: PluginCommand,
    review: PluginReview,
  ) => Promise<PluginReceipt>;
};

function boundedPage(value: PluginCatalogPage) {
  if (value.schema_version !== 1 || value.items.length > 50) throw Error();
  return value;
}

function initialSettings(detail: PluginDetail) {
  return Object.fromEntries(
    detail.settings
      .filter((field) => field.value !== null && field.value !== undefined)
      .map((field) => [field.name, field.value]),
  );
}

export default function PluginSettings({
  session,
  load,
  open,
  review,
  execute,
}: PluginSettingsProps) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const locked = !state.active || Boolean(state.busy || state.pending);

  const refresh = async (cursor?: string) => {
    if (locked) return;
    const abort = session.beginRead();
    session.update({ busy: 'load', reviewed: null, message: '' });
    try {
      const page = boundedPage(
        await load(
          { query: state.query.trim(), source: state.source, cursor },
          abort.signal,
        ),
      );
      if (!abort.signal.aborted)
        session.update({
          page,
          busy: '',
          selected: cursor ? state.selected : null,
        });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message:
            'Saved plugin information changed or is unavailable. Return to the first page.',
        });
    } finally {
      session.endRead(abort);
    }
  };

  useEffect(() => {
    const current = session.getSnapshot();
    if (!current.active || current.page || current.busy) return;
    const abort = session.beginRead();
    session.update({ busy: 'load' });
    void load({ query: current.query, source: current.source }, abort.signal)
      .then((page) => {
        if (!abort.signal.aborted)
          session.update({ page: boundedPage(page), busy: '' });
      })
      .catch(() => {
        if (!abort.signal.aborted)
          session.update({
            busy: '',
            message:
              'Saved plugin information is unavailable. Refresh to try again.',
          });
      })
      .finally(() => session.endRead(abort));
  }, [load, session]);

  const select = async (pluginId: string) => {
    if (locked) return;
    const abort = session.beginRead();
    session.update({ busy: 'detail', reviewed: null, message: '' });
    try {
      const detail = await open(pluginId, abort.signal);
      if (
        detail.schema_version !== 1 ||
        detail.plugin_id !== pluginId ||
        detail.settings.length + detail.secrets.length > 128
      )
        throw Error();
      if (!abort.signal.aborted)
        session.update({
          selected: detail,
          settings: initialSettings(detail),
          secrets: {},
          busy: '',
        });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message: 'Plugin details are unavailable.',
        });
    } finally {
      session.endRead(abort);
    }
  };

  const requestReview = async (action: PluginAction) => {
    const current = session.getSnapshot();
    const detail = current.selected;
    const capability = detail?.capabilities[action.replace('plugin.', '')];
    if (
      !detail ||
      !current.active ||
      current.busy ||
      current.pending ||
      !capability?.available
    )
      return;
    const payload: Record<string, unknown> = {
      plugin_id: detail.plugin_id,
      revision: detail.revision,
      ...(action === 'plugin.configure'
        ? {
            settings: current.settings,
            secrets: Object.fromEntries(
              Object.entries(current.secrets).filter(
                ([, value]) => value !== undefined,
              ),
            ),
          }
        : {}),
    };
    if (new TextEncoder().encode(JSON.stringify(payload)).length > 128 * 1024)
      return session.update({
        message: 'Plugin settings are too large to review.',
      });
    const abort = session.beginRead();
    session.update({ busy: 'review', reviewed: null, message: '' });
    try {
      const result = await review(action, payload, abort.signal);
      if (
        result.plugin_id !== detail.plugin_id ||
        result.action !== action ||
        result.revision !== detail.revision
      )
        throw Error();
      const command: PluginCommand = {
        command_id: crypto.randomUUID(),
        type: action,
        payload: {
          ...payload,
          plugin_id: detail.plugin_id,
          revision: detail.revision,
          action_digest: result.action_digest,
        },
      };
      if (!abort.signal.aborted)
        session.update({
          reviewed: { command, review: result },
          busy: '',
          message:
            'Review complete. Apply this exact plugin change to continue.',
        });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message:
            'The plugin change could not be reviewed. Refresh and try again.',
        });
    } finally {
      session.endRead(abort);
    }
  };

  const apply = async (attempt: Attempt | null) => {
    if (!attempt || !state.active || state.busy) return;
    session.update({
      busy: 'apply',
      reviewed: null,
      pending: attempt,
      message: '',
    });
    try {
      const receipt = await execute(attempt.command, attempt.review);
      if (receipt.command_id !== attempt.command.command_id) throw Error();
      if (receipt.status === 'completed') {
        session.update({
          busy: '',
          pending: null,
          selected: null,
          page: null,
          settings: {},
          secrets: {},
          message: 'Plugin change completed. Refresh to view the saved state.',
        });
      } else if (receipt.status === 'rejected') {
        session.update({
          busy: '',
          pending: null,
          reviewed: null,
          message:
            'The plugin change was rejected. Refresh and review it again.',
        });
      } else {
        session.update({
          busy: '',
          message:
            'The original plugin change is unconfirmed. Check it before making another change.',
        });
      }
    } catch {
      session.update({
        busy: '',
        message:
          'The original plugin change is unconfirmed. Check it before making another change.',
      });
    }
  };

  const editSetting = (name: string, value: unknown) =>
    session.update({
      settings: { ...state.settings, [name]: value },
      reviewed: null,
      message: '',
    });
  const editSecret = (name: string, value: string | null | undefined) =>
    session.update({
      secrets: { ...state.secrets, [name]: value },
      reviewed: null,
      message: '',
    });

  return (
    <section aria-label="Plugin Center" className="settings-section">
      <h2>Plugin Center</h2>
      <p>
        Browse installed plugins and the saved marketplace catalog. Marketplace
        reads stay offline; unavailable lifecycle actions explain their safety
        gate.
      </p>
      <div className="field-row">
        <Field label="Search plugins">
          <Input
            type="search"
            maxLength={256}
            value={state.query}
            disabled={locked}
            onChange={(event) => session.update({ query: event.target.value })}
          />
        </Field>
        <Field label="Plugin source">
          <Select
            value={state.source}
            disabled={locked}
            onChange={(event) =>
              session.update({
                source: event.target.value as State['source'],
                reviewed: null,
              })
            }
          >
            <option value="all">All saved plugins</option>
            <option value="installed">Installed</option>
            <option value="marketplace">Saved marketplace</option>
          </Select>
        </Field>
        <Button disabled={locked} onClick={() => void refresh()}>
          Search
        </Button>
      </div>
      {state.page && (
        <>
          <p role="status">{state.page.total} matching plugins.</p>
          <ul className="settings-results">
            {state.page.items.map((plugin) => (
              <li className="surface" key={plugin.plugin_id}>
                <strong>{plugin.name}</strong> v{plugin.version} —{' '}
                {plugin.installed
                  ? plugin.enabled
                    ? 'Enabled'
                    : 'Disabled'
                  : 'Saved marketplace entry'}
                <p>{plugin.description}</p>
                <p>
                  Tools {plugin.provides.native_tools ?? 0}; MCP servers{' '}
                  {plugin.provides.mcp_servers ?? 0}; channels{' '}
                  {plugin.provides.channels ?? 0}; skills{' '}
                  {plugin.provides.skills ?? 0}.
                </p>
                {plugin.installed ? (
                  <Button
                    disabled={locked}
                    onClick={() => void select(plugin.plugin_id)}
                  >
                    Manage {plugin.name}
                  </Button>
                ) : (
                  <p role="status">
                    Install unavailable: a recoverable plugin lifecycle worker
                    is not available yet.
                  </p>
                )}
              </li>
            ))}
          </ul>
          <div className="button-row">
            <Button disabled={locked} onClick={() => void refresh()}>
              First page
            </Button>
            <Button
              disabled={locked || !state.page.next_cursor}
              onClick={() => void refresh(state.page?.next_cursor ?? undefined)}
            >
              Next page
            </Button>
          </div>
        </>
      )}
      {state.selected && (
        <section aria-label={`Manage ${state.selected.name}`}>
          <h3>{state.selected.name}</h3>
          <p>
            Health: {state.selected.health.status}. Permissions:{' '}
            {state.selected.permissions.join(', ') || 'none'}.
          </p>
          {state.selected.health.checks.length > 0 && (
            <ul>
              {state.selected.health.checks.map((check) => (
                <li key={`${check.label}:${check.status}`}>
                  {check.label}: {check.status}
                </li>
              ))}
            </ul>
          )}
          <fieldset
            disabled={
              locked || !state.selected.capabilities.configure?.available
            }
          >
            <legend>Saved configuration</legend>
            {state.selected.settings.map((field) => (
              <Field
                key={field.name}
                label={field.label}
                hint={
                  field.value === null && field.configured
                    ? 'Configured; enter a replacement to change it.'
                    : undefined
                }
              >
                {field.type === 'checkbox' ? (
                  <Input
                    type="checkbox"
                    checked={Boolean(state.settings[field.name])}
                    onChange={(event) =>
                      editSetting(field.name, event.target.checked)
                    }
                  />
                ) : field.type === 'select' ? (
                  <Select
                    value={String(state.settings[field.name] ?? '')}
                    onChange={(event) =>
                      editSetting(field.name, event.target.value)
                    }
                  >
                    <option value="">Select</option>
                    {field.options.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </Select>
                ) : field.type === 'textarea' ? (
                  <textarea
                    value={String(state.settings[field.name] ?? '')}
                    maxLength={65536}
                    autoComplete="off"
                    onChange={(event) =>
                      editSetting(field.name, event.target.value)
                    }
                  />
                ) : (
                  <Input
                    type={
                      field.type === 'number'
                        ? 'number'
                        : ['password', 'secret'].includes(field.type)
                          ? 'password'
                          : 'text'
                    }
                    value={String(state.settings[field.name] ?? '')}
                    min={field.minimum ?? undefined}
                    max={field.maximum ?? undefined}
                    maxLength={field.type === 'number' ? undefined : 65536}
                    autoComplete="off"
                    onChange={(event) =>
                      editSetting(
                        field.name,
                        field.type === 'number'
                          ? Number(event.target.value)
                          : field.type === 'multi-select'
                            ? event.target.value
                                .split(',')
                                .map((value) => value.trim())
                                .filter(Boolean)
                            : event.target.value,
                      )
                    }
                  />
                )}
              </Field>
            ))}
            {state.selected.secrets.map((field) => (
              <div key={field.name}>
                <Field
                  label={field.label}
                  hint={
                    field.configured
                      ? 'Configured; the saved value is never displayed.'
                      : 'Not configured.'
                  }
                >
                  <Input
                    type="password"
                    value={state.secrets[field.name] ?? ''}
                    maxLength={16384}
                    autoComplete="new-password"
                    onChange={(event) =>
                      editSecret(field.name, event.target.value || undefined)
                    }
                  />
                </Field>
                {field.configured && (
                  <Button onClick={() => editSecret(field.name, null)}>
                    Clear {field.label}
                  </Button>
                )}
              </div>
            ))}
            <Button onClick={() => void requestReview('plugin.configure')}>
              Review configuration
            </Button>
          </fieldset>
          <div className="button-row">
            <Button
              disabled={
                locked || !state.selected.capabilities.enable?.available
              }
              onClick={() => void requestReview('plugin.enable')}
            >
              Enable plugin
            </Button>
            <Button
              variant="danger"
              disabled={
                locked || !state.selected.capabilities.disable?.available
              }
              onClick={() => void requestReview('plugin.disable')}
            >
              Disable plugin
            </Button>
          </div>
          {!state.selected.capabilities.remove?.available && (
            <p role="status">
              Remove and update are unavailable until a recoverable lifecycle
              worker owns those effects.
            </p>
          )}
        </section>
      )}
      {state.reviewed && (
        <section aria-label="Plugin change review">
          <h3>Review plugin change</h3>
          {state.reviewed.review.disclosures.map((item) => (
            <p key={item}>{item}</p>
          ))}
          <Button disabled={locked} onClick={() => void apply(state.reviewed)}>
            Apply plugin change
          </Button>
        </section>
      )}
      {state.pending && (
        <Button
          disabled={Boolean(state.busy) || !state.active}
          onClick={() => void apply(state.pending)}
        >
          Check original plugin change
        </Button>
      )}
      {state.message && <p role="status">{state.message}</p>}
    </section>
  );
}
