import {
  useEffect,
  useState,
  type CSSProperties,
  type FormEvent,
  type ReactNode,
} from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowUp,
  ArrowUpRight,
  Bot,
  Brain,
  CalendarClock,
  ChevronDown,
  ChevronRight,
  Code2,
  HeartPulse,
  Hourglass,
  ImageOff,
  Pencil,
  MessageSquare,
  Palette,
  Paperclip,
  RotateCw,
  Send,
  ShieldAlert,
  Workflow,
  X,
} from 'lucide-react';
import type {
  AttentionProblem,
  AttentionSnapshot,
  ConversationView,
  MonitorSnapshot,
  OnboardingSnapshot,
  PendingApproval,
  PendingApprovalPage,
  ScheduledPost,
  ScheduledPostPage,
  SystemDiagnosis,
  TaskSummary,
  TaskSummaryPage,
  XPostEdit,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { Hint, IconButton } from '../../ui/primitives';
import ApprovalCard from '../shell/ApprovalCard';
import { XPostEditor } from '../shell/XPostDetails';
import { fallbackDue } from '../../api/live-updates';
import { useLive, useLiveUpdate } from '../../live';
import { parseTimestamp, relativeTime } from '../../ui/format';
import { useReducedMotion } from '../buddy/BuddyAvatar';
import { ConversationGlyph } from '../shell/ConversationGlyph';
import { conversationKinds } from '../shell/conversation-groups';
import {
  InterruptedWorkControls,
  interruptedWork,
} from '../shell/DelegatedActivity';
import { ApprovalDecision, WaitingSince } from '../shell/InPlaceApproval';
import {
  conversationsWaiting,
  needsCount,
  useAttention,
  waitingOnYou,
} from '../shell/needs';
import { usePendingApprovals } from '../shell/pending-approvals';
import FixAction, { RunAgain } from './FixAction';
import { useWaits } from '../shell/Waits';
import { When, greeting, plural } from './home-format';

export type OverviewHomeProps = {
  conversations: readonly ConversationView[];
  setup: OnboardingSnapshot | null;
  monitor: MonitorSnapshot | null;
  /** Absent until the workspace is connected. */
  loadTasks?: (signal: AbortSignal) => Promise<TaskSummaryPage>;
  /** Monitor's kept check results (B252): a warning shows under Today. */
  loadHealth?: (signal: AbortSignal) => Promise<SystemDiagnosis>;
  /** Run every check now: a red check's "Check again". */
  onRunDiagnosis?: () => Promise<SystemDiagnosis>;
  /** Every approval waiting for the person, the shell's shared read (B255). */
  loadApprovals?: (signal?: AbortSignal) => Promise<PendingApprovalPage>;
  /** What needs the person, the sidebar indicator's shared read (B383). */
  loadAttention?: (signal?: AbortSignal) => Promise<AttentionSnapshot>;
  /** Posts approved for a time slot, waiting for it: Review lists them (G3). */
  loadScheduledPosts?: (signal?: AbortSignal) => Promise<ScheduledPostPage>;
  onCancelScheduledPost?: (id: string) => Promise<unknown>;
  /** Try again on a post that didn't go out: it goes now, or without its media. */
  onRetryScheduledPost?: (
    id: string,
    withoutMedia: boolean,
  ) => Promise<unknown>;
  /** The person's edit of a post waiting for its time: its text and which files go. */
  onEditScheduledPost?: (id: string, edit: XPostEdit) => Promise<unknown>;
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
  onOpenTab: (tab: 'knowledge' | 'monitor') => void;
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
  'accent' | '2' | '3' | '4' | '5' | 'success' | 'warning' | 'danger';

type Item = {
  key: string;
  icon: ReactNode;
  tone: TileTone;
  title: string;
  meta?: ReactNode;
  time?: ReactNode;
  label: string;
  onOpen: () => void;
  /** Icon actions beside the row (never inside its button). */
  actions?: ReactNode;
};

/** Needs you shows three at first, then "+N more waiting". */
const NEEDS_SHOWN = 3;
const RECENT_COUNT = 3;
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

const KIND_TONES = { designer: '5', code: '2', workflow: '4' } as const;
const SOURCE_TILES: Record<
  PendingApproval['source'],
  { tone: TileTone; icon: ReactNode }
> = {
  workflow: { tone: '4', icon: <Workflow size={16} /> },
  conversation: { tone: 'accent', icon: <MessageSquare size={16} /> },
  agent: { tone: '3', icon: <Bot size={16} /> },
};

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

/** "Today, 1:00 PM" or "Thu 30 Oct, 9:30 AM": when a scheduled post goes out. */
function slotWords(at: string, now: Date) {
  const when = new Date(at);
  const time = when.toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  });
  return when.toDateString() === now.toDateString()
    ? `Today, ${time}`
    : `${when.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}, ${time}`;
}

