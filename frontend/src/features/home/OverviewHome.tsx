import {
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type FormEvent,
  type ReactNode,
} from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowUp,
  ArrowUpRight,
  Bot,
  Brain,
  CalendarClock,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  Code2,
  CornerDownRight,
  Hand,
  HeartPulse,
  Lightbulb,
  MessageSquare,
  Moon,
  Palette,
  Paperclip,
  Pin,
  ShieldAlert,
  Sparkles,
  Workflow,
  X,
} from 'lucide-react';
import type {
  ConversationView,
  InsightsSnapshot,
  KnowledgeGraphSnapshot,
  MonitorSnapshot,
  OnboardingSnapshot,
  PendingApproval,
  PendingApprovalPage,
  SystemDiagnosis,
  TaskSummary,
  TaskSummaryPage,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { AgentAvatar, agentSeed } from '../../ui/AgentAvatar';
import {
  Button,
  Hint,
  IconButton,
  InlineEmpty,
  StatusDot,
} from '../../ui/primitives';
import { humanizeToken, parseTimestamp, relativeTime } from '../../ui/format';
import { useReducedMotion } from '../buddy/BuddyAvatar';
import { ConversationGlyph } from '../shell/ConversationGlyph';
import {
  conversationKinds,
  type ConversationKind,
} from '../shell/conversation-groups';
import {
  InterruptedWorkControls,
  interruptedWork,
} from '../shell/DelegatedActivity';
import { ApprovalDecision, WaitingSince } from '../shell/InPlaceApproval';
import { usePendingApprovals } from '../shell/pending-approvals';
import { typeToken } from './knowledge-palette';
import {
  FAILED_RUN_STATUSES,
  WAITING_RUN_STATUSES,
  When,
  clockTime,
  greeting,
  overnightStart,
  plural,
  runStatus,
  scheduleWords,
} from './home-format';

export type OverviewHomeProps = {
  conversations: readonly ConversationView[];
  setup: OnboardingSnapshot | null;
  monitor: MonitorSnapshot | null;
  /** Absent until the workspace is connected. */
  loadTasks?: (signal: AbortSignal) => Promise<TaskSummaryPage>;
  /** Monitor's kept check results (B252); a red one needs you. */
  loadHealth?: (signal: AbortSignal) => Promise<SystemDiagnosis>;
  /** Every approval waiting for the person, the shell's shared read (B255). */
  loadApprovals?: (signal?: AbortSignal) => Promise<PendingApprovalPage>;
  /** The memory count and the few most connected memories (Memory card). */
  loadMemory?: (signal: AbortSignal) => Promise<KnowledgeGraphSnapshot>;
  /** Insights, where this device may read them. */
  loadInsights?: (signal: AbortSignal) => Promise<InsightsSnapshot>;
  /** Buddy's live avatar beside the greeting. */
  buddy?: ReactNode;
  /**
   * Start a new chat with this text as its first message, or only waiting in
   * its composer (`send: false`) to add files and more there.
   */
  onAsk?: (text: string, options?: { send: false }) => void;
  /** A new chat is being made: the Ask box waits. */
  asking?: boolean;
  /** Set up a new design or code folder in a new chat. */
  onNewResource?: (kind: 'artifact' | 'workspace') => void;
  onNewWorkflow?: () => void;
  onOpenConversation: (id: string) => void;
  /** Workflows tab, optionally with one workflow's runs open. */
  onOpenWorkflows: (taskId?: string) => void;
  onOpenTab: (tab: 'knowledge' | 'monitor' | 'insights') => void;
  onHideSetup?: () => void;
  setupError?: string;
  /** Re-read one listed conversation (approvals and runs change live). */
  refreshConversation?: (id: string, signal: AbortSignal) => Promise<void>;
  /** Run a conversation's interrupted agent work again (B220). */
  onResumeAgentWork?: (row: ConversationView) => Promise<void>;
  /** Close a conversation's interrupted agent work without running it. */
  onDismissAgentWork?: (row: ConversationView) => Promise<void>;
  now?: Date;
  /** Re-read workflows when this changes (e.g. after reconnecting). */
  refreshKey?: string | number;
};

/** Tinted icon tiles: one recipe, a tone per type (chart series 1–6). */
type TileTone =
  | 'accent'
  | '1'
  | '2'
  | '3'
  | '4'
  | '5'
  | '6'
  | 'success'
  | 'warning'
  | 'danger'
  | 'muted';

type Item = {
  icon: ReactNode;
  tone: 'warning' | 'danger';
  title: string;
  meta: ReactNode;
  time?: ReactNode;
  label: string;
  onOpen: () => void;
  /** Icon actions beside the row (never inside its button). */
  actions?: ReactNode;
};

type Need =
  { key: string; approval: PendingApproval } | { key: string; item: Item };

type Moment = {
  key: string;
  at: Date;
  tone: TileTone;
  icon: ReactNode;
  title: string;
  detail: string;
  onOpen: () => void;
};

/** Needs you keeps the first row even: two items, then "+N more waiting". */
const NEEDS_SHOWN = 2;
const CONTINUE_COUNT = 4;
const TIMELINE_LIMIT = 7;
const CHIP_LIMIT = 5;
const AVATAR_LIMIT = 4;
const RUN_MARKS = 12;
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
/** Where the Memory card's few memories sit, most connected first. */
const GRAPH_POINTS: readonly [number, number][] = [
  [40, 15],
  [24, 8],
  [56, 7],
  [54, 24],
  [22, 24],
  [8, 16],
  [70, 15],
  [78, 25],
];

const KIND_TONES: Record<ConversationKind, TileTone> = {
  designer: '5',
  code: '2',
  workflow: '4',
};
const KIND_WORDS: Record<ConversationKind, string> = {
  designer: 'Design',
  code: 'Code folder',
  workflow: 'Workflow',
};
const SOURCE_TILES: Record<
  PendingApproval['source'],
  { tone: TileTone; icon: ReactNode }
> = {
  workflow: { tone: '4', icon: <Workflow size={16} /> },
  conversation: { tone: 'accent', icon: <MessageSquare size={16} /> },
  agent: { tone: '3', icon: <Bot size={16} /> },
};

function waitingApproval(row: ConversationView) {
  return (
    row.activity_state === 'attention' ||
    Boolean(
      row.generation_state?.some(
        (state) => state.status === 'waiting_approval',
      ),
    )
  );
}

function working(row: ConversationView) {
  return (
    row.activity_state === 'active' ||
    Boolean(
      row.generation_state?.some(
        (state) => state.status === 'running' || state.status === 'stopping',
      ),
    )
  );
}

const titleOf = (row: ConversationView) => row.title || 'Untitled conversation';

function within(value: string | null | undefined, from: Date, to: Date) {
  const date = parseTimestamp(value);
  return date && date >= from && date <= to ? date : null;
}

/** "A", "A and B", "A, B and 3 more". */
function listWords(items: readonly string[]) {
  if (items.length <= 2) return items.join(' and ');
  return `${items.slice(0, 2).join(', ')} and ${items.length - 2} more`;
}

const capitalize = (text: string) =>
  text.charAt(0).toUpperCase() + text.slice(1);

function Tile({ tone, children }: { tone: TileTone; children: ReactNode }) {
  return (
    <span className="overview-tile" data-tone={tone} aria-hidden>
      {children}
    </span>
  );
}

/** One section card: a tile, a title, and an optional link on the right. */
function Card({
  id,
  title,
  tone,
  icon,
  action,
  className = '',
  span,
  order,
  calm,
  children,
}: {
  id: string;
  title: ReactNode;
  tone: TileTone;
  icon: ReactNode;
  action?: ReactNode;
  className?: string;
  span?: 2 | 3;
  order: number;
  calm?: boolean;
  children: ReactNode;
}) {
  return (
    <section
      className={`overview-card overview-cell ${className}`}
      data-span={span}
      data-calm={calm ? 'true' : undefined}
      style={{ '--overview-order': order } as CSSProperties}
      aria-labelledby={id}
    >
      <header className={`overview-card-head${calm ? ' visually-hidden' : ''}`}>
        <Tile tone={tone}>{icon}</Tile>
        <h3 id={id}>{title}</h3>
        {action}
      </header>
      {children}
    </section>
  );
}

function SectionLink({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button type="button" className="overview-section-link" onClick={onClick}>
      {children}
      <ChevronRight size={13} aria-hidden />
    </button>
  );
}

function NeedRow({ item }: { item: Item }) {
  return (
    <li className="overview-need">
      <button
        type="button"
        className="overview-need-button"
        aria-label={item.label}
        onClick={item.onOpen}
      >
        <Tile tone={item.tone}>{item.icon}</Tile>
        <span className="overview-need-text">
          <span className="overview-need-title">{item.title}</span>
          <span className="overview-need-meta">{item.meta}</span>
        </span>
        {item.time && <span className="overview-need-time">{item.time}</span>}
        <ChevronRight className="overview-need-chevron" size={15} aria-hidden />
      </button>
      {item.actions && (
        <div className="overview-need-actions">{item.actions}</div>
      )}
    </li>
  );
}

/** A waiting approval, answered in place with the approval card's command. */
function ApprovalRow({
  item,
  onChanged,
}: {
  item: PendingApproval;
  onChanged: () => void;
}) {
  const to = item.task_id
    ? `/?tab=workflows&workflow=${encodeURIComponent(item.task_id)}`
    : item.conversation_id
      ? `/conversations/${encodeURIComponent(item.conversation_id)}`
      : '';
  const where = item.task_id ? 'Open the workflow' : 'Open the conversation';
  const tile = SOURCE_TILES[item.source];
  return (
    <li className="overview-need overview-need-approval">
      <div className="overview-need-body">
        <Tile tone={tile.tone}>{tile.icon}</Tile>
        <span className="overview-need-text">
          <strong className="overview-need-title">{item.title}</strong>
          <span className="overview-need-meta">
            {item.what && <>{item.what} · </>}
            <WaitingSince value={item.requested_at} />
          </span>
        </span>
      </div>
      <div className="overview-need-actions">
        {to && (
          <Hint label={where}>
            <Link
              className="button ghost icon-button icon-action icon-action-sm"
              to={to}
              aria-label={where}
            >
              <ArrowUpRight size={16} aria-hidden />
            </Link>
          </Hint>
        )}
        <ApprovalDecision
          approvalId={item.id}
          subject={item.title}
          onResolved={onChanged}
        />
      </div>
    </li>
  );
}

function AskBox({
  onAsk,
  asking,
}: {
  onAsk: NonNullable<OverviewHomeProps['onAsk']>;
  asking: boolean;
}) {
  const [text, setText] = useState('');
  function submit(event: FormEvent) {
    event.preventDefault();
    const value = text.trim();
    if (value && !asking) onAsk(value);
  }
  return (
    <form className="overview-ask" onSubmit={submit}>
      <input
        className="overview-ask-input"
        type="text"
        aria-label="Ask Row-Bot"
        placeholder="Ask Row-Bot anything, or describe what to make…"
        enterKeyHint="send"
        autoComplete="off"
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <IconButton
        label="Add files in a new chat"
        disabled={asking}
        onClick={() => onAsk(text, { send: false })}
      >
        <Paperclip size={17} aria-hidden />
      </IconButton>
      <IconButton
        label="Start chat"
        type="submit"
        variant="primary"
        className="overview-ask-send"
        disabled={asking || !text.trim()}
      >
        <ArrowUp size={18} aria-hidden />
      </IconButton>
    </form>
  );
}

/** One live status card: a picture, a value and a line; it opens its tab. */
function Stat({
  label,
  tone,
  icon,
  value,
  line,
  lineTone,
  picture,
  note,
  onOpen,
}: {
  label: string;
  tone: TileTone;
  icon: ReactNode;
  value: string;
  line: string;
  lineTone?: 'success';
  picture?: ReactNode;
  note?: string;
  onOpen?: () => void;
}) {
  const body = (
    <>
      <span className="overview-stat-head">
        <Tile tone={tone}>{icon}</Tile>
        {label}
        {onOpen && (
          <ChevronRight
            className="overview-stat-chevron"
            size={14}
            aria-hidden
          />
        )}
      </span>
      <span className="overview-stat-value">{value}</span>
      <span className="overview-stat-line" data-tone={lineTone}>
        {line}
      </span>
      <span className="overview-stat-picture">
        {picture}
        {note && <span className="overview-stat-note">{note}</span>}
      </span>
    </>
  );
  return (
    <li>
      {onOpen ? (
        <button
          type="button"
          className="overview-stat"
          aria-label={[`${label}: ${value}`, line, note]
            .filter(Boolean)
            .join('. ')}
          onClick={onOpen}
        >
          {body}
        </button>
      ) : (
        <div className="overview-stat">{body}</div>
      )}
    </li>
  );
}

/** The few most connected memories as a still picture (no WebGL here). */
function MemoryGraph({ graph }: { graph: KnowledgeGraphSnapshot }) {
  const nodes = graph.nodes.slice(0, GRAPH_POINTS.length);
  const place = new Map(nodes.map((node, index) => [node.id, index]));
  return (
    <svg
      className="overview-graph"
      width="84"
      height="30"
      viewBox="0 0 84 30"
      aria-hidden
      focusable="false"
    >
      <g className="overview-graph-edges">
        {graph.edges.map((edge) => {
          const from = place.get(edge.source_id);
          const to = place.get(edge.target_id);
          if (from === undefined || to === undefined) return null;
          return (
            <line
              key={edge.id}
              x1={GRAPH_POINTS[from][0]}
              y1={GRAPH_POINTS[from][1]}
              x2={GRAPH_POINTS[to][0]}
              y2={GRAPH_POINTS[to][1]}
            />
          );
        })}
      </g>
      {nodes.map((node, index) => (
        <circle
          key={node.id}
          cx={GRAPH_POINTS[index][0]}
          cy={GRAPH_POINTS[index][1]}
          r={Math.min(5, 2.5 + node.relation_count * 0.5)}
          fill={`var(${typeToken(node.entity_type)})`}
        />
      ))}
    </svg>
  );
}

function AgentFaces({
  rows,
  live,
  pulse,
}: {
  rows: readonly ConversationView[];
  live: boolean;
  pulse: boolean;
}) {
  if (!rows.length) return null;
  return (
    <span className="overview-agents">
      {rows.slice(0, AVATAR_LIMIT).map((row) => (
        <span key={row.id} className="overview-agent">
          {/* The list carries no profile, so the icon follows the thread. */}
          <AgentAvatar seed={agentSeed(null, row.id)} size={24} />
          <StatusDot
            className="overview-agent-dot"
            tone={live ? 'info' : 'success'}
            label={live ? 'Working' : 'Finished'}
            pulse={live && pulse}
          />
        </span>
      ))}
    </span>
  );
}

/**
 * What Row-Bot learned this week: memories saved from conversations and the
 * subjects Dream Cycle added to, both from Monitor's journals.
 */
function learnedThisWeek(
  monitor: MonitorSnapshot | null,
  from: Date,
  to: Date,
) {
  const extractions = (monitor?.extraction_journal ?? []).filter((entry) =>
    within(entry.timestamp, from, to),
  );
  const saved = extractions.reduce(
    (sum, entry) =>
      sum + entry.threads.reduce((total, thread) => total + thread.saved, 0),
    0,
  );
  const threads = new Set(
    extractions.flatMap((entry) =>
      entry.threads
        .filter((thread) => thread.saved > 0)
        .map((thread) => thread.label),
    ),
  );
  const seen = new Set<string>();
  const subjects = (monitor?.dream_journal ?? [])
    .filter((entry) => within(entry.timestamp, from, to))
    .sort(
      (left, right) =>
        parseTimestamp(right.timestamp)!.getTime() -
        parseTimestamp(left.timestamp)!.getTime(),
    )
    .flatMap((entry) => [
      ...entry.enrichments.map((item) => item.subject),
      ...entry.merges.map((item) => item.survivor_subject),
      ...entry.inferred_relations.flatMap((item) => [
        item.source_subject,
        item.target_subject,
      ]),
    ])
    .filter((subject) => {
      const key = subject.trim().toLowerCase();
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  return { saved, threads: threads.size, subjects };
}

/** Since 6 PM yesterday, oldest first: runs, learning, Dream Cycle, agents. */
function sinceYesterday({
  tasks,
  monitor,
  conversations,
  from,
  to,
  open,
}: {
  tasks: readonly TaskSummary[] | null;
  monitor: MonitorSnapshot | null;
  conversations: readonly ConversationView[];
  from: Date;
  to: Date;
  open: {
    workflow: (id: string) => void;
    tab: (tab: 'knowledge' | 'monitor') => void;
    conversation: (id: string) => void;
  };
}): Moment[] {
  const moments: Moment[] = [];
  for (const task of tasks ?? [])
    for (const [index, run] of (task.recent_runs ?? []).entries()) {
      const date = within(run.started_at, from, to);
      if (!date) continue;
      const status = runStatus(run.status);
      const failed = status.tone === 'danger';
      moments.push({
        key: `run:${task.id}:${index}`,
        at: date,
        tone: failed
          ? 'danger'
          : status.tone === 'warning'
            ? 'warning'
            : status.tone === 'success'
              ? 'success'
              : '4',
        icon: failed ? <X size={12} /> : <Workflow size={12} />,
        title: `${task.name} ${failed ? 'failed' : 'ran'}`,
        detail: status.label,
        onOpen: () => open.workflow(task.id),
      });
    }
  for (const [index, entry] of (monitor?.extraction_journal ?? []).entries()) {
    const date = within(entry.timestamp, from, to);
    const learned = entry.threads.filter((thread) => thread.saved > 0);
    const saved = learned.reduce((sum, thread) => sum + thread.saved, 0);
    if (!date || !saved) continue;
    moments.push({
      key: `learned:${index}`,
      at: date,
      tone: '3',
      icon: <Sparkles size={12} />,
      title: `Learned ${plural(saved, 'new thing')}`,
      detail: `From ${listWords(learned.map((thread) => thread.label))}`,
      onOpen: () => open.tab('knowledge'),
    });
  }
  const dreams = (monitor?.dream_journal ?? []).filter((entry) =>
    within(entry.timestamp, from, to),
  );
  for (const [index, entry] of dreams.entries()) {
    const changes = [
      entry.merges.length &&
        `merged ${plural(entry.merges.length, 'duplicate')}`,
      entry.enrichments.length &&
        `enriched ${plural(entry.enrichments.length, 'memory', 'memories')}`,
      entry.inferred_relations.length &&
        `inferred ${plural(entry.inferred_relations.length, 'connection')}`,
    ].filter(Boolean);
    moments.push({
      key: `dream:${index}`,
      at: parseTimestamp(entry.timestamp)!,
      tone: '2',
      icon: <Moon size={12} />,
      title: changes.length ? 'Dream Cycle tidied memory' : 'Dream Cycle ran',
      detail: changes.length
        ? capitalize(changes.join(', '))
        : 'Found nothing to change',
      onOpen: () => open.tab('monitor'),
    });
  }
  const dreamRun =
    monitor?.dream.availability === 'available' &&
    within(monitor.dream.last_run, from, to);
  if (!dreams.length && dreamRun)
    moments.push({
      key: 'dream',
      at: dreamRun,
      tone: '2',
      icon: <Moon size={12} />,
      title: 'Dream Cycle ran',
      detail: '',
      onOpen: () => open.tab('monitor'),
    });
  for (const row of conversations) {
    const date = within(row.updated_at, from, to);
    if (!date || !row.parent_conversation_id) continue;
    if (row.activity_state !== 'terminal') continue;
    moments.push({
      key: `agent:${row.id}`,
      at: date,
      tone: 'success',
      icon: <Check size={12} />,
      title: `${titleOf(row)} finished`,
      detail: 'Agent',
      onOpen: () => open.conversation(row.id),
    });
  }
  return moments
    .sort((left, right) => left.at.getTime() - right.at.getTime())
    .slice(-TIMELINE_LIMIT);
}

export default function OverviewHome({
  conversations,
  setup,
  monitor,
  loadTasks,
  loadHealth,
  loadApprovals,
  loadMemory,
  loadInsights,
  buddy,
  onAsk,
  asking = false,
  onNewResource,
  onNewWorkflow,
  onOpenConversation,
  onOpenWorkflows,
  onOpenTab,
  onHideSetup,
  setupError = '',
  refreshConversation,
  onResumeAgentWork,
  onDismissAgentWork,
  now: suppliedNow,
  refreshKey,
}: OverviewHomeProps) {
  const [tasks, setTasks] = useState<readonly TaskSummary[] | null>(null);
  const [tasksError, setTasksError] = useState('');
  const [health, setHealth] = useState<SystemDiagnosis | null>(null);
  const [healthFailed, setHealthFailed] = useState(false);
  const [memory, setMemory] = useState<KnowledgeGraphSnapshot | null>(null);
  const [memoryFailed, setMemoryFailed] = useState(false);
  const [insights, setInsights] = useState<InsightsSnapshot | null>(null);
  const [needsOpen, setNeedsOpen] = useState(false);
  const [clock, setClock] = useState(() => new Date());
  const now = suppliedNow ?? clock;
  const reduced = useReducedMotion();
  const approvalsFeed = usePendingApprovals(loadApprovals);
  const waiting = approvalsFeed.page?.items ?? [];

  useEffect(() => {
    if (!loadHealth) return;
    const abort = new AbortController();
    loadHealth(abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) setHealth(value);
      },
      () => {
        if (!abort.signal.aborted) setHealthFailed(true);
      },
    );
    return () => abort.abort();
  }, [loadHealth, refreshKey]);

  useEffect(() => {
    if (!loadTasks) return;
    const abort = new AbortController();
    setTasksError('');
    loadTasks(abort.signal).then(
      (page) => {
        if (!abort.signal.aborted) setTasks(page.items);
      },
      (cause: unknown) => {
        if (!abort.signal.aborted) setTasksError(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [loadTasks, refreshKey]);

  useEffect(() => {
    if (!loadMemory) return;
    const abort = new AbortController();
    loadMemory(abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) setMemory(value);
      },
      () => {
        if (!abort.signal.aborted) setMemoryFailed(true);
      },
    );
    return () => abort.abort();
  }, [loadMemory, refreshKey]);

  useEffect(() => {
    if (!loadInsights) return;
    const abort = new AbortController();
    // Insights are the owner's on this computer; elsewhere the card stays out.
    loadInsights(abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) setInsights(value);
      },
      () => undefined,
    );
    return () => abort.abort();
  }, [loadInsights, refreshKey]);

  // Listed rows are read when the list loads; approvals and runs change after
  // that. Re-read the live ones on open and while Overview stays open.
  const liveIds = conversations
    .filter((row) => waitingApproval(row) || working(row))
    .slice(0, 12)
    .map((row) => row.id)
    .join('|');
  useEffect(() => {
    if (!liveIds || !refreshConversation) return;
    const abort = new AbortController();
    const read = () =>
      void Promise.allSettled(
        liveIds.split('|').map((id) => refreshConversation(id, abort.signal)),
      );
    read();
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') read();
    }, 15_000);
    return () => {
      abort.abort();
      window.clearInterval(timer);
    };
  }, [liveIds, refreshConversation]);

  // Keep "in 2 hours" and "3 minutes ago" honest while the tab stays open.
  useEffect(() => {
    if (suppliedNow) return;
    const timer = window.setInterval(() => setClock(new Date()), 60_000);
    return () => window.clearInterval(timer);
  }, [suppliedNow]);

  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const weekStart = new Date(now.getTime() - WEEK_MS);
  const since = overnightStart(now);

  const view = useMemo(() => {
    const topLevel = conversations.filter((row) => !row.parent_conversation_id);
    const agents = new Map<string, ConversationView[]>();
    for (const row of conversations)
      if (row.parent_conversation_id)
        agents.set(row.parent_conversation_id, [
          ...(agents.get(row.parent_conversation_id) ?? []),
          row,
        ]);
    const taskRows = tasks ?? [];
    return {
      topLevel,
      agents,
      approvals: conversations.filter(waitingApproval),
      running: conversations.filter(
        (row) => !waitingApproval(row) && working(row),
      ),
      waitingWorkflows: taskRows.filter(
        (task) =>
          task.active_run && WAITING_RUN_STATUSES.has(task.active_run.status),
      ),
      failed: taskRows.filter(
        (task) =>
          !task.active_run &&
          FAILED_RUN_STATUSES.has(String(task.last_status ?? '').toLowerCase()),
      ),
      scheduled: taskRows
        .filter((task) => task.enabled && parseTimestamp(task.next_run))
        .sort(
          (left, right) =>
            parseTimestamp(left.next_run)!.getTime() -
            parseTimestamp(right.next_run)!.getTime(),
        ),
    };
  }, [conversations, tasks]);

  const setupDone = setup ? new Set(setup.completed_steps).size : 0;
  const setupSkipped = setup
    ? setup.skipped_steps.filter(
        (step) => !setup.completed_steps.includes(step),
      ).length
    : 0;
  const needsModel = Boolean(setup?.needs_model);
  const showSetup =
    setup &&
    (needsModel ||
      (!setup.dismissed_home_card &&
        setupDone + setupSkipped < setup.steps.length));

  // An approval answered in place is not listed again as its conversation
  // or workflow.
  const answeredHere = new Set(
    waiting.flatMap((item) => [item.conversation_id, item.task_id]),
  );
  const needs: Need[] = [
    ...waiting.map<Need>((approval) => ({
      key: `waiting:${approval.id}`,
      approval,
    })),
    ...view.approvals.flatMap<Need>((row) => {
      const title = titleOf(row);
      const work = interruptedWork(row);
      if (work)
        return {
          key: `approval:${row.id}`,
          item: {
            icon: <AlertTriangle size={16} />,
            tone: 'warning',
            title,
            meta: 'Agent work was interrupted',
            time: <When value={row.updated_at} now={now} />,
            label: `Review agent work in ${title}`,
            onOpen: () => onOpenConversation(row.id),
            actions: (
              <InterruptedWorkControls
                resumable={work.resumable}
                resumeWork={onResumeAgentWork && (() => onResumeAgentWork(row))}
                dismissWork={
                  onDismissAgentWork && (() => onDismissAgentWork(row))
                }
              />
            ),
          },
        };
      if (answeredHere.has(row.id)) return [];
      return {
        key: `approval:${row.id}`,
        item: {
          icon: <ShieldAlert size={16} />,
          tone: 'warning',
          title,
          meta:
            row.activity_state === 'attention' &&
            row.activity_phase !== 'waiting_approval'
              ? 'Agent work needs your attention'
              : 'Waiting for your approval',
          time: <When value={row.updated_at} now={now} />,
          label: `Review approval in ${title}`,
          onOpen: () => onOpenConversation(row.id),
        },
      };
    }),
    ...view.waitingWorkflows
      .filter((task) => !answeredHere.has(task.id))
      .map<Need>((task) => ({
        key: `workflow-approval:${task.id}`,
        item: {
          icon: <ShieldAlert size={16} />,
          tone: 'warning',
          title: task.name,
          meta: 'Workflow waiting for your approval',
          time: <When value={task.active_run?.started_at} now={now} />,
          label: `Review workflow approval: ${task.name}`,
          onOpen: () => onOpenWorkflows(task.id),
        },
      })),
    ...view.failed.map<Need>((task) => ({
      key: `failed:${task.id}`,
      item: {
        icon: <AlertTriangle size={16} />,
        tone: 'danger',
        title: task.name,
        meta: `${runStatus(task.last_status).label} · ${scheduleWords(task.schedule, task.at)}`,
        time: (
          <When
            value={task.recent_runs?.[0]?.started_at ?? task.last_run}
            now={now}
          />
        ),
        label: `Open failed workflow: ${task.name}`,
        onOpen: () => onOpenWorkflows(task.id),
      },
    })),
    // A Monitor check that turned red, checked in the background (B252).
    ...(health?.checks ?? [])
      .filter((check) => check.status === 'error')
      .map<Need>((check) => ({
        key: `health:${check.id}`,
        item: {
          icon: <AlertTriangle size={16} />,
          tone: 'danger',
          title: `${check.name} needs attention`,
          meta: check.detail,
          time: (
            <When
              value={new Date(check.checked_at * 1000).toISOString()}
              now={now}
            />
          ),
          label: `Open Monitor: ${check.name} needs attention`,
          onOpen: () => onOpenTab('monitor'),
        },
      })),
  ];
  const attention = needs.length + (showSetup ? 1 : 0);
  const readingNeeds =
    (loadTasks && tasks === null && !tasksError) ||
    (loadApprovals && !approvalsFeed.page);
  const caughtUp = !attention && !readingNeeds && !tasksError;

  const week = learnedThisWeek(monitor, weekStart, now);
  const timeline = sinceYesterday({
    tasks,
    monitor,
    conversations,
    from: since,
    to: now,
    open: {
      workflow: onOpenWorkflows,
      tab: onOpenTab,
      conversation: onOpenConversation,
    },
  });

  // Status strip.
  const finishedToday = conversations.filter(
    (row) =>
      row.parent_conversation_id &&
      row.activity_state === 'terminal' &&
      within(row.updated_at, today, now),
  );
  const agentTarget = view.running[0] ?? finishedToday[0];
  const next = view.scheduled.find(
    (task) => parseTimestamp(task.next_run)! > now,
  );
  const todayRuns = (tasks ?? [])
    .flatMap((task) =>
      (task.recent_runs ?? []).flatMap((run) => {
        const date = within(run.started_at, today, now);
        return date
          ? [{ date, failed: runStatus(run.status).tone === 'danger' }]
          : [];
      }),
    )
    .sort((left, right) => left.date.getTime() - right.date.getTime());
  const failedToday = todayRuns.filter((run) => run.failed).length;
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  const nextToday = next && parseTimestamp(next.next_run)! < tomorrow;
  const checks = health?.checks ?? [];
  const redChecks = checks.filter((check) => check.status === 'error');
  const amberChecks = checks.filter((check) => check.status === 'warn');
  const firstIssue = redChecks[0] ?? amberChecks[0];
  const lastChecked = Math.max(0, ...checks.map((check) => check.checked_at));

  const recent = view.topLevel.slice(0, CONTINUE_COUNT);
  const last = [...view.topLevel].sort(
    (left, right) =>
      (parseTimestamp(right.updated_at)?.getTime() ?? 0) -
      (parseTimestamp(left.updated_at)?.getTime() ?? 0),
  )[0];
  // An insight that may no longer apply is not the one to lead with (B124).
  const current = insights?.items.filter((item) => !item.out_of_date) ?? [];
  const insight =
    current.find((item) => item.status === 'pinned') ?? current[0];

  // One plain sentence that changes with the state.
  const lead = attention
    ? `${plural(attention, 'thing')} ${attention === 1 ? 'needs' : 'need'} you.`
    : "You're all caught up.";
  const busy = [
    view.running.length &&
      `${plural(view.running.length, 'agent')} ${view.running.length === 1 ? 'is' : 'are'} working`,
    next && `${next.name} runs ${relativeTime(next.next_run, now)}`,
  ].filter((part): part is string => Boolean(part));
  const rest = busy.length
    ? `${capitalize(busy.join(', and '))}.`
    : !attention && week.saved
      ? `Row-Bot learned ${plural(week.saved, 'new thing')} this week.`
      : '';

  const order = (value: number) =>
    ({ '--overview-order': value }) as CSSProperties;
  const quickStarts = [
    onNewResource && (
      <Button
        key="design"
        className="overview-quick-start"
        data-tone="5"
        onClick={() => onNewResource('artifact')}
      >
        <Palette size={15} aria-hidden />
        New design
      </Button>
    ),
    onNewResource && (
      <Button
        key="code"
        className="overview-quick-start"
        data-tone="2"
        onClick={() => onNewResource('workspace')}
      >
        <Code2 size={15} aria-hidden />
        New code folder
      </Button>
    ),
    onNewWorkflow && (
      <Button
        key="workflow"
        className="overview-quick-start"
        data-tone="4"
        onClick={onNewWorkflow}
      >
        <Workflow size={15} aria-hidden />
        New workflow
      </Button>
    ),
    last && (
      <Button
        key="continue"
        className="overview-quick-start"
        data-tone="accent"
        onClick={() => onOpenConversation(last.id)}
      >
        <CornerDownRight size={15} aria-hidden />
        <span className="overview-quick-title">Continue “{titleOf(last)}”</span>
      </Button>
    ),
  ].filter(Boolean);

  return (
    <section
      className="overview-home"
      aria-labelledby="overview-title"
      data-entrance={reduced ? undefined : 'stagger'}
    >
      <div className="overview-bento">
        <header
          className="overview-hero overview-card overview-cell"
          data-span={2}
          style={order(0)}
        >
          <div className="overview-greet">
            {buddy && (
              <span
                className="overview-buddy"
                data-state={attention ? 'waiting' : 'calm'}
              >
                {buddy}
              </span>
            )}
            <div className="overview-greet-text">
              <h2 id="overview-title">{greeting(now)}</h2>
              <p className="overview-summary">
                <strong>{lead}</strong>
                {rest && ` ${rest}`}
              </p>
            </div>
          </div>
          {onAsk && <AskBox onAsk={onAsk} asking={asking} />}
          {quickStarts.length > 0 && (
            <div
              className="overview-quick"
              role="group"
              aria-label="Quick starts"
            >
              {quickStarts}
            </div>
          )}
        </header>

        <Card
          id="overview-needs"
          className="overview-needs"
          title={
            <>
              Needs you
              {attention ? (
                <span className="overview-count">{attention}</span>
              ) : null}
            </>
          }
          tone="warning"
          icon={<Hand size={15} />}
          order={1}
          calm={caughtUp}
        >
          {showSetup &&
            (!needsModel ? (
              <div
                className="overview-setup"
                role="region"
                aria-label="Continue setup"
              >
                <Tile tone="accent">
                  <CalendarClock size={16} />
                </Tile>
                <span className="overview-need-text">
                  <strong className="overview-need-title">Finish setup</strong>
                  <span className="overview-need-meta">
                    {setupDone} of {setup.steps.length} done
                    {setupSkipped ? ` · ${setupSkipped} skipped` : ''}
                  </span>
                </span>
                <Link className="button small" to="/setup">
                  Continue setup
                </Link>
                {onHideSetup && (
                  <IconButton
                    size="sm"
                    label="Hide setup reminder"
                    onClick={onHideSetup}
                  >
                    <X size={15} aria-hidden />
                  </IconButton>
                )}
                {setupError && (
                  <p role="status" className="overview-setup-error">
                    {setupError}
                  </p>
                )}
              </div>
            ) : (
              <div
                className="overview-setup overview-setup-first"
                role="region"
                aria-label="Continue setup"
              >
                <span className="overview-need-text">
                  <strong className="overview-need-title">
                    Choose how Row-Bot thinks
                  </strong>
                  <span className="overview-need-meta">
                    Row-Bot needs a model before it can answer. It takes a
                    minute.
                  </span>
                </span>
                <Link className="button primary small" to="/setup">
                  Choose a model
                </Link>
              </div>
            ))}
          {needs.length > 0 && (
            <ul
              id="overview-needs-list"
              className="overview-needs-list"
              aria-label="Needs you"
            >
              {(needsOpen ? needs : needs.slice(0, NEEDS_SHOWN)).map((need) =>
                'approval' in need ? (
                  <ApprovalRow
                    key={need.key}
                    item={need.approval}
                    onChanged={approvalsFeed.refresh}
                  />
                ) : (
                  <NeedRow key={need.key} item={need.item} />
                ),
              )}
            </ul>
          )}
          {needs.length > NEEDS_SHOWN && (
            <button
              type="button"
              className="overview-needs-more"
              aria-expanded={needsOpen}
              aria-controls="overview-needs-list"
              onClick={() => setNeedsOpen((value) => !value)}
            >
              {needsOpen
                ? 'Show fewer'
                : `+${needs.length - NEEDS_SHOWN} more waiting`}
              {needsOpen ? (
                <ChevronDown size={13} aria-hidden />
              ) : (
                <ChevronRight size={13} aria-hidden />
              )}
            </button>
          )}
          {caughtUp && (
            <div className="overview-caughtup">
              <span className="overview-caughtup-mark" aria-hidden>
                <Check size={28} strokeWidth={2.5} />
              </span>
              <strong className="overview-caughtup-title">All caught up</strong>
              <p>
                Nothing is waiting for you. Approvals and failed runs show up
                here.
              </p>
            </div>
          )}
          {!attention && readingNeeds && (
            <p className="overview-loading" role="status">
              Checking what needs you…
            </p>
          )}
          {tasksError && (
            <p className="overview-error" role="status">
              Workflows could not be read: {tasksError}
            </p>
          )}
        </Card>

        <ul
          className="overview-strip overview-cell"
          data-span={3}
          style={order(2)}
          aria-label="Live status"
        >
          <Stat
            label="Agents"
            tone="3"
            icon={<Bot size={14} />}
            value={
              view.running.length ? `${view.running.length} working` : 'Resting'
            }
            line={
              view.running.length
                ? `${titleOf(view.running[0])}${view.running.length > 1 ? ` and ${view.running.length - 1} more` : ''}`
                : finishedToday.length
                  ? `${plural(finishedToday.length, 'agent')} finished today`
                  : 'No agents at work'
            }
            picture={
              <AgentFaces
                rows={view.running.length ? view.running : finishedToday}
                live={view.running.length > 0}
                pulse={!reduced}
              />
            }
            onOpen={agentTarget && (() => onOpenConversation(agentTarget.id))}
          />
          <Stat
            label="Workflows"
            tone="4"
            icon={<Workflow size={14} />}
            value={
              tasksError || !loadTasks
                ? 'Unavailable'
                : tasks === null
                  ? 'Reading…'
                  : next
                    ? relativeTime(next.next_run, now)
                    : 'Nothing scheduled'
            }
            line={
              tasksError
                ? "Couldn't read workflows"
                : tasks === null
                  ? 'Reading workflows…'
                  : next
                    ? `Next: ${next.name}`
                    : plural(tasks.length, 'workflow')
            }
            picture={
              todayRuns.length || nextToday ? (
                <span className="overview-runs" aria-hidden>
                  {todayRuns.slice(-RUN_MARKS).map((run, index) => (
                    <i
                      key={index}
                      data-status={run.failed ? 'failed' : 'done'}
                    />
                  ))}
                  {nextToday && <i data-status="next" />}
                </span>
              ) : undefined
            }
            note={
              tasks === null
                ? ''
                : todayRuns.length
                  ? `${todayRuns.length} today${failedToday ? ` · ${failedToday} failed` : ''}`
                  : 'No runs today'
            }
            onOpen={() => onOpenWorkflows()}
          />
          <Stat
            label="Memory"
            tone="2"
            icon={<Brain size={14} />}
            value={
              memory
                ? memory.total_entities
                  ? plural(memory.total_entities, 'memory', 'memories')
                  : 'No memories yet'
                : memoryFailed || !loadMemory
                  ? 'Unavailable'
                  : 'Reading…'
            }
            line={
              memoryFailed
                ? 'Memory could not be read'
                : week.saved
                  ? `+${week.saved.toLocaleString()} this week`
                  : 'Nothing new this week'
            }
            lineTone={week.saved ? 'success' : undefined}
            picture={
              memory?.nodes.length ? <MemoryGraph graph={memory} /> : undefined
            }
            onOpen={() => onOpenTab('knowledge')}
          />
          <Stat
            label="Health"
            tone={
              redChecks.length
                ? 'danger'
                : amberChecks.length
                  ? 'warning'
                  : 'success'
            }
            icon={<HeartPulse size={14} />}
            value={
              healthFailed || !loadHealth
                ? 'Unavailable'
                : !health
                  ? 'Checking…'
                  : !checks.length
                    ? 'Not checked yet'
                    : redChecks.length
                      ? plural(redChecks.length, 'issue')
                      : amberChecks.length
                        ? plural(amberChecks.length, 'warning')
                        : 'All good'
            }
            line={
              firstIssue
                ? `${firstIssue.name}: ${firstIssue.detail}`
                : checks.length
                  ? `Checked ${relativeTime(new Date(lastChecked * 1000), now)}`
                  : 'Monitor checks run by themselves'
            }
            picture={
              checks.length ? (
                <span className="overview-checks" aria-hidden>
                  {checks.map((check) => (
                    <i key={check.id} data-status={check.status} />
                  ))}
                </span>
              ) : undefined
            }
            note={checks.length ? plural(checks.length, 'check') : ''}
            onOpen={() => onOpenTab('monitor')}
          />
        </ul>

        <section
          className="overview-continue overview-cell"
          data-span={2}
          style={order(3)}
          aria-labelledby="overview-continue"
        >
          <header className="overview-section-title">
            <h3 id="overview-continue">Continue where you left off</h3>
            <Link className="overview-section-link" to="/library">
              Library
              <ChevronRight size={13} aria-hidden />
            </Link>
          </header>
          {recent.length ? (
            <ul
              className="overview-conversations"
              aria-label="Continue where you left off"
            >
              {recent.map((row) => {
                const kinds = conversationKinds(row);
                const agents = view.agents.get(row.id) ?? [];
                const live = working(row);
                const phase = humanizeToken(row.activity_phase);
                const line = waitingApproval(row)
                  ? interruptedWork(row)
                    ? 'Agent work was interrupted'
                    : 'Waiting for you'
                  : live
                    ? row.activity_state === 'active'
                      ? `Agents working${phase ? ` · ${phase}` : ''}`
                      : 'Replying'
                    : kinds.length
                      ? capitalize(
                          kinds
                            .map((kind) => KIND_WORDS[kind].toLowerCase())
                            .join(' and '),
                        )
                      : 'Chat';
                return (
                  <li key={row.id}>
                    <button
                      type="button"
                      className="overview-conversation"
                      aria-label={`Open ${titleOf(row)}`}
                      onClick={() => onOpenConversation(row.id)}
                    >
                      <span className="overview-conversation-head">
                        <Tile
                          tone={kinds.length ? KIND_TONES[kinds[0]] : 'accent'}
                        >
                          <ConversationGlyph row={row} size={15} />
                        </Tile>
                        <span className="overview-conversation-title">
                          {titleOf(row)}
                        </span>
                        {row.pinned && (
                          <span className="overview-conversation-pin">
                            <Pin size={13} aria-hidden />
                            <span className="visually-hidden">Pinned</span>
                          </span>
                        )}
                      </span>
                      <span className="overview-conversation-line">{line}</span>
                      <span className="overview-conversation-meta">
                        {live && (
                          <StatusDot
                            tone="info"
                            label="Working"
                            pulse={!reduced}
                          />
                        )}
                        {agents.length > 0 && (
                          <span className="overview-agents overview-agents-small">
                            {agents.slice(0, 3).map((agent) => (
                              <AgentAvatar
                                key={agent.id}
                                seed={agentSeed(null, agent.id)}
                                size={20}
                              />
                            ))}
                            <span className="visually-hidden">
                              {plural(agents.length, 'agent')}
                            </span>
                          </span>
                        )}
                        <span className="overview-conversation-time">
                          <When value={row.updated_at} now={now} fallback="" />
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <InlineEmpty>No conversations yet.</InlineEmpty>
          )}
        </section>

        <Card
          id="overview-today"
          className="overview-today"
          title="Since yesterday evening"
          tone="6"
          icon={<Clock size={15} />}
          order={4}
          action={
            <SectionLink onClick={() => onOpenTab('monitor')}>
              Monitor
            </SectionLink>
          }
        >
          {timeline.length ? (
            <ol
              className="overview-timeline"
              aria-label="Since yesterday evening"
            >
              {timeline.map((moment) => (
                <li key={moment.key}>
                  <button type="button" onClick={moment.onOpen}>
                    <time dateTime={moment.at.toISOString()}>
                      {clockTime(moment.at.getHours(), moment.at.getMinutes())}
                    </time>
                    <span
                      className="overview-timeline-dot"
                      data-tone={moment.tone}
                      aria-hidden
                    >
                      {moment.icon}
                    </span>
                    <span className="overview-timeline-what">
                      {moment.title}
                      {moment.detail && <small>{moment.detail}</small>}
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          ) : (
            <InlineEmpty>Quiet. Nothing ran since 6 PM yesterday.</InlineEmpty>
          )}
        </Card>

        <Card
          id="overview-learned"
          className="overview-learned"
          title="Learned this week"
          tone="3"
          icon={<Sparkles size={15} />}
          span={3}
          order={5}
          action={
            <SectionLink onClick={() => onOpenTab('knowledge')}>
              Knowledge
            </SectionLink>
          }
        >
          <div className="overview-learned-body">
            {week.saved || week.subjects.length ? (
              <>
                {week.saved > 0 && (
                  <p className="overview-learned-line">
                    {plural(week.saved, 'new memory', 'new memories')} from{' '}
                    {plural(week.threads, 'conversation')}
                  </p>
                )}
                {week.subjects.length > 0 && (
                  <ul
                    className="overview-chips"
                    aria-label="Memories added to this week"
                  >
                    {week.subjects.slice(0, CHIP_LIMIT).map((subject) => (
                      <li key={subject}>
                        <button
                          type="button"
                          className="overview-chip"
                          onClick={() => onOpenTab('knowledge')}
                        >
                          {subject}
                        </button>
                      </li>
                    ))}
                    {week.subjects.length > CHIP_LIMIT && (
                      <li>
                        <button
                          type="button"
                          className="overview-chip"
                          data-more="true"
                          onClick={() => onOpenTab('knowledge')}
                        >
                          +{week.subjects.length - CHIP_LIMIT} more
                        </button>
                      </li>
                    )}
                  </ul>
                )}
              </>
            ) : (
              <InlineEmpty>
                Nothing new this week. Row-Bot learns from your conversations as
                you go.
              </InlineEmpty>
            )}
          </div>
          {insight && (
            <div className="overview-insight">
              <Tile tone="4">
                <Lightbulb size={15} />
              </Tile>
              <div className="overview-insight-text">
                <strong>{insight.title}</strong>
                <p>{insight.suggestion || insight.body}</p>
                <Button className="small" onClick={() => onOpenTab('insights')}>
                  Open Insights
                </Button>
              </div>
            </div>
          )}
        </Card>
      </div>
    </section>
  );
}
