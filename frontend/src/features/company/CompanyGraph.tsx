import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowLeft,
  ArrowRight,
  Building2,
  Maximize2,
  RefreshCw,
  Search,
  Unplug,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import type { ClientController } from '../../api/controller';
import type {
  CompanyEntityRelation,
  CompanyEntityView,
  CompanyGraphView,
  CompanyView,
  KnowledgeGraphEdge,
  KnowledgeGraphNode,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { humanizeToken } from '../../ui/format';
import { Drawer } from '../../ui/overlays';
import {
  Button,
  IconButton,
  Select,
  Toolbar,
  ToolbarSeparator,
} from '../../ui/primitives';
import KnowledgeGraphCanvas, {
  type KnowledgeGraphHandle,
} from '../home/KnowledgeGraphCanvas';
import KnowledgeList from '../home/KnowledgeList';
import { plural } from '../home/home-format';
import { typeSlot } from '../home/knowledge-palette';
import {
  EDITED_IN_CONSOLE,
  NO_DOCUMENTS,
  NO_MATCH,
  citationWords,
  disconnectedWords,
  lockdownWords,
  showingWords,
  unreachableWords,
} from './words';

/** The most connected entities drawn at once (the server's default). */
const LIMIT = 300;
/** Typing settles this long before the company is asked. */
const SEARCH_MS = 300;
const NO_NODES: KnowledgeGraphNode[] = [];
const NO_EDGES: KnowledgeGraphEdge[] = [];

type Detail =
  | { state: 'loading' }
  | { state: 'ready'; value: CompanyEntityView }
  | { state: 'error'; error: string };

function TypeDot({ type }: { type: string }) {
  return (
    <span
      className="knowledge-type-dot"
      data-slot={typeSlot(type)}
      aria-hidden
    />
  );
}

/**
 * Knowledge home's Company scope (Row-Bot for Teams, B8): the company's
 * knowledge graph, filtered by its server to the spaces the person may read,
 * drawn with the personal graph's canvas. Read only: owners curate it in the
 * admin console. Filters (a space, a search) are asked of the server.
 */
export default function CompanyGraph({
  controller,
  company,
}: {
  controller: ClientController;
  company: CompanyView;
}) {
  const name = company.company ?? 'your company';
  const [space, setSpace] = useState('');
  const [typed, setTyped] = useState('');
  const [query, setQuery] = useState('');
  const [reload, setReload] = useState(0);
  const [graph, setGraph] = useState<CompanyGraphView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [graphStatus, setGraphStatus] = useState<
    'loading' | 'ready' | 'failed'
  >('loading');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [stage, setStage] = useState<HTMLDivElement | null>(null);
  const graphRef = useRef<KnowledgeGraphHandle>(null);
  const ticket = useRef(0);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(typed.trim()), SEARCH_MS);
    return () => window.clearTimeout(timer);
  }, [typed]);

  useEffect(() => {
    const abort = new AbortController();
    setLoading(true);
    setError('');
    controller
      .companyGraph(
        { spaces: space ? [space] : [], q: query, limit: LIMIT },
        abort.signal,
      )
      .then(
        (next) => {
          if (abort.signal.aborted) return;
          setGraph(next);
          setLoading(false);
        },
        (cause) => {
          if (abort.signal.aborted) return;
          setError(clientError(cause).message);
          setLoading(false);
        },
      );
    return () => abort.abort();
  }, [controller, space, query, reload]);

  const readDetail = useCallback(
    async (id: string) => {
      const mine = ++ticket.current;
      setDetail({ state: 'loading' });
      try {
        const value = await controller.companyEntity(id);
        if (ticket.current === mine) setDetail({ state: 'ready', value });
      } catch (cause) {
        if (ticket.current === mine)
          setDetail({ state: 'error', error: clientError(cause).message });
      }
    },
    [controller],
  );

  const nodes = graph?.nodes ?? NO_NODES;
  const edges = graph?.edges ?? NO_EDGES;
  const visible = useMemo(() => new Set(nodes.map((node) => node.id)), [nodes]);
  const types = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of nodes)
      counts.set(node.entity_type, (counts.get(node.entity_type) ?? 0) + 1);
    return [...counts].sort(
      (left, right) => right[1] - left[1] || left[0].localeCompare(right[0]),
    );
  }, [nodes]);

  function select(id: string | null) {
    setSelectedId(id);
    if (id) void readDetail(id);
    else {
      ticket.current += 1;
      setDetail(null);
    }
  }

  function open(id: string) {
    select(id);
    if (visible.has(id))
      requestAnimationFrame(() => graphRef.current?.focus(id));
  }

  function clearFilters() {
    setSpace('');
    setTyped('');
    setQuery('');
  }

  async function retry() {
    setBusy(true);
    try {
      // Locked down: the company's rules are read again first (C3a's Retry).
      if (graph?.state === 'locked_down') await controller.company('check');
    } catch {
      // The graph's own read says what is wrong.
    } finally {
      setBusy(false);
      setReload((value) => value + 1);
    }
  }

  // The graph read, when the company could be asked.
  const ready = graph?.state === 'ready' ? graph : null;
  const filtered = Boolean(space || query);
  const hasGraph = ready !== null && nodes.length > 0;
  // A space no longer readable is left out by the server: the picker says so.
  const shownSpace =
    ready && space && !ready.space_ids.includes(space) ? '' : space;
  const owned = new Set(
    (graph?.spaces ?? [])
      .filter((item) => item.is_owner)
      .map((item) => item.id),
  );
  const selectedNode = nodes.find((node) => node.id === selectedId) ?? null;
  const view = detail?.state === 'ready' ? detail.value : null;
  const entity = view?.entity ?? null;
  const stateWords = (
    state: CompanyGraphView['state'],
    reason: string | null,
  ) =>
    state === 'locked_down'
      ? lockdownWords({ company: name, policy_reason: null })
      : state === 'unreachable'
        ? unreachableWords(name, reason)
        : '';

  return (
    <section
      className="knowledge-home company-graph"
      aria-labelledby="company-graph-title"
      data-view="graph"
    >
      <h2 id="company-graph-title" className="visually-hidden">
        {`${name}’s knowledge`}
      </h2>
      <div className="knowledge-stage" ref={setStage}>
        {error && (
          <div className="knowledge-stage-message" role="alert">
            <strong>Company knowledge could not be refreshed</strong>
            <p>{error}</p>
            <Button className="small" onClick={() => void retry()}>
              Try again
            </Button>
          </div>
        )}
        {loading && !graph && !error && (
          <p className="knowledge-stage-message" role="status">
            Loading your company’s knowledge…
          </p>
        )}
        {graph && !error && graph.state === 'disconnected' && (
          <div className="knowledge-stage-message">
            <Unplug size={22} aria-hidden />
            <strong>Your company disconnected this Row-Bot</strong>
            <p>{disconnectedWords(name)}</p>
            <Link className="button small" to="/settings/company">
              Sign in again
            </Link>
          </div>
        )}
        {graph &&
          !error &&
          (graph.state === 'locked_down' || graph.state === 'unreachable') && (
            <div className="knowledge-stage-message">
              <Building2 size={22} aria-hidden />
              <strong>{`Can’t show ${name}’s knowledge right now`}</strong>
              <p>{stateWords(graph.state, graph.reason)}</p>
              <IconButton
                label={graph.state === 'locked_down' ? 'Retry' : 'Try again'}
                disabled={busy}
                onClick={() => void retry()}
              >
                <RefreshCw size={16} aria-hidden />
              </IconButton>
            </div>
          )}
        {ready && !error && nodes.length === 0 && (
          <div className="knowledge-stage-message">
            <Building2 size={22} aria-hidden />
            <strong>{filtered ? NO_MATCH : NO_DOCUMENTS}</strong>
            {filtered ? (
              <Button className="small" onClick={clearFilters}>
                Show everything
              </Button>
            ) : (
              <p>
                People, teams, projects and how they connect appear here once{' '}
                {name} adds documents.
              </p>
            )}
          </div>
        )}
        {hasGraph &&
          (graphStatus !== 'failed' ? (
            <KnowledgeGraphCanvas
              ref={graphRef}
              nodes={nodes}
              edges={edges}
              visible={visible}
              selectedId={selectedId}
              onSelect={select}
              onStatus={setGraphStatus}
            />
          ) : (
            <div className="knowledge-list-frame">
              <p className="knowledge-list-note" role="status">
                The interactive graph needs WebGL, which is unavailable here.
                Everything is listed instead.
              </p>
              <KnowledgeList
                nodes={nodes}
                selectedId={selectedId}
                onSelect={select}
              />
            </div>
          ))}
        {ready && (hasGraph || filtered) && (
          <Toolbar
            floating
            placement="top-left"
            label="Company knowledge controls"
            className="knowledge-controls"
          >
            <div className="knowledge-search">
              <Search size={14} aria-hidden className="knowledge-search-icon" />
              <input
                type="search"
                className="knowledge-search-input"
                aria-label="Search company knowledge"
                placeholder="Search names"
                value={typed}
                maxLength={160}
                onChange={(event) => setTyped(event.currentTarget.value)}
              />
            </div>
            {ready.spaces.length > 1 && (
              <>
                <ToolbarSeparator />
                <Select
                  aria-label="Space"
                  className="company-space-picker"
                  value={shownSpace}
                  onChange={(event) => setSpace(event.currentTarget.value)}
                >
                  <option value="">All spaces</option>
                  {ready.spaces.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </Select>
              </>
            )}
            <ToolbarSeparator />
            <IconButton
              size="sm"
              label="Refresh company knowledge"
              disabled={loading}
              onClick={() => setReload((value) => value + 1)}
            >
              <RefreshCw size={15} aria-hidden />
            </IconButton>
          </Toolbar>
        )}
        {hasGraph && types.length > 0 && (
          <ul className="knowledge-legend" aria-label="Entity types">
            {types.map(([type, count]) => (
              <li key={type} className="knowledge-legend-item">
                <TypeDot type={type} />
                <span className="knowledge-legend-name">
                  {humanizeToken(type)}
                </span>
                <span className="knowledge-legend-count">{count}</span>
              </li>
            ))}
          </ul>
        )}
        {hasGraph && graphStatus === 'ready' && (
          <Toolbar
            floating
            placement="bottom-right"
            orientation="vertical"
            label="Graph navigation"
            className="knowledge-navigation"
          >
            <IconButton
              size="sm"
              label="Zoom in"
              onClick={() => graphRef.current?.zoom(0.25)}
            >
              <ZoomIn size={15} aria-hidden />
            </IconButton>
            <IconButton
              size="sm"
              label="Zoom out"
              onClick={() => graphRef.current?.zoom(-0.2)}
            >
              <ZoomOut size={15} aria-hidden />
            </IconButton>
            <IconButton
              size="sm"
              label="Fit"
              className="knowledge-graph-fit"
              onClick={() => graphRef.current?.fit()}
            >
              <Maximize2 size={14} aria-hidden />
            </IconButton>
          </Toolbar>
        )}
        {ready && hasGraph && (
          <p className="knowledge-caption">
            <span>{plural(ready.total_entities, 'entity', 'entities')}</span>
            <span aria-hidden>·</span>
            <span>{plural(ready.total_relations, 'link')}</span>
            {ready.truncated && (
              <>
                <span aria-hidden>·</span>
                <span>
                  {showingWords(ready.shown_entities, ready.total_entities)}
                </span>
              </>
            )}
          </p>
        )}
        {stage && (
          <Drawer
            open={Boolean(selectedId)}
            onOpenChange={(next) => {
              if (!next) select(null);
            }}
            container={stage}
            title={entity?.subject ?? selectedNode?.subject ?? ''}
            description={
              entity
                ? `${humanizeToken(entity.entity_type)} · ${entity.space_name}`
                : selectedNode
                  ? humanizeToken(selectedNode.entity_type)
                  : undefined
            }
            closeLabel="Close entity detail"
            className="knowledge-inspector"
          >
            {selectedId && (
              <div
                className="knowledge-detail"
                role="region"
                aria-label="Selected entity detail"
              >
                {detail?.state === 'loading' && (
                  <p className="home-caption" role="status">
                    Loading…
                  </p>
                )}
                {detail?.state === 'error' && (
                  <div className="task-builder-alert" role="alert">
                    <strong>Details unavailable</strong>
                    <p>{detail.error}</p>
                    <Button
                      className="small"
                      onClick={() => void readDetail(selectedId)}
                    >
                      Try again
                    </Button>
                  </div>
                )}
                {view && view.state !== 'ready' && (
                  <p className="home-caption">
                    {view.state === 'not_found'
                      ? `This is no longer in ${name}’s knowledge.`
                      : view.state === 'disconnected'
                        ? 'Your company disconnected this Row-Bot.'
                        : stateWords(
                            view.state === 'locked_down'
                              ? 'locked_down'
                              : 'unreachable',
                            view.reason,
                          )}
                  </p>
                )}
                {(entity?.description ?? selectedNode?.description) && (
                  <p className="knowledge-detail-summary">
                    {entity?.description ?? selectedNode?.description}
                  </p>
                )}
                {entity && entity.aliases.length > 0 && (
                  <p className="knowledge-detail-chips">
                    {entity.aliases.map((alias) => (
                      <span key={alias} className="knowledge-chip">
                        {alias}
                      </span>
                    ))}
                  </p>
                )}
                {entity && owned.has(entity.space_id) && (
                  <p className="home-caption">{EDITED_IN_CONSOLE}</p>
                )}
                {view?.state === 'ready' && (
                  <Connections relations={view.relations} onOpen={open} />
                )}
                {view?.state === 'ready' && view.mentions.length > 0 && (
                  <section
                    className="knowledge-connections company-citations"
                    aria-label="Mentioned in"
                  >
                    <h3>
                      Mentioned in{' '}
                      <span className="overview-count">
                        {view.mention_count}
                      </span>
                    </h3>
                    <ul>
                      {view.mentions.map((citation, index) => (
                        <li key={index} className="company-citation">
                          <p className="company-citation-source">
                            {citationWords(citation)}
                          </p>
                          <p className="company-citation-excerpt">
                            {citation.excerpt}
                          </p>
                        </li>
                      ))}
                    </ul>
                    {view.mention_count > view.mentions.length && (
                      <p className="home-caption">
                        {plural(
                          view.mention_count - view.mentions.length,
                          'more mention',
                        )}
                      </p>
                    )}
                  </section>
                )}
              </div>
            )}
          </Drawer>
        )}
      </div>
    </section>
  );
}

