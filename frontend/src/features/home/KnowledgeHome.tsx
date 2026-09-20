import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';
import { Button, Field, Input, Select } from '../../ui/primitives';

export type KnowledgeGraphNode = {
  id: string;
  revision: string;
  subject: string;
  description: string;
  entity_type: string;
  source: string;
  updated_at: string;
  relation_count: number;
  orphan: boolean;
  is_user: boolean;
};

export type KnowledgeGraphEdge = {
  id: string;
  source_id: string;
  target_id: string;
  relation_type: string;
  updated_at: string;
};

export type KnowledgeGraphSnapshot = {
  schema_version: 1;
  availability: 'available' | 'missing' | 'unavailable' | 'corrupt';
  revision: string;
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  total_entities: number;
  total_relations: number;
  shown_entities: number;
  shown_relations: number;
  truncated: boolean;
  center_id: string | null;
  entity_types: string[];
  sources: string[];
};

export type KnowledgeNodeRelation = {
  relation_type: string;
  direction?: string;
  peer_id?: string;
  peer_subject?: string;
};

export type KnowledgeNodeDetail = {
  id: string;
  revision?: string;
  subject: string;
  description?: string;
  entity_type?: string;
  source?: string;
  updated_at?: string;
  relation_count?: number;
  status?: string;
  tier?: string;
  confidence?: number | null;
  aliases?: string[];
  tags?: string[];
  relations?: KnowledgeNodeRelation[];
};

export type KnowledgeDreamState = {
  available: boolean;
  enabled: boolean;
  state: 'idle' | 'reviewing' | 'running' | 'success' | 'error';
  message: string;
};

export type KnowledgeHomeProps = {
  snapshot: KnowledgeGraphSnapshot | null;
  loading: boolean;
  error: string;
  reload: () => void;
  loadDetail: (id: string) => Promise<KnowledgeNodeDetail>;
  onEdit: (id: string) => void;
  dream: KnowledgeDreamState;
  onDream: () => void | Promise<void>;
};

type PositionedNode = KnowledgeGraphNode & { x: number; y: number };
type DetailRecord = {
  state: 'loading' | 'ready' | 'error';
  value?: KnowledgeNodeDetail;
  error?: string;
};

const WIDTH = 960;
const HEIGHT = 500;
const CENTER_X = WIDTH / 2;
const CENTER_Y = HEIGHT / 2;

const TYPE_COLORS: Record<string, string> = {
  person: '#4fc3f7',
  preference: '#ce93d8',
  fact: '#81c784',
  event: '#ffb74d',
  place: '#4dd0e1',
  project: '#7986cb',
  organization: '#f06292',
  user: '#ffd54f',
};

function hashColor(value: string) {
  let hash = 0;
  for (const character of value)
    hash = (hash * 31 + character.charCodeAt(0)) | 0;
  const palette = [
    '#90caf9',
    '#a5d6a7',
    '#ffcc80',
    '#b39ddb',
    '#80cbc4',
    '#ef9a9a',
  ];
  return palette[Math.abs(hash) % palette.length];
}

function colorForType(type: string) {
  return TYPE_COLORS[type.toLowerCase()] ?? hashColor(type);
}

function recency(updatedAt: string) {
  const timestamp = Date.parse(updatedAt);
  if (!Number.isFinite(timestamp)) return 'unknown';
  const ageDays = (Date.now() - timestamp) / 86_400_000;
  if (ageDays <= 7) return 'recent';
  if (ageDays <= 30) return 'current';
  if (ageDays <= 90) return 'older';
  return 'stale';
}

function recencyStroke(value: ReturnType<typeof recency>) {
  if (value === 'recent') return '#ffd54f';
  if (value === 'current') return '#ffa726';
  if (value === 'older') return '#8d6e63';
  return '#68707f';
}

function compactLabel(value: string) {
  return value.length > 22 ? `${value.slice(0, 21)}…` : value;
}

function errorMessage(cause: unknown) {
  return cause instanceof Error
    ? cause.message
    : 'The entity detail could not be loaded.';
}

