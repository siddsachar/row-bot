import { useEffect, useId, useRef, useState } from 'react';
import { LayoutList, MoreHorizontal } from 'lucide-react';
import type { ProactiveMomentView } from '../../api/types';
import { parseTimestamp } from '../../ui/format';
import { Button, IconButton, Menu, type MenuAction } from '../../ui/primitives';
import { lessOptions, lessProblem } from './WhyThis';
import type { LessScope } from './moment-actions';
import type { ProactiveView } from './proactive-state';
import { KIND_ONE, KIND_TILES, actionIcon, isLive } from './words';
import './proactive.css';

/** A moment Buddy delivered longer ago than this no longer opens a bubble. */
const BUBBLE_FRESH_MS = 15 * 60_000;
/** Buddy keeps its glance for a delivered moment this long, while it is open. */
const GLANCE_KEEP_MS = 60 * 60_000;
/** A key within this long counts as typing: a bubble waits for a pause. */
export const TYPING_QUIET_MS = 8_000;
/** The fade at the end of a bubble's reading time. */
export const BUBBLE_FADE_MS = 400;

/** How long a bubble stays: clamp(8 s, 3 s + 55 ms a character, 15 s). */
export function readingTime(text: string): number {
  return Math.min(15_000, Math.max(8_000, 3_000 + 55 * text.length));
}

let lastKey = 0;
let watching = false;
function watchTyping() {
  if (watching || typeof window === 'undefined') return;
  watching = true;
  window.addEventListener(
    'keydown',
    () => {
      lastKey = Date.now();
    },
    { capture: true, passive: true },
  );
}

/** A dialog, sheet, drawer or palette is open: a bubble waits, and one already showing steps aside (it never covers one). */
export function overlayOpen(): boolean {
  return (
    typeof document !== 'undefined' &&
    Boolean(
      document.querySelector(
        '[role="dialog"]:not(.proactive-bubble-popover):not(.proactive-bubble):not(.proactive-strip), [role="alertdialog"]',
      ),
    )
  );
}

const SEEN_KEY = 'row-bot:proactive-bubbles-seen:v1';
function readSeen(): Set<string> {
  try {
    return new Set(
      JSON.parse(window.sessionStorage.getItem(SEEN_KEY) ?? '[]') as string[],
    );
  } catch {
    return new Set();
  }
}
const seen = typeof window === 'undefined' ? new Set<string>() : readSeen();
/** Moments the person asked to see again (the palette): no freshness limit. */
const asked = new Set<string>();
const SHOW_AGAIN = 'row-bot-proactive-show-again';

function markSeen(ids: string[]) {
  ids.forEach((id) => {
    seen.add(id);
    asked.delete(id);
  });
  try {
    window.sessionStorage.setItem(
      SEEN_KEY,
      JSON.stringify([...seen].slice(-50)),
    );
  } catch {
    // Without storage a reload may show it once more.
  }
}

/** Test seam: forget which bubbles were seen and when keys were pressed. */
export function resetBuddyMoments() {
  seen.clear();
  asked.clear();
  lastKey = 0;
}

/**
 * "Show what Buddy has for you": the last moment Buddy brought up opens its
 * bubble again while it is still open. False when there is none (the caller
 * opens For you instead).
 */
export function showWhatBuddyHas(
  moments: readonly ProactiveMomentView[],
  now = Date.now(),
): boolean {
  const clock = new Date(now);
  const last = moments
    .filter(
      (moment) =>
        moment.surface === 'buddy' &&
        moment.state === 'shown' &&
        isLive(moment, clock),
    )
    .sort((left, right) => at(right.shown_at) - at(left.shown_at))[0];
  if (!last) return false;
  seen.delete(last.moment_id);
  asked.add(last.moment_id);
  if (typeof window !== 'undefined')
    window.dispatchEvent(new Event(SHOW_AGAIN));
  return true;
}

const at = (value: string) => parseTimestamp(value)?.getTime() ?? 0;

/**
 * Buddy's glance: a moment waits for a break, or one Buddy delivered in the
 * last hour is still open. An expression only, never words.
 */
export function glanceOf(view: ProactiveView, now = Date.now()): boolean {
  const clock = new Date(now);
  return (view.moments ?? []).some(
    (moment) =>
      isLive(moment, clock) &&
      (moment.state === 'pending' ||
        (moment.surface === 'buddy' &&
          now - at(moment.shown_at) < GLANCE_KEEP_MS)),
  );
}

