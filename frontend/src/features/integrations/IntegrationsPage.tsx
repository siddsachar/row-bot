import { useSetupOperations, settleSetupOperation } from './setup-operations';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useSearchParams, useLocation } from 'react-router-dom';
import { useRuntime } from '../../runtime';
import type {
  IntegrationItem,
  IntegrationPage,
  IntegrationPreview,
  PluginLifecycleCommand,
  SkillHubPreview,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { retainCommand, readRetainedCommand } from '../../api/retained-command';
import {
  Button,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
  Toggle,
} from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';
import McpSetup from './McpSetup';
import { SkillReview, SkillSetup, PluginSetup } from './OwnerSetup';
import Catalogs from './Catalogs';
import ProfileContext from './ProfileContext';
import TryInChat from './TryInChat';
import SkillMaintenance from './SkillMaintenance';
import {
  categories,
  catalogs,
  readPreferences,
  savePreferences,
  sourceName,
  setupLabel,
  type Kind,
  type Source,
} from './discovery';
import './integrations.css';
const statusLabels: Record<string, string> = {
  ready: 'Ready',
  off: 'Off',
  setup: 'Setup needed',
  attention: 'Needs attention',
  missing_runtime: 'Runtime needed',
  disconnected: 'Needs sign-in',
  recovery: 'Recovery needed',
  retained: 'Saved data retained',
  discover: 'Available to inspect',
};
type Review = { title: string; lines: string[]; apply: () => Promise<void> };
type Pending = { kind: 'skill' | 'plugin' | 'mcp'; id: string };
const pendingScopes = ['skill', 'plugin', 'mcp'] as const;
function setupAction(raw: string) {
  try {
    const parsed = JSON.parse(raw) as {
      mcpServers?: Record<string, { transport?: string; url?: string }>;
    };
    return Object.values(parsed.mcpServers ?? {}).some(
      (value) => value?.url && value.transport !== 'stdio',
    )
      ? 'Connect'
      : 'Set up';
  } catch {
    return 'Review setup';
  }
}
/** Common inventory, explicit discovery and review; mutations keep their owners. */
export default function IntegrationsPage({
  renderDetail,
  renderAdvanced,
}: {
  renderDetail: (
    item: IntegrationItem,
    onChanged: (removed?: boolean) => Promise<void>,
  ) => ReactNode;
  renderAdvanced: (kind: 'skill' | 'plugin' | 'mcp') => ReactNode;
}) {
  const { controller } = useRuntime();
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const tab =
    params.get('tab') === 'discover' && params.get('type') !== 'all'
      ? 'discover'
      : 'my';
  const [preferences, setPreferences] = useState(readPreferences);
  const [entryCategory] = useState(preferences.category);
  const explicitType = params.get('type');
  const legacySource = params.get('source');
  const selectedKind = params.get('selected')?.split(':')[0];
  const inferredType = categories.some(
    (category) => category.kind === selectedKind,
  )
    ? selectedKind
    : Object.entries(catalogs).find(([, sources]) =>
        sources.includes(legacySource as Source),
      )?.[0];
  const type = (
    ['skill', 'mcp', 'plugin', 'all'].includes(explicitType ?? '')
      ? explicitType
      : (inferredType ?? entryCategory)
  ) as Kind | 'all' | '';
  const query = params.get('q') ?? '';
  const selected = params.get('selected') ?? '';
  const unsupported = params.get('unsupported') === '1';
  const catalogView = params.get('view') === 'catalogs';
  const [draftQuery, setDraftQuery] = useState(query);
  const [page, setPage] = useState<IntegrationPage | null>(null);
  const [preview, setPreview] = useState<IntegrationPreview | null>(null);
  const [detail, setDetail] = useState<IntegrationItem | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [inspecting, setInspecting] = useState(false);
  const [message, setMessage] = useState('');
  const [review, setReview] = useState<Review | null>(null);
  const [add, setAdd] = useState(false);
  const [reference, setReference] = useState('');
  const [importKind, setImportKind] = useState<'plugin' | 'skill' | 'mcp'>(
    'plugin',
  );
  const [local, setLocal] = useState(false);
  const [connectionName, setConnectionName] = useState('');
  const [advanced, setAdvanced] = useState<'skill' | 'plugin' | 'mcp' | null>(
    location.hash === '#mcp-runtimes' ? 'mcp' : null,
  );
  const [pending, setPending] = useState<Pending | null>(() => {
    for (const kind of pendingScopes) {
      const id = readRetainedCommand('integrations:' + kind);
      if (id) return { kind, id };
    }
    return null;
  });
  const heading = useRef<HTMLHeadingElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const generation = useRef(0);
  const publicSearch = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const inspection = useRef<AbortController | null>(null);
  const savedPages = useRef(new Map<string, IntegrationPage>());
  const inspectionGeneration = useRef(0);
  const inspectionIdentity = useRef('');
  const invalidateInspection = useCallback(() => {
    inspectionGeneration.current++;
    inspection.current?.abort();
    inspection.current = null;
  }, []);
  const returnTo = useRef(new Map<string, { id: string; top: number }>());
  const listKey = JSON.stringify([
    tab,
    type,
    query,
    unsupported,
    preferences.disabled,
  ]);
  const inspectionKey = JSON.stringify([
    location.key,
    location.pathname,
    selected,
    listKey,
    page?.revision,
  ]);
  useLayoutEffect(() => {
    // A committed navigation invalidates inspection even if the transport ignores abort.
    inspectionIdentity.current = inspectionKey;
    invalidateInspection();
    setInspecting(false);
    return invalidateInspection;
  }, [inspectionKey, invalidateInspection]);
  const persist = (next: typeof preferences) => {
    setPreferences(next);
    if (!savePreferences(next))
      setMessage(
        'Preferences could not be saved on this device. They apply for this visit.',
      );
  };
  useEffect(() => {
    if (type && type !== 'all' && preferences.category !== type) {
      const next = { ...preferences, category: type };
      setPreferences(next);
      savePreferences(next);
    }
  }, [type, preferences]);
  const update = (values: Record<string, string>) => {
    invalidateInspection();
    setInspecting(false);
    const next = new URLSearchParams(params);
    Object.entries(values).forEach(([key, value]) =>
      value ? next.set(key, value) : next.delete(key),
    );
    if (type && !next.has('type')) next.set('type', type);
    next.delete('source');
    setParams(next);
  };
  const load = useCallback(
    async (refresh = false, cursor?: string) => {
      if (!type) return;
      abort.current?.abort();
      const cancellation = new AbortController();
      abort.current = cancellation;
      const request = ++generation.current;
      setLoading(true);
      try {
        const sources = preferences.disabled.length
          ? (type === 'all'
              ? Object.values(catalogs).flat()
              : catalogs[type]
            ).filter((source) => !preferences.disabled.includes(source))
          : undefined;
        const result =
          tab === 'my'
            ? await controller.integrations(
                { query, kind: type, cursor },
                cancellation.signal,
              )
            : await controller.searchIntegrations(
                {
                  query,
                  kind: type,
                  ...(sources ? { sources } : {}),
                  ...(cursor ? { cursor } : {}),
                  ...(unsupported ? { include_incompatible: true } : {}),
                  refresh,
                },
                cancellation.signal,
              );
        if (request !== generation.current || cancellation.signal.aborted)
          return;
        const old = savedPages.current.get(listKey);
        const next =
          cursor && old?.revision === result.revision
            ? { ...result, items: [...old.items, ...result.items] }
            : result;
        savedPages.current.set(listKey, next);
        setPage(next);
      } catch (e) {
        if (request === generation.current && !cancellation.signal.aborted)
          setMessage(clientError(e).message);
      } finally {
        if (request === generation.current) setLoading(false);
      }
    },
    [controller, tab, type, query, unsupported, preferences.disabled, listKey],
  );
  useEffect(() => {
    setDraftQuery(query);
    setMessage('');
    const refresh = publicSearch.current === listKey;
    publicSearch.current = null;
    const saved = savedPages.current.get(listKey);
    setPage(saved ?? null);
    if (saved && !refresh) setLoading(false);
    else void load(refresh);
    return () => {
      // Invalidate and abort even transports that race their cancellation signal.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      generation.current++;
      abort.current?.abort();
    };
  }, [load, query, listKey]);
  const search = () => {
    setMessage('');
    if (draftQuery === query) void load(tab === 'discover');
    else {
      if (tab === 'discover')
        publicSearch.current = JSON.stringify([
          tab,
          type,
          draftQuery,
          unsupported,
          preferences.disabled,
        ]);
      update({ q: draftQuery, selected: '' });
    }
  };
  useEffect(() => {
    setPreview(null);
    setDetail(null);
    if (!selected) {
      const restore = returnTo.current.get(listKey);
      if (restore) {
        const row = Array.from(
          list.current?.querySelectorAll<HTMLButtonElement>(
            '[data-integration-id]',
          ) ?? [],
        ).find((button) => button.dataset.integrationId === restore.id);
        row?.focus({ preventScroll: true });
        const container = list.current?.closest('.settings-page-content');
        if (container) container.scrollTop = restore.top;
      }
      return;
    }
    heading.current?.focus({ preventScroll: true });
    if (tab === 'my') {
      let active = true;
      const cancellation = new AbortController();
      void controller
        .integration(selected, cancellation.signal)
        .then((row) => {
          if (active) setDetail(row);
        })
        .catch((e) => {
          if (active) setMessage(clientError(e).message);
        });
      return () => {
        active = false;
        cancellation.abort();
      };
    }
  }, [controller, selected, tab, listKey]);
  useEffect(() => {
    if (selected || preview) heading.current?.focus({ preventScroll: true });
  }, [selected, detail, preview]);
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setMessage('');
    try {
      await action();
    } catch (e) {
      setMessage(clientError(e).message);
    } finally {
      setBusy(false);
    }
  };
  const remember = (value: Pending | null) => {
    if (value) retainCommand('integrations:' + value.kind, value.id);
    else if (pending) retainCommand('integrations:' + pending.kind, '');
    setPending(value);
  };
  const recover = async () => {
    if (!pending) return;
    const result = await controller.reconcileIntegrationOperation(
      pending.kind,
      pending.id,
    );
    setMessage(result.message);
    if (result.settled) {
      settleSetupOperation(pending.id);
      remember(null);
    }
    await load();
  };
  const inspect = async (item: IntegrationItem) => {
    if (!page) return;
    invalidateInspection();
    const cancellation = new AbortController();
    inspection.current = cancellation;
    const request = inspectionGeneration.current;
    const identity = inspectionIdentity.current;
    const isCurrent = () =>
      !cancellation.signal.aborted &&
      request === inspectionGeneration.current &&
      identity === inspectionIdentity.current;
    setInspecting(true);
    setMessage('');
    try {
      const result = await controller.previewIntegration(
        { revision: page.revision, item_id: item.id },
        cancellation.signal,
      );
      if (!isCurrent()) return;
      setPreview(result);
      if (result.mcp) setConnectionName(result.mcp.name);
    } catch (error) {
      if (isCurrent()) setMessage(clientError(error).message);
    } finally {
      // An obsolete request cannot clear a newer inspection's loading state.
      if (isCurrent()) setInspecting(false);
    }
  };
  const installSkill = (skill: SkillHubPreview) =>
    setReview({
      title: `Add ${skill.skill_name}`,
      lines: [
        `Install all ${skill.files.length} reviewed files. Scripts remain untrusted; adding a skill does not run them.`,
        ...skill.scan.findings.map((f) => f.message),
      ],
      apply: async () => {
        const id = crypto.randomUUID();
        remember({ kind: 'skill', id });
        const result = await controller.installSkillHub({
          command_id: id,
          preview_id: skill.preview_id,
          content_hash: skill.content_hash,
          make_available: false,
        });
        setMessage(result.message);
        const recovered = await controller.reconcileIntegrationOperation(
          'skill',
          id,
        );
        if (recovered.settled) {
          retainCommand('integrations:skill', '');
          setPending(null);
        }
        if (result.success) {
          setPreview(null);
          update({ tab: 'my', selected: 'skill:' + result.skill_name });
        }
        await load();
      },
    });
  const packageAction = async (
    action: PluginLifecycleCommand['action'],
    pluginId: string,
    previewId = '',
  ) => {
    const value = await setupOperations.run(() =>
      controller.reviewPluginLifecycle(action, pluginId, undefined, previewId),
    );
    setReview({
      title: `${action === 'install' ? 'Add' : action === 'purge' ? 'Delete saved data for' : action} ${value.name}`,
      lines: [
        ...value.disclosures,
        ...(value.changes ?? []),
        `Permissions: ${value.permissions.join(', ') || 'None declared'}`,
        `Version: ${value.version || 'not declared'}`,
        `Source: ${value.source}`,
        `Digest: ${value.checksum || 'local revision review'}`,
      ],
      apply: () =>
        setupOperations.run(async () => {
          const id = crypto.randomUUID();
          setupOperations.retain(
            'integration-lifecycle:plugin:' + pluginId,
            id,
          );
          remember({ kind: 'plugin', id });
          const result = await controller.executePluginLifecycle({
            command_id: id,
            action,
            plugin_id: pluginId,
            preview_id: previewId,
            revision: value.revision,
          });
          setMessage(result.message);
          if (result.status !== 'uncertain') {
            settleSetupOperation(id);
            retainCommand('integrations:plugin', '');
            setPending(null);
          }
          if (result.status === 'completed') {
            setPreview(null);
            setDetail(
              action === 'purge'
                ? null
                : await controller.integration('plugin:' + pluginId),
            );
            update({
              tab: 'my',
              selected: action === 'purge' ? '' : 'plugin:' + pluginId,
            });
          }
          await load();
        }),
    });
  };
  const addMcp = async () => {
    if (!preview?.mcp) return;
    const config = JSON.parse(preview.mcp.import_json) as {
      mcpServers: Record<string, unknown>;
    };
    const value = Object.values(config.mcpServers)[0];
    const saved = await controller.mcpConfiguration('');
    if (!saved.revision) throw new Error('MCP settings are unavailable.');
    const payload = {
      configuration_revision: saved.revision,
      intent: {
        operation: 'import' as const,
        import_json: JSON.stringify({
          mcpServers: { [connectionName]: value },
        }),
      },
    };
    const reviewed = await controller.reviewMcpConfiguration(payload);
    setReview({
      title: `Add ${connectionName}`,
      lines: [
        ...preview.mcp.notes,
        'Save this connection off. Set up credentials and requirements, test it, then review its tools before connecting.',
      ],
      apply: async () => {
        const id = crypto.randomUUID();
        remember({ kind: 'mcp', id });
        const result = await controller.executeMcpConfiguration(
          { command_id: id, type: 'mcp.configuration.save', payload },
          reviewed,
        );
        if (result.status === 'completed') {
          retainCommand('integrations:mcp', '');
          setPending(null);
          setPreview(null);
          const next = await controller.integrations({ kind: 'mcp' });
          const row = next.items.find((item) => item.name === connectionName);
          update({ tab: 'my', selected: row?.id ?? '' });
        }
        setMessage(
          result.status === 'completed'
            ? 'Connection added. Finish setup in its details.'
            : 'Save needs recovery. Check the original operation.',
        );
        await load();
      },
    });
  };
  const importReference = async () => {
    if (importKind === 'mcp') {
      const url = new URL(reference);
      if (!['https:', 'http:'].includes(url.protocol))
        throw new Error('Use an HTTP MCP endpoint.');
      const name = url.hostname.replace(/[^a-z0-9.-]/gi, '-');
      setPreview({
        kind: 'mcp',
        mcp: {
          name,
          import_json: JSON.stringify({
            mcpServers: {
              [name]: {
                transport: 'streamable_http',
                url: reference,
                enabled: false,
              },
            },
          }),
          requires_auth: false,
          notes: ['Use the service’s documented MCP endpoint.'],
          source_url: reference,
        },
      });
      setConnectionName(name);
    } else if (importKind === 'skill') {
      const found = await controller.searchSkillHub({
        query: reference,
        source: 'all',
        refresh: true,
      });
      if (found.entries.length !== 1)
        throw new Error(
          'Choose the matching skill in Discover before importing it.',
        );
      const value = await controller.previewSkillHub({
        revision: found.revision,
        entry_id: found.entries[0].id,
      });
      setPreview({ kind: 'skill', skill: value });
    } else
      setPreview(
        await controller.previewIntegration({
          kind: 'plugin',
          reference,
          local,
        }),
      );
    setAdd(false);
  };
  const selectedRow =
    detail ?? page?.items.find((row) => row.id === selected) ?? null;
  const setupOperations = useSetupOperations(selectedRow);
  const rows = (page?.items ?? []).filter(
    (item) =>
      (type === 'all' ||
        item.kind === type ||
        item.children.some((child) => child.kind === type)) &&
      (tab === 'my' || unsupported || item.compatibility !== 'unsupported'),
  );
  const refreshDetail = async (removed = false) => {
    savedPages.current.clear();
    if (removed) {
      setDetail(null);
      update({ selected: '' });
      await load();
      return;
    }
    await Promise.all([
      load(),
      selected && tab === 'my'
        ? controller
            .integration(selected)
            .then(setDetail)
            .catch((e) => {
              setMessage(clientError(e).message);
            })
        : Promise.resolve(),
    ]);
  };
  if (catalogView && type && type !== 'all')
    return (
      <Catalogs
        kind={type}
        disabled={preferences.disabled}
        onChange={(source, enabled) => {
          savedPages.current.clear();
          persist({
            ...preferences,
            disabled: enabled
              ? preferences.disabled.filter((s) => s !== source)
              : [...preferences.disabled, source],
          });
        }}
        onBack={() => update({ view: '' })}
      />
    );
  if (advanced)
    return (
      <section className="stack">
        <Button
          onClick={() => {
            setAdvanced(null);
            setDetail(null);
            update({ selected: '' });
            void load();
          }}
        >
          Back to integrations
        </Button>
        {selectedRow && selectedRow.kind === advanced
          ? renderDetail(selectedRow, refreshDetail)
          : renderAdvanced(advanced)}
      </section>
    );
  return (
    <div className="integrations-page stack" aria-busy={busy || inspecting}>
      {!selected && !preview && (
        <>
          <nav
            className={`integration-categories ${!type ? 'is-chooser' : ''}`}
            aria-label="Integration categories"
          >
            {categories.map((category) => (
              <Button
                key={category.kind}
                aria-label={
                  category.kind === 'mcp'
                    ? 'Apps & tools · MCP'
                    : category.label
                }
                aria-pressed={type === category.kind}
                variant={type === category.kind ? 'primary' : 'secondary'}
                onClick={() => {
                  persist({ ...preferences, category: category.kind });
                  update({
                    type: category.kind,
                    selected: '',
                    tab: 'discover',
                    q: '',
                    view: '',
                  });
                }}
              >
                <strong>
                  {category.label}
                  {category.kind === 'mcp' ? ' · MCP' : ''}
                </strong>
                {!type && <span>{category.purpose}</span>}
              </Button>
            ))}
          </nav>
          {type && (
            <>
              <p>
                {categories.find((category) => category.kind === type)
                  ?.purpose ??
                  'Advanced inventory across all integration types.'}
              </p>
              <div className="button-row" aria-label="Integration views">
                <Button
                  variant={tab === 'discover' ? 'primary' : 'secondary'}
                  aria-pressed={tab === 'discover'}
                  disabled={type === 'all'}
                  onClick={() => update({ tab: 'discover', selected: '' })}
                >
                  Discover
                </Button>
                <Button
                  variant={tab === 'my' ? 'primary' : 'secondary'}
                  aria-pressed={tab === 'my'}
                  onClick={() => update({ tab: 'my', selected: '' })}
                >
                  Installed
                </Button>
                <Button
                  onClick={() => {
                    setImportKind(type === 'all' ? 'plugin' : type);
                    setReference('');
                    setLocal(false);
                    setAdd(true);
                  }}
                >
                  Add from link or file
                </Button>
              </div>
              <form
                className="integrations-filters"
                onSubmit={(e) => {
                  e.preventDefault();
                  search();
                }}
              >
                <Input
                  aria-label="Search integrations"
                  value={draftQuery}
                  onChange={(e) => setDraftQuery(e.target.value)}
                  maxLength={256}
                  placeholder={
                    tab === 'my'
                      ? 'Filter installed integrations'
                      : 'Find a service or capability'
                  }
                />
                <Button type="submit">
                  {tab === 'discover' ? 'Search' : 'Filter'}
                </Button>
              </form>
              {tab === 'discover' && (
                <p className="muted">
                  Search sends your query to enabled public catalogs. Typing
                  stays on this device; local snapshots never send a public
                  query.
                </p>
              )}
            </>
          )}
        </>
      )}
      {message && <p role="status">{message}</p>}
      {pending && (
        <Button disabled={busy} onClick={() => void run(recover)}>
          Check original {pending.kind} operation
        </Button>
      )}
      {!selected && !preview && type && tab === 'discover' && (
        <>
          {page?.sources.some(
            (source) =>
              source.eligibility === 'eligible' &&
              [
                'error',
                'timeout',
                'rate_limited',
                'malformed',
                'busy',
                'partial',
                'auth_required',
              ].includes(source.status),
          ) && (
            <details className="integration-notice">
              <summary>
                Some catalogs unavailable. Available results are shown.
              </summary>
              {page.sources
                .filter(
                  (source) =>
                    source.eligibility === 'eligible' &&
                    !['live', 'cached', 'empty'].includes(source.status),
                )
                .map((source) => (
                  <p key={source.source}>
                    {sourceName(source.source)}:{' '}
                    {source.message || source.status}
                  </p>
                ))}
              <Button onClick={() => void load(true)}>Retry search</Button>
            </details>
          )}
          {page?.sources.some((source) =>
            ['cached', 'stale'].includes(source.status),
          ) && (
            <p className="muted">
              Showing local snapshots or saved catalog results. Saved results
              may be out of date; inspect an item before setup.
            </p>
          )}
          <Field label="Include unsupported entries" layout="row">
            <Toggle
              label="Include unsupported entries"
              checked={unsupported}
              onChange={(e) =>
                update({
                  unsupported: e.target.checked ? '1' : '',
                  selected: '',
                })
              }
            />
          </Field>
        </>
      )}
      <div
        className={`integrations-layout ${selected || preview ? 'has-detail' : ''}`}
      >
        <div
          hidden={Boolean(selected || preview || !type)}
          ref={list}
          tabIndex={-1}
          className="integrations-list"
          aria-label="Integrations"
        >
          {loading && !page && <Skeleton label="Loading integrations" />}
          {!loading && !rows.length && (
            <EmptyState
              title={
                message
                  ? 'Catalog results unavailable'
                  : tab === 'my'
                    ? 'No matching installed integrations'
                    : page?.sources.some(
                          (source) =>
                            [
                              'error',
                              'timeout',
                              'unavailable',
                              'auth_required',
                              'rate_limited',
                              'malformed',
                              'busy',
                            ].includes(source.status) &&
                            source.eligibility === 'eligible',
                        )
                      ? 'Catalogs unavailable'
                      : 'No matching results'
              }
            >
              {tab === 'my'
                ? 'Add an integration or choose another filter.'
                : 'Try another query or review enabled Catalogs. Search explicitly when you want current public results.'}
            </EmptyState>
          )}
          {rows.map((item) => (
            <Button
              className="integration-row"
              aria-label={item.name}
              key={item.id}
              data-integration-id={item.id}
              aria-pressed={selected === item.id}
              onClick={() => {
                returnTo.current.set(listKey, {
                  id: item.id,
                  top:
                    list.current?.closest('.settings-page-content')
                      ?.scrollTop ?? 0,
                });
                update({ selected: item.id });
              }}
            >
              <span>
                <strong>{item.name}</strong>
                <small className="integration-purpose">
                  {item.description}
                </small>
                <small>
                  {item.publisher
                    ? `By ${item.publisher}`
                    : 'Publisher not supplied'}{' '}
                  · {sourceName(item.source)}
                  {(item.attributions?.length ?? 0) > 1
                    ? ` · Also found in ${item
                        .attributions!.filter((a) => a.source !== item.source)
                        .map((a) => sourceName(a.source))
                        .join(', ')}`
                    : ''}
                  {item.kind === 'skill' && item.installed
                    ? item.source === 'bundled' || item.source === 'builtin'
                      ? ' · Built-in skill'
                      : ' · Added skill'
                    : ''}
                  {item.children.length
                    ? ` · ${item.children.length} included`
                    : ''}
                </small>
                {item.children
                  .filter((child) => child.kind === type)
                  .map((child) => (
                    <small key={child.id}>
                      {child.name} · Included with {item.name}; managed by this
                      plugin
                    </small>
                  ))}
                {tab === 'discover' && (
                  <small>
                    {setupLabel(item)} ·{' '}
                    {item.compatibility === 'not_inspected'
                      ? 'Compatibility not yet verified'
                      : item.compatibility}
                  </small>
                )}
              </span>
              <span className="integration-status">
                {item.installed
                  ? (statusLabels[item.status] ?? item.status)
                  : 'View details'}
              </span>
            </Button>
          ))}
          {page?.next_cursor && (
            <Button
              disabled={loading}
              onClick={() => void load(false, page.next_cursor!)}
            >
              Show more
            </Button>
          )}
        </div>
        {selected && !selectedRow && !preview && (
          <section>
            <Button onClick={() => update({ selected: '' })}>
              Back to integrations
            </Button>
            <p role="status">
              {loading
                ? 'Loading integration details'
                : 'This item is no longer in the saved results. Return to search or Installed.'}
            </p>
          </section>
        )}
        {(selectedRow || preview) && (
          <section
            className="integration-detail stack"
            aria-label="Integration details"
          >
            <Button
              variant="ghost"
              onClick={() => {
                setPreview(null);
                update({ selected: '' });
              }}
            >
              Back to integrations
            </Button>
            <h2 ref={heading} tabIndex={-1}>
              {selectedRow?.name ??
                preview?.plugin?.name ??
                preview?.skill?.skill_name ??
                preview?.mcp?.name}
            </h2>
            {selectedRow && (
              <>
                <p>{selectedRow.description}</p>
                <p>
                  {tab === 'my'
                    ? setupOperations.blocked
                      ? 'Operation needs checking'
                      : statusLabels[selectedRow.status]
                    : 'Available to inspect'}{' '}
                  · {selectedRow.publisher || selectedRow.source}
                </p>
                {tab === 'discover' && (
                  <>
                    <p>{setupLabel(selectedRow)}</p>
                    <p>
                      Compatibility:{' '}
                      {selectedRow.compatibility === 'not_inspected'
                        ? 'Not yet verified'
                        : selectedRow.compatibility}
                    </p>
                    <details>
                      <summary>Source and provenance</summary>
                      <p>
                        {sourceName(selectedRow.source)} ·{' '}
                        {selectedRow.publisher || 'Publisher not supplied'}
                      </p>
                      <p>{selectedRow.evidence}</p>
                      <p>
                        Version: {selectedRow.version || 'not supplied'} ·
                        License: {selectedRow.license || 'not supplied'}
                      </p>
                      {selectedRow.attributions?.map((attribution, index) => (
                        <p key={`${attribution.source}:${index}`}>
                          {sourceName(attribution.source)} ·{' '}
                          {attribution.publisher || 'Publisher not supplied'} ·{' '}
                          {attribution.url}
                        </p>
                      ))}
                      <code>{selectedRow.pin || selectedRow.revision}</code>
                    </details>
                  </>
                )}
                {selectedRow.reasons.map((reason, i) => (
                  <p key={i}>{reason}</p>
                ))}
              </>
            )}
            {tab === 'discover' && selectedRow && !preview && (
              <Button
                disabled={
                  busy ||
                  inspecting ||
                  selectedRow.compatibility === 'unsupported'
                }
                onClick={() => void inspect(selectedRow)}
              >
                Inspect integration
              </Button>
            )}
            {preview?.skill && (
              <>
                <SkillReview preview={preview.skill} />
                <Button
                  disabled={
                    busy || Boolean(pending) || preview.skill.scan.blocked
                  }
                  onClick={() => installSkill(preview.skill!)}
                >
                  Add skill
                </Button>
              </>
            )}
            {preview?.plugin && (
              <>
                <p>{preview.plugin.description}</p>
                <p>{preview.plugin.evidence}</p>
                <p>
                  Version {preview.plugin.version || 'not declared'} · License{' '}
                  {preview.plugin.license || 'not declared'}
                </p>
                <ul>
                  {preview.plugin.skills.map((s) => (
                    <li key={s.name}>Skill: {s.name}</li>
                  ))}
                  {preview.plugin.servers.map((s) => (
                    <li key={s.key}>
                      MCP: {s.key} · {s.transport}
                    </li>
                  ))}
                </ul>
                {preview.plugin.diagnostics.map((d, i) => (
                  <p key={i}>
                    {d.component}: {d.reason}
                  </p>
                ))}
                <Button
                  disabled={busy || Boolean(pending)}
                  onClick={() =>
                    void run(() =>
                      packageAction(
                        detail?.installed ? 'update' : 'install',
                        preview.plugin!.plugin_id,
                        preview.plugin!.preview_id,
                      ),
                    )
                  }
                >
                  {detail?.installed ? 'Review update' : 'Add package'}
                </Button>
                <details>
                  <summary>Source and revision</summary>
                  <p>{preview.plugin.source}</p>
                  <code>
                    {preview.plugin.pin || preview.plugin.tree_digest}
                  </code>
                </details>
              </>
            )}
            {preview?.kind === 'native' && (
              <Button
                onClick={() =>
                  void run(() => packageAction('install', preview.plugin_id!))
                }
              >
                Review installation
              </Button>
            )}
            {preview?.mcp && (
              <>
                <Field label="Connection name">
                  <Input
                    value={connectionName}
                    onChange={(e) => setConnectionName(e.target.value)}
                    maxLength={128}
                  />
                </Field>
                <p>
                  Review the destination and execution settings before saving.
                  Metadata inspection has not executed this connection.
                  Subscription and cost requirements are unknown unless
                  explicitly stated below.
                </p>
                <details>
                  <summary>
                    Connection destination and reviewed configuration
                  </summary>
                  <pre className="text-preview">{preview.mcp.import_json}</pre>
                </details>
                {preview.mcp.notes.map((note, i) => (
                  <p key={i}>{note}</p>
                ))}
                <Button
                  disabled={busy || Boolean(pending) || !connectionName}
                  onClick={() => void run(addMcp)}
                >
                  {setupAction(preview.mcp.import_json)}
                </Button>
              </>
            )}
            {tab === 'my' && selectedRow && !preview && (
              <>
                <ProfileContext />
                <TryInChat item={selectedRow} />
                {selectedRow.kind === 'mcp' ? (
                  <McpSetup
                    key={selectedRow.id}
                    item={selectedRow}
                    onChanged={refreshDetail}
                  />
                ) : selectedRow.installed && selectedRow.kind === 'skill' ? (
                  <SkillSetup
                    key={selectedRow.id}
                    item={selectedRow}
                    onChanged={refreshDetail}
                    onAdvanced={() => setAdvanced('skill')}
                  />
                ) : selectedRow.installed && selectedRow.kind === 'plugin' ? (
                  <PluginSetup
                    key={selectedRow.id}
                    item={selectedRow}
                    onChanged={refreshDetail}
                    onAdvanced={() => setAdvanced('plugin')}
                  />
                ) : null}
                {selectedRow.kind === 'skill' &&
                  selectedRow.installed &&
                  [
                    'github',
                    'clawhub',
                    'skills_sh',
                    'browse_sh',
                    'lobehub',
                  ].includes(selectedRow.source) && (
                    <SkillMaintenance
                      item={selectedRow}
                      onChanged={refreshDetail}
                    />
                  )}
                {selectedRow.kind === 'mcp' && (
                  <Button
                    disabled={setupOperations.blocked}
                    onClick={() => setAdvanced('mcp')}
                  >
                    Advanced connection configuration
                  </Button>
                )}
                {selectedRow.kind === 'plugin' && (
                  <div className="button-row">
                    {(
                      [
                        'prepare',
                        'update',
                        'restore',
                        'recover',
                        'remove',
                        'purge',
                      ] as const
                    )
                      .filter(
                        (action) =>
                          selectedRow.actions.includes(action) ||
                          (action === 'remove' && selectedRow.installed),
                      )
                      .map((action) => (
                        <Button
                          key={action}
                          disabled={
                            busy || Boolean(pending) || setupOperations.blocked
                          }
                          onClick={() =>
                            void run(() =>
                              packageAction(action, selectedRow.owner_ref),
                            )
                          }
                        >
                          {action === 'prepare'
                            ? 'Prepare native environment'
                            : action === 'update'
                              ? 'Update package'
                              : action === 'restore'
                                ? 'Restore previous version'
                                : action === 'recover'
                                  ? 'Recover publication'
                                  : action === 'purge'
                                    ? 'Delete saved data'
                                    : 'Remove package'}
                        </Button>
                      ))}
                    {selectedRow.source_url && (
                      <Button
                        disabled={setupOperations.blocked}
                        onClick={() =>
                          void run(async () => {
                            setPreview(
                              await controller.previewIntegration({
                                kind: 'plugin',
                                reference: selectedRow.source_url,
                              }),
                            );
                          })
                        }
                      >
                        Check for updates
                      </Button>
                    )}
                  </div>
                )}
                <details>
                  <summary>Details and provenance</summary>
                  <p>{selectedRow.evidence}</p>
                  <p>License: {selectedRow.license || 'not declared'}</p>
                  <code>{selectedRow.pin || selectedRow.revision}</code>
                  {selectedRow.source_url && (
                    <p>
                      <a
                        href={selectedRow.source_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Publisher source
                      </a>
                    </p>
                  )}
                </details>
              </>
            )}
          </section>
        )}
      </div>
      {!selected && !preview && (
        <details className="integrations-advanced">
          <summary>Advanced configuration and existing editors</summary>
          <div className="button-row">
            {type && type !== 'all' && (
              <Button onClick={() => update({ view: 'catalogs' })}>
                Catalogs
              </Button>
            )}
            <Button
              onClick={() => update({ type: 'all', tab: 'my', selected: '' })}
            >
              All types inventory
            </Button>
            {(['skill', 'mcp', 'plugin'] as const).map((kind) => (
              <Button key={kind} onClick={() => setAdvanced(kind)}>
                {kind === 'skill'
                  ? 'Create, import, pin and maintain skills'
                  : kind === 'mcp'
                    ? 'Custom MCP configuration and chat access'
                    : 'Native plugin configuration'}
              </Button>
            ))}
          </div>
        </details>
      )}
      <ModalTask
        description="Inspect a supported source before adding it."
        open={add}
        onOpenChange={setAdd}
        title="Add from link or file"
        ariaLabel="Add from link or file"
      >
        <div className="stack">
          {importKind !== 'plugin' && (
            <Button
              onClick={() => {
                setAdd(false);
                setAdvanced(importKind);
              }}
            >
              {importKind === 'skill'
                ? 'Import a skill file or create a skill'
                : 'Import custom MCP configuration'}
            </Button>
          )}
          <Field label="Import type">
            <Select
              value={importKind}
              onChange={(e) =>
                setImportKind(e.target.value as typeof importKind)
              }
            >
              <option value="plugin">Portable or native package</option>
              <option value="skill">Skill link</option>
              <option value="mcp">MCP endpoint</option>
            </Select>
          </Field>
          <Field
            label={local ? 'Authorized local folder or archive' : 'Source link'}
          >
            <Input
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              maxLength={2048}
            />
          </Field>
          {importKind === 'plugin' && (
            <Field label="Import from this computer" layout="row">
              <Toggle
                label="Import from this computer"
                checked={local}
                onChange={(e) => setLocal(e.target.checked)}
              />
            </Field>
          )}
          <p>
            Inspection contacts the specified public source or reads the
            authorized local path. Nothing is executed.
          </p>
          <Button
            disabled={busy || !reference}
            onClick={() => void run(importReference)}
          >
            Inspect source
          </Button>
        </div>
      </ModalTask>
      <ModalTask
        description="Review the source and effects before confirming."
        open={Boolean(review)}
        onOpenChange={(open) => {
          if (!open && !busy) setReview(null);
        }}
        title={review?.title ?? 'Review integration'}
        ariaLabel="Review integration change"
      >
        <ul>
          {review?.lines.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
        <Button
          disabled={busy}
          variant="primary"
          onClick={() =>
            void run(async () => {
              const current = review;
              setReview(null);
              await current?.apply();
            })
          }
        >
          Confirm
        </Button>
      </ModalTask>
    </div>
  );
}
