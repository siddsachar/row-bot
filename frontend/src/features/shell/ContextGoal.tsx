import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import * as Popover from '@radix-ui/react-popover';
import {
  CircleAlert,
  CircleCheck,
  CircleSlash,
  CircleStop,
  Info,
  LoaderCircle,
  Pause,
  Play,
  ShieldCheck,
  Target,
  type LucideIcon,
} from 'lucide-react';
import type {
  GoalCommandPayload,
  GoalPage,
  GoalReceipt,
  GoalReview,
  GoalSummary,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useLiveUpdate } from '../../live';
import { writeClipboardText } from '../../platform/clipboard';
import { useDialogFooter } from '../../ui/dialog-footer';
import { parseTimestamp } from '../../ui/format';
import {
  Button,
  CopyGlyph,
  Disclosure,
  IconButton,
  Input,
  StatusDot,
  useCopyFeedback,
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

/**
 * "Turn 3", or "Turn 3 of 10" when the goal has a limit (0 is none): the
 * turn under way, else the turns done.
 */
export function goalTurn(
  goal: Pick<GoalSummary, 'turns_used' | 'max_turns' | 'status'>,
  running: boolean,
) {
  const working = running && goal.status === 'active';
  const limit = goal.max_turns;
  const next = limit
    ? Math.min(goal.turns_used + 1, limit)
    : goal.turns_used + 1;
  const turn = working ? next : goal.turns_used;
  return limit ? `Turn ${turn} of ${limit}` : `Turn ${turn}`;
}

/** "2 h 10 min", "45 min", "under a minute". */
function span(minutes: number) {
  if (minutes < 1) return 'under a minute';
  const hours = Math.floor(minutes / 60);
  const rest = Math.floor(minutes % 60);
  if (!hours) return `${rest} min`;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/** "12.4k tokens", "950 tokens". */
function tokenWords(tokens: number) {
  if (tokens < 1000) return `${tokens} ${tokens === 1 ? 'token' : 'tokens'}`;
  const thousands = tokens / 1000;
  return `${thousands < 100 ? thousands.toFixed(1).replace(/\.0$/, '') : Math.round(thousands)}k tokens`;
}

/**
 * What the goal has used (B244): time running while it is live ("2 h 10 min
 * of 8 h" with a time limit) and its tokens.
 */
export function goalUsage(
  goal: Pick<
    GoalSummary,
    | 'status'
    | 'tokens_used'
    | 'started_at'
    | 'window_started_at'
    | 'max_minutes'
  >,
  now: Date,
) {
  const parts: string[] = [];
  const began = Date.parse(
    goal.max_minutes ? goal.window_started_at : goal.started_at,
  );
  if (
    ['active', 'waiting_approval'].includes(goal.status) &&
    !Number.isNaN(began)
  ) {
    const elapsed = span(Math.max(0, (now.getTime() - began) / 60_000));
    parts.push(
      goal.max_minutes ? `${elapsed} of ${span(goal.max_minutes)}` : elapsed,
    );
  }
  if (goal.tokens_used > 0) parts.push(tokenWords(goal.tokens_used));
  return parts;
}

/** "3 of 10 turns", or "3 turns" without a limit. */
function turnsDone(goal: Pick<GoalSummary, 'turns_used' | 'max_turns'>) {
  return goal.max_turns
    ? `${goal.turns_used} of ${goal.max_turns} turns`
    : `${goal.turns_used} ${goal.turns_used === 1 ? 'turn' : 'turns'}`;
}

/** Text with `code` spans, as the server writes commands ("runs `npm test`"). */
export function WithCode({ text }: { text: string }) {
  return (
    <>
      {text
        .split('`')
        .map((part, index) =>
          index % 2 ? <code key={index}>{part}</code> : part,
        )}
    </>
  );
}

/** "14:05" (the device's own clock style) for a server time. */
function clock(value: string) {
  const date = parseTimestamp(value);
  return date
    ? new Intl.DateTimeFormat(undefined, { timeStyle: 'short' }).format(date)
    : '';
}

/**
 * The goal's check in one line (B3): what it checks with, that it is
 * checking, "Verified by `npm test` · 14:05", that it failed and Row-Bot is
 * fixing it, or that it couldn't run.
 */
export function goalCheckLine(
  goal: Pick<GoalSummary, 'status' | 'check'>,
): { icon: LucideIcon; tone: Tone; text: string; busy?: boolean } | null {
  const check = goal.check;
  if (!check) return null;
  const command = `\`${check.command}\``;
  switch (check.state) {
    case 'pending':
    case 'running':
      return {
        icon: LoaderCircle,
        tone: 'accent',
        text: `Checking with ${command}…`,
        busy: true,
      };
    case 'passed': {
      const at = check.result ? clock(check.result.finished_at) : '';
      return {
        icon: CircleCheck,
        tone: 'success',
        text: `Verified by ${command}${at ? ` · ${at}` : ''}`,
      };
    }
    case 'failed':
      return {
        icon: CircleAlert,
        tone: 'warning',
        text:
          goal.status === 'active'
            ? `${command} failed · Row-Bot is fixing it`
            : `${command} failed`,
      };
    case 'error':
      return {
        icon: CircleSlash,
        tone: 'danger',
        text: `Couldn't run ${command}`,
      };
    default:
      return {
        icon: ShieldCheck,
        tone: 'neutral',
        text: `Checks with ${command}`,
      };
  }
}

/**
 * The card's reason line, unless the check line already says it: while the
 * check decides or Row-Bot fixes what it reported, and once it verified the
 * goal. A pause or an error keeps its reason.
 */
function shownReason(goal: GoalSummary) {
  const reason = goal.last_reason || goal.last_progress || '';
  const state = goal.check?.state;
  if (
    (goal.status === 'active' &&
      (state === 'pending' || state === 'running' || state === 'failed')) ||
    (goal.status === 'completed' && state === 'passed')
  )
    return '';
  return reason;
}

/** The check's last output in a popover (a sheet on a phone), with Copy. */
function CheckOutput({ goal }: { goal: GoalSummary }) {
  const [copied, setCopied] = useCopyFeedback();
  const result = goal.check?.result;
  if (!goal.check || !result) return null;
  const output = result.output_tail.trim();
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <IconButton label="Show the check's output" size="sm">
          <Info size={14} aria-hidden />
        </IconButton>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          className="popover surface-effect context-goal-output"
          aria-label="The check's output"
          side="bottom"
          align="end"
          sideOffset={6}
          collisionPadding={12}
        >
          <header className="context-goal-output-head">
            <span>
              <WithCode text={`\`${goal.check.command}\``} />
              {result.exit_code !== null && ` · exit ${result.exit_code}`}
            </span>
            {output && (
              <IconButton
                label={copied ? 'Copied' : 'Copy the output'}
                size="sm"
                onClick={() =>
                  void writeClipboardText(output).then((ok) => setCopied(ok))
                }
              >
                <CopyGlyph copied={copied} size={14} />
              </IconButton>
            )}
          </header>
          {output ? (
            <pre className="context-goal-output-text" tabIndex={0}>
              {output}
            </pre>
          ) : (
            <p className="context-goal-meta">It printed nothing.</p>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/**
 * The review of a goal with a check, before it starts (B3): where the check
 * runs and that only exit 0 counts. Start goal is the person's approval of
 * that command; its actions sit in the dialog's footer.
 */
export function GoalCheckConfirm({
  review,
  onStart,
  onCancel,
}: {
  review: Pick<GoalReview, 'disclosures'>;
  onStart: () => void;
  onCancel: () => void;
}) {
  const footer = useDialogFooter(true);
  const actions = (
    <>
      <Button onClick={onCancel}>Cancel</Button>
      <Button variant="primary" onClick={onStart}>
        <Target size={14} aria-hidden /> Start goal
      </Button>
    </>
  );
  return (
    <div className="context-goal-review">
      {review.disclosures.map((line) => (
        <p key={line}>
          <WithCode text={line} />
        </p>
      ))}
      {footer ? (
        createPortal(actions, footer)
      ) : (
        <div className="button-row">{actions}</div>
      )}
    </div>
  );
}

const CHECK_ERRORS = new Set([
  'goal_check_refused',
  'goal_check_read_only',
  'goal_check_unavailable',
]);

/** Goal pages read per conversation, reused across remounts in a session. */
const pages = new Map<string, GoalPage>();

/**
 * The conversation's goal, in Context (decision 16): the objective, "Turn 3"
 * (or "Turn 3 of 10" with a limit), the verifier's latest reason, and Pause /
 * Resume / Stop. Starting one begins work at once and the server continues it
 * turn after turn (up to its limit, if it has one); it stops for approvals
 * and when it needs the person. It re-reads whenever a turn of this
 * conversation starts or ends.
 */
export default function ContextGoal({
  conversationId,
  activity,
  running,
  ready,
  io,
  compose,
  onCompose,
  onComposeDone,
  onStopTurn,
  now: suppliedNow,
}: {
  conversationId: string;
  /** Changes when a turn of this conversation starts or ends. */
  activity: string;
  /** A turn of this conversation is running now. */
  running: boolean;
  ready: boolean;
  io: ContextGoalIO;
  compose: boolean;
  /** Open the goal composer (Set a goal, shown while there is no goal). */
  onCompose: () => void;
  onComposeDone: () => void;
  /** Stop the running turn (Stop ends the goal and what it is doing). */
  onStopTurn?: () => void;
  /** The clock for "time running"; it advances by itself when not given. */
  now?: Date;
}) {
  const [page, setPage] = useState<GoalPage | null>(
    () => pages.get(conversationId) ?? null,
  );
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [objective, setObjective] = useState('');
  // Untouched, the limit field shows the Agent runtime default ("" = none).
  const [typedTurns, setTypedTurns] = useState<string | null>(null);
  const [hours, setHours] = useState('');
  // What done means and the check (B3), behind their own disclosure.
  const [doneMeans, setDoneMeans] = useState('');
  const [check, setCheck] = useState('');
  const [showDone, setShowDone] = useState(false);
  const [checkError, setCheckError] = useState('');
  // A start with a check is reviewed first; Start goal then confirms it.
  const [reviewed, setReviewed] = useState<GoalReview | null>(null);
  const placeId = useId();
  const [ticking, setTicking] = useState(() => new Date());
  const now = suppliedNow ?? ticking;
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

  // Keep "time running" honest while a goal is live.
  useEffect(() => {
    if (suppliedNow || !live) return;
    const timer = window.setInterval(() => setTicking(new Date()), 60_000);
    return () => window.clearInterval(timer);
  }, [suppliedNow, live]);

  // A check runs between turns: its card follows it as the server moves on.
  const checking =
    current?.check?.state === 'pending' || current?.check?.state === 'running';
  useLiveUpdate(checking ? ['conversations'] : [], () => {
    read().catch(() => undefined);
  });

  function payloadFor(
    operation: GoalCommandPayload['operation'],
    extra: Partial<GoalCommandPayload>,
  ): GoalCommandPayload | null {
    if (!page) return null;
    return {
      conversation_id: conversationId,
      goal_id: page.current_goal_id,
      revision: page.current_revision,
      operation,
      objective: null,
      max_turns: null,
      reason: operation === 'start' ? null : '',
      ...extra,
    };
  }

  /**
   * Review and apply one goal command. A start with a check stops after its
   * review (`confirmFirst`): the person reads where it runs, and their Start
   * goal applies that same review.
   */
  async function run(
    operation: GoalCommandPayload['operation'],
    extra: Partial<GoalCommandPayload> = {},
    { confirmFirst = false }: { confirmFirst?: boolean } = {},
  ) {
    const payload = payloadFor(operation, extra);
    if (!payload || busy) return;
    setBusy(true);
    setMessage('');
    setCheckError('');
    try {
      const review =
        confirmFirst && reviewed
          ? reviewed
          : await io.review(conversationId, payload);
      if (
        review.conversation_id !== conversationId ||
        review.operation !== operation
      )
        throw { code: 'protocol_incompatible' };
      if (confirmFirst && !reviewed) {
        setReviewed(review);
        return;
      }
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
        clearForm();
        onComposeDone();
      }
    } catch (cause) {
      const error = clientError(cause);
      setReviewed(null);
      if (operation === 'start' && CHECK_ERRORS.has(error.code)) {
        setShowDone(true);
        setCheckError(error.message);
      } else setMessage(error.message);
    } finally {
      setBusy(false);
    }
  }

  function clearForm() {
    setObjective('');
    setTypedTurns(null);
    setHours('');
    setDoneMeans('');
    setCheck('');
    setShowDone(false);
    setCheckError('');
    setReviewed(null);
  }

  /** Any change to the form needs a fresh review before it starts. */
  function edited<T>(set: (value: T) => void) {
    return (value: T) => {
      set(value);
      setReviewed(null);
      setCheckError('');
    };
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
  const state = current ? goalState(current, running) : null;
  const maxTurns =
    typedTurns ??
    (page?.default_max_turns ? String(page.default_max_turns) : '');
  // No number means no limit (B243).
  const turns = maxTurns.trim() ? Number(maxTurns) : null;
  const validTurns =
    turns === null || (Number.isInteger(turns) && turns >= 1 && turns <= 1000);
  // An optional time limit in whole hours, up to a week (B244).
  const limitHours = hours.trim() ? Number(hours) : null;
  const validHours =
    limitHours === null ||
    (Number.isInteger(limitHours) && limitHours >= 1 && limitHours <= 168);
  const reason = current ? shownReason(current) : '';
  const checkLine = current ? goalCheckLine(current) : null;
  const place = page?.check_place;
  const typedCheck = check.trim();
  // Opening the form moves focus into it; Cancel brings it back to Set a goal.
  const objectiveField = useRef<HTMLTextAreaElement>(null);
  const setGoalButton = useRef<HTMLButtonElement>(null);
  const backToSetGoal = useRef(false);
  useEffect(() => {
    if (compose) objectiveField.current?.focus();
    else if (backToSetGoal.current) setGoalButton.current?.focus();
    backToSetGoal.current = false;
  }, [compose]);
  const setGoal = (
    <Button
      ref={setGoalButton}
      variant="ghost"
      className="context-goal-set"
      disabled={!ready}
      title="Give this conversation an objective to work toward"
      onClick={onCompose}
    >
      <Target size={14} aria-hidden /> Set a goal
    </Button>
  );
  // No goal, none before it and nothing to report: no Goal section, only a
  // small Set a goal (a whole row saying "No goal" was noise on every chat).
  if (!started && !compose && !earlier.length && !message)
    return (
      <div className="context-goal-slot">
        <div className="context-rail-section context-goal-compact">
          {setGoal}
        </div>
      </div>
    );
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
            {current.done_means && (
              <p className="context-goal-done-means" title={current.done_means}>
                Done means: {current.done_means}
              </p>
            )}
            {checkLine && (
              <div
                className="context-goal-check"
                data-tone={checkLine.tone}
                role="status"
              >
                <checkLine.icon
                  size={14}
                  aria-hidden
                  className={checkLine.busy ? 'context-goal-spin' : undefined}
                />
                <span>
                  <WithCode text={checkLine.text} />
                </span>
                {!checkLine.busy && <CheckOutput goal={current} />}
              </div>
            )}
            {current.max_turns > 0 && (
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
                    width: `${Math.min(100, (current.turns_used / current.max_turns) * 100)}%`,
                  }}
                />
              </div>
            )}
            <small className="context-goal-meta">
              {[
                goalTurn(current, readRunning),
                ...goalUsage(current, now),
              ].join(' · ')}
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
              if (!objective.trim() || !validTurns || !validHours) return;
              void run(
                'start',
                {
                  objective: objective.trim(),
                  max_turns: turns,
                  max_minutes: limitHours === null ? null : limitHours * 60,
                  reason: null,
                  done_means: doneMeans.trim() || null,
                  check_command: typedCheck || null,
                },
                { confirmFirst: Boolean(typedCheck) },
              );
            }}
          >
            <label className="context-goal-field">
              <span>What should this conversation achieve?</span>
              <textarea
                ref={objectiveField}
                className="input"
                rows={2}
                maxLength={4096}
                placeholder="For example: draft three short posts about tides"
                value={objective}
                disabled={busy || !page}
                onChange={(event) => edited(setObjective)(event.target.value)}
              />
            </label>
            {place?.available !== false && (
              <Disclosure
                className="context-goal-done"
                summary="Done means and check"
                meta={
                  typedCheck ? (
                    <ShieldCheck size={12} aria-label="Has a check" />
                  ) : undefined
                }
                open={showDone}
                onOpenChange={setShowDone}
              >
                <label className="context-goal-field">
                  <span>Done means…</span>
                  <textarea
                    className="input"
                    rows={2}
                    maxLength={1000}
                    placeholder="For example: the docs build and every link works"
                    value={doneMeans}
                    disabled={busy || !page}
                    onChange={(event) =>
                      edited(setDoneMeans)(event.target.value)
                    }
                  />
                </label>
                <label className="context-goal-field">
                  <span>Check with</span>
                  <Input
                    className="context-goal-check-input"
                    maxLength={500}
                    spellCheck={false}
                    autoCapitalize="off"
                    autoCorrect="off"
                    placeholder="A command that must pass, like npm test"
                    value={check}
                    disabled={busy || !page}
                    aria-invalid={checkError ? true : undefined}
                    aria-describedby={placeId}
                    onChange={(event) => edited(setCheck)(event.target.value)}
                  />
                </label>
                {Boolean(place?.suggestions.length) && (
                  <div
                    className="context-goal-chips"
                    role="group"
                    aria-label="The project's checks"
                  >
                    {place!.suggestions.map((command) => (
                      <Button
                        key={command}
                        variant="ghost"
                        className="context-goal-chip"
                        aria-pressed={typedCheck === command}
                        disabled={busy}
                        onClick={() => edited(setCheck)(command)}
                      >
                        <code>{command}</code>
                      </Button>
                    ))}
                  </div>
                )}
                <small
                  id={placeId}
                  className={
                    checkError
                      ? 'context-goal-check-error'
                      : 'context-goal-meta'
                  }
                  role={checkError ? 'alert' : undefined}
                >
                  {checkError ||
                    (place?.runs_in
                      ? `Runs in ${place.runs_in}.`
                      : 'Runs where this conversation works.')}{' '}
                  {!checkError &&
                    !reviewed &&
                    'The goal counts as done only when it exits 0.'}
                </small>
              </Disclosure>
            )}
            {reviewed && (
              <div
                className="context-goal-review"
                role="group"
                aria-label="Before it starts"
              >
                {reviewed.disclosures.map((line) => (
                  <p key={line}>
                    <WithCode text={line} />
                  </p>
                ))}
              </div>
            )}
            <div className="context-goal-form-row">
              <label>
                <span>Turn limit</span>
                <Input
                  type="number"
                  min={1}
                  max={1000}
                  placeholder="No limit"
                  value={maxTurns}
                  disabled={busy || !page}
                  onChange={(event) =>
                    edited(setTypedTurns)(event.target.value)
                  }
                />
              </label>
              <label>
                <span>Time limit (hours)</span>
                <Input
                  type="number"
                  min={1}
                  max={168}
                  placeholder="No limit"
                  value={hours}
                  disabled={busy || !page}
                  onChange={(event) => edited(setHours)(event.target.value)}
                />
              </label>
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  clearForm();
                  backToSetGoal.current = true;
                  onComposeDone();
                }}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                variant="primary"
                disabled={
                  busy ||
                  !page ||
                  !objective.trim() ||
                  !validTurns ||
                  !validHours
                }
              >
                <Target size={14} aria-hidden />{' '}
                {reviewed ? 'Confirm and start' : 'Start goal'}
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
          <div className="context-goal-empty">{setGoal}</div>
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
                    {goalState(goal, false).label} · {turnsDone(goal)}
                    {goal.check?.state === 'passed' && (
                      <>
                        {' · '}
                        <WithCode
                          text={`Verified by \`${goal.check.command}\``}
                        />
                      </>
                    )}
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