function layoutNodes(
  nodes: KnowledgeGraphNode[],
  centerId: string | null,
): PositionedNode[] {
  const ordered = [...nodes]
    .sort((left, right) => left.id.localeCompare(right.id))
    .slice(0, 250);
  const center = ordered.find((node) => node.id === centerId);
  const orbit = center
    ? ordered.filter((node) => node.id !== center.id)
    : ordered;
  const positioned = orbit.map((node, index) => {
    const ring = index < 16 ? 0 : Math.floor((index - 16) / 28) + 1;
    const start = ring === 0 ? 0 : 16 + (ring - 1) * 28;
    const count =
      ring === 0
        ? Math.min(16, orbit.length)
        : Math.min(28, orbit.length - start);
    const angle =
      ((index - start) / Math.max(count, 1)) * Math.PI * 2 - Math.PI / 2;
    const radiusX = Math.min(360, 175 + ring * 72);
    const radiusY = Math.min(205, 115 + ring * 42);
    return {
      ...node,
      x: CENTER_X + Math.cos(angle) * radiusX,
      y: CENTER_Y + Math.sin(angle) * radiusY,
    };
  });
  return center
    ? [{ ...center, x: CENTER_X, y: CENTER_Y }, ...positioned]
    : positioned;
}

function availabilityCopy(
  availability: KnowledgeGraphSnapshot['availability'],
) {
  if (availability === 'missing')
    return 'The knowledge graph has not been created yet.';
  if (availability === 'corrupt')
    return 'The knowledge graph could not be read safely.';
  return 'The knowledge graph is unavailable in this client context.';
}

function dreamLabel(dream: KnowledgeDreamState) {
  if (!dream.available) return 'Dream unavailable';
  if (!dream.enabled) return 'Dream disabled';
  if (dream.state === 'reviewing') return 'Run Dream Cycle';
  if (dream.state === 'running') return 'Dreaming…';
  if (dream.state === 'error') return 'Retry Dream';
  if (dream.state === 'success') return 'Dream again';
  return 'Review Dream';
}

