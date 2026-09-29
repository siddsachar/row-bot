import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { Check, ChevronDown, CircleAlert, LoaderCircle, X } from 'lucide-react';
import type {
  TranscriptTraceGroup,
  TranscriptTraceItem,
} from '../../api/types';
import { useRuntime } from '../../runtime';
import { Button, CopyGlyph, useCopyFeedback } from '../../ui/primitives';
import {
  activityLabel,
  formatElapsed,
  isAttention,
  keyArgument,
  orderedSteps,
  stepIcon,
  stepVerb,
  summarizeActivity,
} from './tool-activity';

const MAX_RESULT_PAGES = 20;

// Durations are observed by this client while a step runs. Durable rows carry
// no timing, so steps that finished before this page loaded show none.
const timing = new Map<string, { start: number; end?: number }>();
function observeTiming(steps: TranscriptTraceItem[], now: number) {
  let changed = false;
  for (const step of steps) {
    const known = timing.get(step.call_id);
    if (step.status === 'pending') {
      if (!known) {
        timing.set(step.call_id, { start: now });
        changed = true;
      }
    } else if (known && known.end === undefined) {
      known.end = now;
      changed = true;
    }
  }
  while (timing.size > 512) timing.delete(timing.keys().next().value!);
  return changed;
}
/** How long after this client saw a step finish its glyph still draws in. */
const FRESH_FINISH_MS = 2500;
/**
 * True when this client watched the step run and saw it finish moments ago.
 * A reload or a later visit never replays the motion: durable rows carry no
 * timing, and old observations are too old.
 */
function finishedMoments(step: TranscriptTraceItem, now: number) {
  const end = timing.get(step.call_id)?.end;
  return end !== undefined && now - end < FRESH_FINISH_MS;
}
function stepDuration(step: TranscriptTraceItem) {
  const value = timing.get(step.call_id);
  return value?.end === undefined ? null : value.end - value.start;
}

