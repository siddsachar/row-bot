import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';
import * as Popover from '@radix-ui/react-popover';
import {
  ArrowLeft,
  ArrowRight,
  GitMerge,
  ListFilter,
  Maximize2,
  Moon,
  Network,
  Pencil,
  Plus,
  RefreshCw,
  Rows3,
  Search,
  Trash2,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import {
  Button,
  IconButton,
  Segmented,
  Select,
  Toggle,
  Toolbar,
  ToolbarSeparator,
} from '../../ui/primitives';
import { Drawer } from '../../ui/overlays';
import KnowledgeGraphCanvas, {
  type KnowledgeGraphHandle,
} from './KnowledgeGraphCanvas';
import KnowledgeList, { sourceWords } from './KnowledgeList';
import { typeSlot } from './knowledge-palette';
import { When, plural } from './home-format';
import { humanizeToken, relativeTime } from '../../ui/format';

/** Recall tiers from memory_policy.py, in words people use (B8). */
const MEMORY_TIERS: Record<string, string> = {
  core: 'Core · always recalled',
  semantic: 'Long-term knowledge',
  episodic: 'From a conversation',
  resource: 'From a document or media',
};

function memoryTierLabel(tier: string) {
  return MEMORY_TIERS[tier.trim().toLowerCase()] ?? humanizeToken(tier);
}

function memorySourceLabel(source: string) {
  const value = source.trim();
  if (!value) return 'Unknown';
  if (value.startsWith('document:'))
    return `Document · ${value.slice('document:'.length) || 'unnamed'}`;
  if (value.startsWith('dream_')) return 'Dream Cycle';
  return humanizeToken(value);
}

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
  source_context?: string[];
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
  /** Add memory: opens a blank knowledge editor. */
  onAdd?: () => void;
  /** Open the editor's relations and replacement for this memory. */
  onMerge?: (id: string) => void;
  /** Review, confirm and delete one memory; resolves once it is gone. */
  onDelete?: (id: string, subject: string) => Promise<boolean>;
  onOpenConversation?: (id: string) => void;
  /** True once every memory up to the server limit is loaded. */
  showingAll?: boolean;
  onShowAll?: () => void;
  dream: KnowledgeDreamState;
  dreamLastRun?: string | null;
  onDream: () => void | Promise<void>;
};

type DetailRecord = {
  state: 'loading' | 'ready' | 'error';
  value?: KnowledgeNodeDetail;
  error?: string;
};

type SourceFilter =
  '' | 'manual' | 'extraction' | 'document' | 'wiki' | 'other';

function errorMessage(cause: unknown) {
  return cause instanceof Error
    ? cause.message
    : 'The memory detail could not be loaded.';
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
  if (dream.state === 'reviewing') return 'Checking Dream Cycle…';
  if (dream.state === 'running') return 'Dreaming…';
  if (dream.state === 'error') return 'Retry Dream';
  if (dream.state === 'success') return 'Dream again';
  return 'Run Dream Cycle';
}

/** "thread id: abc" lines from the detail's source context. */
function contextValue(lines: readonly string[] | undefined, key: string) {
  const line = lines?.find((item) => item.startsWith(`${key}: `));
  return line ? line.slice(key.length + 2).trim() : '';
}

function TypeDot({ type }: { type: string }) {
  return (
    <span
      className="knowledge-type-dot"
      data-slot={typeSlot(type)}
      aria-hidden
    />
  );
}

