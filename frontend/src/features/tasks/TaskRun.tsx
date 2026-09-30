import { useEffect, useRef, useState } from 'react';
import type {
  TaskApprovalPage,
  TaskApprovalResult,
  TaskApprovalReview,
  TaskRunPage,
  TaskRunResult,
  TaskRunReview,
  TaskRunSummary,
  TaskStopResult,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { Play, RefreshCw } from 'lucide-react';
import { Button, IconButton, InlineEmpty, Skeleton } from '../../ui/primitives';
import { humanizeToken } from '../../ui/format';
import { When, runStatus } from '../home/home-format';
import { WaitingSince } from '../shell/InPlaceApproval';

export interface TaskRunProps {
  taskId: string;
  loadReview: (taskId: string, signal?: AbortSignal) => Promise<TaskRunReview>;
  run: (review: TaskRunReview) => Promise<TaskRunResult>;
  loadHistory: (
    taskId: string,
    cursor?: string,
    signal?: AbortSignal,
  ) => Promise<TaskRunPage>;
  loadApprovals: (
    taskId: string,
    runId: string,
    cursor?: string,
    signal?: AbortSignal,
  ) => Promise<TaskApprovalPage>;
  respondApproval: (
    approval: TaskApprovalReview,
    approved: boolean,
  ) => Promise<TaskApprovalResult>;
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
]);

