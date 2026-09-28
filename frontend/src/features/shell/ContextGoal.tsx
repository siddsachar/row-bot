import { useEffect, useRef, useState } from 'react';
import { CircleStop, Pause, Play, Target } from 'lucide-react';
import type {
  GoalCommandPayload,
  GoalPage,
  GoalReceipt,
  GoalReview,
  GoalSummary,
} from '../../api/types';
import { clientError } from '../../api/errors';
import {
  Button,
  Disclosure,
  Input,
  StatusDot,
  type Tone,
} from '../../ui/primitives';

export type ContextGoalIO = {
  load: (conversation: string, signal?: AbortSignal) => Promise<GoalPage>;
  review: (
    conversation: string,
    payload: GoalCommandPayload,
    signal?: AbortSignal,
  ) => Promise<GoalReview>;
  execute: (
    conversation: string,
    command: {
      command_id: string;
      type: 'goal.control';
      payload: Record<string, unknown>;
    },
  ) => Promise<GoalReceipt>;
};

/** A new goal works for up to this many turns unless the person says. */
export const DEFAULT_GOAL_TURNS = 10;

/**
 * The goal's state in words. "Working" only while a turn of this
 * conversation actually runs; an active goal between turns is about to
 * continue, never shown as working (B123).
 */
export function goalState(
  goal: Pick<GoalSummary, 'status'>,
  running: boolean,
): { label: string; tone: Tone; pulse?: boolean } {
  switch (goal.status) {
    case 'active':
      return running
        ? { label: 'Working', tone: 'accent', pulse: true }
        : { label: 'Continuing', tone: 'neutral' };
    case 'waiting_approval':
      return { label: 'Waiting for your approval', tone: 'warning' };
    case 'paused':
      return { label: 'Paused', tone: 'neutral' };
    case 'blocked':
      return { label: 'Needs you', tone: 'warning' };
    case 'completed':
      return { label: 'Done', tone: 'success' };
    default:
      return { label: 'Stopped', tone: 'neutral' };
  }
}

/** "Turn 3 of 10": the turn under way, else the turns done. */
export function goalTurn(
  goal: Pick<GoalSummary, 'turns_used' | 'max_turns' | 'status'>,
  running: boolean,
) {
  const working = running && goal.status === 'active';
  const turn = working
    ? Math.min(goal.turns_used + 1, goal.max_turns)
    : goal.turns_used;
  return `Turn ${turn} of ${goal.max_turns}`;
}

/** Goal pages read per conversation, reused across remounts in a session. */
const pages = new Map<string, GoalPage>();

/**
 * The conversation's goal, in Context (decision 16): the objective, "Turn 3
 * of 10", the verifier's latest reason, and Pause / Resume / Stop. Starting
 * one begins work at once and the server continues it turn after turn up to
 * its limit; it stops for approvals and when it needs the person. It re-reads
 * whenever a turn of this conversation starts or ends.
 */