/** Typeahead over memory names: choosing one focuses it and its neighbours. */
function KnowledgeSearch({
  nodes,
  onChoose,
  total,
  onSearchAll,
}: {
  nodes: readonly KnowledgeGraphNode[];
  onChoose: (node: KnowledgeGraphNode) => void;
  /** Every saved memory, when only some of them are loaded. */
  total?: number;
  /** Load every memory so the search covers them all. */
  onSearchAll?: () => void;
}) {
  const id = useId();
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const matches = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return [];
    const scored: { node: KnowledgeGraphNode; score: number }[] = [];
    for (const node of nodes) {
      const subject = node.subject.toLocaleLowerCase();
      const score = subject.startsWith(needle)
        ? 0
        : subject.includes(needle)
          ? 1
          : `${node.description} ${node.entity_type}`
                .toLocaleLowerCase()
                .includes(needle)
            ? 2
            : -1;
      if (score >= 0) scored.push({ node, score });
    }
    return scored
      .sort(
        (left, right) =>
          left.score - right.score ||
          right.node.relation_count - left.node.relation_count,
      )
      .slice(0, 8)
      .map((item) => item.node);
  }, [nodes, query]);
  const choose = (node: KnowledgeGraphNode) => {
    onChoose(node);
    setQuery(node.subject);
    setOpen(false);
  };
  const keyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setOpen(true);
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive((value) =>
        matches.length ? (value + step + matches.length) % matches.length : 0,
      );
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const node = matches[active] ?? matches[0];
      if (node) choose(node);
    } else if (event.key === 'Escape') {
      if (open) {
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
      } else setQuery('');
    }
  };
  const expanded = open && matches.length > 0;
  return (
    <div className="knowledge-search">
      <Search size={14} aria-hidden className="knowledge-search-icon" />
      <input
        type="search"
        className="knowledge-search-input"
        role="combobox"
        aria-label="Search memories"
        aria-autocomplete="list"
        aria-expanded={expanded}
        aria-controls={`${id}-results`}
        aria-activedescendant={expanded ? `${id}-option-${active}` : undefined}
        placeholder="Search memories"
        value={query}
        maxLength={200}
        onChange={(event) => {
          setQuery(event.currentTarget.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        onKeyDown={keyDown}
      />
      {query && matches.length === 0 && open && (
        <p className="knowledge-search-empty" role="status">
          {onSearchAll && total ? (
            <>
              No match in the {nodes.length} shown.{' '}
              <button
                type="button"
                className="knowledge-caption-action"
                onMouseDown={(event) => event.preventDefault()}
                onClick={onSearchAll}
              >
                Search all {total} memories
              </button>
            </>
          ) : (
            <>No memory matches “{query.trim()}”.</>
          )}
        </p>
      )}
      <ul
        id={`${id}-results`}
        role="listbox"
        aria-label="Matching memories"
        className="knowledge-search-results"
        hidden={!expanded}
      >
        {matches.map((node, index) => (
          <li
            key={node.id}
            id={`${id}-option-${index}`}
            role="option"
            aria-selected={index === active}
            onMouseDown={(event) => event.preventDefault()}
            onMouseEnter={() => setActive(index)}
            onClick={() => choose(node)}
          >
            <TypeDot type={node.entity_type} />
            <span className="knowledge-search-subject">{node.subject}</span>
            <span className="knowledge-search-meta">
              {humanizeToken(node.entity_type)} · {node.relation_count}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function KnowledgeHome({
  snapshot,
  loading,
  error,
  reload,
  loadDetail,
  onEdit,
  onAdd,
  onMerge,
  onDelete,
  onOpenConversation,
  showingAll = false,
  onShowAll,
  dream,
  dreamLastRun,
  onDream,
}: KnowledgeHomeProps) {
  const [view, setView] = useState<'graph' | 'list'>('graph');
  const [hiddenTypes, setHiddenTypes] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [source, setSource] = useState<SourceFilter>('');
  const [showUserHub, setShowUserHub] = useState(true);
  const [hideOrphans, setHideOrphans] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, DetailRecord>>({});
  const [announcement, setAnnouncement] = useState('');
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [dreamError, setDreamError] = useState('');
  const [graphStatus, setGraphStatus] = useState<
    'loading' | 'ready' | 'failed'
  >('loading');
  const [stage, setStage] = useState<HTMLDivElement | null>(null);
  const requestTicket = useRef(0);
  const graphRef = useRef<KnowledgeGraphHandle>(null);
  const selectedRef = useRef<string | null>(null);
  const loadDetailRef = useRef(loadDetail);
  useEffect(() => {
    selectedRef.current = selectedId;
    loadDetailRef.current = loadDetail;
  });

  const readDetail = useCallback(async (id: string) => {
    const ticket = ++requestTicket.current;
    setDetails((current) => ({ ...current, [id]: { state: 'loading' } }));
    try {
      const value = await loadDetailRef.current(id);
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
  }, []);

  // A new snapshot drops loaded details. A selection that is still in the
  // graph stays open and its detail is read again.
  useEffect(() => {
    setDetails({});
    requestTicket.current += 1;
    const current = selectedRef.current;
    const kept =
      current && snapshot?.nodes.some((node) => node.id === current)
        ? current
        : null;
    setSelectedId(kept);
    if (kept) void readDetail(kept);
  }, [readDetail, snapshot?.revision, snapshot?.nodes]);

  const allNodes = useMemo(() => snapshot?.nodes ?? [], [snapshot?.nodes]);
  const allEdges = useMemo(() => snapshot?.edges ?? [], [snapshot?.edges]);
  const typeCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of allNodes)
      counts.set(node.entity_type, (counts.get(node.entity_type) ?? 0) + 1);
    return [...counts].sort(
      (left, right) =>
        (typeSlot(left[0]) || 99) - (typeSlot(right[0]) || 99) ||
        right[1] - left[1] ||
        left[0].localeCompare(right[0]),
    );
  }, [allNodes]);
  const visibleNodes = useMemo(
    () =>
      allNodes.filter((node) => {
        if (hiddenTypes.has(node.entity_type)) return false;
        if (!showUserHub && node.is_user) return false;
        if (hideOrphans && node.orphan) return false;
        if (source && node.source !== source) return false;
        return true;
      }),
    [allNodes, hiddenTypes, hideOrphans, showUserHub, source],
  );
  const visibleIds = useMemo(
    () => new Set(visibleNodes.map((node) => node.id)),
    [visibleNodes],
  );
  const visibleLinks = useMemo(
    () =>
      allEdges.filter(
        (edge) =>
          visibleIds.has(edge.source_id) && visibleIds.has(edge.target_id),
      ).length,
    [allEdges, visibleIds],
  );
  const filtered =
    hiddenTypes.size > 0 || Boolean(source) || !showUserHub || hideOrphans;
  const selectedNode = allNodes.find((node) => node.id === selectedId) ?? null;
  const selectedDetail = selectedId ? details[selectedId] : undefined;

  async function select(id: string | null, force = false) {
    setSelectedId(id);
    setSummaryOpen(false);
    if (!id) return;
    if (!force && details[id]?.state === 'ready') return;
    await readDetail(id);
  }

  function clearFilters() {
    setHiddenTypes(new Set());
    setSource('');
    setShowUserHub(true);
    setHideOrphans(false);
    setAnnouncement('All memories and connections are shown.');
  }

  function focusMemory(id: string) {
    const node = allNodes.find((item) => item.id === id);
    if (!node) return;
    if (!visibleIds.has(id)) clearFilters();
    void select(id);
    setAnnouncement(`${node.subject} and its connections are highlighted.`);
    // Wait a frame so newly shown memories are placed before the camera moves.
    requestAnimationFrame(() => graphRef.current?.focus(id));
  }

  function toggleType(type: string) {
    setHiddenTypes((current) => {
      const next = new Set(current);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
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
    !dream.available ||
    !dream.enabled ||
    dream.state === 'reviewing' ||
    dream.state === 'running';
  const dreamTone =
    dream.state === 'running' || dream.state === 'reviewing'
      ? 'accent'
      : dream.state === 'error'
        ? 'danger'
        : dream.state === 'success'
          ? 'success'
          : !dream.available || !dream.enabled
            ? 'neutral'
            : 'idle';
  const hasGraph =
    snapshot?.availability === 'available' && snapshot.total_entities > 0;

  return (
    <section
      className="knowledge-home"
      aria-labelledby="knowledge-home-title"
      data-view={view}
    >
      <h2 id="knowledge-home-title" className="visually-hidden">
        Knowledge
      </h2>
      <div className="knowledge-stage" ref={setStage}>
        {error && (
          <div className="knowledge-stage-message" role="alert">
            <strong>Knowledge could not be refreshed</strong>
            <p>{error}</p>
            <Button className="small" onClick={reload}>
              Try again
            </Button>
          </div>
        )}
        {loading && !snapshot && !error && (
          <p className="knowledge-stage-message" role="status">
            Loading knowledge graph…
          </p>
        )}
        {snapshot && snapshot.availability !== 'available' && (
          <div className="knowledge-stage-message">
            <strong>Knowledge unavailable</strong>
            <p>{availabilityCopy(snapshot.availability)}</p>
            <Button className="small" onClick={reload}>
              Try again
            </Button>
          </div>
        )}
        {snapshot?.availability === 'available' &&
          snapshot.total_entities === 0 && (
            <div className="knowledge-stage-message">
              <Network size={22} aria-hidden />
              <strong>Your memory map is empty</strong>
              <p>
                Memories and their connections appear here as Row-Bot learns
                about you.
              </p>
              {onAdd && (
                <Button className="small" onClick={onAdd}>
                  Add memory
                </Button>
              )}
            </div>
          )}
        {hasGraph && (
          <>
            {view === 'graph' && graphStatus !== 'failed' ? (
              <KnowledgeGraphCanvas
                ref={graphRef}
                nodes={allNodes}
                edges={allEdges}
                visible={visibleIds}
                selectedId={selectedId}
                onSelect={(id) => void select(id)}
                onStatus={setGraphStatus}
              />
            ) : (
              <div className="knowledge-list-frame">
                {view === 'graph' && (
                  <p className="knowledge-list-note" role="status">
                    The interactive graph needs WebGL, which is unavailable
                    here. Every memory is listed instead.
                  </p>
                )}
                <KnowledgeList
                  nodes={visibleNodes}
                  selectedId={selectedId}
                  onSelect={(id) => void select(id)}
                />
              </div>
            )}
            {visibleNodes.length === 0 && (
              <div className="knowledge-stage-message">
                <strong>No memories match these filters</strong>
                <Button className="small" onClick={clearFilters}>
                  Show everything
                </Button>
              </div>
            )}
          </>
        )}
        {snapshot && (
          <Toolbar
            floating
            placement="top-left"
            label="Knowledge controls"
            className="knowledge-controls"
          >
            {hasGraph && (
              <>
                <KnowledgeSearch
                  nodes={allNodes}
                  onChoose={(node) => focusMemory(node.id)}
                  total={snapshot.total_entities}
                  onSearchAll={
                    snapshot.truncated && !showingAll ? onShowAll : undefined
                  }
                />
                <ToolbarSeparator />
                <Segmented
                  label="Knowledge view"
                  size="sm"
                  value={view}
                  onChange={setView}
                  options={[
                    {
                      value: 'graph',
                      label: 'Graph',
                      icon: <Network size={14} aria-hidden />,
                    },
                    {
                      value: 'list',
                      label: 'List',
                      icon: <Rows3 size={14} aria-hidden />,
                    },
                  ]}
                />
                <ToolbarSeparator />
                <Popover.Root>
                  <Popover.Trigger asChild>
                    <IconButton
                      size="sm"
                      label="Filters"
                      pressed={filtered}
                      className="knowledge-filter-trigger"
                    >
                      <ListFilter size={15} aria-hidden />
                    </IconButton>
                  </Popover.Trigger>
                  <Popover.Portal>
                    <Popover.Content
                      className="popover knowledge-filter-popover"
                      aria-label="Memory filters"
                      align="start"
                      sideOffset={8}
                      collisionPadding={12}
                    >
                      <label className="knowledge-filter-row">
                        <span>Show you</span>
                        <Toggle
                          label="User hub"
                          checked={showUserHub}
                          onChange={(event) =>
                            setShowUserHub(event.currentTarget.checked)
                          }
                        />
                      </label>
                      <label className="knowledge-filter-row">
                        <span>Hide unconnected memories</span>
                        <Toggle
                          label="Hide orphans"
                          checked={hideOrphans}
                          onChange={(event) =>
                            setHideOrphans(event.currentTarget.checked)
                          }
                        />
                      </label>
                      <label className="knowledge-filter-row">
                        <span>Source</span>
                        <Select
                          aria-label="Source"
                          value={source}
                          onChange={(event) =>
                            setSource(event.currentTarget.value as SourceFilter)
                          }
                        >
                          <option value="">All sources</option>
                          {snapshot.sources.map((item) => (
                            <option key={item} value={item}>
                              {sourceWords(item)}
                            </option>
                          ))}
                        </Select>
                      </label>
                      {filtered && (
                        <Button className="small" onClick={clearFilters}>
                          Show everything
                        </Button>
                      )}
                    </Popover.Content>
                  </Popover.Portal>
                </Popover.Root>
              </>
            )}
            {onAdd && (
              <IconButton size="sm" label="Add memory" onClick={onAdd}>
                <Plus size={15} aria-hidden />
              </IconButton>
            )}
            <IconButton
              size="sm"
              label={dreamLabel(dream)}
              disabled={dreamDisabled}
              className="knowledge-dream-action"
              data-state={dreamTone}
              aria-describedby="knowledge-dream-last"
              onClick={() => void runDream()}
            >
              <Moon size={15} aria-hidden />
              <span className="knowledge-dream-dot" aria-hidden />
            </IconButton>
            <span id="knowledge-dream-last" className="visually-hidden">
              {!dream.available
                ? 'Dream Cycle is unavailable.'
                : !dream.enabled
                  ? 'Dream Cycle is off in Settings.'
                  : dreamLastRun
                    ? `Dream Cycle last ran ${relativeTime(dreamLastRun)}.`
                    : 'Dream Cycle has not run yet.'}
            </span>
            <IconButton
              size="sm"
              label="Refresh knowledge"
              disabled={loading}
              onClick={reload}
            >
              <RefreshCw size={15} aria-hidden />
            </IconButton>
          </Toolbar>
        )}
        {(dream.message || dreamError) && (
          <p
            className="knowledge-dream-status"
            data-tone={
              dream.state === 'error' || dreamError
                ? 'danger'
                : dream.state === 'success'
                  ? 'success'
                  : 'accent'
            }
            role={dream.state === 'error' || dreamError ? 'alert' : 'status'}
          >
            {dreamError || dream.message}
          </p>
        )}
        {hasGraph && typeCounts.length > 0 && (
          <div
            className="knowledge-legend"
            role="group"
            aria-label="Memory types"
          >
            {typeCounts.map(([type, count]) => {
              const shown = !hiddenTypes.has(type);
              return (
                <button
                  key={type}
                  type="button"
                  className="knowledge-legend-item"
                  aria-pressed={shown}
                  aria-label={`${humanizeToken(type)}, ${plural(count, 'memory', 'memories')}`}
                  title={shown ? 'Hide this type' : 'Show this type'}
                  onClick={() => toggleType(type)}
                >
                  <TypeDot type={type} />
                  <span className="knowledge-legend-name">
                    {humanizeToken(type)}
                  </span>
                  <span className="knowledge-legend-count">{count}</span>
                </button>
              );
            })}
            {hiddenTypes.size > 0 && (
              <button
                type="button"
                className="knowledge-legend-reset"
                onClick={() => setHiddenTypes(new Set())}
              >
                Show all types
              </button>
            )}
          </div>
        )}
        {hasGraph && view === 'graph' && graphStatus === 'ready' && (
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
              onClick={() => {
                graphRef.current?.fit();
                setAnnouncement(
                  `Graph fitted to ${plural(visibleNodes.length, 'memory', 'memories')}.`,
                );
              }}
            >
              <Maximize2 size={14} aria-hidden />
            </IconButton>
          </Toolbar>
        )}
        {hasGraph && (
          <p className="knowledge-caption" aria-label="Knowledge statistics">
            <span>{plural(snapshot.total_entities, 'memory', 'memories')}</span>
            <span aria-hidden>·</span>
            <span>{plural(snapshot.total_relations, 'link')}</span>
            {(snapshot.truncated || filtered) && (
              <>
                <span aria-hidden>·</span>
                <span>
                  showing {visibleNodes.length.toLocaleString()}
                  {filtered ? ` of ${allNodes.length.toLocaleString()}` : ''}
                </span>
              </>
            )}
            {snapshot.truncated && !showingAll && onShowAll && (
              <button
                type="button"
                className="knowledge-caption-action"
                aria-label="Show all memories"
                onClick={onShowAll}
              >
                Show all
              </button>
            )}
            {filtered && (
              <button
                type="button"
                className="knowledge-caption-action"
                onClick={clearFilters}
              >
                Clear filters
              </button>
            )}
            {view === 'graph' && visibleLinks !== allEdges.length && (
              <span className="visually-hidden">
                {plural(visibleLinks, 'link')} visible.
              </span>
            )}
          </p>
        )}
        <p className="visually-hidden" aria-live="polite">
          {announcement}
        </p>
        {stage && (
          <Drawer
            open={Boolean(selectedNode)}
            onOpenChange={(open) => {
              if (!open) setSelectedId(null);
            }}
            container={stage}
            title={
              selectedDetail?.value?.subject ?? selectedNode?.subject ?? ''
            }
            description={
              selectedNode
                ? `${humanizeToken(selectedDetail?.value?.entity_type ?? selectedNode.entity_type)} · ${sourceWords(selectedNode.source)}`
                : undefined
            }
            closeLabel="Close memory detail"
            className="knowledge-inspector"
            actions={
              selectedNode && (
                <>
                  <IconButton
                    size="sm"
                    label="Edit memory"
                    onClick={() => onEdit(selectedNode.id)}
                  >
                    <Pencil size={14} aria-hidden />
                  </IconButton>
                  {onMerge && (
                    <IconButton
                      size="sm"
                      label="Merge or replace"
                      onClick={() => onMerge(selectedNode.id)}
                    >
                      <GitMerge size={14} aria-hidden />
                    </IconButton>
                  )}
                  {onDelete && (
                    <IconButton
                      size="sm"
                      label="Delete memory"
                      variant="danger"
                      onClick={() =>
                        void onDelete(
                          selectedNode.id,
                          selectedNode.subject,
                        ).then((deleted) => {
                          if (deleted) setSelectedId(null);
                        })
                      }
                    >
                      <Trash2 size={14} aria-hidden />
                    </IconButton>
                  )}
                </>
              )
            }
          >
            {selectedNode && (
              <div
                className="knowledge-detail"
                aria-label="Selected memory detail"
                role="region"
              >
                {selectedDetail?.state === 'loading' && (
                  <p className="home-caption" role="status">
                    Loading memory detail…
                  </p>
                )}
                {selectedDetail?.state === 'error' && (
                  <div className="task-builder-alert" role="alert">
                    <strong>Memory detail unavailable</strong>
                    <p>{selectedDetail.error}</p>
                    <Button
                      className="small"
                      onClick={() => void select(selectedNode.id, true)}
                    >
                      Try again
                    </Button>
                  </div>
                )}
                <p
                  className="knowledge-detail-summary"
                  data-clamped={summaryOpen ? undefined : 'true'}
                >
                  {(selectedDetail?.value?.description ??
                    selectedNode.description) ||
                    'No description.'}
                </p>
                {(
                  selectedDetail?.value?.description ?? selectedNode.description
                ).length > 420 && (
                  <button
                    type="button"
                    className="knowledge-detail-more"
                    aria-expanded={summaryOpen}
                    onClick={() => setSummaryOpen((value) => !value)}
                  >
                    {summaryOpen ? 'Show less' : 'Show more'}
                  </button>
                )}
                <dl className="knowledge-detail-facts">
                  <div>
                    <dt>Updated</dt>
                    <dd>
                      <When
                        value={
                          selectedDetail?.value?.updated_at ??
                          selectedNode.updated_at
                        }
                      />
                    </dd>
                  </div>
                  <div>
                    <dt>Source</dt>
                    <dd
                      title={
                        selectedDetail?.value?.source ?? selectedNode.source
                      }
                    >
                      {memorySourceLabel(
                        selectedDetail?.value?.source ?? selectedNode.source,
                      )}
                    </dd>
                  </div>
                  {selectedDetail?.value?.tier && (
                    <div>
                      <dt>Memory type</dt>
                      <dd>{memoryTierLabel(selectedDetail.value.tier)}</dd>
                    </div>
                  )}
                  {selectedDetail?.value?.status &&
                    selectedDetail.value.status !== 'active' && (
                      <div>
                        <dt>Status</dt>
                        <dd>{humanizeToken(selectedDetail.value.status)}</dd>
                      </div>
                    )}
                  {selectedDetail?.value?.confidence != null && (
                    <div>
                      <dt>Confidence</dt>
                      <dd>
                        {Math.round(selectedDetail.value.confidence * 100)}%
                      </dd>
                    </div>
                  )}
                </dl>
                {(() => {
                  const thread = contextValue(
                    selectedDetail?.value?.source_context,
                    'thread id',
                  );
                  const name = contextValue(
                    selectedDetail?.value?.source_context,
                    'thread name',
                  );
                  const document = contextValue(
                    selectedDetail?.value?.source_context,
                    'document title',
                  );
                  if (thread && onOpenConversation)
                    return (
                      <Button
                        variant="ghost"
                        className="small knowledge-source-link"
                        onClick={() => onOpenConversation(thread)}
                      >
                        Open source conversation{name ? `: ${name}` : ''}
                      </Button>
                    );
                  if (document)
                    return (
                      <p className="home-caption">From document “{document}”</p>
                    );
                  return null;
                })()}
                {(!!selectedDetail?.value?.aliases?.length ||
                  !!selectedDetail?.value?.tags?.length) && (
                  <p className="knowledge-detail-chips">
                    {selectedDetail?.value?.aliases?.map((alias) => (
                      <span key={`alias-${alias}`} className="knowledge-chip">
                        {alias}
                      </span>
                    ))}
                    {selectedDetail?.value?.tags?.map((tag) => (
                      <span
                        key={`tag-${tag}`}
                        className="knowledge-chip"
                        data-kind="tag"
                      >
                        #{tag}
                      </span>
                    ))}
                  </p>
                )}
                <Connections
                  relations={selectedDetail?.value?.relations ?? []}
                  total={
                    selectedDetail?.value?.relation_count ??
                    selectedNode.relation_count
                  }
                  onFocus={focusMemory}
                />
              </div>
            )}
          </Drawer>
        )}
      </div>
    </section>
  );
}

function Connections({
  relations,
  total,
  onFocus,
}: {
  relations: readonly KnowledgeNodeRelation[];
  total: number;
  onFocus: (id: string) => void;
}) {
  const groups = useMemo(() => {
    const map = new Map<string, KnowledgeNodeRelation[]>();
    for (const relation of relations) {
      const key = humanizeToken(relation.relation_type) || 'Related';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(relation);
    }
    return [...map].sort((left, right) => right[1].length - left[1].length);
  }, [relations]);
  if (!total) return <p className="home-caption">No connections yet.</p>;
  return (
    <section className="knowledge-connections" aria-label="Connections">
      <h3>
        Connections <span className="overview-count">{total}</span>
      </h3>
      {groups.map(([label, items]) => (
        <div key={label} className="knowledge-connection-group">
          <h4>{label}</h4>
          <ul>
            {items.map((relation, index) => (
              <li key={`${relation.peer_id ?? relation.peer_subject}-${index}`}>
                <button
                  type="button"
                  className="knowledge-connection"
                  disabled={!relation.peer_id}
                  onClick={() => relation.peer_id && onFocus(relation.peer_id)}
                >
                  {relation.direction === 'incoming' ? (
                    <ArrowLeft size={12} aria-hidden />
                  ) : (
                    <ArrowRight size={12} aria-hidden />
                  )}
                  <span className="visually-hidden">
                    {relation.direction === 'incoming' ? 'from ' : 'to '}
                  </span>
                  <span>{relation.peer_subject ?? 'Related memory'}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {relations.length < total && (
        <p className="home-caption">
          {plural(total - relations.length, 'more connection')} in Settings ›
          Memory.
        </p>
      )}
    </section>
  );
}
