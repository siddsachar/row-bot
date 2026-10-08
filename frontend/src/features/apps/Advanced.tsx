import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useRuntime } from '../../runtime';
import { clientError } from '../../api/errors';
import type {
  AppViewSettings,
  CatalogSchedule,
  IntegrationSourceView,
  McpChatState,
  McpPolicyPage,
} from '../../api/types';
import { relativeTime } from '../../ui/format';
import { Button, Field, Select, Toggle } from '../../ui/primitives';
import { SettingsGroup, StatusLine } from '../settings/anatomy';
import RuntimeInstallations from '../mcp/RuntimeInstallations';

/** Where a catalog stands, in plain words. */
function standing(source: IntegrationSourceView) {
  if (source.opt_in)
    return source.opt_in.on
      ? 'On · a separate service with its own account'
      : 'Off · a separate service with its own account';
  const update = source.catalog;
  if (!update)
    return source.network === 'none'
      ? 'Included with Row-Bot'
      : 'Searched when you ask';
  if (update.state === 'updating') return 'Updating…';
  if (update.state === 'failed')
    return "Couldn't update. The last copy is still used.";
  if (update.state === 'never' || !update.updated_at)
    return 'Included with Row-Bot';
  const entries = update.entries
    ? ` · ${update.entries.toLocaleString()} entries`
    : '';
  return `Updated ${relativeTime(update.updated_at)}${entries}`;
}

/**
 * "Use apps": the one switch for apps. On, apps run and chats and workflows can use their tools; off stops
 * every app at once. Each half goes through its own reviewed command, as before: the apps' policy and the
 * agent's app tools.
 */
function UseApps() {
  const { controller } = useRuntime();
  const [page, setPage] = useState<McpPolicyPage | null>(null);
  const [chats, setChats] = useState<McpChatState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const read = useCallback(
    (signal?: AbortSignal) =>
      // A runtime without MCP settings shows the switch as unknown.
      Promise.all([
        Promise.resolve()
          .then(() =>
            controller.mcpPolicy({ server_id: null, query: '' }, signal),
          )
          .then(setPage),
        Promise.resolve()
          .then(() => controller.mcpChat(signal))
          .then(
            (state) => setChats(state ?? null),
            () => setChats(null),
          ),
      ]),
    [controller],
  );
  useEffect(() => {
    const abort = new AbortController();
    read(abort.signal).catch(() => undefined);
    return () => abort.abort();
  }, [read]);
  // Chats and workflows reach apps through the agent's app tools; one that can't report itself counts as on.
  const toolsOn = chats?.registered ? chats.saved_enabled === true : true;
  const change = async (enabled: boolean) => {
    if (!page?.revision) return;
    setBusy(true);
    setError('');
    try {
      if (page.global_enabled !== enabled) {
        const command = {
          command_id: crypto.randomUUID(),
          type: 'mcp.configuration.control' as const,
          payload: {
            configuration_revision: page.revision,
            intent: { operation: 'global_enabled', enabled },
          },
        };
        const review = await controller.reviewMcpPolicy(command.payload);
        const result = await controller.executeMcpConfiguration(
          command,
          review,
        );
        if (result.status !== 'completed') throw Error();
      }
      // Turning apps on also lets chats use them; turning them off stops every app, so that half stays as it is.
      if (enabled && !toolsOn && chats?.resource_revision) {
        const command = {
          command_id: crypto.randomUUID(),
          type: 'mcp.facade.control' as const,
          payload: { resource_revision: chats.resource_revision, enabled },
        };
        const review = await controller.reviewMcpChat(command.payload);
        const result = await controller.executeMcpChat(command, review);
        if (result.status !== 'completed') throw Error();
      }
    } catch (cause) {
      setError(
        clientError(cause).message || "Couldn't change this. Try again.",
      );
    } finally {
      setBusy(false);
      await read().catch(() => undefined);
    }
  };
  return (
    <div className="app-section">
      <Field
        label="Use apps"
        hint="Chats and workflows can use your connected apps; changes ask first. Off stops every app at once, and each keeps its settings."
        layout="row"
      >
        <Toggle
          label="Use apps"
          checked={page?.global_enabled === true && toolsOn}
          disabled={busy || !page?.revision || page.global_enabled === null}
          onChange={(event) => void change(event.target.checked)}
        />
      </Field>
      {error && <StatusLine tone="danger">{error}</StatusLine>}
    </div>
  );
}

