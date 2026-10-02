import { useEffect, useRef, useSyncExternalStore } from 'react';
import {
  FlaskConical,
  MoreHorizontal,
  Puzzle,
  RefreshCw,
  Search,
  Trash2,
  Upload,
} from 'lucide-react';
import {
  Button,
  Field,
  Input,
  Menu,
  Select,
  StatusDot,
  Toggle,
  type MenuAction,
  type Tone,
} from '../../ui/primitives';
import { clientError } from '../../api/errors';
import { humanizeToken } from '../../ui/format';
import PluginLifecycleActions, {
  PluginProvenance,
  usePluginLifecycle,
  type PluginLifecycleApi,
} from './PluginLifecycleActions';
import { SettingsSummary, SettingsTabs, SummaryChip } from './anatomy';
import { ConnectSheet, type ConnectStep } from './ConnectSheet';

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
  source_label?: string;
  checksum?: string;
  verified?: boolean;
  capabilities: Record<string, PluginCapability>;
};
export type PluginCatalogPage = {
  schema_version: 1;
  revision: string;
  availability: string;
  items: PluginCatalogItem[];
  total: number;
  next_cursor: string | null;
  /** Over every plugin, whatever the tab or search shows (B120). */
  installed_count?: number;
  attention_count?: number;
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
  /** The connect sheet (parity row 39): README steps, sign-ins, changelog. */
  guide?: string;
  sign_in?: { label: string; kind: string }[];
  changelog_url?: string | null;
};
export type PluginAction =
  | 'plugin.enable'
  | 'plugin.disable'
  | 'plugin.configure'
  | 'plugin.test'
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
    source: 'installed',
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
        source: 'installed',
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
  integrationId?: string;
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
  lifecycle?: PluginLifecycleApi;
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
  integrationId,
  session,
  load,
  open,
  review,
  execute,
  lifecycle,
}: PluginSettingsProps) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const locked = !state.active || Boolean(state.busy || state.pending);

  const refresh = async (
    cursor?: string,
    source: State['source'] = state.source,
  ) => {
    if (locked) return;
    const abort = session.beginRead();
    session.update({ busy: 'load', reviewed: null, message: '' });
    try {
      const page = boundedPage(
        await load(
          { query: session.getSnapshot().query.trim(), source, cursor },
          abort.signal,
        ),
      );
      if (!abort.signal.aborted)
        session.update({
          page,
          source,
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

  const select = async (pluginId: string, preserveMessage = false) => {
    if (locked) return null;
    const abort = session.beginRead();
    session.update({
      busy: 'detail',
      reviewed: null,
      message: preserveMessage ? state.message : '',
    });
    try {
      const detail = await open(pluginId, abort.signal);
      if (
        detail.schema_version !== 1 ||
        detail.plugin_id !== pluginId ||
        detail.settings.length + detail.secrets.length > 128
      )
        throw Error();
      if (!abort.signal.aborted) {
        session.update({
          selected: detail,
          settings: initialSettings(detail),
          secrets: {},
          busy: '',
        });
        return detail;
      }
      return null;
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message: 'Plugin details are unavailable.',
        });
      return null;
    } finally {
      session.endRead(abort);
    }
  };

  useEffect(() => {
    if (
      !integrationId ||
      state.busy ||
      state.pending ||
      !state.page ||
      state.selected?.plugin_id === integrationId
    )
      return;
    void select(integrationId, state.selected === null);
    // Selection is URL-owned; settings changes stay in the existing session.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [integrationId, state.page]);

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
        message: 'Plugin settings are too large to save.',
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
      if (!abort.signal.aborted) {
        const attempt = { command, review: result };
        session.update({
          reviewed: attempt,
          busy: '',
          message: '',
        });
        void apply(attempt);
      }
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message:
            'The plugin change could not be validated. Refresh and try again.',
        });
    } finally {
      session.endRead(abort);
    }
  };

  const selectForAction = async (
    pluginId: string,
    action: 'plugin.enable' | 'plugin.disable' | 'plugin.test',
  ) => {
    const detail = await select(pluginId);
    if (detail) await requestReview(action);
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
        const current = session.getSnapshot();
        session.update({
          busy: 'load',
          pending: null,
          selected: null,
          page: null,
          settings: {},
          secrets: {},
          message: 'Plugin change completed.',
        });
        const abort = session.beginRead();
        try {
          const page = boundedPage(
            await load(
              {
                query: current.query.trim(),
                source: current.source,
              },
              abort.signal,
            ),
          );
          if (!abort.signal.aborted)
            session.update({
              page,
              busy: '',
              message: 'Plugin change completed.',
            });
        } catch {
          if (!abort.signal.aborted)
            session.update({
              busy: '',
              message:
                'Plugin change completed. Refresh to view the saved state.',
            });
        } finally {
          session.endRead(abort);
        }
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

  const searchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  useEffect(() => () => clearTimeout(searchTimer.current), []);
  const tab = state.source === 'installed' ? 'installed' : 'discover';
  // Counted over every plugin by the server, not this tab's page (B120).
  const installedCount =
    state.page?.installed_count ??
    state.page?.items.filter((plugin) => plugin.installed).length ??
    0;
  const failedCount =
    state.page?.attention_count ??
    state.page?.items.filter((plugin) => UNHEALTHY.has(plugin.health)).length ??
    0;
  const toolbar = (
    <form
      className="settings-list-toolbar"
      role="search"
      aria-label="Search plugins"
      onSubmit={(event) => {
        event.preventDefault();
        clearTimeout(searchTimer.current);
        void refresh();
      }}
    >
      <label className="settings-inline-search">
        <span className="visually-hidden">Search plugins</span>
        <Search size={14} aria-hidden />
        <Input
          type="search"
          maxLength={256}
          placeholder="Search plugins"
          value={state.query}
          disabled={locked}
          onChange={(event) => {
            session.update({ query: event.target.value });
            clearTimeout(searchTimer.current);
            searchTimer.current = setTimeout(() => void refresh(), 350);
          }}
        />
      </label>
      {tab === 'discover' && (
        <Select
          aria-label="Plugin source"
          value={state.source}
          disabled={locked}
          onChange={(event) => {
            const source = event.target.value as State['source'];
            session.update({ source, reviewed: null });
            void refresh(undefined, source);
          }}
        >
          <option value="marketplace">Saved marketplace</option>
          <option value="all">All saved plugins</option>
        </Select>
      )}
      <Menu
        label="More plugin actions"
        iconOnly
        variant="ghost"
        className="icon-action icon-action-md"
        actions={[
          {
            label: 'Reload plugins',
            icon: <RefreshCw size={16} />,
            disabled: locked,
            onSelect: () => void refresh(),
          },
        ]}
      >
        <MoreHorizontal size={18} aria-hidden />
      </Menu>
    </form>
  );
  const list = state.page && (
    <>
      <p role="status" className="settings-list-count">
        {state.page.total} matching plugins.
      </p>
      <ul className="settings-results settings-plugin-list">
        {state.page.items.map((plugin) => (
          <PluginRow
            key={plugin.plugin_id}
            plugin={plugin}
            locked={locked}
            lifecycle={lifecycle}
            onManage={() => void select(plugin.plugin_id)}
            onAction={(action) =>
              void selectForAction(plugin.plugin_id, action)
            }
            // Prepare or uninstall on Installed stays on Installed (a row's
            // outcome message would vanish with the tab); an install from
            // Discover shows it beside the marketplace.
            onChanged={() =>
              void refresh(
                undefined,
                state.source === 'installed' ? 'installed' : 'all',
              )
            }
          />
        ))}
      </ul>
      {(state.page.next_cursor || state.source !== 'installed') && (
        <div className="button-row settings-plugin-pagination">
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
      )}
    </>
  );

  return (
    <section
      aria-label="Plugin Center"
      className="settings-section settings-plugins-page"
    >
      {!integrationId && (
        <>
          <SettingsSummary>
            <SummaryChip>{installedCount} installed</SummaryChip>
            {failedCount > 0 && (
              <SummaryChip tone="danger">
                {failedCount} need{failedCount === 1 ? 's' : ''} attention
              </SummaryChip>
            )}
          </SettingsSummary>
          <SettingsTabs
            label="Plugins"
            value={tab}
            onChange={(next) => {
              if (next === tab || locked) return;
              const source = next === 'installed' ? 'installed' : 'marketplace';
              session.update({ source, reviewed: null });
              void refresh(undefined, source);
            }}
            tabs={[
              {
                id: 'installed',
                label: 'Installed',
                content: (
                  <div
                    className="stack"
                    data-setting-anchor="installed-plugins"
                  >
                    {tab === 'installed' && toolbar}
                    {tab === 'installed' && list}
                  </div>
                ),
              },
              {
                id: 'discover',
                label: 'Discover',
                content: (
                  <div
                    className="stack"
                    data-setting-anchor="plugin-marketplace"
                  >
                    {lifecycle && (
                      <PluginLifecycleActions
                        api={lifecycle}
                        onChanged={() => void refresh(undefined, 'all')}
                      />
                    )}
                    {tab === 'discover' && toolbar}
                    {tab === 'discover' && list}
                  </div>
                ),
              },
            ]}
          />
        </>
      )}
      {state.selected && (
        <section aria-label={`Manage ${state.selected.name}`}>
          <h3>{state.selected.name}</h3>
          <ConnectSheet
            title={`Set up ${state.selected.name}`}
            steps={pluginSteps(state.selected)}
          >
            {state.selected.changelog_url && (
              <p>
                <a
                  className="settings-inline-action"
                  href={state.selected.changelog_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  What’s new in {state.selected.name}
                </a>
              </p>
            )}
          </ConnectSheet>
          <p>
            Health: {humanizeToken(state.selected.health.status).toLowerCase()}.
            Permissions:{' '}
            {state.selected.permissions.map(humanizeToken).join(', ') || 'none'}
            .
          </p>
          {state.selected.health.checks.length > 0 && (
            <ul>
              {state.selected.health.checks.map((check) => (
                <li key={`${check.label}:${check.status}`}>
                  {check.label}: {humanizeToken(check.status).toLowerCase()}
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
                  <Toggle
                    label={field.label}
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
                    className="input"
                    rows={3}
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
              Save configuration
            </Button>
          </fieldset>
          <div className="button-row">
            <Button
              disabled={locked || !state.selected.capabilities.test?.available}
              onClick={() => void requestReview('plugin.test')}
            >
              Run local test
            </Button>
            <Toggle
              label="Plugin enabled"
              checked={state.selected.enabled}
              disabled={
                locked ||
                !(state.selected.enabled
                  ? state.selected.capabilities.disable?.available
                  : state.selected.capabilities.enable?.available)
              }
              onChange={(event) =>
                void requestReview(
                  event.target.checked ? 'plugin.enable' : 'plugin.disable',
                )
              }
            />
          </div>
          {!state.selected.capabilities.remove?.available && (
            <p role="status">
              Remove and update are unavailable until a recoverable lifecycle
              worker owns those effects.
            </p>
          )}
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

const unavailableLifecycle: PluginLifecycleApi = {
  review: () => Promise.reject(new Error('Plugin lifecycle is unavailable.')),
  execute: () => Promise.reject(new Error('Plugin lifecycle is unavailable.')),
  receipt: () => Promise.reject(new Error('Plugin lifecycle is unavailable.')),
};

const UNHEALTHY = new Set(['failed', 'error', 'unhealthy', 'load_failed']);

/**
 * A plugin's connect sheet (parity row 39): its own setup notes, the
 * sign-ins it declares, then settings, the local test and turning it on,
 * with why a step is unavailable instead of a greyed control (U52).
 */
function pluginSteps(plugin: PluginDetail): ConnectStep[] {
  const steps: ConnectStep[] = [];
  if (plugin.guide)
    steps.push({
      id: 'guide',
      text: 'Read its setup notes.',
      children: (
        <details className="settings-plugin-guide">
          <summary>Setup notes</summary>
          <div className="settings-plugin-guide-text">{plugin.guide}</div>
        </details>
      ),
    });
  (plugin.sign_in ?? []).forEach((item, index) =>
    steps.push({
      id: `sign-in-${index}`,
      text: `Sign in to ${item.label}${
        item.kind ? ` (${humanizeToken(item.kind)})` : ''
      } when the plugin asks.`,
    }),
  );
  const fields = [...plugin.settings, ...plugin.secrets];
  if (fields.length)
    steps.push({
      id: 'settings',
      text: plugin.capabilities.configure?.available
        ? 'Fill in its settings below and save them.'
        : 'Turn it off to change its settings.',
      done: fields.every((field) => !field.required || field.configured),
    });
  steps.push({
    id: 'test',
    text: 'Run its local test.',
    done: plugin.health.status === 'passed',
  });
  steps.push({
    id: 'enable',
    text: plugin.enabled
      ? 'It is on.'
      : plugin.capabilities.enable?.available
        ? 'Turn it on.'
        : 'Turn it on once its settings are saved and the local test passed.',
    done: plugin.enabled,
  });
  return steps;
}

function pluginStatus(plugin: PluginCatalogItem): {
  tone: Tone;
  label: string;
} {
  if (!plugin.installed) return { tone: 'info', label: 'Marketplace' };
  // Enabled but not running: the last load failed (e.g. its environment is
  // not prepared), whatever its last explicit test said.
  if (plugin.health === 'load_failed')
    return {
      tone: 'danger',
      label: plugin.capabilities.prepare?.available
        ? 'Needs preparing'
        : 'Failed to load',
    };
  if (UNHEALTHY.has(plugin.health))
    return { tone: 'danger', label: 'Needs attention' };
  if (plugin.enabled) return { tone: 'success', label: 'Enabled' };
  if (!plugin.setup_complete) return { tone: 'warning', label: 'Setup needed' };
  return { tone: 'neutral', label: 'Disabled' };
}

const providedWords: [string, string, string][] = [
  ['native_tools', 'tool', 'tools'],
  ['mcp_servers', 'MCP server', 'MCP servers'],
  ['channels', 'channel', 'channels'],
  ['skills', 'skill', 'skills'],
];

/** One plugin: status, what it adds, one primary action and a ⋯ menu. */
function PluginRow({
  plugin,
  locked,
  lifecycle,
  onManage,
  onAction,
  onChanged,
}: {
  plugin: PluginCatalogItem;
  locked: boolean;
  lifecycle?: PluginLifecycleApi;
  onManage: () => void;
  onAction: (
    action: 'plugin.enable' | 'plugin.disable' | 'plugin.test',
  ) => void;
  onChanged: () => void;
}) {
  const life = usePluginLifecycle(
    plugin,
    lifecycle ?? unavailableLifecycle,
    onChanged,
  );
  const status = pluginStatus(plugin);
  const provided = providedWords
    .map(([key, one, many]) => {
      const count = plugin.provides[key] ?? 0;
      return count ? `${count} ${count === 1 ? one : many}` : '';
    })
    .filter(Boolean);
  const meta = [
    `v${plugin.version}`,
    plugin.source === 'installed' ? 'Installed locally' : 'Saved marketplace',
    ...provided,
    plugin.permissions.length
      ? `Uses ${plugin.permissions.map((item) => humanizeToken(item).toLowerCase()).join(', ')}`
      : '',
    plugin.update_version ? `Update ${plugin.update_version}` : '',
  ]
    .filter(Boolean)
    .join(' · ');
  const busy = locked || life.locked;
  // An install or update the plugin's source can't do says why instead of
  // offering a button that fails (B266).
  const lifecycleGate = !plugin.installed
    ? plugin.capabilities.install
    : plugin.update_version
      ? plugin.capabilities.update
      : undefined;
  const blockedReason =
    lifecycle && lifecycleGate?.available === false && lifecycleGate.code
      ? clientError({ code: lifecycleGate.code }).message
      : '';
  const menu: MenuAction[] = [];
  if (plugin.installed)
    menu.push({
      label: `Test ${plugin.name}`,
      icon: <FlaskConical size={16} />,
      disabled: busy || !plugin.capabilities.test?.available,
      onSelect: () => onAction('plugin.test'),
    });
  if (
    lifecycle &&
    plugin.installed &&
    plugin.update_version &&
    plugin.capabilities.update?.available
  )
    menu.push({
      label: `Update to ${plugin.update_version}`,
      icon: <Upload size={16} />,
      disabled: busy,
      onSelect: () => void life.action('update'),
    });
  if (lifecycle && plugin.installed)
    menu.push({
      label: `Uninstall ${plugin.name}`,
      icon: <Trash2 size={16} />,
      danger: true,
      disabled: busy,
      onSelect: life.requestRemove,
    });
  return (
    <li className="settings-plugin-row">
      <span className="settings-row-list-icon" aria-hidden>
        <Puzzle size={15} aria-hidden />
      </span>
      <div className="settings-plugin-summary">
        <div className="settings-plugin-title">
          <strong>{plugin.name}</strong>
          <StatusDot tone={status.tone} label={status.label} showLabel />
        </div>
        <p>{plugin.description}</p>
        <small>{meta}</small>
        {blockedReason && <small>{blockedReason}</small>}
        <details className="settings-plugin-details">
          <summary>Source and permissions</summary>
          <PluginProvenance plugin={plugin} />
        </details>
      </div>
      <div className="settings-plugin-row-actions">
        {plugin.installed ? (
          <>
            {lifecycle && plugin.capabilities.prepare?.available && (
              // A worker plugin loads only from its own prepared
              // environment, which nothing made before (B129, B164).
              <Button
                variant="primary"
                disabled={busy}
                onClick={() => void life.action('prepare')}
              >
                Prepare
              </Button>
            )}
            <Button
              aria-label={`Manage ${plugin.name}`}
              disabled={locked}
              onClick={onManage}
            >
              Configure
            </Button>
            <Toggle
              label={`${plugin.name} enabled`}
              checked={plugin.enabled}
              disabled={
                locked ||
                !(plugin.enabled
                  ? plugin.capabilities.disable?.available
                  : plugin.capabilities.enable?.available)
              }
              onChange={(event) =>
                onAction(
                  event.target.checked ? 'plugin.enable' : 'plugin.disable',
                )
              }
            />
          </>
        ) : lifecycle && plugin.capabilities.install?.available ? (
          <Button
            variant="primary"
            disabled={busy}
            onClick={() => void life.action('install')}
          >
            Install
          </Button>
        ) : (
          <span className="status-chip warning" role="status">
            Install unavailable
          </span>
        )}
        {menu.length > 0 && (
          <Menu
            label={`More actions for ${plugin.name}`}
            actions={menu}
            iconOnly
            variant="ghost"
            className="icon-action icon-action-sm"
          >
            <MoreHorizontal size={16} aria-hidden />
          </Menu>
        )}
      </div>
      <div className="settings-plugin-feedback">{life.feedback}</div>
      {life.confirmation}
    </li>
  );
}