/** An entity's connections, by kind; each opens its peer. */
function Connections({
  relations,
  onOpen,
}: {
  relations: readonly CompanyEntityRelation[];
  onOpen: (id: string) => void;
}) {
  const groups = useMemo(() => {
    const map = new Map<string, CompanyEntityRelation[]>();
    for (const relation of relations) {
      const key = humanizeToken(relation.relation_type) || 'Related';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(relation);
    }
    return [...map].sort((left, right) => right[1].length - left[1].length);
  }, [relations]);
  if (!relations.length)
    return <p className="home-caption">No connections yet.</p>;
  return (
    <section className="knowledge-connections" aria-label="Connections">
      <h3>
        Connections <span className="overview-count">{relations.length}</span>
      </h3>
      {groups.map(([label, items]) => (
        <div key={label} className="knowledge-connection-group">
          <h4>{label}</h4>
          <ul>
            {items.map((relation) => (
              <li key={`${relation.direction}-${relation.peer_id}`}>
                <button
                  type="button"
                  className="knowledge-connection"
                  onClick={() => onOpen(relation.peer_id)}
                >
                  {relation.direction === 'incoming' ? (
                    <ArrowLeft size={12} aria-hidden />
                  ) : (
                    <ArrowRight size={12} aria-hidden />
                  )}
                  <span className="visually-hidden">
                    {relation.direction === 'incoming' ? 'from ' : 'to '}
                  </span>
                  <span>{relation.peer_subject}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