function prettyInput(value: string) {
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

function specialization(item: TranscriptTraceItem) {
  const value = item.specialization;
  if (!value) return null;
  if (value.kind === 'skill_load')
    return (
      <p className="trace-specialization">
        Skill · {value.display_name || value.skill_id}
        {value.newly_active ? ' activated' : ' already active'}
      </p>
    );
  if (value.kind === 'delegated_agent')
    return (
      <ul className="trace-specialization" aria-label="Delegated agent runs">
        {(value.agent_runs ?? []).map((run) => (
          <li key={run.run_id}>
            {run.display_name} · {run.status}
          </li>
        ))}
      </ul>
    );
  // Generated media renders once, inline with the answer (B22); the step
  // only says what it produced.
  const count = (value.media ?? []).length;
  const kind = value.media_kind || 'result';
  return (
    <div className="trace-specialization trace-media">
      {count > 0 && (
        <p>
          Created {count} {kind}
          {count === 1 ? '' : 's'} · shown in the conversation
        </p>
      )}
      {value.error_code ? (
        <p role="alert">The generated media is unavailable.</p>
      ) : null}
    </div>
  );
}

function StepNode({ item }: { item: TranscriptTraceItem }) {
  // A step this client watched finish settles in; history never animates.
  const [fresh, setFresh] = useState(false);
  const previous = useRef(item.status);
  useLayoutEffect(() => {
    const was = previous.current;
    previous.current = item.status;
    if (item.status === 'pending') return;
    if (was === 'pending' || finishedMoments(item, Date.now())) setFresh(true);
  }, [item]);
  if (item.status === 'pending')
    return (
      <span className="activity-node" data-state="running" aria-hidden>
        <LoaderCircle className="activity-spinner" />
      </span>
    );
  if (isAttention(item.status))
    return (
      <span className="activity-node" data-state="failed" aria-hidden>
        <X />
      </span>
    );
  const Icon = stepIcon(item.canonical_name);
  return (
    <span className="activity-node" data-state="done" aria-hidden>
      <Icon className={fresh ? 'icon-draw-settle' : undefined} />
    </span>
  );
}

function TraceItem({
  conversation,
  item,
}: {
  conversation: string;
  item: TranscriptTraceItem;
}) {
  const { controller, platform } = useRuntime();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(item.safe_summary);
  const [cursor, setCursor] = useState<string | undefined>();
  const [pages, setPages] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [copyStatus, setCopyStatus] = useState('');
  const [resultCopied, setResultCopied] = useCopyFeedback();
  const request = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const attempted = useRef(false);
  const details = useRef<HTMLDetailsElement>(null);
  const loadRef = useRef<(nextCursor?: string) => Promise<void>>(
    async () => {},
  );

  useEffect(() => {
    generation.current++;
    request.current?.abort();
    request.current = null;
    attempted.current = false;
    setText(item.safe_summary);
    setCursor(undefined);
    setPages(0);
    setBusy(false);
    setError('');
    setCopyStatus('');
    if (details.current?.open && item.content_ref) void loadRef.current();
    const activeRequest = request;
    const activeGeneration = generation;
    return () => {
      activeGeneration.current++;
      activeRequest.current?.abort();
    };
  }, [
    conversation,
    item.item_id,
    item.result_message_id,
    item.content_ref,
    item.safe_summary,
  ]);

  async function load(nextCursor?: string) {
    if (!item.content_ref || request.current) return;
    if (!nextCursor) attempted.current = true;
    const abort = new AbortController();
    const current = ++generation.current;
    request.current = abort;
    setBusy(true);
    setError('');
    try {
      const page = await controller.messageText(
        conversation,
        item.content_ref,
        nextCursor,
        abort.signal,
      );
      if (abort.signal.aborted || current !== generation.current) return;
      const value = new TextDecoder().decode(
        Uint8Array.from(atob(page.data), (character) =>
          character.charCodeAt(0),
        ),
      );
      setText((previous) => (nextCursor ? `${previous}${value}` : value));
      setCursor(page.has_more ? (page.next_cursor ?? undefined) : undefined);
      setPages((count) => count + 1);
    } catch {
      if (!abort.signal.aborted && current === generation.current)
        setError(
          'Public result could not be loaded. Showing the available text.',
        );
    } finally {
      if (current === generation.current) {
        request.current = null;
        setBusy(false);
      }
    }
  }
  loadRef.current = load;

  function toggle(isOpen: boolean) {
    setOpen(isOpen);
    if (isOpen && item.content_ref && !attempted.current && !request.current)
      void load();
    if (!isOpen) {
      attempted.current = false;
      if (request.current) {
        generation.current++;
        request.current.abort();
        request.current = null;
        setBusy(false);
      }
    }
  }

  async function copy() {
    try {
      const ok = (await platform.writeClipboard(text)).status === 'ok';
      setResultCopied(ok);
      setCopyStatus(ok ? 'Result copied.' : 'Copy is unavailable.');
    } catch {
      setCopyStatus('Copy is unavailable.');
    }
  }

  const argument = keyArgument(item.safe_input);
  const duration = stepDuration(item);
  return (
    <li className="activity-step" data-trace-status={item.status}>
      <details
        ref={details}
        className="activity-step-details"
        data-trace-status={item.status}
        onToggle={(event) => toggle(event.currentTarget.open)}
      >
        <summary
          onClick={(event) =>
            toggle(
              !(event.currentTarget.parentElement as HTMLDetailsElement).open,
            )
          }
        >
          <StepNode item={item} />
          <span className="activity-step-text">
            <span className="activity-step-verb">
              {stepVerb(item.canonical_name, item.status)}
            </span>
            {argument ? (
              <span className="activity-step-arg">{argument}</span>
            ) : (
              !open &&
              item.safe_summary && (
                <span className="activity-step-preview">
                  {item.safe_summary}
                </span>
              )
            )}
          </span>
          {duration !== null && (
            <span className="activity-step-duration">
              {formatElapsed(duration)}
            </span>
          )}
        </summary>
        <div className="activity-step-body">
          <p className="activity-step-tool">
            <span className="visually-hidden">Tool </span>
            <code>{item.canonical_name}</code>
            <span className="activity-step-status">{item.status}</span>
          </p>
          {specialization(item)}
          {item.safe_input && (
            <div className="trace-input">
              <span className="activity-step-label">Arguments</span>
              <pre>{prettyInput(item.safe_input)}</pre>
            </div>
          )}
          <div className="trace-result-heading">
            <span className="activity-step-label">Result</span>
            {text && (
              <Button
                variant="ghost"
                className="activity-copy"
                onClick={() => void copy()}
              >
                <CopyGlyph copied={resultCopied} size={14} /> Copy result
              </Button>
            )}
          </div>
          {text && <pre className="trace-output">{text}</pre>}
          {busy && <small role="status">Loading public result…</small>}
          {error && <p role="alert">{error}</p>}
          {error && item.content_ref && (
            <Button disabled={busy} onClick={() => void load(cursor)}>
              Retry public result
            </Button>
          )}
          {copyStatus && <small role="status">{copyStatus}</small>}
          {cursor && pages < MAX_RESULT_PAGES && (
            <Button disabled={busy} onClick={() => void load(cursor)}>
              Load next result page
            </Button>
          )}
          {cursor && pages >= MAX_RESULT_PAGES && (
            <small>Result display limited to {MAX_RESULT_PAGES} pages.</small>
          )}
        </div>
      </details>
    </li>
  );
}

export type LiveActivity = {
  /** The response is still being produced. */
  running: boolean;
  /** The model signalled private reasoning. */
  thinking?: boolean;
  /** An approval is holding the run. */
  waiting?: boolean;
  /** The run is paused while the person uses the computer. */
  paused?: boolean;
  /** Stop was requested and the worker is finishing. */
  stopping?: boolean;
  /** When this client saw the run start (Date.now()). */
  startedAt?: number;
};

function useElapsed(startedAt: number | undefined, active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active || startedAt === undefined) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active, startedAt]);
  return active && startedAt !== undefined ? Math.max(0, now - startedAt) : 0;
}