/** "A", "A and B", "A, B and C". */
function listWords(items: readonly string[]) {
  if (items.length <= 2) return items.join(' and ');
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

function Tile({ tone, children }: { tone: TileTone; children: ReactNode }) {
  return (
    <span className="overview-tile" data-tone={tone} aria-hidden>
      {children}
    </span>
  );
}

/** One plain row: a tile, a title and a line, opening its place. */
function Row({ item }: { item: Item }) {
  return (
    <li className="overview-row">
      <button
        type="button"
        className="overview-row-button"
        aria-label={item.label}
        onClick={item.onOpen}
      >
        <Tile tone={item.tone}>{item.icon}</Tile>
        <span className="overview-row-text">
          <span className="overview-row-title">{item.title}</span>
          {item.meta && <span className="overview-row-meta">{item.meta}</span>}
        </span>
        {item.time && <span className="overview-row-time">{item.time}</span>}
        <ChevronRight className="overview-row-chevron" size={15} aria-hidden />
      </button>
      {item.actions && (
        <div className="overview-row-actions">{item.actions}</div>
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
    <li className="overview-row overview-row-approval">
      <div className="overview-row-body">
        <Tile tone={tile.tone}>{tile.icon}</Tile>
        <span className="overview-row-text">
          <strong className="overview-row-title">{item.title}</strong>
          <span className="overview-row-meta">
            {item.what && <>{item.what} · </>}
            <WaitingSince value={item.requested_at} />
          </span>
        </span>
      </div>
      <div className="overview-row-actions">
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
          kind={item.kind}
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

/** One section of the column: a heading, an optional link, its rows. */
function Section({
  id,
  title,
  action,
  className = '',
  order,
  children,
}: {
  id: string;
  title: ReactNode;
  action?: ReactNode;
  className?: string;
  order: number;
  children: ReactNode;
}) {
  return (
    <section
      className={`overview-section overview-cell ${className}`}
      style={{ '--overview-order': order } as CSSProperties}
      aria-labelledby={id}
    >
      <header className="overview-section-title">
        <h3 id={id}>{title}</h3>
        {action}
      </header>
      {children}
    </section>
  );
}

/** Memories saved this week, from Monitor's extraction journal. */
function savedThisWeek(monitor: MonitorSnapshot | null, from: Date, to: Date) {
  return (monitor?.extraction_journal ?? []).reduce((sum, entry) => {
    const date = parseTimestamp(entry.timestamp);
    if (!date || date < from || date > to) return sum;
    return (
      sum + entry.threads.reduce((total, thread) => total + thread.saved, 0)
    );
  }, 0);
}

export default function OverviewHome({
  conversations,
  setup,
  monitor,
  loadTasks,
  loadHealth,
  onRunDiagnosis,
  loadApprovals,
  loadAttention,
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
  loadScheduledPosts,
  onCancelScheduledPost,
  onRetryScheduledPost,
  onEditScheduledPost,
}: OverviewHomeProps) {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<readonly TaskSummary[] | null>(null);
  const [tasksFailed, setTasksFailed] = useState(false);
  const [health, setHealth] = useState<SystemDiagnosis | null>(null);
  const [healthFailed, setHealthFailed] = useState(false);
  const [needsOpen, setNeedsOpen] = useState(false);
  // A fix changed something: read workflows and checks again.
  const [fixed, setFixed] = useState(0);
  // Ticks once a minute; times are read at each render, so a run starting
  // or a chat saved since the last tick is never words from the past minute.
  const [, setClock] = useState(0);
  const now = suppliedNow ?? new Date();
  const reduced = useReducedMotion();
  const approvalsFeed = usePendingApprovals(loadApprovals);
  const attentionFeed = useAttention(loadAttention);
  // Review (G3): each post or email waiting to go out, in full, and the posts scheduled for later.
  const [later, setLater] = useState<ReadonlySet<string>>(() => new Set());
  const [scheduled, setScheduled] = useState<readonly ScheduledPost[]>([]);
  const [scheduleError, setScheduleError] = useState('');
  // The post being edited in place (its id), and whether its edit is being saved.
  const [editingPost, setEditingPost] = useState('');
  const [savingEdit, setSavingEdit] = useState(false);
  const reviewKey = (approvalsFeed.page?.items ?? [])
    .filter((item) => item.goes_out)
    .map((item) => item.id)
    .join('|');
  useEffect(() => {
    if (!loadScheduledPosts) return;
    const abort = new AbortController();
    loadScheduledPosts(abort.signal).then(
      (page) => {
        if (!abort.signal.aborted) setScheduled(page.items);
      },
      () => undefined,
    );
    return () => abort.abort();
  }, [loadScheduledPosts, reviewKey, refreshKey]);
  async function cancelScheduled(post: ScheduledPost) {
    if (!onCancelScheduledPost) return;
    setScheduleError('');
    try {
      await onCancelScheduledPost(post.id);
      setScheduled((items) => items.filter((item) => item.id !== post.id));
    } catch (cause) {
      setScheduleError(clientError(cause).message);
    }
  }
  async function editScheduled(post: ScheduledPost, change: XPostEdit) {
    if (!onEditScheduledPost) return;
    setScheduleError('');
    setSavingEdit(true);
    try {
      await onEditScheduledPost(post.id, change);
      const kept = new Set(change.media ?? []);
      setScheduled((items) =>
        items.map((item) =>
          item.id === post.id
            ? {
                ...item,
                text: change.text,
                media: (item.media ?? []).filter((file) =>
                  kept.has(file.sha256),
                ),
              }
            : item,
        ),
      );
      setEditingPost('');
    } catch (cause) {
      setScheduleError(clientError(cause).message);
    } finally {
      setSavingEdit(false);
    }
  }
  async function retryScheduled(post: ScheduledPost, withoutMedia: boolean) {
    if (!onRetryScheduledPost) return;
    setScheduleError('');
    try {
      await onRetryScheduledPost(post.id, withoutMedia);
      // It goes now; the list reads again once X has answered.
      setScheduled((items) => items.filter((item) => item.id !== post.id));
    } catch (cause) {
      setScheduleError(clientError(cause).message);
    }
  }
  const notSent = scheduled.filter(
    (post) => post.status && post.status !== 'scheduled',
  );
  const upcoming = scheduled.filter(
    (post) => !post.status || post.status === 'scheduled',
  );
  const refixed = () => {
    setFixed((value) => value + 1);
    attentionFeed.refresh();
  };

  // A workflow run started, finished or waits (B407): Today shows it at once.
  const [runsChanged, setRunsChanged] = useState(0);
  useLiveUpdate(['runs'], () => setRunsChanged((value) => value + 1));
  // The Workflows check counts runs and their approvals (B414): an answered
  // approval clears its warning at once.
  const [workflowsChanged, setWorkflowsChanged] = useState(0);
  useLiveUpdate(['runs', 'approvals'], () =>
    setWorkflowsChanged((value) => value + 1),
  );

  useEffect(() => {
    if (!loadHealth) return;
    const abort = new AbortController();
    setHealthFailed(false);
    loadHealth(abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) setHealth(value);
      },
      () => {
        if (!abort.signal.aborted) setHealthFailed(true);
      },
    );
    return () => abort.abort();
  }, [fixed, loadHealth, refreshKey, workflowsChanged]);

  useEffect(() => {
    if (!loadTasks) return;
    const abort = new AbortController();
    setTasksFailed(false);
    loadTasks(abort.signal).then(
      (page) => {
        if (!abort.signal.aborted) setTasks(page.items);
      },
      () => {
        if (!abort.signal.aborted) setTasksFailed(true);
      },
    );
    return () => abort.abort();
  }, [fixed, loadTasks, refreshKey, runsChanged]);

  // Listed rows are read when the list loads; approvals and runs change after
  // that. Re-read the live ones on open and while Overview stays open.
  const live = useLive();
  const liveIds = conversations
    .filter((row) => waitingOnYou(row) || working(row))
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
    // Rows follow live updates (B407); this is the fallback while the
    // live stream is down.
    let readAt = Date.now();
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible' || !fallbackDue(live, readAt))
        return;
      readAt = Date.now();
      read();
    }, 15_000);
    return () => {
      abort.abort();
      window.clearInterval(timer);
    };
  }, [liveIds, live, refreshConversation]);

  // Keep "in 2 hours" and "3 minutes ago" honest while the tab stays open.
  useEffect(() => {
    if (suppliedNow) return;
    const timer = window.setInterval(
      () => setClock((tick) => tick + 1),
      60_000,
    );
    return () => window.clearInterval(timer);
  }, [suppliedNow]);

  const needsModel = Boolean(setup?.needs_model);
  const setupDone = setup ? new Set(setup.completed_steps).size : 0;
  const setupSkipped = setup
    ? setup.skipped_steps.filter(
        (step) => !setup.completed_steps.includes(step),
      ).length
    : 0;
  const showSetup =
    setup &&
    !needsModel &&
    !setup.dismissed_home_card &&
    setupDone + setupSkipped < setup.steps.length;

  // Needs you: the same items, and the same number, as the sidebar (B383).
  // What goes out (a post, an email) shows in full under Review, with one row here.
  const waiting = approvalsFeed.page?.items ?? [];
  const review = waiting.filter((item) => item.goes_out);
  const reviewShown = review.filter((item) => !later.has(item.id));
  const reviewAside = review.length - reviewShown.length;
  const reviewWords = listWords(
    [
      ['post', review.filter((item) => item.goes_out === 'post').length],
      ['email', review.filter((item) => item.goes_out === 'email').length],
    ]
      .filter(([, count]) => count)
      .map(([word, count]) => plural(count as number, word as string)),
  );
  const problems = attentionFeed.value?.problems ?? [];
  const attention = needsCount({
    approvals: approvalsFeed.page,
    problems,
    conversations,
  });
  const conversationNeeds = conversationsWaiting(conversations, waiting).map(
    (row): Item => {
      const title = titleOf(row);
      const work = interruptedWork(row);
      const goal = row.activity_phase === 'goal_needs_you';
      return {
        key: `conversation:${row.id}`,
        icon: work ? <AlertTriangle size={16} /> : <ShieldAlert size={16} />,
        tone: 'warning',
        title,
        meta: work
          ? 'Agent work was interrupted'
          : goal
            ? 'Goal waiting for your answer'
            : row.activity_state === 'attention' &&
                row.activity_phase !== 'waiting_approval'
              ? 'Agent work needs your attention'
              : 'Waiting for your approval',
        time: <When value={row.updated_at} now={now} />,
        label: work
          ? `Review agent work in ${title}`
          : goal
            ? `Answer the goal in ${title}`
            : `Review approval in ${title}`,
        onOpen: () => onOpenConversation(row.id),
        actions: work && (
          <InterruptedWorkControls
            resumable={work.resumable}
            resumeWork={onResumeAgentWork && (() => onResumeAgentWork(row))}
            dismissWork={onDismissAgentWork && (() => onDismissAgentWork(row))}
          />
        ),
      };
    },
  );
  const problemNeeds = problems.map((problem: AttentionProblem): Item => {
    // A workflow its removed code folder stopped (N9): running it again can't help;
    // its fix is choosing a folder, as on the run itself.
    const folder =
      problem.fix?.kind === 'choose_folder' && problem.fix.href
        ? problem.fix
        : null;
    const taskId =
      problem.place === 'workflows' && !folder
        ? problem.id.replace(/^workflow:/, '')
        : '';
    const open = folder
      ? () => navigate(folder.href!)
      : taskId
        ? () => onOpenWorkflows(taskId)
        : problem.place === 'health' || !problem.fix?.href
          ? () => onOpenTab('monitor')
          : () => navigate(problem.fix!.href!);
    return {
      key: `problem:${problem.id}`,
      icon:
        taskId || folder ? <Workflow size={16} /> : <AlertTriangle size={16} />,
      tone: taskId || problem.place === 'health' ? 'danger' : 'warning',
      title: problem.title,
      meta: problem.detail,
      label: `Open ${problem.title}`,
      onOpen: open,
      actions: taskId ? (
        <RunAgain
          taskId={taskId}
          name={problem.fix?.name ?? problem.title}
          onStarted={refixed}
        />
      ) : (
        problem.fix && (
          <FixAction
            look="icon"
            fix={problem.fix}
            onCheckAgain={
              onRunDiagnosis &&
              (() => void onRunDiagnosis().then(refixed, refixed))
            }
            onFixed={refixed}
          />
        )
      ),
    };
  });
  const needRows = [
    ...(review.length
      ? [
          {
            key: 'review',
            item: {
              key: 'review',
              icon: <Send size={16} />,
              tone: 'warning' as const,
              title: `${reviewWords} to review`,
              meta: 'Shown in full under Review',
              label: 'Go to Review',
              onOpen: () =>
                document
                  .getElementById('overview-review')
                  ?.closest('section')
                  ?.scrollIntoView({
                    behavior: reduced ? 'auto' : 'smooth',
                    block: 'start',
                  }),
            },
          },
        ]
      : []),
    ...waiting
      .filter((approval) => !approval.goes_out)
      .map((approval) => ({
        key: `approval:${approval.id}`,
        approval,
      })),
    ...conversationNeeds.map((item) => ({ key: item.key, item })),
    ...problemNeeds.map((item) => ({ key: item.key, item })),
  ];
  const readingNeeds =
    (loadApprovals && !approvalsFeed.page) ||
    (loadAttention && !attentionFeed.value);

  // Today: up to four rows, only facts that exist, each opening its tab.
  const running = conversations.filter(
    (row) => !waitingOnYou(row) && working(row),
  );
  const next = (tasks ?? [])
    .filter((task) => task.enabled && parseTimestamp(task.next_run))
    .map((task) => ({ task, at: parseTimestamp(task.next_run)! }))
    .filter(({ at }) => at > now)
    .sort((left, right) => left.at.getTime() - right.at.getTime())[0]?.task;
  // A kept warning that is current; a stale one may be from before a
  // channel was turned off or Row-Bot updated (B384). Red checks are needs.
  const warnings = (health?.checks ?? []).filter(
    (check) => check.status === 'warn' && !check.stale,
  );
  const saved = savedThisWeek(monitor, new Date(now.getTime() - WEEK_MS), now);
  const today: Item[] = [];
  if (running.length)
    today.push({
      key: 'running',
      icon: <Bot size={16} />,
      tone: '3',
      title:
        running.length === 1
          ? `${titleOf(running[0])} is working`
          : `${plural(running.length, 'agent')} are working`,
      label: 'Open Monitor: agents at work',
      onOpen: () => onOpenTab('monitor'),
    });
  if (next)
    today.push({
      key: 'next',
      icon: <CalendarClock size={16} />,
      tone: '4',
      title: `${next.name} runs ${relativeTime(next.next_run, now)}`,
      label: `Open Workflows: ${next.name}`,
      onOpen: () => onOpenWorkflows(next.id),
    });
  if (warnings.length) {
    const first = warnings[0];
    today.push({
      key: 'health',
      icon: <HeartPulse size={16} />,
      tone: 'warning',
      title: `${first.name}: ${first.detail}`,
      meta:
        warnings.length > 1
          ? `And ${plural(warnings.length - 1, 'more warning')}`
          : undefined,
      label: `Open Monitor: ${first.name}`,
      onOpen: () => onOpenTab('monitor'),
      actions: first.fix && (
        <FixAction
          look="icon"
          fix={first.fix}
          onCheckAgain={
            onRunDiagnosis &&
            (() => void onRunDiagnosis().then(setHealth, refixed))
          }
          onFixed={refixed}
        />
      ),
    });
  }
  if (saved)
    today.push({
      key: 'memory',
      icon: <Brain size={16} />,
      tone: '2',
      title: `${plural(saved, 'memory', 'memories')} saved this week`,
      label: 'Open Knowledge',
      onOpen: () => onOpenTab('knowledge'),
    });
  const unread = [
    tasksFailed && 'workflows',
    healthFailed && 'health checks',
  ].filter((name): name is string => Boolean(name));

  // What conversations wait for (5.3.1): nothing needs the person; they carry on by themselves.
  const { waits, stop: stopWait } = useWaits();
  const waitRows = waits.map((wait): Item => ({
    key: wait.wait_id,
    icon: <Hourglass size={15} aria-hidden />,
    tone: '3',
    title: wait.label,
    meta: `${wait.conversation_title || 'A conversation'} · ${wait.how}`,
    label: `Open ${wait.conversation_title || 'the conversation'}`,
    onOpen: () => onOpenConversation(wait.conversation_id),
    actions: (
      <IconButton
        size="sm"
        label="Stop waiting"
        onClick={() => void stopWait(wait.wait_id)}
      >
        <X size={15} aria-hidden />
      </IconButton>
    ),
  }));

  const recent = conversations
    .filter((row) => !row.parent_conversation_id)
    .map((row) => ({ row, at: parseTimestamp(row.updated_at)?.getTime() ?? 0 }))
    .sort((left, right) => right.at - left.at)
    .slice(0, RECENT_COUNT)
    .map(({ row }): Item => {
      const kinds = conversationKinds(row);
      return {
        key: row.id,
        icon: <ConversationGlyph row={row} size={15} />,
        tone: kinds.length ? KIND_TONES[kinds[0]] : 'accent',
        title: titleOf(row),
        time: <When value={row.updated_at} now={now} fallback="" />,
        label: `Open ${titleOf(row)}`,
        onOpen: () => onOpenConversation(row.id),
      };
    });

  // One sentence: the most important thing.
  const lead = attention
    ? `${plural(attention, 'thing')} ${attention === 1 ? 'needs' : 'need'} you.`
    : readingNeeds || (loadTasks && tasks === null && !tasksFailed)
      ? ''
      : next
        ? `Next: ${next.name} ${relativeTime(next.next_run, now)}.`
        : waits.length
          ? `Waiting for ${plural(waits.length, 'thing')}.`
          : 'All quiet.';

  const quickStarts = [
    onNewResource && (
      <IconButton
        key="design"
        label="New design"
        className="overview-quick-start"
        data-tone="5"
        onClick={() => onNewResource('artifact')}
      >
        <Palette size={17} aria-hidden />
      </IconButton>
    ),
    onNewResource && (
      <IconButton
        key="code"
        label="New code folder"
        className="overview-quick-start"
        data-tone="2"
        onClick={() => onNewResource('workspace')}
      >
        <Code2 size={17} aria-hidden />
      </IconButton>
    ),
    onNewWorkflow && (
      <IconButton
        key="workflow"
        label="New workflow"
        className="overview-quick-start"
        data-tone="4"
        onClick={onNewWorkflow}
      >
        <Workflow size={17} aria-hidden />
      </IconButton>
    ),
  ].filter(Boolean);

  return (
    <section
      className="overview-home"
      aria-labelledby="overview-title"
      data-entrance={reduced ? undefined : 'stagger'}
    >
      <header
        className="overview-hero overview-card overview-cell"
        style={{ '--overview-order': 0 } as CSSProperties}
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
            {!needsModel && lead && <p className="overview-summary">{lead}</p>}
          </div>
        </div>
        {needsModel ? (
          <div
            className="overview-setup overview-setup-first"
            role="region"
            aria-label="Continue setup"
          >
            <span className="overview-row-text">
              <strong className="overview-row-title">
                Choose how Row-Bot thinks
              </strong>
              <span className="overview-row-meta">
                Row-Bot needs a model before it can answer. It takes a minute.
              </span>
            </span>
            <Link className="button primary small" to="/setup">
              Choose a model
            </Link>
          </div>
        ) : (
          <>
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
          </>
        )}
      </header>

      {!needsModel && (needRows.length > 0 || showSetup) && (
        <Section
          id="overview-needs"
          className="overview-card overview-needs"
          order={1}
          title={
            <>
              Needs you
              {attention ? (
                <span className="overview-count">{attention}</span>
              ) : null}
            </>
          }
        >
          {needRows.length > 0 && (
            <ul
              id="overview-needs-list"
              className="overview-rows"
              aria-label="Needs you"
            >
              {(needsOpen ? needRows : needRows.slice(0, NEEDS_SHOWN)).map(
                (need) =>
                  'approval' in need ? (
                    <ApprovalRow
                      key={need.key}
                      item={need.approval}
                      onChanged={approvalsFeed.refresh}
                    />
                  ) : (
                    <Row key={need.key} item={need.item} />
                  ),
              )}
            </ul>
          )}
          {needRows.length > NEEDS_SHOWN && (
            <button
              type="button"
              className="overview-needs-more"
              aria-expanded={needsOpen}
              aria-controls="overview-needs-list"
              onClick={() => setNeedsOpen((value) => !value)}
            >
              {needsOpen
                ? 'Show fewer'
                : `+${needRows.length - NEEDS_SHOWN} more waiting`}
              {needsOpen ? (
                <ChevronDown size={13} aria-hidden />
              ) : (
                <ChevronRight size={13} aria-hidden />
              )}
            </button>
          )}
          {showSetup && (
            <div
              className="overview-setup"
              role="region"
              aria-label="Continue setup"
            >
              <span className="overview-row-text">
                <strong className="overview-row-title">Finish setup</strong>
                <span className="overview-row-meta">
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
          )}
        </Section>
      )}

      {!needsModel && (review.length > 0 || scheduled.length > 0) && (
        <Section
          id="overview-review"
          className="overview-review"
          order={1}
          title={
            <>
              Review
              {review.length ? (
                <span className="overview-count">{review.length}</span>
              ) : null}
            </>
          }
        >
          {reviewShown.length > 0 && (
            <ul
              className="overview-review-cards"
              aria-label="Waiting to go out"
            >
              {reviewShown.map((item) => (
                <li key={item.id}>
                  <ApprovalCard
                    id={item.id}
                    conversationId={item.conversation_id ?? undefined}
                    shortcut={false}
                    onLater={() => setLater((ids) => new Set(ids).add(item.id))}
                    onResolved={approvalsFeed.refresh}
                  />
                </li>
              ))}
            </ul>
          )}
          {reviewAside > 0 && (
            <button
              type="button"
              className="overview-needs-more"
              onClick={() => setLater(new Set())}
            >
              {reviewAside} set aside · Show
              <ChevronRight size={13} aria-hidden />
            </button>
          )}
          {notSent.length > 0 && (
            <>
              <h4 className="overview-review-heading">Didn't go out</h4>
              <ul
                className="overview-rows"
                aria-label="Posts that didn't go out"
              >
                {notSent.map((post) => (
                  <li
                    key={post.id}
                    className="overview-row overview-row-approval"
                  >
                    <div className="overview-row-body">
                      <Tile tone="warning">
                        <AlertTriangle size={16} />
                      </Tile>
                      <span className="overview-row-text">
                        <strong className="overview-row-title">
                          {slotWords(post.post_at, now)}
                          {post.username && ` · @${post.username}`}
                        </strong>
                        <span className="overview-row-meta overview-review-text">
                          {post.text}
                        </span>
                        <span className="overview-row-meta overview-not-sent-reason">
                          {post.reason}
                        </span>
                      </span>
                    </div>
                    <div className="overview-row-actions">
                      <IconButton
                        size="sm"
                        label="Try again now"
                        onClick={() => void retryScheduled(post, false)}
                      >
                        <RotateCw size={15} aria-hidden />
                      </IconButton>
                      {(post.media?.length ?? 0) > 0 && (
                        <IconButton
                          size="sm"
                          label="Post it now without the picture"
                          onClick={() => void retryScheduled(post, true)}
                        >
                          <ImageOff size={15} aria-hidden />
                        </IconButton>
                      )}
                      <IconButton
                        size="sm"
                        label="Dismiss"
                        onClick={() => void cancelScheduled(post)}
                      >
                        <X size={15} aria-hidden />
                      </IconButton>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
          {upcoming.length > 0 && (
            <>
              <h4 className="overview-review-heading">Scheduled</h4>
              <ul className="overview-rows" aria-label="Scheduled posts">
                {upcoming.map((post) =>
                  editingPost === post.id ? (
                    <li key={post.id} className="overview-row-edit">
                      <XPostEditor
                        post={{
                          ...post,
                          username: post.username ?? '',
                        }}
                        busy={savingEdit}
                        onSave={(change) => void editScheduled(post, change)}
                        onCancel={() => setEditingPost('')}
                      />
                    </li>
                  ) : (
                    <li
                      key={post.id}
                      className="overview-row overview-row-approval"
                    >
                      <div className="overview-row-body">
                        <Tile tone="4">
                          <CalendarClock size={16} />
                        </Tile>
                        <span className="overview-row-text">
                          <strong className="overview-row-title">
                            {slotWords(post.post_at, now)}
                            {post.username && ` · @${post.username}`}
                          </strong>
                          <span className="overview-row-meta overview-review-text">
                            {post.text}
                          </span>
                        </span>
                      </div>
                      <div className="overview-row-actions">
                        {onEditScheduledPost && (
                          <IconButton
                            size="sm"
                            label="Edit this post"
                            onClick={() => {
                              setScheduleError('');
                              setEditingPost(post.id);
                            }}
                          >
                            <Pencil size={15} aria-hidden />
                          </IconButton>
                        )}
                        <IconButton
                          size="sm"
                          label="Cancel this post"
                          onClick={() => void cancelScheduled(post)}
                        >
                          <X size={15} aria-hidden />
                        </IconButton>
                      </div>
                    </li>
                  ),
                )}
              </ul>
            </>
          )}
          {scheduleError && (
            <p role="alert" className="overview-setup-error">
              {scheduleError}
            </p>
          )}
        </Section>
      )}

      {!needsModel && waitRows.length > 0 && (
        <Section id="overview-waiting" title="Waiting" order={2}>
          <ul className="overview-rows" aria-label="Waiting">
            {waitRows.map((item) => (
              <Row key={item.key} item={item} />
            ))}
          </ul>
        </Section>
      )}

      {!needsModel && recent.length > 0 && (
        <Section
          id="overview-recent"
          title="Recent"
          order={2}
          action={
            <Link className="overview-section-link" to="/library">
              All
              <ChevronRight size={13} aria-hidden />
            </Link>
          }
        >
          <ul className="overview-rows" aria-label="Recent">
            {recent.map((item) => (
              <Row key={item.key} item={item} />
            ))}
          </ul>
        </Section>
      )}

      {!needsModel && (today.length > 0 || unread.length > 0) && (
        <Section id="overview-today" title="Today" order={3}>
          {today.length > 0 && (
            <ul className="overview-rows" aria-label="Today">
              {today.map((item) => (
                <Row key={item.key} item={item} />
              ))}
            </ul>
          )}
          {unread.length > 0 && (
            <p className="overview-error" role="status">
              Couldn’t read {listWords(unread)} just now. Their tabs may say
              more.
            </p>
          )}
        </Section>
      )}
    </section>
  );
}
