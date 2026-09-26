import { useEffect, useRef, useState } from 'react';
import { MoreHorizontal, Pause, Play, Target, CircleCheck } from 'lucide-react';
import type {
  GoalCommandPayload,
  GoalPage,
  GoalReceipt,
  GoalReview,
} from '../../api/types';
import { clientError } from '../../api/errors';
import {
  Button,
  Disclosure,
  Input,
  Menu,
  StatusDot,
  type Tone,
} from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';

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

const statusWords: Record<string, { label: string; tone: Tone }> = {
  active: { label: 'Working', tone: 'accent' },
  waiting_approval: { label: 'Waiting for approval', tone: 'warning' },
  paused: { label: 'Paused', tone: 'neutral' },
  blocked: { label: 'Blocked', tone: 'danger' },
  completed: { label: 'Completed', tone: 'success' },
  cleared: { label: 'Cleared', tone: 'neutral' },
};

/** Goal pages read per conversation, reused across remounts in a session. */
const pages = new Map<string, GoalPage>();
const REFRESH_MS = 10_000;

/**
 * The conversation's goal, in Context: its objective, progress and the
 * pause/resume/complete controls, plus a small form to set one. Goals are
 * reviewed by the server and applied in one step; clearing asks first.
 */
export default function ContextGoal({
  conversationId,
  revision,
  ready,
  io,
  compose,
  onComposeDone,
}: {
  conversationId: string;
  /** Changes as the conversation moves; a live goal re-reads (throttled). */
  revision: string;
  ready: boolean;
  io: ContextGoalIO;
  compose: boolean;
  onComposeDone: () => void;
}) {
  const [page, setPage] = useState<GoalPage | null>(
    () => pages.get(conversationId) ?? null,
  );
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [objective, setObjective] = useState('');
  const [maxTurns, setMaxTurns] = useState('24');
  const [confirmClear, setConfirmClear] = useState(false);
  const lastRead = useRef(0);
  const ioRef = useRef(io);
  ioRef.current = io;

  const read = async (signal?: AbortSignal) => {
    const next = await ioRef.current.load(conversationId, signal);
    if (signal?.aborted || next.conversation_id !== conversationId) return;
    pages.set(conversationId, next);
    lastRead.current = Date.now();
    setPage(next);
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
  useEffect(() => {
    if (!ready || !live || Date.now() - lastRead.current < REFRESH_MS) return;
    const abort = new AbortController();
    const timer = window.setTimeout(() => {
      read(abort.signal).catch(() => undefined);
    }, 400);
    return () => {
      window.clearTimeout(timer);
      abort.abort();
    };
    // read is stable for a conversation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revision, live, ready]);

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
        setMessage('The goal change is unconfirmed. Refresh before retrying.');
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

  const started = current && current.status !== 'cleared';
  const earlier =
    page?.items.filter(
      (goal) => goal.id !== current?.id || goal.status === 'cleared',
    ) ?? [];
  if (!started && !compose && !earlier.length) return null;
  const state = current ? (statusWords[current.status] ?? null) : null;
  const turns = Number(maxTurns);
  const validTurns = Number.isInteger(turns) && turns >= 1 && turns <= 1000;
  return (
    <div className="context-goal-slot">
      <Disclosure
        className="context-rail-section context-goal"
        summary="Goal"
        meta={
          state ? (
            <StatusDot tone={state.tone} label={state.label} showLabel />
          ) : undefined
        }
        defaultOpen
      >
        {started && current && (
          <div className="context-goal-body">
            <p className="context-goal-objective">{current.objective}</p>
            <div
              className="context-goal-progress"
              role="progressbar"
              aria-label="Goal turns used"
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
              {current.turns_used} of {current.max_turns} turns
              {current.last_progress ? ` · ${current.last_progress}` : ''}
            </small>
            {current.blockers.length > 0 && (
              <small className="context-goal-blockers">
                Blocked: {current.blockers.join('; ')}
              </small>
            )}
            <div className="context-goal-actions">
              {['active', 'waiting_approval'].includes(current.status) && (
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() => void run('pause')}
                >
                  <Pause size={14} aria-hidden /> Pause goal
                </Button>
              )}
              {['paused', 'blocked'].includes(current.status) && (
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() => void run('resume')}
                >
                  <Play size={14} aria-hidden /> Resume goal
                </Button>
              )}
              {current.status !== 'completed' && (
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() => void run('complete')}
                >
                  <CircleCheck size={14} aria-hidden /> Mark done
                </Button>
              )}
              <Menu
                label="More goal actions"
                iconOnly
                variant="ghost"
                className="icon-action icon-action-sm"
                actions={[
                  {
                    label: 'Clear goal',
                    danger: true,
                    disabled: busy,
                    onSelect: () => setConfirmClear(true),
                  },
                ]}
              >
                <MoreHorizontal size={16} aria-hidden />
              </Menu>
            </div>
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
            <label>
              <span className="visually-hidden">Goal objective</span>
              <textarea
                className="input"
                rows={2}
                maxLength={4096}
                placeholder="What should this conversation achieve?"
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
            {started && (
              <small className="context-goal-meta">
                Starting a new goal replaces the current one.
              </small>
            )}
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
                    {(statusWords[goal.status] ?? statusWords.cleared).label} ·{' '}
                    {goal.turns_used} of {goal.max_turns} turns
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
      <ModalTask
        open={confirmClear}
        onOpenChange={setConfirmClear}
        title="Clear this goal?"
        description="The conversation stops working toward it. It stays listed under Earlier goals."
        ariaLabel="Clear goal"
      >
        <div className="button-row">
          <Button onClick={() => setConfirmClear(false)}>Cancel</Button>
          <Button
            variant="danger"
            onClick={() => {
              setConfirmClear(false);
              void run('clear');
            }}
          >
            Clear goal
          </Button>
        </div>
      </ModalTask>
    </div>
  );
}

/** Forget cached goal pages (tests, sign-out). */
export function resetContextGoals() {
  pages.clear();
}