/**
 * One quiet line per turn ("Used 3 tools · 8.4s"). While work runs it reads
 * as live, gently pulsing text; expanded it becomes a step timeline with human
 * verbs, the key argument, duration and status. Step details load on demand.
 */
export default function TranscriptTrace({
  conversation,
  groups,
  live,
  children,
}: {
  conversation: string;
  groups: TranscriptTraceGroup[];
  live?: LiveActivity;
  /** Always-visible content anchored below the row, such as an approval. */
  children?: ReactNode;
}) {
  const steps = orderedSteps(groups);
  const summary = summarizeActivity(groups);
  const running = Boolean(live?.running) || summary.pending > 0;
  const [, setTimingVersion] = useState(0);
  const signature = steps
    .map((step) => `${step.call_id}:${step.status}`)
    .join();
  useLayoutEffect(() => {
    if (observeTiming(steps, Date.now()))
      setTimingVersion((value) => value + 1);
    // The signature captures every status change that matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);
  const elapsed = useElapsed(live?.startedAt, running);
  const durations = steps.map(stepDuration);
  const measured =
    !running && steps.length && durations.every((value) => value !== null)
      ? (() => {
          const spans = steps.map((step) => timing.get(step.call_id)!);
          return (
            Math.max(...spans.map((span) => span.end!)) -
            Math.min(...spans.map((span) => span.start))
          );
        })()
      : null;
  const attention = summary.failed + summary.skipped;
  const status = running ? 'pending' : attention ? 'failed' : 'succeeded';
  // The turn's check draws itself when this client watched it finish (the
  // live row and the stored row that replaces it share the step timing).
  const [drawCheck, setDrawCheck] = useState(false);
  useLayoutEffect(() => {
    if (status !== 'succeeded') return;
    const now = Date.now();
    if (steps.some((step) => finishedMoments(step, now))) setDrawCheck(true);
    // The signature captures every status change that matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, signature]);
  const current = summary.current;
  const text = live?.stopping
    ? 'Stopping…'
    : live?.paused
      ? 'Paused while you use the computer'
      : live?.waiting
        ? 'Waiting for your approval'
        : current
          ? [
              stepVerb(current.canonical_name, 'pending'),
              keyArgument(current.safe_input),
            ]
              .filter(Boolean)
              .join(' ')
          : running && (live?.thinking || !steps.length)
            ? 'Thinking…'
            : running
              ? `Working · ${summary.total} ${summary.total === 1 ? 'tool' : 'tools'} so far`
              : activityLabel(summary);
  if (!steps.length && !running && !children) return null;
  return (
    <div
      className="activity-row"
      data-trace-status={status}
      aria-label={steps.length ? 'Tool activity' : 'Activity'}
      role="group"
    >
      {steps.length > 0 ? (
        <details className="activity-disclosure">
          <summary className="activity-summary">
            <span className="activity-summary-icon" aria-hidden>
              {running ? (
                <LoaderCircle className="activity-spinner" />
              ) : attention ? (
                <CircleAlert />
              ) : (
                <Check className={drawCheck ? 'icon-draw' : undefined} />
              )}
            </span>
            <span
              className="activity-summary-text"
              data-live={running ? 'true' : undefined}
            >
              {text}
            </span>
            {!running && (
              <span className="activity-glyphs" aria-hidden>
                {summary.icons.map((Icon, index) => (
                  <Icon key={index} />
                ))}
              </span>
            )}
            {(running ? elapsed >= 1000 : measured !== null) && (
              <span className="activity-duration">
                {formatElapsed(running ? elapsed : measured!)}
              </span>
            )}
            <ChevronDown className="activity-chevron" aria-hidden />
          </summary>
          <ol className="activity-steps" aria-label="Steps">
            {steps.map((item) => (
              <TraceItem
                conversation={conversation}
                item={item}
                key={item.item_id}
              />
            ))}
          </ol>
        </details>
      ) : (
        running && (
          <p className="activity-summary activity-summary-static" role="status">
            <span className="activity-summary-icon" aria-hidden>
              <LoaderCircle className="activity-spinner" />
            </span>
            <span className="activity-summary-text" data-live="true">
              {text}
            </span>
            {elapsed >= 1000 && (
              <span className="activity-duration">
                {formatElapsed(elapsed)}
              </span>
            )}
          </p>
        )
      )}
      {children}
    </div>
  );
}
