import { useEffect, useMemo, useRef, useState } from 'react';
import { Maximize2, ZoomIn, ZoomOut } from 'lucide-react';
import {
  Button,
  CompactAction,
  Field,
  Input,
  Select,
} from '../../ui/primitives';
import KnowledgeGraphCanvas, {
  type KnowledgeGraphHandle,
} from './KnowledgeGraphCanvas';

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

type DetailRecord = {
  state: 'loading' | 'ready' | 'error';
  value?: KnowledgeNodeDetail;
  error?: string;
};

function errorMessage(cause: unknown) {
  return cause instanceof Error
    ? cause.message
    : 'The entity detail could not be loaded.';
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
  const graphRef = useRef<KnowledgeGraphHandle>(null);

  useEffect(() => {
    setSelectedId(null);
    setDetails({});
    requestTicket.current += 1;
  }, [snapshot?.revision]);

  const allNodes = useMemo(
    () =>
      [...(snapshot?.nodes ?? [])]
        .sort((left, right) => left.id.localeCompare(right.id))
        .slice(0, 250),
    [snapshot?.nodes],
  );

  const visibleNodes = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return allNodes.filter((node) => {
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
  }, [allNodes, entityType, hideOrphans, query, showUserHub, source]);

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
    graphRef.current?.fit();
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
              <div
                className="knowledge-graph-navigation"
                aria-label="Graph navigation"
              >
                <CompactAction
                  label="Zoom in"
                  onClick={() => graphRef.current?.zoom(0.2)}
                >
                  <ZoomIn aria-hidden="true" />
                </CompactAction>
                <CompactAction
                  label="Zoom out"
                  onClick={() => graphRef.current?.zoom(-0.2)}
                >
                  <ZoomOut aria-hidden="true" />
                </CompactAction>
                <CompactAction
                  className="knowledge-graph-fit"
                  label="Fit"
                  onClick={fitGraph}
                >
                  <Maximize2 aria-hidden="true" />
                </CompactAction>
              </div>
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
                  <KnowledgeGraphCanvas
                    ref={graphRef}
                    nodes={visibleNodes}
                    edges={visibleEdges}
                    centerId={snapshot.center_id}
                    selectedId={selectedId}
                    onSelect={(nodeId) => void selectNode(nodeId)}
                  />
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
