import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  Bot,
  CalendarClock,
  ChevronRight,
  CircleDot,
  Compass,
  Library,
  Moon,
  ShieldAlert,
  Workflow,
  X,
} from 'lucide-react';
import type {
  ConversationView,
  MonitorSnapshot,
  OnboardingSnapshot,
  TaskSummary,
  TaskSummaryPage,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { IconButton, InlineEmpty, type Tone } from '../../ui/primitives';
import { humanizeToken, parseTimestamp } from '../../ui/format';
import { ConversationGlyph } from '../shell/ConversationGlyph';
import {
  FAILED_RUN_STATUSES,
  WAITING_RUN_STATUSES,
  When,
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
  onOpenConversation: (id: string) => void;
  /** Workflows tab, optionally with one workflow's runs open. */
  onOpenWorkflows: (taskId?: string) => void;
  onOpenTab: (tab: 'knowledge' | 'monitor') => void;
  onHideSetup?: () => void;
  setupError?: string;
  /** Re-read one listed conversation (approvals and runs change live). */
  refreshConversation?: (id: string, signal: AbortSignal) => Promise<void>;
  now?: Date;
  /** Re-read workflows when this changes (e.g. after reconnecting). */
  refreshKey?: string | number;
};

type Item = {
  key: string;
  icon: ReactNode;
  tone: Tone;
  title: string;
  meta: ReactNode;
  time?: ReactNode;
  label: string;
  onOpen: () => void;
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

function Row({ item }: { item: Item }) {
  return (
    <li className="overview-row" data-tone={item.tone}>
      <button
        type="button"
        className="overview-row-button"
        aria-label={item.label}
        onClick={item.onOpen}
      >
        <span className="overview-row-icon" aria-hidden>
          {item.icon}
        </span>
        <span className="overview-row-text">
          <span className="overview-row-title">{item.title}</span>
          <span className="overview-row-meta">{item.meta}</span>
        </span>
        {item.time && <span className="overview-row-time">{item.time}</span>}
        <ChevronRight className="overview-row-chevron" size={15} aria-hidden />
      </button>
    </li>
  );
}

function Section({
  title,
  count,
  children,
  action,
  className = '',
}: {
  title: string;
  count?: number;
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  const id = `overview-${title.toLowerCase().replaceAll(/[^a-z]+/g, '-')}`;
  return (
    <section className={`overview-section ${className}`} aria-labelledby={id}>
      <header className="overview-section-head">
        <h3 id={id}>
          {title}
          {count ? <span className="overview-count">{count}</span> : null}
        </h3>
        {action}
      </header>
      {children}
    </section>
  );
}

export default function OverviewHome({
  conversations,
  setup,
  monitor,
  loadTasks,
  onOpenConversation,
  onOpenWorkflows,
  onOpenTab,
  onHideSetup,
  setupError = '',
  refreshConversation,
  now: suppliedNow,
  refreshKey,
}: OverviewHomeProps) {
  const [tasks, setTasks] = useState<readonly TaskSummary[] | null>(null);
  const [tasksError, setTasksError] = useState('');
  const [clock, setClock] = useState(() => new Date());
  const now = suppliedNow ?? clock;

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

  const view = useMemo(() => {
    const topLevel = conversations.filter((row) => !row.parent_conversation_id);
    const approvals = conversations.filter(waitingApproval);
    const running = conversations.filter(
      (row) => !waitingApproval(row) && working(row),
    );
    const taskRows = tasks ?? [];
    const waitingWorkflows = taskRows.filter(
      (task) =>
        task.active_run && WAITING_RUN_STATUSES.has(task.active_run.status),
    );
    const runningWorkflows = taskRows.filter(
      (task) =>
        task.active_run && !WAITING_RUN_STATUSES.has(task.active_run.status),
    );
    const failed = taskRows.filter(
      (task) =>
        !task.active_run &&
        FAILED_RUN_STATUSES.has(String(task.last_status ?? '').toLowerCase()),
    );
    const upcoming = taskRows
      .filter((task) => task.enabled && parseTimestamp(task.next_run))
      .sort(
        (left, right) =>
          parseTimestamp(left.next_run)!.getTime() -
          parseTimestamp(right.next_run)!.getTime(),
      )
      .slice(0, 5);
    return {
      topLevel,
      approvals,
      running,
      waitingWorkflows,
      runningWorkflows,
      failed,
      upcoming,
    };
  }, [conversations, tasks]);

  const setupDone = setup
    ? new Set([...setup.completed_steps, ...setup.skipped_steps]).size
    : 0;
  const showSetup =
    setup &&
    (!setup.setup_complete ||
      (!setup.dismissed_home_card && setupDone < setup.steps.length));

  const needs: Item[] = [
    ...view.approvals.map<Item>((row) => ({
      key: `approval:${row.id}`,
      icon: <ShieldAlert size={16} />,
      tone: 'warning',
      title: row.title || 'Untitled conversation',
      meta:
        row.activity_state === 'attention' &&
        row.activity_phase !== 'waiting_approval'
          ? 'Agent work needs your attention'
          : 'Waiting for your approval',
      time: <When value={row.updated_at} now={now} />,
      label: `Review approval in ${row.title || 'Untitled conversation'}`,
      onOpen: () => onOpenConversation(row.id),
    })),
    ...view.waitingWorkflows.map<Item>((task) => ({
      key: `workflow-approval:${task.id}`,
      icon: <ShieldAlert size={16} />,
      tone: 'warning',
      title: task.name,
      meta: 'Workflow waiting for your approval',
      time: <When value={task.active_run?.started_at} now={now} />,
      label: `Review workflow approval: ${task.name}`,
      onOpen: () => onOpenWorkflows(task.id),
    })),
    ...view.failed.map<Item>((task) => ({
      key: `failed:${task.id}`,
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
    })),
  ];

  const runningItems: Item[] = [
    ...view.running.map<Item>((row) => {
      const child = Boolean(row.parent_conversation_id);
      const phase = humanizeToken(row.activity_phase);
      return {
        key: `running:${row.id}`,
        icon: child ? <Bot size={16} /> : <CircleDot size={16} />,
        tone: 'accent',
        title: row.title || 'Untitled conversation',
        meta: child
          ? `Agent · ${phase || 'Working'}`
          : row.activity_state === 'active'
            ? `Agents working${phase ? ` · ${phase}` : ''}`
            : 'Replying',
        time: <When value={row.updated_at} now={now} />,
        label: `Open running conversation: ${row.title || 'Untitled conversation'}`,
        onOpen: () => onOpenConversation(row.id),
      };
    }),
    ...view.runningWorkflows.map<Item>((task) => {
      const run = task.active_run!;
      return {
        key: `workflow-running:${task.id}`,
        icon: <Workflow size={16} />,
        tone: 'accent',
        title: task.name,
        meta:
          run.steps_total > 0
            ? `Workflow · step ${Math.min(run.steps_done + 1, run.steps_total)}/${run.steps_total}`
            : `Workflow · ${runStatus(run.status).label}`,
        time: <When value={run.started_at} now={now} />,
        label: `Open running workflow: ${task.name}`,
        onOpen: () => onOpenWorkflows(task.id),
      };
    }),
  ];

  const since = overnightStart(now);
  const overnight = useMemo(() => {
    const within = (value: string | null | undefined) => {
      const date = parseTimestamp(value);
      return Boolean(date && date >= since && date <= now);
    };
    const runs = (tasks ?? []).flatMap((task) =>
      (task.recent_runs ?? []).filter((run) => within(run.started_at)),
    );
    const failedRuns = runs.filter((run) =>
      FAILED_RUN_STATUSES.has(run.status.toLowerCase()),
    ).length;
    const lines: { key: string; icon: ReactNode; text: string }[] = [];
    if (runs.length)
      lines.push({
        key: 'runs',
        icon: <Workflow size={15} />,
        text: `${plural(runs.length, 'workflow run')}${failedRuns ? `, ${failedRuns} failed` : ', all completed'}`,
      });
    const extraction = monitor?.extraction;
    if (extraction?.availability === 'available' && within(extraction.last_run))
      lines.push({
        key: 'extraction',
        icon: <Library size={15} />,
        text: `Knowledge extraction read ${plural(extraction.threads_scanned, 'conversation')} and saved ${plural(extraction.entities_saved, 'memory', 'memories')}`,
      });
    const dreams = (monitor?.dream_journal ?? []).filter((entry) =>
      within(entry.timestamp),
    );
    if (dreams.length) {
      const merges = dreams.reduce(
        (sum, entry) => sum + entry.merges.length,
        0,
      );
      const enriched = dreams.reduce(
        (sum, entry) => sum + entry.enrichments.length,
        0,
      );
      const inferred = dreams.reduce(
        (sum, entry) => sum + entry.inferred_relations.length,
        0,
      );
      const changes = [
        merges && `merged ${plural(merges, 'duplicate')}`,
        enriched && `enriched ${plural(enriched, 'memory', 'memories')}`,
        inferred && `inferred ${plural(inferred, 'connection')}`,
      ].filter(Boolean);
      lines.push({
        key: 'dream',
        icon: <Moon size={15} />,
        text: changes.length
          ? `Dream Cycle ${changes.join(', ')}`
          : 'Dream Cycle ran and found nothing to change',
      });
    } else if (
      monitor?.dream.availability === 'available' &&
      within(monitor.dream.last_run)
    ) {
      lines.push({
        key: 'dream',
        icon: <Moon size={15} />,
        text: 'Dream Cycle ran',
      });
    }
    const active = conversations.filter(
      (row) => !row.parent_conversation_id && within(row.updated_at),
    ).length;
    if (active)
      lines.push({
        key: 'threads',
        icon: <Compass size={15} />,
        text: `${plural(active, 'conversation')} active`,
      });
    return lines;
    // `since` derives from `now`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasks, monitor, conversations, now]);

  const recent = view.topLevel.slice(0, 6);
  const attention = needs.length + (showSetup ? 1 : 0);
  const summary = [
    attention
      ? `${plural(attention, 'thing')} ${attention === 1 ? 'needs' : 'need'} you`
      : 'Nothing needs you',
    runningItems.length ? `${runningItems.length} running` : '',
    view.upcoming[0] ? `next: ${view.upcoming[0].name}` : '',
  ].filter(Boolean);

  return (
    <section className="overview-home" aria-labelledby="overview-title">
      <header className="overview-header">
        <h2 id="overview-title">{greeting(now)}</h2>
        <p>
          {summary.map((part, index) => (
            <span key={part}>
              {index > 0 && <span aria-hidden> · </span>}
              {part}
            </span>
          ))}
        </p>
      </header>
      <div className="overview-grid">
        <div className="overview-column">
          <Section title="Needs you" count={attention}>
            {showSetup &&
              (setup.setup_complete ? (
                <div
                  className="overview-setup"
                  role="region"
                  aria-label="Continue setup"
                >
                  <span className="overview-row-icon" aria-hidden>
                    <CalendarClock size={16} />
                  </span>
                  <span className="overview-row-text">
                    <strong className="overview-row-title">Finish setup</strong>
                    <span className="overview-row-meta">
                      {setupDone} of {setup.steps.length} areas complete
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
                  <span className="overview-row-text">
                    <strong className="overview-row-title">
                      Welcome to Row-Bot
                    </strong>
                    <span className="overview-row-meta">
                      Connect one working model first. Your other choices can
                      wait.
                    </span>
                  </span>
                  <Link className="button primary small" to="/setup">
                    Open Setup Center
                  </Link>
                </div>
              ))}
            {needs.length > 0 ? (
              <ul className="overview-list" aria-label="Needs you">
                {needs.map((item) => (
                  <Row key={item.key} item={item} />
                ))}
              </ul>
            ) : (
              !showSetup && (
                <InlineEmpty>
                  Nothing needs you. Approvals and failed runs show up here.
                </InlineEmpty>
              )
            )}
            {tasksError && (
              <p className="overview-error" role="status">
                Workflows could not be read: {tasksError}
              </p>
            )}
          </Section>
          <Section title="Running now" count={runningItems.length}>
            {runningItems.length ? (
              <ul className="overview-list" aria-label="Running now">
                {runningItems.map((item) => (
                  <Row key={item.key} item={item} />
                ))}
              </ul>
            ) : (
              <InlineEmpty>No agents or workflows are running.</InlineEmpty>
            )}
          </Section>
          <Section
            title="Recent threads"
            action={
              <Link className="overview-section-link" to="/library">
                Library
              </Link>
            }
          >
            {recent.length ? (
              <ul className="overview-list" aria-label="Recent threads">
                {recent.map((row) => (
                  <li className="overview-row" key={row.id}>
                    <button
                      type="button"
                      className="overview-row-button"
                      aria-label={`Open ${row.title || 'Untitled conversation'}`}
                      onClick={() => onOpenConversation(row.id)}
                    >
                      <span className="overview-row-icon" aria-hidden>
                        <ConversationGlyph row={row} size={15} />
                      </span>
                      <span className="overview-row-text">
                        <span className="overview-row-title">
                          {row.title || 'Untitled conversation'}
                        </span>
                      </span>
                      <span className="overview-row-time">
                        <When value={row.updated_at} now={now} fallback="" />
                      </span>
                      <ChevronRight
                        className="overview-row-chevron"
                        size={15}
                        aria-hidden
                      />
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <InlineEmpty>No conversations yet.</InlineEmpty>
            )}
          </Section>
        </div>
        <div className="overview-column">
          <Section
            title="Upcoming"
            count={view.upcoming.length}
            action={
              <button
                type="button"
                className="overview-section-link"
                onClick={() => onOpenWorkflows()}
              >
                Workflows
              </button>
            }
          >
            {tasks === null && !tasksError ? (
              <p className="overview-loading" role="status">
                Reading workflows…
              </p>
            ) : view.upcoming.length ? (
              <ul className="overview-list" aria-label="Upcoming">
                {view.upcoming.map((task) => (
                  <Row
                    key={task.id}
                    item={{
                      key: task.id,
                      icon: <CalendarClock size={16} />,
                      tone: 'neutral',
                      title: task.name,
                      meta: scheduleWords(task.schedule, task.at),
                      time: <When value={task.next_run} now={now} />,
                      label: `Open scheduled workflow: ${task.name}`,
                      onOpen: () => onOpenWorkflows(task.id),
                    }}
                  />
                ))}
              </ul>
            ) : (
              <InlineEmpty>No scheduled workflows.</InlineEmpty>
            )}
          </Section>
          <Section
            title="Since yesterday evening"
            action={
              <button
                type="button"
                className="overview-section-link"
                onClick={() => onOpenTab('monitor')}
              >
                Monitor
              </button>
            }
          >
            {overnight.length ? (
              <ul
                className="overview-digest"
                aria-label="Since yesterday evening"
              >
                {overnight.map((line) => (
                  <li key={line.key}>
                    <span aria-hidden>{line.icon}</span>
                    {line.text}
                  </li>
                ))}
              </ul>
            ) : (
              <InlineEmpty>
                Quiet. Nothing ran since 6 PM yesterday.
              </InlineEmpty>
            )}
          </Section>
        </div>
      </div>
    </section>
  );
}
