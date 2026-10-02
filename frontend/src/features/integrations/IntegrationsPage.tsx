import {
  useCallback,
  useEffect,
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
  IntegrationSearchRequest,
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
import './integrations.css';

const statusLabels: Record<string, string> = {
  ready: 'Ready',
  off: 'Off',
  setup: 'Setup needed',
  attention: 'Needs attention',
  missing_runtime: 'Runtime needed',
  disconnected: 'Disconnected',
  recovery: 'Recovery needed',
  retained: 'Saved data retained',
  discover: 'Available to inspect',
};
type Review = { title: string; lines: string[]; apply: () => Promise<void> };
type Pending = { kind: 'skill' | 'plugin' | 'mcp'; id: string };
const pendingScopes = ['skill', 'plugin', 'mcp'] as const;

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
  const tab = params.get('tab') === 'discover' ? 'discover' : 'my';
  const type = ['skill', 'mcp', 'plugin'].includes(params.get('type') ?? '')
    ? params.get('type')!
    : 'all';
  const query = params.get('q') ?? '';
  const selected = params.get('selected') ?? '';
  const source = (
    [
      'recommended',
      'hermes',
      'hermes_mcp',
      'clawhub',
      'skills_sh',
      'official',
      'native',
    ].includes(params.get('source') ?? '')
      ? params.get('source')
      : 'recommended'
  ) as NonNullable<IntegrationSearchRequest['sources']>[number];
  const [draftQuery, setDraftQuery] = useState(query);
  const [page, setPage] = useState<IntegrationPage | null>(null);
  const [preview, setPreview] = useState<IntegrationPreview | null>(null);
  const [detail, setDetail] = useState<IntegrationItem | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [review, setReview] = useState<Review | null>(null);
  const [unsupported, setUnsupported] = useState(false);
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
  const update = (values: Record<string, string>) => {
    const next = new URLSearchParams(params);
    Object.entries(values).forEach(([key, value]) =>
      value ? next.set(key, value) : next.delete(key),
    );
    setParams(next);
  };
  const load = useCallback(
    async (refresh = false, cursor?: string) => {
      const request = ++generation.current;
      setLoading(true);
      try {
        const result =
          tab === 'my'
            ? await controller.integrations({ query, kind: type, cursor })
            : await controller.searchIntegrations({
                query,
                sources: [source],
                refresh,
              });
        if (request !== generation.current) return;
        setPage((old) =>
          cursor && old?.revision === result.revision
            ? { ...result, items: [...old.items, ...result.items] }
            : result,
        );
      } catch (e) {
        if (request === generation.current) setMessage(clientError(e).message);
      } finally {
        if (request === generation.current) setLoading(false);
      }
    },
    [controller, tab, type, query, source],
  );
  useEffect(() => {
    setDraftQuery(query);
    void load();
    return () => {
      // Numeric request generation, not a DOM ref; invalidate late responses.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      generation.current++;
    };
  }, [load, query]);
  useEffect(() => {
    setPreview(null);
    setDetail(null);
    if (!selected) {
      list.current?.focus({ preventScroll: true });
      return;
    }
    heading.current?.focus({ preventScroll: true });
    if (tab === 'my') {
      let active = true;
      void controller
        .integration(selected)
        .then((row) => {
          if (active) setDetail(row);
        })
        .catch((e) => {
          if (active) setMessage(clientError(e).message);
        });
      return () => {
        active = false;
      };
    }
  }, [controller, selected, tab]);
  useEffect(() => {
    if (detail || preview) heading.current?.focus({ preventScroll: true });
  }, [detail, preview]);
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
    if (result.settled) remember(null);
    await load();
  };
  const inspect = async (item: IntegrationItem) => {
    if (!page) return;
    const result = await controller.previewIntegration({
      revision: page.revision,
      item_id: item.id,
    });
    setPreview(result);
    if (result.mcp) setConnectionName(result.mcp.name);
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
        await controller.reconcileIntegrationOperation('skill', id);
        retainCommand('integrations:skill', '');
        setPending(null);
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
    const value = await controller.reviewPluginLifecycle(
      action,
      pluginId,
      undefined,
      previewId,
    );
    setReview({
      title: `${action === 'install' ? 'Add' : action === 'purge' ? 'Delete saved data for' : action} ${value.name}`,
      lines: [
        ...value.disclosures,
        `Version: ${value.version || 'not declared'}`,
        `Source: ${value.source}`,
        `Digest: ${value.checksum || 'local revision review'}`,
      ],
      apply: async () => {
        const id = crypto.randomUUID();
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
      },
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
  const rows = (page?.items ?? []).filter(
    (item) =>
      (type === 'all' ||
        item.kind === type ||
        item.children.some((child) => child.kind === type)) &&
      (tab === 'my' || unsupported || item.compatibility !== 'unsupported'),
  );
  const refreshDetail = async (removed = false) => {
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
        {renderAdvanced(advanced)}
      </section>
    );
  return (
    <div className="integrations-page stack" aria-busy={busy}>
      <div className="button-row" aria-label="Integration views">
        <Button
          variant={tab === 'my' ? 'primary' : 'secondary'}
          aria-pressed={tab === 'my'}
          onClick={() => update({ tab: 'my', selected: '' })}
        >
          My integrations
        </Button>
        <Button
          variant={tab === 'discover' ? 'primary' : 'secondary'}
          aria-pressed={tab === 'discover'}
          onClick={() => update({ tab: 'discover', selected: '' })}
        >
          Discover
        </Button>
        <Button onClick={() => setAdd(true)}>Add integration</Button>
      </div>
      <form
        className="integrations-filters"
        onSubmit={(e) => {
          e.preventDefault();
          update({ q: draftQuery, selected: '' });
          if (draftQuery === query) void load(tab === 'discover');
        }}
      >
        <Input
          aria-label="Search integrations"
          value={draftQuery}
          onChange={(e) => setDraftQuery(e.target.value)}
          maxLength={256}
          placeholder="Search integrations"
        />
        <Select
          aria-label="Integration type"
          value={type}
          onChange={(e) => update({ type: e.target.value, selected: '' })}
        >
          <option value="all">All types</option>
          <option value="skill">Skills</option>
          <option value="mcp">MCP connections</option>
          <option value="plugin">Packages</option>
        </Select>
        {tab === 'discover' && (
          <Select
            aria-label="Discovery source"
            value={source}
            onChange={(e) => update({ source: e.target.value, selected: '' })}
          >
            {[
              'recommended',
              'hermes',
              'hermes_mcp',
              'clawhub',
              'skills_sh',
              'official',
              'native',
            ].map((value) => (
              <option key={value} value={value}>
                {value === 'hermes_mcp'
                  ? 'Hermes MCP recipes'
                  : value === 'official'
                    ? 'Official MCP Registry'
                    : value === 'skills_sh'
                      ? 'skills.sh'
                      : value[0].toUpperCase() + value.slice(1)}
              </option>
            ))}
          </Select>
        )}
        <Button type="submit">Search</Button>
        {tab === 'discover' && (
          <Button
            disabled={loading}
            onClick={() =>
              void (source === 'native'
                ? run(() => packageAction('refresh', ''))
                : load(true))
            }
          >
            Search public source
          </Button>
        )}
      </form>
      {tab === 'discover' && (
        <p className="muted">
          Recommendations and saved results load locally. Search public source
          contacts only the selected source.
        </p>
      )}
      {message && <p role="status">{message}</p>}
      {pending && (
        <Button disabled={busy} onClick={() => void run(recover)}>
          Check original {pending.kind} operation
        </Button>
      )}
      {page?.sources.map((value, i) => (
        <p className="muted" key={`${value.source}:${i}`}>
          {value.source}: {value.message || value.status}
          {value.fetched_at
            ? ` · Saved ${new Date(value.fetched_at * 1000).toLocaleString()}`
            : ''}
        </p>
      ))}
      {tab === 'discover' && (
        <Toggle
          label="Include unsupported entries"
          checked={unsupported}
          onChange={(e) => setUnsupported(e.target.checked)}
        />
      )}
      <div
        className={`integrations-layout ${selected || preview ? 'has-detail' : ''}`}
      >
        <div
          ref={list}
          tabIndex={-1}
          className="integrations-list"
          aria-label="Integrations"
        >
          {loading && !page && <Skeleton label="Loading integrations" />}
          {!loading && !rows.length && (
            <EmptyState
              title={
                tab === 'my' ? 'No matching integrations' : 'No saved results'
              }
            >
              {tab === 'my'
                ? 'Add an integration or choose another filter.'
                : 'Choose a source and search when you want to look online.'}
            </EmptyState>
          )}
          {rows.map((item) => (
            <Button
              className="integration-row"
              aria-label={item.name}
              key={item.id}
              aria-pressed={selected === item.id}
              onClick={() => update({ selected: item.id })}
            >
              <span>
                <strong>{item.name}</strong>
                <small>{item.description}</small>
                <small>
                  {item.kind} · {item.source}
                  {item.children.length
                    ? ` · ${item.children.length} included`
                    : ''}
                </small>
              </span>
              <span className="integration-status">
                {statusLabels[item.status] ?? item.status}
              </span>
            </Button>
          ))}
          {page?.next_cursor && tab === 'my' && (
            <Button
              disabled={loading}
              onClick={() => void load(false, page.next_cursor!)}
            >
              Show more
            </Button>
          )}
        </div>
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
                  {statusLabels[selectedRow.status]} ·{' '}
                  {selectedRow.publisher || selectedRow.source}
                </p>
                {selectedRow.reasons.map((reason, i) => (
                  <p key={i}>{reason}</p>
                ))}
              </>
            )}
            {tab === 'discover' && selectedRow && !preview && (
              <Button
                disabled={busy || selectedRow.compatibility === 'unsupported'}
                onClick={() => void run(() => inspect(selectedRow))}
              >
                Inspect integration
              </Button>
            )}
            {preview?.skill && (
              <>
                <pre className="text-preview">{preview.skill.primary_text}</pre>
                <details>
                  <summary>
                    {preview.skill.files.length} included files and checks
                  </summary>
                  <ul>
                    {preview.skill.files.map((file) => (
                      <li key={file}>{file}</li>
                    ))}
                  </ul>
                  {preview.skill.scan.findings.map((f, i) => (
                    <p key={i}>{f.message}</p>
                  ))}
                </details>
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
                {preview.mcp.notes.map((note, i) => (
                  <p key={i}>{note}</p>
                ))}
                <Button
                  disabled={busy || Boolean(pending) || !connectionName}
                  onClick={() => void run(addMcp)}
                >
                  Add connection
                </Button>
              </>
            )}
            {tab === 'my' && selectedRow && !preview && (
              <>
                {selectedRow.kind === 'mcp' ? (
                  <McpSetup
                    key={selectedRow.id}
                    item={selectedRow}
                    onChanged={refreshDetail}
                  />
                ) : selectedRow.installed ? (
                  renderDetail(selectedRow, refreshDetail)
                ) : null}
                {selectedRow.children.length > 0 && (
                  <section className="stack">
                    <h3>Included with {selectedRow.name}</h3>
                    {selectedRow.children.map((child) => (
                      <details key={child.id}>
                        <summary>
                          {child.name} · {child.kind}
                        </summary>
                        {child.kind === 'mcp' ? (
                          <McpSetup item={child} onChanged={refreshDetail} />
                        ) : (
                          <p>
                            Supplied by this package. Its files and availability
                            follow the parent package.
                          </p>
                        )}
                      </details>
                    ))}
                  </section>
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
                          disabled={busy || Boolean(pending)}
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
      <details className="integrations-advanced">
        <summary>Advanced configuration and existing editors</summary>
        <div className="button-row">
          {(['skill', 'mcp'] as const).map((kind) => (
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
      <ModalTask
        description="Inspect a supported source before adding it."
        open={add}
        onOpenChange={setAdd}
        title="Add integration"
        ariaLabel="Add integration"
      >
        <div className="stack">
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
            <Toggle
              label="Import from this computer"
              checked={local}
              onChange={(e) => setLocal(e.target.checked)}
            />
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