/** A catalog the person turns on themselves (a hosted broker): on only after reading what it means, off at once. */
function OptIn({
  source,
  onChange,
}: {
  source: IntegrationSourceView;
  onChange: (next: IntegrationSourceView) => void;
}) {
  const { controller } = useRuntime();
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState('');
  const optIn = source.opt_in;
  if (!optIn) return null;
  const save = async (on: boolean) => {
    setError('');
    try {
      onChange(await controller.setIntegrationSourceOptIn(source.id, on));
      setAsking(false);
    } catch (cause) {
      setError(clientError(cause).message);
    }
  };
  return (
    <>
      <Toggle
        label={`Use ${source.label}`}
        checked={optIn.on || asking}
        onChange={(event) =>
          event.target.checked ? setAsking(true) : void save(false)
        }
      />
      {asking && !optIn.on && (
        <section
          className="catalog-disclosure"
          aria-label={`Before you use ${source.label}`}
        >
          <p>{optIn.disclosure}</p>
          <ul className="catalog-links">
            {optIn.links.map((link) => (
              <li key={link.url}>
                <a href={link.url} target="_blank" rel="noreferrer">
                  {link.label}
                </a>
              </li>
            ))}
          </ul>
          <div className="button-row">
            <Button variant="primary" onClick={() => void save(true)}>
              Turn on {source.label}
            </Button>
            <Button onClick={() => setAsking(false)}>Cancel</Button>
          </div>
        </section>
      )}
      {error && <StatusLine tone="danger">{error}</StatusLine>}
    </>
  );
}

/** "Show app views in chat": the person's switch for every app's views (on by default). */
function AppViews() {
  const { controller } = useRuntime();
  const [settings, setSettings] = useState<AppViewSettings | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    controller.appViewSettings().then(setSettings, () => undefined);
  }, [controller]);
  const change = async (enabled: boolean) => {
    if (!settings) return;
    setError('');
    try {
      setSettings(
        await controller.setAppViewSettings({ ...settings, enabled }),
      );
    } catch (cause) {
      setError(clientError(cause).message);
    }
  };
  return (
    <div className="app-section">
      <Field
        label="Show app views in chat"
        hint="Some apps show their results as an interactive view. Views run apart from Row-Bot and ask before changing anything."
        layout="row"
      >
        <Toggle
          label="Show app views in chat"
          checked={settings?.enabled === true}
          disabled={!settings}
          onChange={(event) => void change(event.target.checked)}
        />
      </Field>
      {error && <StatusLine tone="danger">{error}</StatusLine>}
    </div>
  );
}

