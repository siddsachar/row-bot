import { useEffect, useRef, useState } from 'react';
import type {
  TaskRunPage,
  TaskRunResult,
  TaskRunReview,
  TaskRunSummary,
  TaskStopResult,
} from '../../api/types';
import { clientError } from '../../api/errors';
import {
  ChevronDown,
  ExternalLink,
  Folder,
  FolderOpen,
  MessageSquare,
  Play,
  RefreshCw,
} from 'lucide-react';
import { Button, IconButton, InlineEmpty, Skeleton } from '../../ui/primitives';
import { humanizeToken, parseTimestamp } from '../../ui/format';
import { When, runStatus } from '../home/home-format';
import ApprovalCard from '../shell/ApprovalCard';
import { approvalTag } from '../shell/approval-levels';
import { wallTimeOf } from './schedule-zones';

export interface TaskRunProps {
  taskId: string;
  loadReview: (taskId: string, signal?: AbortSignal) => Promise<TaskRunReview>;
  run: (review: TaskRunReview) => Promise<TaskRunResult>;
  loadHistory: (
    taskId: string,
    cursor?: string,
    signal?: AbortSignal,
  ) => Promise<TaskRunPage>;
  stop: (taskId: string, runId: string) => Promise<TaskStopResult>;
  /** Re-read one run; the drawer follows a run that is still going (B121). */
  loadRun?: (
    taskId: string,
    runId: string,
    signal?: AbortSignal,
  ) => Promise<TaskRunSummary>;
  openConversation: (conversationId: string) => void;
  /** A followed run ended: the list behind the drawer can re-read (B178). */
  onFinished?: () => void;
  /** Open this workflow's settings at its Code folder (its folder was removed, N9). */
  onChooseFolder?: () => void;
  /**
   * `phone`: the workflow's sheet (C2, D11 A): Run now and Open chat, then
   * the recent runs, each opening to its answer, its approval card and Stop.
   */
  layout?: 'drawer' | 'phone';
  /** The workflow's chat for Open chat, until its review names it. */
  chatId?: string | null;
}

/** A run's status in words: one its code folder stopped needs you, not a safety block (N9). */
function runView(run: TaskRunSummary) {
  return run.fix === 'choose_folder'
    ? { label: 'Needs you', tone: 'warning' as const }
    : runStatus(run.status);
}

// How often the drawer re-reads a run that is still going.
const FOLLOW_MS = 2000;

const terminal = new Set([
  'completed',
  'completed_delivery_failed',
  'failed',
  'stopped',
  'blocked',
  'skipped',
  'denied',
  'expired',
  'unsure',
]);

