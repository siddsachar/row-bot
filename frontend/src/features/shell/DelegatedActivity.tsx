import { useEffect, useRef, useState } from 'react';
import { CircleStop, MessageSquare, Play, X } from 'lucide-react';
import type {
  ConversationView,
  DelegatedActivityView,
  DelegatedRun,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, IconButton, Skeleton } from '../../ui/primitives';
import { useOverlay } from '../../ui/overlays';

/**
 * Agent work of a conversation that a restart or a failed step cut off and
 * that now waits on the person (B220): Resume while something is left to
 * run, Dismiss always.
 */
export function interruptedWork(
  row: Pick<ConversationView, 'activity_state' | 'activity_phase'> | null,
): { resumable: boolean } | null {
  if (row?.activity_state !== 'attention') return null;
  if (row.activity_phase === 'resume_required') return { resumable: true };
  if (row.activity_phase === 'interrupted') return { resumable: false };
  return null;
}

type Props = {
  conversationId: string;
  refreshKey: string;
  ready?: boolean;
  loadPage: (
    cursor?: string,
    signal?: AbortSignal,
  ) => Promise<DelegatedActivityView>;
  loadRun: (runId: string, signal?: AbortSignal) => Promise<DelegatedRun>;
  openConversation: (id: string) => Promise<void>;
  /** Stop a delegated agent (parity row 9). */
  stopRun?: (runId: string) => Promise<void>;
  /** Send a delegated agent a message it reads at its next step. */
  messageRun?: (runId: string, text: string) => Promise<void>;
  /** The conversation's interrupted agent work, if any (`interruptedWork`). */
  interrupted?: { resumable: boolean } | null;
  /** Run the interrupted work again (a model call). */
  resumeWork?: () => Promise<void>;
  /** Close the interrupted work without running it. */
  dismissWork?: () => Promise<void>;
  compact?: boolean;
  /** Reports whether there is anything to show once a load settles. */
  onContentChange?: (hasContent: boolean) => void;
  /** Reports how many delegated agents are queued, running or waiting. */
  onLiveChange?: (live: number) => void;
  /** Keeps recent first-page reads across remounts of this section. */
  recentRead?: RecentReads;
};

export type DelegatedRead = {
  key: string;
  page: DelegatedActivityView;
  at: number;
};

// A remount with the same activity state (the context rail unmounts while an
// open panel narrows the chat, or the reader switches back to a conversation)
// reuses a recent read instead of reading again. New agent activity changes
// the key, and Retry always reads.
const RECENT_READ_MS = 30_000;

export type RecentReads = {
  get: (key: string) => DelegatedRead | null;
  set: (read: DelegatedRead) => void;
};

/** Recent reads for the last few conversations, so switching back and forth
 * does not spend a view request each time. */
export function recentReads(limit = 8): RecentReads {
  const reads = new Map<string, DelegatedRead>();
  return {
    get: (key) => reads.get(key) ?? null,
    set: (read) => {
      reads.delete(read.key);
      reads.set(read.key, read);
      for (const key of reads.keys()) {
        if (reads.size <= limit) break;
        reads.delete(key);
      }
    },
  };
}

const ACTIVE_STATES = new Set([
  'queued',
  'starting',
  'running',
  'waiting',
  'waiting_approval',
  'waiting_user',
  'paused',
  'interrupted',
  'stopping',
]);

const STATUS_WORDS: Record<string, string> = {
  queued: 'Queued',
  starting: 'Starting',
  running: 'Working',
  waiting: 'Waiting',
  waiting_approval: 'Waiting for approval',
  waiting_user: 'Needs you',
  paused: 'Paused',
  interrupted: 'Interrupted',
  stopping: 'Stopping',
  completed: 'Done',
  completed_delivery_failed: 'Done · not delivered',
  failed: 'Failed',
  stopped: 'Stopped',
  blocked: 'Blocked',
  timed_out: 'Timed out',
  cancelled: 'Cancelled',
};

/** A run's status in words ("running" reads "Working"). */
export function runStatus(status: string) {
  return STATUS_WORDS[status] ?? status.replaceAll('_', ' ');
}

/**
 * Stop and Message for a delegated agent that is still going. A message is
 * read at the agent's next step; Stop ends it (parity row 9).
 */