export default function TaskRun({
  taskId,
  loadReview,
  run,
  loadHistory,
  loadApprovals,
  respondApproval,
  stop,
  loadRun,
  openConversation,
  onFinished,
}: TaskRunProps) {
  const [review, setReview] = useState<TaskRunReview | null>(null);
  const [history, setHistory] = useState<TaskRunPage | null>(null);
  const [selected, setSelected] = useState<TaskRunSummary | null>(null);
  const [approvals, setApprovals] = useState<TaskApprovalPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingApprovals, setLoadingApprovals] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [reload, setReload] = useState(0);
  const [earlierRows, setEarlierRows] = useState(0);
  const [stale, setStale] = useState(false);
  const [actionUnconfirmed, setActionUnconfirmed] = useState(false);
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
    setApprovals(null);
    setLoading(true);
    setError('');
    setStale(false);
    setActionUnconfirmed(false);
    setEarlierRows(0);
    Promise.all([
      loadReview(taskId, abort.signal),
      loadHistory(taskId, undefined, abort.signal),
    ]).then(
      ([nextReview, nextHistory]) => {
        if (abort.signal.aborted || ticket !== epoch.current) return;
        setReview(nextReview);
        setHistory(nextHistory);
        setSelected(nextHistory.items[0] ?? null);
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
  }, [taskId, loadReview, loadHistory, reload]);

  const selectedId = selected?.id ?? null;
  const selectedState = selected?.status ?? null;
  useEffect(() => {
    const abort = new AbortController();
    setApprovals(null);
    if (!selectedId || !selectedState || terminal.has(selectedState)) {
      setLoadingApprovals(false);
      return () => abort.abort();
    }
    setLoadingApprovals(true);
    loadApprovals(taskId, selectedId, undefined, abort.signal).then(
      (page) => {
        if (abort.signal.aborted) return;
        setApprovals(page);
        setLoadingApprovals(false);
      },
      (cause: unknown) => {
        if (abort.signal.aborted) return;
        setError(clientError(cause).message);
        setLoadingApprovals(false);
      },
    );
    return () => abort.abort();
  }, [taskId, selectedId, selectedState, loadApprovals]);

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
    setNotice(`Run finished · ${runStatus(done.status).label}.`);
    onFinished?.();
    const ticket = epoch.current;
    loadReview(taskId).then(
      (next) => {
        if (ticket !== epoch.current) return;
        setReview(next);
        setStale(false);
        setActionUnconfirmed(false);
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
          kind === 'approval' ||
          [
            'task_revision_conflict',
            'task_policy_revision_conflict',
            'task_approval_revision_conflict',
            'task_run_unconfirmed',
            'task_approval_unconfirmed',
            'operation_uncertain',
          ].includes(failure.code)
        ) {
          setStale(true);
          setActionUnconfirmed(true);
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

  const selectedStatus = selected ? runStatus(selected.status) : null;
  const progress =
    selected && selected.steps_total > 0
      ? Math.min(1, selected.steps_done / selected.steps_total)
      : null;
  return (
    <section className="task-run" aria-label="Task runs and approvals">
      <h2 className="visually-hidden">Run and history</h2>
      <div className="task-run-review">
        {review ? (
          <p className="task-run-facts">
            <span>
              {review.notify_only
                ? 'Reminder'
                : `${review.steps_total} ${review.steps_total === 1 ? 'step' : 'steps'}`}
            </span>
            <span aria-hidden>·</span>
            <span>Profile {profileWords(review.agent_profile_id)}</span>
            <span aria-hidden>·</span>
            <span>{approvalWords(review.approval_mode)}</span>
          </p>
        ) : (
          <p className="task-run-facts">
            Uses the saved steps, profile, approvals and delivery.
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
            disabled={
              !review ||
              loading ||
              !!busy ||
              stale ||
              (!review.notify_only && review.steps_total === 0)
            }
            onClick={() => void runNow()}
          >
            <Play size={14} aria-hidden />
            {busy === 'run' ? 'Starting…' : 'Run now'}
          </Button>
        </div>
      </div>
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
              </header>
              {progress !== null && (
                <div className="task-run-progress">
                  <progress
                    max={selected.steps_total}
                    value={selected.steps_done}
                    aria-label="Run progress"
                  />
                  <span>
                    {selected.steps_done} of {selected.steps_total}{' '}
                    {selected.steps_total === 1 ? 'step' : 'steps'}
                  </span>
                </div>
              )}
              {selected.status === 'starting' && (
                <p className="home-caption">
                  Dispatch may still be starting or its outcome may be
                  unconfirmed. Refresh or open the conversation; this request
                  will not run again automatically.
                </p>
              )}
              {selected.status === 'stopping' && (
                <p className="home-caption">
                  Stop is requested. Completion and cleanup are not yet
                  confirmed.
                </p>
              )}
              {selected.status === 'skipped' && (
                <p className="home-caption">
                  This scheduled run was skipped: an earlier run still waited
                  for your approval.
                </p>
              )}
              <div className="task-run-actions">
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
                    onClick={() => {
                      const current = selected,
                        ticket = epoch.current;
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
                    }}
                  >
                    Stop run
                  </Button>
                )}
              </div>
              {loadingApprovals && (
                <Skeleton label="Loading pending approvals" />
              )}
              {approvals?.items.map((approval) => (
                <article
                  className="task-run-approval"
                  key={approval.id}
                  aria-label="Pending task approval"
                >
                  <h3>Approval needed</h3>
                  <p className="task-approval-message">{approval.message}</p>
                  <p className="home-caption">
                    <WaitingSince value={approval.requested_at} />
                  </p>
                  {approval.expires_at && (
                    <p className="home-caption">
                      Expires <When value={approval.expires_at} />
                    </p>
                  )}
                  {approval.message_truncated && (
                    <p role="status">
                      This review is too large to show completely. Open the
                      existing conversation approval surface to review it.
                    </p>
                  )}
                  {!approval.response_available &&
                    !approval.message_truncated && (
                      <p className="home-caption">
                        This approval uses its existing conversation controls,
                        or the workflow is still finishing its pause. Open the
                        conversation or refresh after it finishes.
                      </p>
                    )}
                  <div className="task-run-actions">
                    {[false, true].map((approved) => (
                      <Button
                        key={String(approved)}
                        variant={approved ? 'primary' : 'secondary'}
                        className="small"
                        disabled={
                          !!busy ||
                          !approval.response_available ||
                          actionUnconfirmed
                        }
                        onClick={() => {
                          const ticket = epoch.current;
                          void actOnRun('approval', async () => {
                            const result = await respondApproval(
                              approval,
                              approved,
                            );
                            if (ticket !== epoch.current) return;
                            setApprovals(null);
                            updateRun(result.run);
                            setNotice(
                              approved
                                ? 'Approval recorded.'
                                : 'Rejection recorded.',
                            );
                          });
                        }}
                      >
                        {approved ? 'Approve' : 'Reject'}
                      </Button>
                    ))}
                  </div>
                </article>
              ))}
              {approvals && approvals.total > approvals.items.length && (
                <p className="home-caption">
                  Showing {approvals.items.length} of {approvals.total} pending
                  approvals on this page.
                </p>
              )}
              {approvals?.next_cursor && (
                <Button
                  className="small"
                  disabled={!!busy || actionUnconfirmed}
                  onClick={() => {
                    const ticket = epoch.current;
                    const abort = new AbortController();
                    more.current = abort;
                    void actOnRun('approval-page', async () => {
                      const page = await loadApprovals(
                        taskId,
                        selected.id,
                        approvals.next_cursor ?? undefined,
                        abort.signal,
                      );
                      if (ticket !== epoch.current || abort.signal.aborted)
                        return;
                      if (page.revision !== approvals.revision)
                        throw { code: 'task_approval_revision_conflict' };
                      setApprovals(page);
                    }).finally(() => {
                      if (more.current === abort) more.current = null;
                    });
                  }}
                >
                  Next approvals
                </Button>
              )}
            </section>
          )}
          <h3 className="task-run-history-title">History</h3>
          {!history?.items.length ? (
            <InlineEmpty>No runs yet. Run it when you are ready.</InlineEmpty>
          ) : (
            <ul className="task-run-history">
              {history.items.map((item) => {
                const view = runStatus(item.status);
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
                      {item.steps_total > 0 && (
                        <span className="task-run-row-steps">
                          {item.steps_done}/{item.steps_total}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
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
      )}
    </section>
  );
}

function profileWords(id: string) {
  if (!id) return 'Default';
  return (
    humanizeToken(id.replace(/^builtin:/, '').replace(/^row_bot_/, '')) ||
    'Default'
  );
}

function approvalWords(mode: string) {
  if (mode === 'approve') return 'Asks before actions';
  if (mode === 'block') return 'Blocks actions';
  if (mode === 'allow_all') return 'Auto approvals';
  return mode ? humanizeToken(mode) : 'Asks before actions';
}
