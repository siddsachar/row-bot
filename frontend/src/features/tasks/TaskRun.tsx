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
import { Button, EmptyState, Skeleton } from '../../ui/primitives';

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
  openConversation: (conversationId: string) => void;
}

const terminal = new Set([
  'completed',
  'completed_delivery_failed',
  'failed',
  'stopped',
  'blocked',
]);

export default function TaskRun({
  taskId,
  loadReview,
  run,
  loadHistory,
  loadApprovals,
  respondApproval,
  stop,
  openConversation,
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

  useEffect(() => {
    const abort = new AbortController();
    setApprovals(null);
    if (!selected || terminal.has(selected.status)) {
      setLoadingApprovals(false);
      return () => abort.abort();
    }
    setLoadingApprovals(true);
    loadApprovals(taskId, selected.id, undefined, abort.signal).then(
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
  }, [taskId, selected, loadApprovals]);

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
          : 'Run admitted. Refresh to see its latest saved progress.',
      );
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

  return (
    <section className="task-run" aria-label="Task runs and approvals">
      <h2>Run and history</h2>
      <p className="muted">
        Run now uses the saved workflow, agent profile, approval policy, and
        delivery settings.
      </p>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <div className="actions">
        <Button
          variant="primary"
          disabled={
            !review ||
            loading ||
            !!busy ||
            stale ||
            (!review.notify_only && review.steps_total === 0)
          }
          onClick={() => void runNow()}
        >
          {busy === 'run' ? 'Starting…' : 'Run now'}
        </Button>
        <Button
          disabled={!!busy}
          onClick={() => {
            setNotice('');
            setReload((value) => value + 1);
          }}
        >
          Refresh
        </Button>
      </div>
      {review && (
        <p className="muted">
          {review.notify_only
            ? 'Reminder'
            : `${review.steps_total} workflow steps`}{' '}
          · Profile: {review.agent_profile_id} · Approval policy:{' '}
          {review.approval_mode}
        </p>
      )}
      {loading ? (
        <Skeleton label="Loading run review and history" />
      ) : (
        <>
          {selected && (
            <section aria-label="Selected run">
              <h3>Run {selected.id}</h3>
              <p>
                Saved status: {selected.status}. Progress: {selected.steps_done}{' '}
                of {selected.steps_total} steps.
              </p>
              {selected.status === 'starting' && (
                <p className="muted">
                  Dispatch may still be starting or its outcome may be
                  unconfirmed. Refresh or open the conversation; this request
                  will not run again automatically.
                </p>
              )}
              {selected.status === 'stopping' && (
                <p className="muted">
                  Stop is requested. Completion and cleanup are not yet
                  confirmed.
                </p>
              )}
              <div className="actions">
                <Button
                  onClick={() => openConversation(selected.conversation_id)}
                >
                  Open conversation
                </Button>
                {!terminal.has(selected.status) && (
                  <Button
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
                            : 'Stop requested. Waiting for confirmed worker cleanup; refresh for progress.',
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
                <article key={approval.id} aria-label="Pending task approval">
                  <h3>Approval required</h3>
                  <p className="task-approval-message">{approval.message}</p>
                  {approval.expires_at && (
                    <p className="muted">Expires: {approval.expires_at}</p>
                  )}
                  {approval.message_truncated && (
                    <p role="status">
                      This review is too large to show completely. Open the
                      existing conversation approval surface to review it.
                    </p>
                  )}
                  {!approval.response_available &&
                    !approval.message_truncated && (
                      <p className="muted">
                        This approval uses its existing conversation controls,
                        or the workflow is still finishing its pause. Open the
                        conversation or refresh after it finishes.
                      </p>
                    )}
                  <div className="actions">
                    {[true, false].map((approved) => (
                      <Button
                        key={String(approved)}
                        variant={approved ? 'primary' : 'secondary'}
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
                                ? 'Approval recorded. Refresh for progress.'
                                : 'Rejection recorded. Refresh for progress.',
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
                <p className="muted">
                  Showing {approvals.items.length} of {approvals.total} pending
                  approvals on this page.
                </p>
              )}
              {approvals?.next_cursor && (
                <Button
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
          <h3>Saved run history</h3>
          {!history?.items.length ? (
            <EmptyState title="No saved runs">
              Run this task when you are ready.
            </EmptyState>
          ) : (
            <ul className="resource-list">
              {history.items.map((item) => (
                <li key={item.id}>
                  <Button
                    disabled={!!busy}
                    onClick={() => setSelected(item)}
                    aria-label={`Show run ${item.id}`}
                  >
                    {item.started_at || item.id} · {item.status}
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {earlierRows > 0 && (
            <p className="muted">
              {earlierRows} earlier loaded runs are outside this 200-row view.
              Refresh returns to the newest runs.
            </p>
          )}
          {history?.next_cursor && (
            <Button
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