export function AgentControls({
  run,
  stopRun,
  messageRun,
  onChanged,
}: {
  run: DelegatedRun;
  stopRun?: Props['stopRun'];
  messageRun?: Props['messageRun'];
  onChanged?: () => void;
}) {
  const [writing, setWriting] = useState(false);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  if (!ACTIVE_STATES.has(run.status) || (!stopRun && !messageRun)) return null;
  async function act(work: () => Promise<void>, done: string) {
    setBusy(true);
    setError('');
    try {
      await work();
      setStatus(done);
      onChanged?.();
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="agent-controls">
      {writing && messageRun ? (
        <form
          className="agent-message"
          onSubmit={(event) => {
            event.preventDefault();
            const value = text.trim();
            if (!value) return;
            void act(async () => {
              await messageRun(run.run_id, value);
              setText('');
              setWriting(false);
            }, 'Message sent. The agent reads it at its next step.');
          }}
        >
          <label>
            <span>Message to {run.name}</span>
            <textarea
              className="input"
              rows={2}
              maxLength={16000}
              autoFocus
              value={text}
              disabled={busy}
              onChange={(event) => setText(event.target.value)}
            />
          </label>
          <div className="button-row">
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => setWriting(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              disabled={busy || !text.trim()}
            >
              Send to agent
            </Button>
          </div>
        </form>
      ) : (
        <div className="button-row">
          {messageRun && (
            <Button disabled={busy} onClick={() => setWriting(true)}>
              <MessageSquare size={14} aria-hidden /> Message
            </Button>
          )}
          {stopRun && run.status !== 'stopping' && (
            <Button
              disabled={busy}
              onClick={() =>
                void act(() => stopRun(run.run_id), 'Stop requested.')
              }
            >
              <CircleStop size={14} aria-hidden /> Stop
            </Button>
          )}
        </div>
      )}
      {status && (
        <p role="status" className="agent-controls-status">
          {status}
        </p>
      )}
      {error && (
        <p role="alert" className="agent-controls-status">
          {error}
        </p>
      )}
    </div>
  );
}

/** "Agent work was interrupted" with Resume and Dismiss (B220). */
export function InterruptedWorkControls({
  resumable,
  resumeWork,
  dismissWork,
  onChanged,
}: {
  resumable: boolean;
  resumeWork?: () => Promise<void>;
  dismissWork?: () => Promise<void>;
  onChanged?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function act(work: () => Promise<void>) {
    setBusy(true);
    setError('');
    try {
      await work();
      onChanged?.();
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      {resumable && resumeWork && (
        <IconButton
          size="sm"
          label="Resume agent work"
          disabled={busy}
          onClick={() => void act(resumeWork)}
        >
          <Play size={14} aria-hidden />
        </IconButton>
      )}
      {dismissWork && (
        <IconButton
          size="sm"
          label="Dismiss agent work"
          disabled={busy}
          onClick={() => void act(dismissWork)}
        >
          <X size={14} aria-hidden />
        </IconButton>
      )}
      {error && (
        <p role="alert" className="agent-controls-status">
          {error}
        </p>
      )}
    </>
  );
}

function RunDetail({
  runId,
  loadRun,
  openConversation,
  stopRun,
  messageRun,
  close,
}: {
  runId: string;
  loadRun: Props['loadRun'];
  openConversation: Props['openConversation'];
  stopRun?: Props['stopRun'];
  messageRun?: Props['messageRun'];
  close: () => void;
}) {
  const [run, setRun] = useState<DelegatedRun | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [opening, setOpening] = useState(false);
  const alive = useRef(false);
  const loader = useRef(loadRun);
  loader.current = loadRun;
  useEffect(() => {
    alive.current = true;
    const request = new AbortController();
    setError(false);
    void loader
      .current(runId, request.signal)
      .then((result) => {
        if (!request.signal.aborted) setRun(result);
      })
      .catch(() => {
        if (!request.signal.aborted) setError(true);
      });
    return () => {
      alive.current = false;
      request.abort();
    };
  }, [runId, attempt]);
  async function openChild() {
    if (opening) return;
    setOpening(true);
    try {
      const fresh = await loader.current(runId);
      if (!alive.current) return;
      setRun(fresh);
      if (fresh.child_conversation_id) {
        await openConversation(fresh.child_conversation_id);
        if (alive.current) close();
      }
    } catch {
      if (alive.current) setError(true);
    } finally {
      if (alive.current) setOpening(false);
    }
  }
  return (
    <>
      {error && (
        <p role="alert">
          This agent's details didn't load.{' '}
          <Button onClick={() => setAttempt((value) => value + 1)}>
            Try again
          </Button>
        </p>
      )}
      {!run && !error && <Skeleton label="Loading agent" />}
      {run && (
        <div className="delegated-run-detail">
          <p role="status" className="delegated-run-status">
            <span className="eyebrow">Status</span> {runStatus(run.status)}
          </p>
          <p className="delegated-run-summary">
            {run.summary || 'Nothing to report yet.'}
          </p>
          <AgentControls
            run={run}
            stopRun={stopRun}
            messageRun={messageRun}
            onChanged={() => setAttempt((value) => value + 1)}
          />
          {run.child_conversation_id ? (
            <Button
              disabled={opening || error}
              onClick={() => void openChild()}
            >
              Open full thread
            </Button>
          ) : (
            <p>Its thread isn't available.</p>
          )}
        </div>
      )}
    </>
  );
}

export default function DelegatedActivity(props: Props) {
  const ready = props.ready ?? true;
  const overlay = useOverlay();
  const callbacks = useRef(props);
  callbacks.current = props;
  const [page, setPage] = useState<DelegatedActivityView | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const ticket = useRef(0);
  const continuation = useRef<AbortController | null>(null);
  const [laterPage, setLaterPage] = useState(false);
  const dismiss = useRef(overlay.dismiss);
  dismiss.current = overlay.dismiss;
  const key = `delegated-${props.conversationId}`;
  useEffect(
    () => () => {
      dismiss.current(key);
    },
    [key],
  );
  useEffect(() => {
    const current = ++ticket.current;
    if (!ready) {
      setLoading(false);
      setError(false);
      setPage(null);
      dismiss.current(key);
      return;
    }
    const readKey = `${props.conversationId}\u0000${props.refreshKey}`;
    const recent =
      attempt === 0 ? callbacks.current.recentRead?.get(readKey) : null;
    if (
      recent &&
      recent.key === readKey &&
      Date.now() - recent.at < RECENT_READ_MS
    ) {
      setLoading(false);
      setError(false);
      setPage(recent.page);
      setLaterPage(false);
      return;
    }
    const request = new AbortController();
    setLoading(true);
    setError(false);
    // A refresh keeps the last page (and a control's "Message sent") in
    // place; only another conversation starts from nothing (B163).
    setPage((previous) =>
      previous?.conversation_id === props.conversationId ? previous : null,
    );
    setLaterPage(false);
    void callbacks.current
      .loadPage(undefined, request.signal)
      .then((result) => {
        if (!request.signal.aborted && current === ticket.current) {
          setPage(result);
          callbacks.current.recentRead?.set({
            key: readKey,
            page: result,
            at: Date.now(),
          });
        }
      })
      .catch((cause: unknown) => {
        if (request.signal.aborted || current !== ticket.current) return;
        // A host without delegated activity simply has nothing to show.
        if (
          cause &&
          typeof cause === 'object' &&
          (cause as { code?: unknown }).code === 'capability_unavailable'
        )
          setPage({
            conversation_id: callbacks.current.conversationId,
            parent_conversation_id: null,
            items: [],
            next_cursor: null,
            has_more: false,
          });
        else setError(true);
      })
      .finally(() => {
        if (!request.signal.aborted && current === ticket.current)
          setLoading(false);
      });
    return () => {
      request.abort();
      continuation.current?.abort();
      // This is a request epoch, not a DOM node; invalidate load-more on unmount.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      ++ticket.current;
    };
  }, [props.conversationId, props.refreshKey, attempt, ready, key]);
  async function more() {
    if (loading || !page?.next_cursor) return;
    const current = ticket.current;
    const request = new AbortController();
    continuation.current?.abort();
    continuation.current = request;
    setLoading(true);
    try {
      const result = await callbacks.current.loadPage(
        page.next_cursor,
        request.signal,
      );
      if (!request.signal.aborted && current === ticket.current) {
        setPage(result);
        setLaterPage(true);
      }
    } catch {
      if (!request.signal.aborted && current === ticket.current) setError(true);
    } finally {
      if (!request.signal.aborted && current === ticket.current)
        setLoading(false);
    }
  }
  const items = [...(page?.items ?? [])].sort((left, right) => {
    const leftActive = ACTIVE_STATES.has(left.status) ? 0 : 1;
    const rightActive = ACTIVE_STATES.has(right.status) ? 0 : 1;
    return leftActive - rightActive;
  });
  const hasContent =
    error ||
    laterPage ||
    Boolean(props.interrupted) ||
    Boolean(page?.parent_conversation_id) ||
    Boolean(page?.items.length);
  useEffect(() => {
    // Keep the last answer while a refresh is in flight to avoid flicker.
    if (!loading || hasContent) callbacks.current.onContentChange?.(hasContent);
  }, [hasContent, loading]);
  const live = page
    ? page.items.filter((run) => ACTIVE_STATES.has(run.status)).length
    : null;
  useEffect(() => {
    // Only a settled page reports; a refresh in flight keeps the last count.
    if (live !== null) callbacks.current.onLiveChange?.(live);
  }, [live]);
  return (
    <section
      aria-label="Delegated tasks"
      className="activity delegated-activity"
      aria-busy={loading}
    >
      {props.interrupted && (
        <div
          className="delegated-interrupted"
          role="group"
          aria-label="Interrupted agent work"
        >
          <span className="delegated-run-name">
            {props.interrupted.resumable
              ? 'Agent work was interrupted before it finished.'
              : 'Agent work was interrupted. Nothing is left to resume.'}
          </span>
          <InterruptedWorkControls
            resumable={props.interrupted.resumable}
            resumeWork={props.resumeWork}
            dismissWork={props.dismissWork}
            onChanged={() => setAttempt((value) => value + 1)}
          />
        </div>
      )}
      {page?.own_run && (
        // Inside a delegated agent's own thread: its status, Stop and Message.
        <div className="delegated-own-run" aria-label="This agent" role="group">
          <span className="delegated-run-name">{page.own_run.name}</span>
          <span className="delegated-run-state">
            {runStatus(page.own_run.status)}
          </span>
          <AgentControls
            run={page.own_run}
            stopRun={props.stopRun}
            messageRun={props.messageRun}
            onChanged={() => setAttempt((value) => value + 1)}
          />
        </div>
      )}
      {page?.parent_conversation_id && (
        <Button
          onClick={() =>
            void callbacks.current
              .openConversation(page.parent_conversation_id!)
              .catch(() => setError(true))
          }
        >
          Back to parent conversation
        </Button>
      )}
      {error && (
        <p role="alert">
          Delegated tasks could not be loaded.{' '}
          <Button onClick={() => setAttempt((value) => value + 1)}>
            Retry delegated tasks
          </Button>
        </p>
      )}
      {loading && !page && <Skeleton label="Loading delegated tasks" />}
      {page && !page.items.length && !page.own_run && !loading && !error && (
        <p className="muted">No delegated agents in this conversation.</p>
      )}
      {!!items.length && (
        <>
          {!props.compact && (
            <header className="activity-heading">
              <h2>Delegated tasks</h2>
              <span className="muted">{items.length} on this page</span>
            </header>
          )}
          <ul className="delegated-run-list">
            {items.map((run) => (
              <li className="delegated-run-item" key={run.run_id}>
                <Button
                  variant="ghost"
                  aria-label={run.name}
                  onClick={() =>
                    overlay.open({
                      key,
                      title: run.name,
                      description: 'What this agent is doing and has found.',
                      content: (
                        <RunDetail
                          runId={run.run_id}
                          loadRun={props.loadRun}
                          openConversation={props.openConversation}
                          stopRun={props.stopRun}
                          messageRun={props.messageRun}
                          close={() => dismiss.current(key)}
                        />
                      ),
                    })
                  }
                >
                  <span className="delegated-run-name">{run.name}</span>
                  <span className="delegated-run-state">
                    {runStatus(run.status)}
                  </span>
                </Button>
                {props.stopRun &&
                  ACTIVE_STATES.has(run.status) &&
                  run.status !== 'stopping' && (
                    <IconButton
                      size="sm"
                      label={`Stop ${run.name}`}
                      onClick={() =>
                        void props.stopRun!(run.run_id)
                          .then(() => setAttempt((value) => value + 1))
                          .catch(() => setError(true))
                      }
                    >
                      <CircleStop size={14} aria-hidden />
                    </IconButton>
                  )}
              </li>
            ))}
          </ul>
        </>
      )}
      {page?.has_more && (
        <Button disabled={loading} onClick={() => void more()}>
          More delegated tasks
        </Button>
      )}
      {laterPage && (
        <Button onClick={() => setAttempt((value) => value + 1)}>
          Return to first delegated tasks
        </Button>
      )}
    </section>
  );
}