/** "a brief, a follow-up and a date". */
function batchWords(moments: readonly ProactiveMomentView[]): string {
  const words = moments.map((moment) => `a ${KIND_ONE[moment.kind]}`);
  if (words.length <= 2) return words.join(' and ');
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`;
}

export type Bubble =
  | { kind: 'one'; moment: ProactiveMomentView }
  | { kind: 'batch'; moments: ProactiveMomentView[]; line: string };

export function bubbleLine(bubble: Bubble): string {
  return bubble.kind === 'one' ? bubble.moment.title : bubble.line;
}

/**
 * Buddy's bubble: the moments the server delivered on Buddy that this
 * window hasn't shown. One shows as itself; two or more as one batch line.
 * A bubble waits while the person types or an overlay is open, stays for
 * its reading time (held while hovered or focused), fades and leaves; the
 * moments stay in For you. Nothing stacks.
 */
export function useBuddyMoment(view: ProactiveView) {
  const moments = view.moments ?? [];
  const now = Date.now();
  const clock = new Date(now);
  const [, tick] = useState(0);
  const [phase, setPhase] = useState<'shown' | 'fading'>('shown');
  const [held, setHeld] = useState(false);
  const [bubble, setBubble] = useState<Bubble | null>(null);
  useEffect(watchTyping, []);
  useEffect(() => {
    const again = () => tick((n) => n + 1);
    window.addEventListener(SHOW_AGAIN, again);
    return () => window.removeEventListener(SHOW_AGAIN, again);
  }, []);
  const waiting = moments
    .filter(
      (moment) =>
        moment.surface === 'buddy' &&
        moment.state === 'shown' &&
        isLive(moment, clock) &&
        !seen.has(moment.moment_id) &&
        (asked.has(moment.moment_id) ||
          now - at(moment.shown_at) < BUBBLE_FRESH_MS),
    )
    .sort(
      (left, right) =>
        Number(right.urgency === 'urgent') -
          Number(left.urgency === 'urgent') ||
        at(left.shown_at) - at(right.shown_at),
    );
  const key = waiting.map((moment) => moment.moment_id).join(' ');
  const blocked = now - lastKey < TYPING_QUIET_MS || overlayOpen();
  useEffect(() => {
    if (bubble || !waiting.length) return;
    if (blocked) {
      const timer = setTimeout(() => tick((n) => n + 1), 2_000);
      return () => clearTimeout(timer);
    }
    setPhase('shown');
    setBubble(
      waiting.length === 1
        ? { kind: 'one', moment: waiting[0] }
        : {
            kind: 'batch',
            moments: waiting,
            line: `${waiting.length} things for you: ${batchWords(waiting)}.`,
          },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, bubble, blocked]);
  // A moment settled or expired elsewhere: its bubble leaves.
  const open = (id: string) =>
    moments.some((item) => item.moment_id === id && isLive(item, clock));
  const gone = Boolean(
    bubble &&
    (bubble.kind === 'one'
      ? !open(bubble.moment.moment_id)
      : !bubble.moments.some((item) => open(item.moment_id))),
  );
  useEffect(() => {
    if (gone) setBubble(null);
  }, [gone]);
  // A dialog opened over the page while a bubble shows: the bubble steps
  // aside at once (it never covers a dialog) and its moment stays in For you.
  const showing = Boolean(bubble);
  useEffect(() => {
    if (!showing || typeof MutationObserver === 'undefined') return;
    const check = () => {
      if (overlayOpen()) setBubble(null);
    };
    check();
    const observer = new MutationObserver(check);
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['data-state', 'aria-modal'],
    });
    return () => observer.disconnect();
  }, [showing]);
  const ids =
    bubble?.kind === 'one'
      ? [bubble.moment.moment_id]
      : (bubble?.moments.map((item) => item.moment_id) ?? []);
  const close = () => {
    if (!bubble) return;
    markSeen(ids);
    setBubble(null);
    setHeld(false);
  };
  // Reading time, then a short fade, unless the person is in it.
  const line = bubble ? bubbleLine(bubble) : '';
  useEffect(() => {
    if (!bubble || held) return;
    if (phase === 'fading') {
      const timer = setTimeout(() => {
        markSeen(ids);
        setBubble(null);
      }, BUBBLE_FADE_MS);
      return () => clearTimeout(timer);
    }
    const timer = setTimeout(() => setPhase('fading'), readingTime(line));
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bubble, held, phase, line]);
  return {
    bubble,
    fading: phase === 'fading' && !held,
    glance: glanceOf(view, now),
    hold: (value: boolean) => {
      setHeld(value);
      if (value) setPhase('shown');
    },
    close,
  };
}

/** Three chips in place of the bubble's actions: the one Less-like-this question. */
function LessChips({
  moment,
  onLess,
  onCancel,
}: {
  moment: ProactiveMomentView;
  onLess(scope: LessScope): Promise<void>;
  onCancel(): void;
}) {
  const name = useId();
  const [error, setError] = useState('');
  return (
    <div className="proactive-bubble-less">
      <p className="proactive-bubble-line" id={`${name}-q`}>
        Less like this about…
      </p>
      <div
        className="segmented proactive-chips"
        role="radiogroup"
        aria-labelledby={`${name}-q`}
      >
        {lessOptions(moment).map((option, index) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={false}
            title={option.rule}
            className="proactive-chip"
            tabIndex={index === 0 ? 0 : -1}
            onKeyDown={(event) => {
              if (!['ArrowRight', 'ArrowLeft'].includes(event.key)) return;
              event.preventDefault();
              const all = [
                ...event.currentTarget.parentElement!.querySelectorAll<HTMLButtonElement>(
                  'button',
                ),
              ];
              const step = event.key === 'ArrowRight' ? 1 : -1;
              all[(index + step + all.length) % all.length]?.focus();
            }}
            onClick={() =>
              void onLess(option.value).catch((cause) =>
                setError(lessProblem(option.value, cause)),
              )
            }
          >
            {option.label}
          </button>
        ))}
      </div>
      {error && (
        <p className="proactive-error" role="alert">
          {error}
        </p>
      )}
      <div className="proactive-bubble-actions">
        <Button variant="ghost" className="small" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/**
 * The bubble's content: one line, a primary verb (the one text button), an
 * optional icon action and "…". A batch has one line and Open For you.
 * Never takes focus; Esc closes it; no close button (it fades by itself).
 */
export function MomentBubble({
  bubble,
  fading = false,
  menu,
  onAction,
  onOpenForYou,
  onLess,
  onClose,
  onHold,
  contained = false,
}: {
  bubble: Bubble;
  fading?: boolean;
  /** Rendered in a popover that is already the named, non-modal dialog. */
  contained?: boolean;
  menu: MenuAction[];
  onAction(
    moment: ProactiveMomentView,
    action: ProactiveMomentView['actions'][number],
  ): void;
  onOpenForYou(): void;
  /** "Less like this" answered in place; the rule's notice comes from the caller. */
  onLess(moment: ProactiveMomentView, scope: LessScope): Promise<void>;
  onClose(): void;
  onHold?: (held: boolean) => void;
}) {
  const id = useId();
  const [asking, setAsking] = useState(false);
  const announced = useRef('');
  const [announcement, setAnnouncement] = useState('');
  const line = bubbleLine(bubble);
  useEffect(() => {
    if (announced.current === line) return;
    announced.current = line;
    setAnnouncement(`Buddy: ${line}`);
  }, [line]);
  const one = bubble.kind === 'one' ? bubble.moment : null;
  const tile = one ? KIND_TILES[one.kind] : null;
  const [primary, secondary] = one?.actions ?? [];
  const lessItem = menu.find((item) => item.label === 'Less like this');
  const shownMenu = one
    ? menu.map((item) =>
        item === lessItem ? { ...item, onSelect: () => setAsking(true) } : item,
      )
    : menu.filter((item) => item !== lessItem);
  return (
    <div
      className="proactive-bubble"
      // Inside a popover (itself the dialog) the bubble is just its content.
      role={contained ? undefined : 'dialog'}
      aria-modal={contained ? undefined : 'false'}
      aria-labelledby={contained ? undefined : `${id}-line`}
      data-fading={fading ? 'true' : undefined}
      data-variant={bubble.kind}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.stopPropagation();
          onClose();
        }
      }}
      onPointerEnter={() => onHold?.(true)}
      onPointerLeave={() => onHold?.(false)}
      onFocus={() => onHold?.(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null))
          onHold?.(false);
      }}
    >
      {asking && one ? (
        <LessChips
          moment={one}
          onLess={async (scope) => {
            await onLess(one, scope);
            onClose();
          }}
          onCancel={() => setAsking(false)}
        />
      ) : (
        <>
          <p className="proactive-bubble-line" id={`${id}-line`} title={line}>
            <span
              className="overview-tile proactive-bubble-tile"
              data-tone={tile?.tone ?? 'accent'}
              aria-hidden
            >
              {tile?.icon ?? <LayoutList size={16} />}
            </span>
            <span className="proactive-bubble-title">{line}</span>
          </p>
          <div className="proactive-bubble-actions">
            {one ? (
              primary && (
                <Button
                  variant="primary"
                  className="small"
                  onClick={() => onAction(one, primary)}
                >
                  {actionIcon(primary.id, 15)} {primary.label}
                </Button>
              )
            ) : (
              <Button
                variant="primary"
                className="small"
                onClick={onOpenForYou}
              >
                <LayoutList size={15} aria-hidden /> Open For you
              </Button>
            )}
            {one && secondary && (
              <IconButton
                size="sm"
                label={secondary.label}
                onClick={() => onAction(one, secondary)}
              >
                {actionIcon(secondary.id, 15)}
              </IconButton>
            )}
            <Menu
              label={`More for ${line}`}
              hint="More"
              iconOnly
              variant="ghost"
              className="icon-action icon-action-sm"
              actions={shownMenu}
            >
              <MoreHorizontal size={15} aria-hidden />
            </Menu>
          </div>
        </>
      )}
      <p className="visually-hidden" role="status" aria-live="polite">
        {announcement}
      </p>
    </div>
  );
}
