import { useEffect, useRef, useState } from 'react';
import {
  ChevronRight,
  CircleCheck,
  CircleStop,
  MessageSquare,
  Play,
  X,
} from 'lucide-react';
import type {
  ConversationView,
  DelegatedActivityView,
  DelegatedRun,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { AgentAvatar, agentSeed } from '../../ui/AgentAvatar';
import { Button, Hint, IconButton, Skeleton } from '../../ui/primitives';
import { useOverlay } from '../../ui/overlays';
import {
  AGENT_STATE_WORDS,
  agentLive,
  agentState,
  AgentStatus,
} from './agent-status';

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
  /** Reports whether there is anything to show once a load settles. */
  onContentChange?: (hasContent: boolean) => void;
  /**
   * Reports how many delegated agents are still going (queued, running or
   * waiting) and how many of those are working, once a page settles.
   */
  onLiveChange?: (counts: { live: number; working: number }) => void;
  /**
   * Reports each settled first page: its agents' live status feeds the
   * transcript's stubs, its parent the header's way back (B241, B242).
   */
  onFirstPage?: (page: DelegatedActivityView) => void;
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

/**
 * One agent on one line (B240): its icon, its name (the full name on hover),
 * its status as a dot and a word. Message and Stop sit inside the row while it
 * is still going (on hover and focus; always on touch). Clicking the row opens
 * the agent's conversation; the status word stays in its accessible name.
 */
function AgentRow({
  run,
  onOpen,
  stopRun,
  messageRun,
  onChanged,
}: {
  run: DelegatedRun;
  /** Absent for the conversation's own agent: it is already open. */
  onOpen?: () => void;
  stopRun?: Props['stopRun'];
  messageRun?: Props['messageRun'];
  onChanged: () => void;
}) {
  const { notify } = useOverlay();
  const [writing, setWriting] = useState(false);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const state = agentState(run.status);
  const live = agentLive(state);
  const canStop = live && stopRun && run.status !== 'stopping';
  const canMessage = live && messageRun;
  async function act(work: () => Promise<void>, done?: string) {
    setBusy(true);
    setError('');
    try {
      await work();
      if (done) notify(done);
      onChanged();
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }
  const content = (
    <>
      <AgentAvatar seed={agentSeed(run.profile_id, run.run_id)} size={20} />
      <span className="agent-row-name">{run.name}</span>
      <AgentStatus state={state} />
    </>
  );
  const label = `${run.name}, ${AGENT_STATE_WORDS[state]}`;
  return (
    <li className="agent-row" data-state={state}>
      <div className="agent-row-line">
        {onOpen ? (
          <Hint label={run.name}>
            <button
              type="button"
              className="agent-row-main"
              aria-label={label}
              onClick={onOpen}
            >
              {content}
            </button>
          </Hint>
        ) : (
          <span className="agent-row-main" role="group" aria-label={label}>
            {content}
          </span>
        )}
        {(canMessage || canStop) && (
          <span
            className="agent-row-actions"
            role="group"
            aria-label={`${run.name} actions`}
          >
            {canMessage && (
              <IconButton
                size="sm"
                label={`Message ${run.name}`}
                pressed={writing}
                disabled={busy}
                onClick={() => setWriting((value) => !value)}
              >
                <MessageSquare size={15} aria-hidden />
              </IconButton>
            )}
            {canStop && (
              <IconButton
                size="sm"
                label={`Stop ${run.name}`}
                disabled={busy}
                onClick={() => void act(() => stopRun(run.run_id))}
              >
                <CircleStop size={15} aria-hidden />
              </IconButton>
            )}
          </span>
        )}
      </div>
      {writing && canMessage && (
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
      )}
      {error && (
        <p role="alert" className="agent-controls-status">
          {error}
        </p>
      )}
    </li>
  );
}

/** "2 done · 1 stopped": finished agents fold under the live ones. */
function foldLabel(runs: DelegatedRun[]) {
  const done = runs.filter((run) => agentState(run.status) === 'done').length;
  const stopped = runs.length - done;
  return [done && `${done} done`, stopped && `${stopped} stopped`]
    .filter(Boolean)
    .join(' · ');
}

const ORDER = { working: 0, waiting: 0, failed: 1, done: 2, stopped: 2 };

export default function DelegatedActivity(props: Props) {
  const ready = props.ready ?? true;
  const { notify } = useOverlay();
  const callbacks = useRef(props);
  callbacks.current = props;
  const [page, setPage] = useState<DelegatedActivityView | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [foldOpen, setFoldOpen] = useState(false);
  const ticket = useRef(0);
  const continuation = useRef<AbortController | null>(null);
  const [laterPage, setLaterPage] = useState(false);
  useEffect(() => setFoldOpen(false), [props.conversationId]);
  useEffect(() => {
    const current = ++ticket.current;
    if (!ready) {
      setLoading(false);
      setError(false);
      setPage(null);
      return;
    }
    const settle = (result: DelegatedActivityView) => {
      setPage(result);
      callbacks.current.onFirstPage?.(result);
    };
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
      settle(recent.page);
      setLaterPage(false);
      return;
    }
    const request = new AbortController();
    setLoading(true);
    setError(false);
    // A refresh keeps the last page in place; only another conversation
    // starts from nothing (B163).
    setPage((previous) =>
      previous?.conversation_id === props.conversationId ? previous : null,
    );
    setLaterPage(false);
    void callbacks.current
      .loadPage(undefined, request.signal)
      .then((result) => {
        if (!request.signal.aborted && current === ticket.current) {
          settle(result);
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
          settle({
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
  }, [props.conversationId, props.refreshKey, attempt, ready]);
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
  async function open(run: DelegatedRun) {
    const conversation = props.conversationId;
    try {
      // A queued agent may have its conversation by now: ask again.
      const target =
        run.child_conversation_id ??
        (await callbacks.current.loadRun(run.run_id)).child_conversation_id;
      if (callbacks.current.conversationId !== conversation) return;
      if (target) await callbacks.current.openConversation(target);
      else notify("This agent's conversation isn't available yet.", 'warning');
    } catch (cause) {
      notify(clientError(cause).message, 'danger');
    }
  }
  const refresh = () => setAttempt((value) => value + 1);
  const items = [...(page?.items ?? [])].sort(
    (left, right) =>
      ORDER[agentState(left.status)] - ORDER[agentState(right.status)],
  );
  // Live and failed agents stay in view; done and stopped ones fold.
  const shown = items.filter((run) => ORDER[agentState(run.status)] < 2);
  const folded = items.filter((run) => ORDER[agentState(run.status)] === 2);
  const hasContent =
    error ||
    laterPage ||
    Boolean(props.interrupted) ||
    Boolean(page?.own_run) ||
    Boolean(page?.items.length);
  useEffect(() => {
    // Keep the last answer while a refresh is in flight to avoid flicker.
    if (!loading || hasContent) callbacks.current.onContentChange?.(hasContent);
  }, [hasContent, loading]);
  const states = page ? page.items.map((run) => agentState(run.status)) : null;
  const live = states ? states.filter(agentLive).length : null;
  const working = states
    ? states.filter((state) => state === 'working').length
    : null;
  useEffect(() => {
    // Only a settled page reports; a refresh in flight keeps the last count.
    if (live !== null && working !== null)
      callbacks.current.onLiveChange?.({ live, working });
  }, [live, working]);
  const row = (run: DelegatedRun) => (
    <AgentRow
      key={run.run_id}
      run={run}
      onOpen={() => void open(run)}
      stopRun={props.stopRun}
      messageRun={props.messageRun}
      onChanged={refresh}
    />
  );
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
            onChanged={refresh}
          />
        </div>
      )}
      {page?.own_run && (
        // Inside a delegated agent's own conversation: this agent, with its
        // Message and Stop always in view (B242).
        <ul className="agent-list agent-list-own" aria-label="This agent">
          <AgentRow
            run={page.own_run}
            stopRun={props.stopRun}
            messageRun={props.messageRun}
            onChanged={refresh}
          />
        </ul>
      )}
      {error && (
        <p role="alert">
          Delegated tasks could not be loaded.{' '}
          <Button onClick={refresh}>Retry delegated tasks</Button>
        </p>
      )}
      {loading && !page && <Skeleton label="Loading delegated tasks" />}
      {page && !page.items.length && !page.own_run && !loading && !error && (
        <p className="muted">No delegated agents in this conversation.</p>
      )}
      {!!items.length && (
        <ul className="agent-list">
          {shown.map(row)}
          {folded.length > 0 && (
            <li className="agent-fold">
              <button
                type="button"
                className="agent-fold-toggle"
                aria-expanded={foldOpen}
                onClick={() => setFoldOpen((value) => !value)}
              >
                <CircleCheck size={14} aria-hidden />
                <span>{foldLabel(folded)}</span>
                <ChevronRight
                  className="agent-fold-chevron"
                  size={14}
                  aria-hidden
                />
              </button>
              {foldOpen && <ul className="agent-list">{folded.map(row)}</ul>}
            </li>
          )}
        </ul>
      )}
      {page?.has_more && (
        <Button disabled={loading} onClick={() => void more()}>
          More delegated tasks
        </Button>
      )}
      {laterPage && (
        <Button onClick={refresh}>Return to first delegated tasks</Button>
      )}
    </section>
  );
}