export default function KnowledgeHome({
  snapshot,
  loading,
  error,
  reload,
  loadDetail,
  onEdit,
  dream,
  onDream,
}: KnowledgeHomeProps) {
  const [query, setQuery] = useState('');
  const [entityType, setEntityType] = useState('');
  const [source, setSource] = useState('');
  const [showUserHub, setShowUserHub] = useState(true);
  const [hideOrphans, setHideOrphans] = useState(false);
  const [view, setView] = useState<'graph' | 'list'>('graph');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, DetailRecord>>({});
  const [fitAnnouncement, setFitAnnouncement] = useState('');
  const [dreamError, setDreamError] = useState('');
  const requestTicket = useRef(0);
  const graphRef = useRef<SVGSVGElement>(null);
  const markerId = `${useId().replaceAll(':', '')}-knowledge-arrow`;

  useEffect(() => {
    setSelectedId(null);
    setDetails({});
    requestTicket.current += 1;
  }, [snapshot?.revision]);

  const allPositioned = useMemo(
    () => layoutNodes(snapshot?.nodes ?? [], snapshot?.center_id ?? null),
    [snapshot?.center_id, snapshot?.nodes],
  );

  const visibleNodes = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return allPositioned.filter((node) => {
      if (!showUserHub && node.is_user) return false;
      if (hideOrphans && node.orphan) return false;
      if (entityType && node.entity_type !== entityType) return false;
      if (source && node.source !== source) return false;
      if (
        needle &&
        !`${node.subject} ${node.description} ${node.entity_type} ${node.source}`
          .toLocaleLowerCase()
          .includes(needle)
      )
        return false;
      return true;
    });
  }, [allPositioned, entityType, hideOrphans, query, showUserHub, source]);

  const visibleIds = useMemo(
    () => new Set(visibleNodes.map((node) => node.id)),
    [visibleNodes],
  );
  const visibleEdges = useMemo(
    () =>
      (snapshot?.edges ?? []).filter(
        (edge) =>
          visibleIds.has(edge.source_id) && visibleIds.has(edge.target_id),
      ),
    [snapshot?.edges, visibleIds],
  );
  const positions = useMemo(
    () => new Map(allPositioned.map((node) => [node.id, node])),
    [allPositioned],
  );
  const selectedNode =
    snapshot?.nodes.find((node) => node.id === selectedId) ?? null;
  const selectedDetail = selectedId ? details[selectedId] : undefined;

  async function selectNode(id: string, force = false) {
    setSelectedId(id);
    if (!force && details[id]?.state === 'ready') return;
    const ticket = ++requestTicket.current;
    setDetails((current) => ({ ...current, [id]: { state: 'loading' } }));
    try {
      const value = await loadDetail(id);
      if (requestTicket.current !== ticket) return;
      setDetails((current) => ({
        ...current,
        [id]: { state: 'ready', value },
      }));
    } catch (cause) {
      if (requestTicket.current !== ticket) return;
      setDetails((current) => ({
        ...current,
        [id]: { state: 'error', error: errorMessage(cause) },
      }));
    }
  }

  function onNodeKeyDown(event: KeyboardEvent<SVGGElement>, id: string) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    void selectNode(id);
  }

  function showAll() {
    setQuery('');
    setEntityType('');
    setSource('');
    setShowUserHub(true);
    setHideOrphans(false);
    setFitAnnouncement('All memories and connections are shown.');
  }

  function fitGraph() {
    setFitAnnouncement(
      `Graph fitted to ${visibleNodes.length} ${visibleNodes.length === 1 ? 'memory' : 'memories'}.`,
    );
    graphRef.current?.focus({ preventScroll: true });
  }

  async function runDream() {
    setDreamError('');
    try {
      await onDream();
    } catch (cause) {
      setDreamError(errorMessage(cause));
    }
  }

  const dreamDisabled =
    !dream.available || !dream.enabled || dream.state === 'running';

  return (
    <section className="knowledge-home" aria-labelledby="knowledge-home-title">
      <header className="capability-header knowledge-home-header">
        <div>
          <h2 id="knowledge-home-title">Knowledge</h2>
          <p>
            Explore the memories and connections Row-Bot can use in
            conversation.
          </p>
        </div>
        <div className="action-cluster knowledge-home-actions">
          <Button
            className="knowledge-dream-action"
            disabled={dreamDisabled}
            onClick={() => void runDream()}
          >
            {dreamLabel(dream)}
          </Button>
          <Button onClick={reload} disabled={loading}>
            Refresh
          </Button>
        </div>
      </header>

      {(dream.message || dreamError) && (
        <p
          className={`knowledge-dream-status status-chip ${dream.state === 'error' || dreamError ? 'danger' : dream.state === 'success' ? 'success' : dream.state === 'reviewing' ? 'warning' : ''}`}
          role={dream.state === 'error' || dreamError ? 'alert' : 'status'}
        >
          {dreamError || dream.message}
        </p>
      )}
      {error && (
        <div className="state-message knowledge-home-error" role="alert">
          <div>
            <strong>Knowledge could not be refreshed</strong>
            <p>{error}</p>
            <Button onClick={reload}>Try again</Button>
          </div>
        </div>
      )}
      {loading && !snapshot && <p role="status">Loading knowledge graph…</p>}

      {snapshot && snapshot.availability !== 'available' && (
        <div className="empty-state knowledge-home-unavailable">
          <h3>Knowledge unavailable</h3>
          <p>{availabilityCopy(snapshot.availability)}</p>
          <Button onClick={reload}>Try again</Button>
        </div>
      )}

      {snapshot?.availability === 'available' &&
        snapshot.total_entities === 0 && (
          <div className="empty-state knowledge-home-empty">
            <span aria-hidden className="knowledge-home-empty-icon">
              ◎
            </span>
            <h3>Your memory map is empty</h3>
            <p>
              Memories and their connections will appear here as Row-Bot learns
              about you.
            </p>
          </div>
        )}

      {snapshot?.availability === 'available' &&
        snapshot.total_entities > 0 && (
          <>
            <div
              className="capability-summary knowledge-home-summary"
              aria-label="Knowledge statistics"
            >
              <p>
                <strong>{snapshot.total_entities}</strong> memories
              </p>
              <p>
                <strong>{snapshot.total_relations}</strong> connections
              </p>
              <p>
                <strong>{visibleNodes.length}</strong> visible memories
              </p>
              <p>
                <strong>{visibleEdges.length}</strong> visible connections
              </p>
            </div>
            {snapshot.truncated && (
              <p
                className="status-chip warning knowledge-home-truncated"
                role="status"
              >
                Showing a bounded view of {snapshot.shown_entities} of{' '}
                {snapshot.total_entities} memories.
              </p>
            )}

            <div
              className="panel-toolbar knowledge-graph-toolbar"
              aria-label="Knowledge graph controls"
            >
              <Field label="Search entities">
                <Input
                  type="search"
                  value={query}
                  placeholder="Search entities…"
                  onChange={(event) => setQuery(event.currentTarget.value)}
                />
              </Field>
              <Field label="Entity type">
                <Select
                  value={entityType}
                  onChange={(event) => setEntityType(event.currentTarget.value)}
                >
                  <option value="">All types</option>
                  {snapshot.entity_types.map((type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Source">
                <Select
                  value={source}
                  onChange={(event) => setSource(event.currentTarget.value)}
                >
                  <option value="">All sources</option>
                  {snapshot.sources.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </Select>
              </Field>
              <label className="knowledge-graph-toggle">
                <input
                  type="checkbox"
                  checked={showUserHub}
                  onChange={(event) =>
                    setShowUserHub(event.currentTarget.checked)
                  }
                />
                User hub
              </label>
              <label className="knowledge-graph-toggle">
                <input
                  type="checkbox"
                  checked={hideOrphans}
                  onChange={(event) =>
                    setHideOrphans(event.currentTarget.checked)
                  }
                />
                Hide orphans
              </label>
              <Button className="knowledge-graph-fit" onClick={fitGraph}>
                Fit
              </Button>
              <Button className="knowledge-graph-show-all" onClick={showAll}>
                Show All
              </Button>
            </div>

            <div className="knowledge-view-toggle" aria-label="Knowledge view">
              <Button
                aria-pressed={view === 'graph'}
                onClick={() => setView('graph')}
              >
                Graph
              </Button>
              <Button
                aria-pressed={view === 'list'}
                onClick={() => setView('list')}
              >
                Accessible list
              </Button>
            </div>
            <p className="visually-hidden" aria-live="polite">
              {fitAnnouncement}
            </p>

            <div className="knowledge-explorer-layout">
              <div className="knowledge-explorer-surface">
                {visibleNodes.length === 0 ? (
                  <div className="empty-state knowledge-filter-empty">
                    <h3>No matching memories</h3>
                    <p>
                      Change the search or filters, or show the complete graph.
                    </p>
                    <Button onClick={showAll}>Show All</Button>
                  </div>
                ) : view === 'graph' ? (
                  <svg
                    ref={graphRef}
                    className="knowledge-graph"
                    viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
                    width="100%"
                    height="500"
                    role="group"
                    aria-label={`Knowledge graph with ${visibleNodes.length} memories and ${visibleEdges.length} connections`}
                    tabIndex={-1}
                    preserveAspectRatio="xMidYMid meet"
                  >
                    <defs>
                      <marker
                        id={markerId}
                        viewBox="0 0 10 10"
                        refX="9"
                        refY="5"
                        markerWidth="6"
                        markerHeight="6"
                        orient="auto-start-reverse"
                      >
                        <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
                      </marker>
                    </defs>
                    <g className="knowledge-graph-edges" aria-hidden="true">
                      {visibleEdges.map((edge) => {
                        const from = positions.get(edge.source_id);
                        const to = positions.get(edge.target_id);
                        if (!from || !to) return null;
                        const color = hashColor(edge.relation_type);
                        return (
                          <g
                            key={edge.id}
                            className="knowledge-graph-edge"
                            data-relation-type={edge.relation_type}
                          >
                            <line
                              x1={from.x}
                              y1={from.y}
                              x2={to.x}
                              y2={to.y}
                              stroke={color}
                              strokeWidth="1.5"
                              strokeOpacity="0.62"
                              markerEnd={`url(#${markerId})`}
                            />
                            {visibleEdges.length <= 40 && (
                              <text
                                className="knowledge-graph-edge-label"
                                x={(from.x + to.x) / 2}
                                y={(from.y + to.y) / 2 - 4}
                                textAnchor="middle"
                                fill={color}
                                fontSize="10"
                              >
                                {compactLabel(edge.relation_type)}
                              </text>
                            )}
                          </g>
                        );
                      })}
                    </g>
                    <g className="knowledge-graph-nodes">
                      {visibleNodes.map((node) => {
                        const freshness = recency(node.updated_at);
                        const radius = Math.min(
                          23,
                          12 + Math.sqrt(Math.max(0, node.relation_count)) * 2,
                        );
                        return (
                          <g
                            key={node.id}
                            className={`knowledge-graph-node recency-${freshness}${node.id === selectedId ? ' selected' : ''}`}
                            data-source={node.source}
                            data-relation-count={node.relation_count}
                            role="button"
                            aria-label={`${node.subject}, ${node.entity_type}, ${node.relation_count} connections`}
                            tabIndex={0}
                            transform={`translate(${node.x} ${node.y})`}
                            onClick={() => void selectNode(node.id)}
                            onKeyDown={(event) => onNodeKeyDown(event, node.id)}
                          >
                            <title>{`${node.subject} · ${node.entity_type} · ${node.source} · ${freshness}`}</title>
                            <circle
                              r={radius}
                              fill={colorForType(node.entity_type)}
                              stroke={recencyStroke(freshness)}
                              strokeWidth={
                                freshness === 'recent'
                                  ? 4
                                  : freshness === 'current'
                                    ? 3
                                    : 2
                              }
                              strokeDasharray={
                                node.source.toLowerCase().startsWith('document')
                                  ? '5 3'
                                  : undefined
                              }
                            />
                            {(visibleNodes.length <= 40 ||
                              node.id === selectedId) && (
                              <text
                                y={radius + 16}
                                textAnchor="middle"
                                fill="currentColor"
                                fontSize="12"
                              >
                                {compactLabel(node.subject)}
                              </text>
                            )}
                          </g>
                        );
                      })}
                    </g>
                  </svg>
                ) : (
                  <ul
                    className="knowledge-entity-list"
                    aria-label="Knowledge entities"
                  >
                    {visibleNodes.map((node) => (
                      <li
                        key={node.id}
                        className="list-row knowledge-entity-list-item"
                      >
                        <button
                          type="button"
                          className="knowledge-entity-list-button"
                          aria-current={
                            node.id === selectedId ? 'true' : undefined
                          }
                          onClick={() => void selectNode(node.id)}
                        >
                          <strong>{node.subject}</strong>
                          <span>
                            {node.entity_type} · {node.source} ·{' '}
                            {node.relation_count} connections
                          </span>
                          {node.description && <span>{node.description}</span>}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <aside
                className="card knowledge-node-detail"
                aria-label="Selected memory detail"
              >
                {!selectedNode ? (
                  <p>Select a memory to inspect its details and connections.</p>
                ) : (
                  <>
                    <header className="section-heading">
                      <div>
                        <h3>
                          {selectedDetail?.value?.subject ??
                            selectedNode.subject}
                        </h3>
                        <p>
                          {selectedDetail?.value?.entity_type ??
                            selectedNode.entity_type}
                        </p>
                      </div>
                      <Button onClick={() => onEdit(selectedNode.id)}>
                        Edit
                      </Button>
                    </header>
                    {selectedDetail?.state === 'loading' && (
                      <p role="status">Loading memory detail…</p>
                    )}
                    {selectedDetail?.state === 'error' && (
                      <div
                        className="state-message knowledge-detail-error"
                        role="alert"
                      >
                        <div>
                          <strong>Memory detail unavailable</strong>
                          <p>{selectedDetail.error}</p>
                          <Button
                            onClick={() =>
                              void selectNode(selectedNode.id, true)
                            }
                          >
                            Try again
                          </Button>
                        </div>
                      </div>
                    )}
                    {selectedDetail?.state === 'ready' &&
                      selectedDetail.value && (
                        <div className="knowledge-detail-content">
                          <p>
                            {selectedDetail.value.description ||
                              'No description.'}
                          </p>
                          <dl>
                            <dt>Source</dt>
                            <dd>
                              {selectedDetail.value.source ??
                                selectedNode.source}
                            </dd>
                            <dt>Updated</dt>
                            <dd>
                              {(selectedDetail.value.updated_at ??
                                selectedNode.updated_at) ||
                                'Unknown'}
                            </dd>
                            <dt>Connections</dt>
                            <dd>
                              {selectedDetail.value.relation_count ??
                                selectedNode.relation_count}
                            </dd>
                            {selectedDetail.value.status && (
                              <>
                                <dt>Status</dt>
                                <dd>{selectedDetail.value.status}</dd>
                              </>
                            )}
                            {selectedDetail.value.tier && (
                              <>
                                <dt>Tier</dt>
                                <dd>{selectedDetail.value.tier}</dd>
                              </>
                            )}
                            {selectedDetail.value.confidence != null && (
                              <>
                                <dt>Confidence</dt>
                                <dd>
                                  {Math.round(
                                    selectedDetail.value.confidence * 100,
                                  )}
                                  %
                                </dd>
                              </>
                            )}
                          </dl>
                          {!!selectedDetail.value.aliases?.length && (
                            <p>
                              Aliases: {selectedDetail.value.aliases.join(', ')}
                            </p>
                          )}
                          {!!selectedDetail.value.tags?.length && (
                            <p>Tags: {selectedDetail.value.tags.join(', ')}</p>
                          )}
                          {!!selectedDetail.value.relations?.length && (
                            <div>
                              <h4>Connections</h4>
                              <ul>
                                {selectedDetail.value.relations.map(
                                  (relation, index) => (
                                    <li
                                      key={`${relation.peer_id ?? relation.peer_subject ?? 'relation'}-${index}`}
                                    >
                                      {relation.relation_type}:{' '}
                                      {relation.peer_subject ??
                                        relation.peer_id ??
                                        'Related memory'}
                                    </li>
                                  ),
                                )}
                              </ul>
                            </div>
                          )}
                        </div>
                      )}
                  </>
                )}
              </aside>
            </div>
          </>
        )}
    </section>
  );
}