/** Apps › Advanced: app-wide switches first, then catalogs and their schedule, runtimes and a custom connection. */
export default function Advanced() {
  const { controller } = useRuntime();
  const [sources, setSources] = useState<IntegrationSourceView[]>([]);
  const [schedule, setSchedule] = useState<CatalogSchedule | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    controller.integrationSources(abort.signal).then(
      (list) => {
        // Only catalogs searched for you, or one you turn on: those not usable yet and developer examples are left out.
        setSources(
          list.items.filter(
            (source) => source.opt_in || source.eligibility === 'eligible',
          ),
        );
        // An update runs in the background; look again until it settles.
        if (list.items.some((source) => source.catalog?.state === 'updating'))
          timer = setTimeout(() => setReload((n) => n + 1), 1500);
      },
      (cause) => !abort.signal.aborted && setError(clientError(cause).message),
    );
    return () => {
      abort.abort();
      clearTimeout(timer);
    };
  }, [controller, reload]);
  const updating = sources.some(
    (source) => source.catalog?.state === 'updating',
  );
  useEffect(() => {
    controller.catalogSchedule().then(setSchedule, () => undefined);
  }, [controller]);
  const updatable = sources.filter(
    (source) =>
      source.network === 'explicit' &&
      source.catalog &&
      source.eligibility === 'eligible',
  );
  const update = async (ids: string[]) => {
    setError('');
    try {
      for (const id of ids) await controller.updateIntegrationSource(id);
      setReload((n) => n + 1);
    } catch (cause) {
      setError(clientError(cause).message);
    }
  };
  const save = async (next: CatalogSchedule) => {
    try {
      setSchedule(await controller.setCatalogSchedule(next));
    } catch (cause) {
      setError(clientError(cause).message);
    }
  };
  return (
    <div className="stack">
      <Link className="settings-link app-back" to="/settings/apps">
        <ArrowLeft size={14} aria-hidden /> Apps
      </Link>
      <SettingsGroup title="Apps in chats" anchor="chats">
        <UseApps />
        <AppViews />
      </SettingsGroup>
      <SettingsGroup title="Catalogs" anchor="catalogs">
        <div className="app-section">
          <p className="settings-help">
            Row-Bot searches copies kept on this computer. Updating fetches the
            latest copy; what you type leaves this computer only when you choose
            Search online catalogs in an Apps search.
          </p>
          <ul className="catalog-list">
            {sources.map((source) => (
              <li key={source.id}>
                <span>
                  <strong>{source.label}</strong>
                  <small>
                    {source.kinds.includes('skill') ? 'Skills' : 'Apps'} ·{' '}
                    {standing(source)}
                  </small>
                </span>
                {updatable.includes(source) && (
                  <Button
                    disabled={source.catalog?.state === 'updating'}
                    onClick={() => void update([source.id])}
                    aria-label={`Update ${source.label}`}
                  >
                    Update
                  </Button>
                )}
                <OptIn
                  source={source}
                  onChange={(next) =>
                    setSources((all) =>
                      all.map((item) => (item.id === next.id ? next : item)),
                    )
                  }
                />
              </li>
            ))}
          </ul>
          <div className="button-row">
            <Button
              disabled={updating || !updatable.length}
              onClick={() => void update(updatable.map((s) => s.id))}
            >
              Update all
            </Button>
          </div>
          {schedule && (
            <>
              <Field label="Update catalogs automatically" layout="row">
                <Toggle
                  label="Update catalogs automatically"
                  checked={schedule.enabled}
                  onChange={(event) =>
                    void save({ ...schedule, enabled: event.target.checked })
                  }
                />
              </Field>
              {schedule.enabled && (
                <Field label="How often">
                  <Select
                    value={String(schedule.interval_days)}
                    onChange={(event) =>
                      void save({
                        ...schedule,
                        interval_days: Number(event.target.value) as 1 | 7 | 30,
                      })
                    }
                  >
                    <option value="1">Every day</option>
                    <option value="7">Every week</option>
                    <option value="30">Every month</option>
                  </Select>
                </Field>
              )}
            </>
          )}
          {error && <StatusLine tone="danger">{error}</StatusLine>}
        </div>
      </SettingsGroup>
      <SettingsGroup title="On this computer" anchor="runtimes">
        <RuntimeInstallations />
      </SettingsGroup>
      <SettingsGroup title="Your own connection" anchor="custom">
        <div className="app-section">
          <p className="settings-help">
            Add a connection by its settings, for servers that aren't in a
            catalog.
          </p>
          <Link className="button" to="/settings/apps/custom?edit=1">
            Add a custom connection
          </Link>
        </div>
      </SettingsGroup>
    </div>
  );
}