export default function ContextGoal({
  conversationId,
  activity,
  running,
  ready,
  io,
  compose,
  onComposeDone,
  onStopTurn,
}: {
  conversationId: string;
  /** Changes when a turn of this conversation starts or ends. */
  activity: string;
  /** A turn of this conversation is running now. */
  running: boolean;
  ready: boolean;
  io: ContextGoalIO;
  compose: boolean;
  onComposeDone: () => void;
  /** Stop the running turn (Stop ends the goal and what it is doing). */
  onStopTurn?: () => void;
}) {
  const [page, setPage] = useState<GoalPage | null>(
    () => pages.get(conversationId) ?? null,
  );
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [objective, setObjective] = useState('');
  const [maxTurns, setMaxTurns] = useState(String(DEFAULT_GOAL_TURNS));
  const ioRef = useRef(io);
  ioRef.current = io;
  const runningRef = useRef(running);
  runningRef.current = running;
  // The count follows the goal as last read, not the live turn: between a
  // turn's end and the re-read "Turn 4 of 10" must not drop back to 3.
  const [readRunning, setReadRunning] = useState(running);

  const read = async (signal?: AbortSignal) => {
    const next = await ioRef.current.load(conversationId, signal);
    if (signal?.aborted || next.conversation_id !== conversationId) return;
    pages.set(conversationId, next);
    setPage(next);
    setReadRunning(runningRef.current);
  };

  useEffect(() => {
    setPage(pages.get(conversationId) ?? null);
    setMessage('');
    if (!ready || pages.has(conversationId)) return;
    const abort = new AbortController();
    read(abort.signal).catch(() => undefined);
    return () => abort.abort();
    // read is stable for a conversation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, ready]);

  const current =
    page?.items.find((goal) => goal.id === page.current_goal_id) ?? null;
  const live =
    current && ['active', 'waiting_approval'].includes(current.status);
  // Each turn start and end moves a live goal (turns, reason, status); a
  // command elsewhere (/goal in the composer) starts a turn too.
  const seen = useRef(activity);
  useEffect(() => {
    if (!ready || seen.current === activity) return;
    seen.current = activity;
    if (!live && !running && current) return;
    const abort = new AbortController();
    const timer = window.setTimeout(() => {
      read(abort.signal).catch(() => undefined);
    }, 300);
    return () => {
      window.clearTimeout(timer);
      abort.abort();
    };
    // read is stable for a conversation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activity, live, ready, running]);

  async function run(
    operation: GoalCommandPayload['operation'],
    extra: Partial<GoalCommandPayload> = {},
  ) {
    if (!page || busy) return;
    setBusy(true);
    setMessage('');
    const payload: GoalCommandPayload = {
      conversation_id: conversationId,
      goal_id: page.current_goal_id,
      revision: page.current_revision,
      operation,
      objective: null,
      max_turns: null,
      reason: operation === 'start' ? null : '',
      ...extra,
    };
    try {
      const review = await io.review(conversationId, payload);
      if (
        review.conversation_id !== conversationId ||
        review.operation !== operation
      )
        throw { code: 'protocol_incompatible' };
      const receipt = await io.execute(conversationId, {
        command_id: crypto.randomUUID(),
        type: 'goal.control',
        payload: { ...payload, review_id: review.review_id },
      });
      if (receipt.status !== 'completed') {
        setMessage("The goal change wasn't confirmed. Try again.");
        return;
      }
      pages.delete(conversationId);
      await read();
      if (operation === 'start') {
        setObjective('');
        onComposeDone();
      }
    } catch (cause) {
      setMessage(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  function stop() {
    if (running) onStopTurn?.();
    void run('clear', { reason: 'Stopped by you.' });
  }

  const started = current && current.status !== 'cleared';
  const earlier =
    page?.items.filter(
      (goal) => goal.id !== current?.id || goal.status === 'cleared',
    ) ?? [];
  if (!started && !compose && !earlier.length) return null;
  const state = current ? goalState(current, running) : null;
  const turns = Number(maxTurns);
  const validTurns = Number.isInteger(turns) && turns >= 1 && turns <= 1000;
  const reason = current?.last_reason || current?.last_progress || '';
  return (
    <div className="context-goal-slot">
      <Disclosure
        className="context-rail-section context-goal"
        summary="Goal"
        meta={
          state && started ? (
            <StatusDot
              tone={state.tone}
              pulse={state.pulse}
              label={state.label}
              showLabel
            />
          ) : undefined
        }
        defaultOpen
      >
        {started && current && (
          <div className="context-goal-body" aria-label="Goal" role="group">
            <p className="context-goal-objective">{current.objective}</p>
            <div
              className="context-goal-progress"
              role="progressbar"
              aria-label="Goal turns"
              aria-valuetext={goalTurn(current, readRunning)}
              aria-valuemin={0}
              aria-valuemax={current.max_turns}
              aria-valuenow={current.turns_used}
            >
              <span
                style={{
                  width: `${Math.min(100, (current.turns_used / Math.max(1, current.max_turns)) * 100)}%`,
                }}
              />
            </div>
            <small className="context-goal-meta">
              {goalTurn(current, readRunning)}
            </small>
            {reason && <p className="context-goal-reason">{reason}</p>}
            {['active', 'waiting_approval', 'paused', 'blocked'].includes(
              current.status,
            ) && (
              <div className="context-goal-actions">
                {['active', 'waiting_approval'].includes(current.status) ? (
                  <Button
                    variant="ghost"
                    disabled={busy}
                    onClick={() => void run('pause')}
                  >
                    <Pause size={14} aria-hidden /> Pause
                  </Button>
                ) : (
                  <Button
                    variant="ghost"
                    disabled={busy}
                    onClick={() => void run('resume')}
                  >
                    <Play size={14} aria-hidden /> Resume
                  </Button>
                )}
                <Button variant="ghost" disabled={busy} onClick={stop}>
                  <CircleStop size={14} aria-hidden /> Stop
                </Button>
              </div>
            )}
          </div>
        )}
        {compose && (
          <form
            className="context-goal-form"
            aria-label="Set a goal"
            onSubmit={(event) => {
              event.preventDefault();
              if (!objective.trim() || !validTurns) return;
              void run('start', {
                objective: objective.trim(),
                max_turns: turns,
                reason: null,
              });
            }}
          >
            <label className="context-goal-field">
              <span>What should this conversation achieve?</span>
              <textarea
                className="input"
                rows={2}
                maxLength={4096}
                placeholder="For example: draft three short posts about tides"
                value={objective}
                disabled={busy || !page}
                onChange={(event) => setObjective(event.target.value)}
              />
            </label>
            <div className="context-goal-form-row">
              <label>
                <span>Turn limit</span>
                <Input
                  type="number"
                  min={1}
                  max={1000}
                  value={maxTurns}
                  disabled={busy || !page}
                  onChange={(event) => setMaxTurns(event.target.value)}
                />
              </label>
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  setObjective('');
                  onComposeDone();
                }}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                variant="primary"
                disabled={busy || !page || !objective.trim() || !validTurns}
              >
                <Target size={14} aria-hidden /> Start goal
              </Button>
            </div>
            <small className="context-goal-meta">
              {started
                ? 'Starting a new goal replaces the current one.'
                : 'Row-Bot starts at once and keeps going turn after turn; it stops for approvals and when it needs you.'}
            </small>
          </form>
        )}
        {!started && !compose && (
          <p className="context-goal-meta">No goal is running.</p>
        )}
        {earlier.length > 0 && (
          <details className="context-goal-history">
            <summary>
              Earlier goals <span>{earlier.length}</span>
            </summary>
            <ul>
              {earlier.map((goal) => (
                <li key={goal.id}>
                  <span>{goal.objective}</span>
                  <small>
                    {goalState(goal, false).label} · {goal.turns_used} of{' '}
                    {goal.max_turns} turns
                  </small>
                </li>
              ))}
            </ul>
          </details>
        )}
        {message && (
          <p className="context-goal-message" role="status">
            {message}
          </p>
        )}
      </Disclosure>
    </div>
  );
}

/** Forget cached goal pages (tests, sign-out, a goal started elsewhere). */
export function resetContextGoals(conversationId?: string) {
  if (conversationId) pages.delete(conversationId);
  else pages.clear();
}