export default function TaskRun({
  taskId,
  loadReview,
  run,
  loadHistory,
  stop,
  loadRun,
  openConversation,
  onFinished,
  onChooseFolder,
  layout = 'drawer',
  chatId = null,
}: TaskRunProps) {
  const phone = layout === 'phone';
  const [review, setReview] = useState<TaskRunReview | null>(null);
  const [history, setHistory] = useState<TaskRunPage | null>(null);
  const [selected, setSelected] = useState<TaskRunSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [reload, setReload] = useState(0);
  const [earlierRows, setEarlierRows] = useState(0);
  const [stale, setStale] = useState(false);
  const epoch = useRef(0);
  const pending = useRef(false);
  const more = useRef<AbortController | null>(null);

  useEffect(() => {
    const ticket = ++epoch.current;
    const abort = new AbortController();
    more.current?.abort();
    setReview(null);
    setHistory(null);
    setSelected(null);
    setLoading(true);
    setError('');
    setStale(false);
    setEarlierRows(0);
    Promise.all([
      loadReview(taskId, abort.signal),
      loadHistory(taskId, undefined, abort.signal),
    ]).then(
      ([nextReview, nextHistory]) => {
        if (abort.signal.aborted || ticket !== epoch.current) return;
        setReview(nextReview);
        setHistory(nextHistory);
        // A phone opens the newest run only while it is going or waits for you.
        const newest = nextHistory.items[0] ?? null;
        setSelected(
          phone && newest && terminal.has(newest.status) ? null : newest,
        );
        setLoading(false);
      },
      (cause: unknown) => {
        if (abort.signal.aborted || ticket !== epoch.current) return;
        setError(clientError(cause).message);
        setLoading(false);
      },
    );
    return () => {
      abort.abort();
      more.current?.abort();
      epoch.current += 1;
    };
    // The layout is fixed for the drawer's or sheet's life.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, loadReview, loadHistory, reload]);

  const selectedId = selected?.id ?? null;
  const selectedState = selected?.status ?? null;

  // B121: a run that is still going is read again every couple of seconds
  // until it ends; then the drawer reviews again, so Run now is ready.
  const followId =
    loadRun && selectedId && selectedState && !terminal.has(selectedState)
      ? selectedId
      : null;
  useEffect(() => {
    if (!followId || !loadRun) return;
    const abort = new AbortController();
    const ticket = epoch.current;
    let timer = 0;
    const tick = async () => {
      try {
        const next = await loadRun(taskId, followId, abort.signal);
        if (abort.signal.aborted || ticket !== epoch.current) return;
        updateRun(next);
        if (terminal.has(next.status)) {
          finished(next);
          return;
        }
      } catch {
        if (abort.signal.aborted) return;
      }
      timer = window.setTimeout(() => void tick(), FOLLOW_MS);
    };
    timer = window.setTimeout(() => void tick(), FOLLOW_MS);
    return () => {
      abort.abort();
      window.clearTimeout(timer);
    };
    // updateRun/finished only set state; the loop restarts per followed run.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [followId, loadRun, taskId]);

  function finished(done: TaskRunSummary) {
    setNotice(`Run finished · ${runView(done).label}.`);
    onFinished?.();
    const ticket = epoch.current;
    loadReview(taskId).then(
      (next) => {
        if (ticket !== epoch.current) return;
        setReview(next);
        setStale(false);
      },
      () => {},
    );
  }

  function updateRun(next: TaskRunSummary) {
    setSelected(next);
    setHistory(
      (current) =>
        current && {
          ...current,
          items: current.items.map((item) =>
            item.id === next.id ? next : item,
          ),
        },
    );
  }

  async function actOnRun(kind: string, action: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true;
    setBusy(kind);
    setError('');
    const ticket = epoch.current;
    try {
      await action();
    } catch (cause) {
      if (epoch.current === ticket) {
        const failure = clientError(cause);
        setError(failure.message);
        if (
          kind === 'run' ||
          [
            'task_revision_conflict',
            'task_policy_revision_conflict',
            'task_run_unconfirmed',
            'operation_uncertain',
          ].includes(failure.code)
        ) {
          setStale(true);
        }
      }
    } finally {
      pending.current = false;
      setBusy('');
    }
  }

  async function runNow() {
    if (!review || stale) return;
    const ticket = epoch.current;
    await actOnRun('run', async () => {
      const result = await run(review);
      if (ticket !== epoch.current) return;
      setSelected(result.run);
      setHistory((current) => {
        if (!current) return current;
        const exists = current.items.some((item) => item.id === result.run.id);
        return {
          ...current,
          total: current.total + (exists ? 0 : 1),
          items: exists
            ? current.items.map((item) =>
                item.id === result.run.id ? result.run : item,
              )
            : [result.run, ...current.items].slice(0, 200),
        };
      });
      setStale(true); // A new Run requires a fresh review, never a repeated click.
      setNotice(
        result.replayed
          ? 'Showing the existing run for this request.'
          : 'Run started.',
      );
      if (terminal.has(result.run.status)) finished(result.run);
    });
  }

  async function moreHistory() {
    if (!history?.next_cursor || more.current || stale) return;
    const abort = new AbortController();
    more.current = abort;
    setBusy('more');
    const ticket = epoch.current;
    try {
      const next = await loadHistory(taskId, history.next_cursor, abort.signal);
      if (abort.signal.aborted || ticket !== epoch.current) return;
      if (next.revision !== history.revision) {
        setError('Run history changed. Refresh to continue.');
        setStale(true);
        return;
      }
      const items = [...history.items, ...next.items];
      setEarlierRows((value) => value + Math.max(0, items.length - 200));
      setHistory({ ...next, items: items.slice(-200) });
    } catch (cause) {
      if (!abort.signal.aborted && ticket === epoch.current) {
        setError(clientError(cause).message);
        setStale(true);
      }
    } finally {
      if (more.current === abort) more.current = null;
      setBusy('');
    }
  }

  const selectedStatus = selected ? runView(selected) : null;
  // A run of one message is one turn: only a run of several says which it is on.
  const progress =
    selected && selected.steps_total > 1 && !terminal.has(selected.status)
      ? Math.min(1, selected.steps_done / selected.steps_total)
      : null;
  const runNowDisabled =
    !review ||
    loading ||
    !!busy ||
    stale ||
    (!review.notify_only && review.steps_total === 0);
  function stopRun(current: TaskRunSummary) {
    const ticket = epoch.current;
    void actOnRun('stop', async () => {
      const result = await stop(taskId, current.id);
      if (ticket !== epoch.current) return;
      updateRun(result.run);
      setNotice(
        result.quiesced
          ? 'The run has stopped and its worker has finished.'
          : 'Stop requested. Waiting for the run to finish.',
      );
    });
  }
  // What a run did and how far it is: the drawer's selected run, and a
  // phone's opened run row.
  const detail = (current: TaskRunSummary, answered = false) => (
    <>
      {current.late_for && (
        <p className="home-caption">
          Ran late: Row-Bot was closed at {wallTimeOf(current.late_for)}.
        </p>
      )}
      {current.notice &&
        // A phone's opened run row says its answer already.
        !answered &&
        current.status !== 'waiting' &&
        // A run that acted says so in its list below, with links, not twice.
        !(current.actions?.length ?? 0) && (
          <p className="task-run-notice">{current.notice}</p>
        )}
      {(current.actions?.length ?? 0) > 0 && (
        <ul className="task-run-actions-list" aria-label="What it did">
          {(current.actions ?? []).map((action, index) => (
            <li key={index}>
              <span className="task-run-action-title">{action.title}</span>
              <span className="task-run-action-result">{action.result}</span>
              {action.link && (
                <a
                  href={action.link}
                  target="_blank"
                  rel="noreferrer noopener"
                  aria-label={`Open: ${action.result}`}
                >
                  <ExternalLink size={13} aria-hidden />
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
      {progress !== null && (
        <div className="task-run-progress">
          <progress
            max={current.steps_total}
            value={current.steps_done}
            aria-label="Run progress"
          />
          <span>
            Message {Math.min(current.steps_done + 1, current.steps_total)} of{' '}
            {current.steps_total}
          </span>
        </div>
      )}
      {current.status === 'starting' && (
        <p className="home-caption">
          Dispatch may still be starting or its outcome may be unconfirmed.
          Refresh or open the conversation; this request will not run again
          automatically.
        </p>
      )}
      {current.status === 'stopping' && (
        <p className="home-caption">
          Stop is requested. Completion and cleanup are not yet confirmed.
        </p>
      )}
    </>
  );
  // The card the run waits on: the exact action, account or recipient and full text.
  const waitingCard = (current: TaskRunSummary) =>
    current.status === 'waiting' &&
    current.approval_id && (
      <ApprovalCard
        key={current.approval_id}
        id={current.approval_id}
        onResolved={() => setReload((value) => value + 1)}
      />
    );
  const alerts = (
    <>
      {error && (
        <p className="task-builder-alert" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="home-caption" role="status">
          {notice}
        </p>
      )}
    </>
  );
  const moreRows = (
    <>
      {earlierRows > 0 && (
        <p className="home-caption">
          {earlierRows} earlier loaded runs are outside this 200-row view.
          Refresh returns to the newest runs.
        </p>
      )}
      {history?.next_cursor && (
        <Button
          className="small"
          disabled={!!busy || stale}
          onClick={() => void moreHistory()}
        >
          Load more runs
        </Button>
      )}
    </>
  );
  if (phone) {
    // Every run is a turn of the workflow's one conversation (F31).
    const chat =
      review?.conversation_id ||
      chatId ||
      history?.items[0]?.conversation_id ||
      null;
    return (
      <section
        className="task-run phone-task-run"
        aria-label="Task runs and approvals"
      >
        <div className="phone-workflow-actions">
          <Button
            variant="primary"
            disabled={runNowDisabled}
            onClick={() => void runNow()}
          >
            <Play size={18} aria-hidden />
            {busy === 'run' ? 'Starting…' : 'Run now'}
          </Button>
          {chat && (
            <Button onClick={() => openConversation(chat)}>
              <MessageSquare size={18} aria-hidden />
              Open chat
            </Button>
          )}
        </div>
        {alerts}
        {loading ? (
          <Skeleton label="Loading recent runs" />
        ) : (
          <>
            <h3 className="phone-workflow-runs-title">Recent runs</h3>
            {!history?.items.length ? (
              <InlineEmpty>No runs yet. Run it when you are ready.</InlineEmpty>
            ) : (
              <ul
                className="phone-chat-list-rows phone-workflow-runs"
                aria-label="Recent runs"
              >
                {history.items.map((item) => {
                  const shown = item.id === selected?.id ? selected : null;
                  const view = runView(shown ?? item);
                  const live = Boolean(shown && !terminal.has(shown.status));
                  return (
                    <li
                      key={item.id}
                      className="phone-row phone-workflow-run"
                      data-open={shown ? 'true' : undefined}
                    >
                      <button
                        type="button"
                        className="phone-row-main"
                        aria-expanded={Boolean(shown)}
                        disabled={!!busy}
                        onClick={() => setSelected(shown ? null : item)}
                      >
                        <span
                          className="task-run-row-dot"
                          data-tone={view.tone}
                          aria-hidden
                        />
                        <span className="phone-row-text">
                          <span className="phone-row-title">
                            {runTime(item.started_at)}
                          </span>
                          <span className="phone-row-meta">
                            {(shown ?? item).notice || view.label}
                          </span>
                        </span>
                        <ChevronDown
                          size={18}
                          className="phone-workflow-run-chevron"
                          aria-hidden
                        />
                      </button>
                      {shown && (
                        <div className="phone-workflow-run-detail">
                          <p className="phone-workflow-run-status">
                            <span
                              className="task-run-pill"
                              data-tone={view.tone}
                            >
                              <span aria-hidden className="task-run-pill-dot" />
                              {view.label}
                            </span>
                            <span className="home-caption">
                              Started{' '}
                              <When
                                value={shown.started_at}
                                fallback="at an unknown time"
                              />
                            </span>
                          </p>
                          {detail(shown, true)}
                          {live && (
                            <Button
                              variant="danger"
                              className="small"
                              disabled={!!busy}
                              onClick={() => stopRun(shown)}
                            >
                              Stop run
                            </Button>
                          )}
                          {waitingCard(shown)}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
            {moreRows}
          </>
        )}
      </section>
    );
  }
  return (
    <section className="task-run" aria-label="Task runs and approvals">
      <h2 className="visually-hidden">Run and history</h2>
      <div className="task-run-review">
        {review ? (
          <p className="task-run-facts">
            <span>
              {review.notify_only
                ? 'Reminder'
                : `${review.steps_total} ${review.steps_total === 1 ? 'message' : 'messages'}`}
            </span>
            <span aria-hidden>·</span>
            <span>Profile {profileWords(review.agent_profile_id)}</span>
            <span aria-hidden>·</span>
            <span>{approvalWords(review.approval_mode)}</span>
          </p>
        ) : (
          <p className="task-run-facts">
            Sends its messages to its own conversation, with its approvals and
            delivery.
          </p>
        )}
        <div className="task-run-actions">
          <IconButton
            size="sm"
            label="Refresh runs"
            disabled={!!busy}
            onClick={() => {
              setNotice('');
              setReload((value) => value + 1);
            }}
          >
            <RefreshCw size={14} aria-hidden />
          </IconButton>
          <Button
            variant="primary"
            className="small"
            disabled={runNowDisabled}
            onClick={() => void runNow()}
          >
            <Play size={14} aria-hidden />
            {busy === 'run' ? 'Starting…' : 'Run now'}
          </Button>
        </div>
      </div>
      {alerts}
      {loading ? (
        <Skeleton label="Loading run review and history" />
      ) : (
        <>
          {selected && selectedStatus && (
            <section className="task-run-selected" aria-label="Selected run">
              <header>
                <span className="task-run-pill" data-tone={selectedStatus.tone}>
                  <span aria-hidden className="task-run-pill-dot" />
                  {selectedStatus.label}
                </span>
                <span className="home-caption">
                  Started{' '}
                  <When
                    value={selected.started_at}
                    fallback="at an unknown time"
                  />
                </span>
                {selected.workspace_name && (
                  <span className="task-run-folder">
                    <Folder size={12} aria-hidden />
                    In {selected.workspace_name}
                  </span>
                )}
              </header>
              {detail(selected)}
              <div className="task-run-actions">
                {selected.fix === 'choose_folder' && onChooseFolder && (
                  <Button
                    variant="primary"
                    className="small"
                    onClick={onChooseFolder}
                  >
                    <FolderOpen size={14} aria-hidden />
                    Choose a folder
                  </Button>
                )}
                <Button
                  className="small"
                  onClick={() => openConversation(selected.conversation_id)}
                >
                  Open conversation
                </Button>
                {!terminal.has(selected.status) && (
                  <Button
                    variant="danger"
                    className="small"
                    disabled={!!busy}
                    onClick={() => stopRun(selected)}
                  >
                    Stop run
                  </Button>
                )}
              </div>
              {waitingCard(selected)}
            </section>
          )}
          <h3 className="task-run-history-title">History</h3>
          {!history?.items.length ? (
            <InlineEmpty>No runs yet. Run it when you are ready.</InlineEmpty>
          ) : (
            <ul className="task-run-history">
              {history.items.map((item) => {
                const view = runView(item);
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      className="task-run-row"
                      aria-current={
                        item.id === selected?.id ? 'true' : undefined
                      }
                      disabled={!!busy}
                      onClick={() => setSelected(item)}
                      aria-label={`Show run ${item.id}`}
                    >
                      <span
                        className="task-run-row-dot"
                        data-tone={view.tone}
                        aria-hidden
                      />
                      <span className="task-run-row-status">{view.label}</span>
                      <span className="task-run-row-time">
                        <When value={item.started_at} fallback="Unknown time" />
                      </span>
                      {item.notice && (
                        <span className="task-run-row-steps">
                          {item.notice}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {moreRows}
        </>
      )}
    </section>
  );
}

/** When a run started, on a phone's run row: "Today 8:00", "Yesterday 8:00", "Tue 8:00", "3 Nov". */
export function runTime(value: string, now = new Date()): string {
  const date = parseTimestamp(value);
  if (!date) return 'Unknown time';
  const start = (day: Date) =>
    new Date(day.getFullYear(), day.getMonth(), day.getDate()).getTime();
  const days = Math.round((start(now) - start(date)) / 86_400_000);
  const time = new Intl.DateTimeFormat(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);
  if (days <= 0) return `Today ${time}`;
  if (days === 1) return `Yesterday ${time}`;
  if (days < 7)
    return `${new Intl.DateTimeFormat(undefined, { weekday: 'short' }).format(date)} ${time}`;
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
  }).format(date);
}

function profileWords(id: string) {
  if (!id) return 'Default';
  return (
    humanizeToken(id.replace(/^builtin:/, '').replace(/^row_bot_/, '')) ||
    'Default'
  );
}

function approvalWords(mode: string) {
  return approvalTag(mode);
}
