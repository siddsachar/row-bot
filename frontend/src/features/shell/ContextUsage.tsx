import * as Popover from '@radix-ui/react-popover';
import { Check } from 'lucide-react';
import { useRef, type RefObject } from 'react';
import { Link, useInRouterContext } from 'react-router-dom';
import type {
  ContextBreakdown,
  ContextUsageView,
  ConversationTokenUsage,
} from '../../api/types';
import { Disclosure, Hint } from '../../ui/primitives';
import {
  modelSettingsPath,
  windowWords,
} from '../local-runtime/LocalModelSheet';

function tokens(value: number | null | undefined) {
  return value == null ? 'unknown' : value.toLocaleString('en-GB');
}

// en-US: its compact suffixes (K, M, B) are the same in every ICU build.
const compact = new Intl.NumberFormat('en-US', {
  notation: 'compact',
  maximumFractionDigits: 1,
});

/**
 * What this conversation's model calls used, from the local usage ledger:
 * a short hover suffix and a full sentence for the details. The cost is only
 * one the provider itself returned (OpenRouter); there is no price table.
 */
export function describeConversationUsage(
  usage?: ConversationTokenUsage | null,
): {
  hint: string;
  detail: string;
  cache: string | null;
} | null {
  if (!usage || usage.calls <= 0) return null;
  const total = usage.input_tokens + usage.output_tokens;
  const cache =
    usage.cache_read_percent == null
      ? ''
      : `${usage.cache_read_percent}% from cache`;
  const last = usage.last_cache_read_percent ?? null;
  const cost =
    usage.cost_usd == null
      ? ''
      : `$${usage.cost_usd.toFixed(usage.cost_usd > 0 && usage.cost_usd < 0.01 ? 4 : 2)}`;
  return {
    hint: `${compact.format(total)} tokens used${cache ? `, ${cache}` : ''}`,
    // With the last request's share, the cache has its own line and the total leaves it out.
    detail: `This conversation: ${tokens(total)} tokens used${
      cache && last == null
        ? `, ${usage.cache_read_percent}% of input read from cache`
        : ''
    }${cost ? `. Cost reported by the provider: ${cost}` : ''}.`,
    cache:
      last == null
        ? null
        : `Cache: ${last}% of the last request came from the provider's cache${
            cache
              ? ` (${usage.cache_read_percent}% over this conversation)`
              : ''
          }.`,
  };
}

const PARTS = [
  ['instructions', "Row-Bot's instructions"],
  ['tools', 'Tools'],
  ['skills', 'Skills and guides'],
  ['memory', 'Memories'],
  ['conversation', 'Conversation'],
] as const;

function share(value: number, total: number) {
  const percent = (value / total) * 100;
  return percent > 0 && percent < 1 ? '<1%' : `${Math.round(percent)}%`;
}

/**
 * What fills the context after the last reply: Row-Bot's instructions, the
 * tools, skills and guides, memories and the conversation, as a thin bar and
 * one row each. Estimates, counted once a reply settles; empty parts are left
 * out. A live count past that reply marks the parts "As of the last reply".
 */
export function ContextBreakdownSection({
  breakdown,
  estimated,
}: {
  breakdown: ContextBreakdown;
  estimated: number | null | undefined;
}) {
  const parts = PARTS.map(([key, label]) => ({
    key,
    label: key === 'tools' ? `${label} (${breakdown.tool_count})` : label,
    value: breakdown[key],
  })).filter((part) => part.value > 0);
  const total = parts.reduce((sum, part) => sum + part.value, 0);
  if (total <= 0) return null;
  const described = parts
    .map((part) => `${part.label} ${share(part.value, total)}`)
    .join(', ');
  return (
    <section className="context-breakdown" aria-label="What fills the context">
      <div
        className="context-breakdown-bar"
        role="img"
        aria-label={`What fills the context: ${described}`}
      >
        {parts.map((part) => (
          <span
            key={part.key}
            data-part={part.key}
            style={{ flexGrow: part.value }}
          />
        ))}
      </div>
      <ul className="context-breakdown-list">
        {parts.map((part) => (
          <li key={part.key}>
            <span
              className="context-breakdown-dot"
              data-part={part.key}
              aria-hidden
            />
            <span className="context-breakdown-label">{part.label}</span>
            <span className="context-breakdown-tokens">
              {tokens(part.value)}
            </span>
            <span className="context-breakdown-share">
              {share(part.value, total)}
            </span>
          </li>
        ))}
      </ul>
      {estimated != null && estimated !== total && (
        <p className="context-breakdown-note">As of the last reply</p>
      )}
    </section>
  );
}

const RADIUS = 7;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export function Ring({
  percent,
  threshold,
}: {
  percent: number;
  threshold: number;
}) {
  const angle = (threshold / 100) * 2 * Math.PI - Math.PI / 2;
  return (
    <svg className="context-ring-svg" viewBox="0 0 20 20" aria-hidden>
      <circle className="context-ring-track" cx="10" cy="10" r={RADIUS} />
      {/* Nothing used draws no arc: a lone round cap reads as a spinner. */}
      {percent > 0 && (
        <circle
          className="context-ring-fill"
          cx="10"
          cy="10"
          r={RADIUS}
          strokeDasharray={`${(percent / 100) * CIRCUMFERENCE} ${CIRCUMFERENCE}`}
          transform="rotate(-90 10 10)"
        />
      )}
      {threshold > 0 && (
        <line
          className="context-ring-threshold"
          x1={10 + Math.cos(angle) * (RADIUS - 2.6)}
          y1={10 + Math.sin(angle) * (RADIUS - 2.6)}
          x2={10 + Math.cos(angle) * (RADIUS + 2.6)}
          y2={10 + Math.sin(angle) * (RADIUS + 2.6)}
        />
      )}
    </svg>
  );
}

/**
 * One line for a menu or tooltip: "43% of context · compacts at 80%", with the
 * ring's values. A one-line composer shows it in its + menu.
 */
export function describeContextUsage(usage?: ContextUsageView | null): {
  percent: number;
  threshold: number;
  text: string;
} {
  if (!usage || usage.state === 'unknown')
    return {
      percent: 0,
      threshold: 0,
      text: 'Context will appear after the next response',
    };
  const { usable, percent, threshold, percentLabel, stale } = measure(usage);
  const text =
    usage.status === 'compacting'
      ? 'Compacting context…'
      : usable
        ? `${stale ? '~' : ''}${percentLabel} of context${threshold ? ` · compacts at ${Math.round(threshold)}%` : ''}`
        : 'Context unavailable';
  return { percent, threshold, text };
}

/**
 * Context usage as a small ring beside the send button. The tooltip reads
 * "43% · compacts at 80%"; the full measurement opens on click. Before the
 * first response there is nothing to show, and an empty ring there looked
 * like a loading spinner, so it appears with the first measurement.
 */
export default function ContextUsage({
  usage,
}: {
  usage?: ContextUsageView | null;
}) {
  if (!usage || usage.state === 'unknown') return null;
  const { used, usable, percent, threshold, percentLabel, stale, label } =
    measure(usage);
  const warning =
    usage.status === 'failed' ||
    usage.status === 'compacting' ||
    (threshold > 0 && percent >= threshold);
  const spent = describeConversationUsage(usage.conversation_usage);
  const contextHint =
    usage.status === 'compacting'
      ? 'Compacting context…'
      : usable
        ? `${stale ? '~' : ''}${percentLabel} of context${threshold ? ` · compacts at ${Math.round(threshold)}%` : ''}`
        : 'Context unavailable';
  const hint = spent ? `${contextHint} · ${spent.hint}` : contextHint;
  return (
    <span
      className={`context-meter context-ring${warning ? ' is-warning' : ''}${stale ? ' is-stale' : ''}`}
      data-context-status={usage.status}
    >
      <span
        className="visually-hidden"
        role="meter"
        aria-label="Context usage"
        aria-valuemin={0}
        aria-valuemax={usable || undefined}
        aria-valuenow={usable ? used : undefined}
        aria-valuetext={usable ? `${percentLabel} used` : 'Unavailable'}
      />
      <Popover.Root>
        <Hint label={hint}>
          <Popover.Trigger asChild>
            <button
              type="button"
              className="context-ring-button"
              aria-label={`${label}; automatic compaction threshold marker`}
            >
              <Ring percent={percent} threshold={threshold} />
              <span className="visually-hidden">{label}</span>
            </button>
          </Popover.Trigger>
        </Hint>
        <Popover.Portal>
          <Popover.Content
            className="popover surface-effect context-meter-detail"
            side="top"
            align="end"
            sideOffset={8}
            collisionPadding={12}
            aria-label="Context usage details"
          >
            <ContextDetails usage={usage} />
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
    </span>
  );
}

/**
 * The same details for a one-line composer, where the ring is a row of the +
 * menu: choosing it opens them over the + button once the menu has closed.
 */
export function ContextDetailsPopover({
  usage,
  open,
  onOpenChange,
  anchor,
}: {
  usage?: ContextUsageView | null;
  open: boolean;
  onOpenChange(open: boolean): void;
  anchor: RefObject<HTMLElement | null>;
}) {
  const content = useRef<HTMLDivElement>(null);
  if (!usage || usage.state === 'unknown') return null;
  return (
    <Popover.Root open={open} onOpenChange={onOpenChange}>
      <Popover.Anchor virtualRef={anchor} />
      <Popover.Portal>
        <Popover.Content
          ref={content}
          className="popover surface-effect context-meter-detail"
          side="top"
          align="start"
          sideOffset={8}
          collisionPadding={12}
          aria-label="Context usage details"
          // Focus the details themselves, not the first control in them (Details).
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            content.current?.focus();
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            anchor.current?.focus();
          }}
        >
          <ContextDetails usage={usage} />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function measure(usage: ContextUsageView) {
  const used = usage.estimated_input_tokens ?? 0;
  const usable = usage.usable_input_tokens ?? 0;
  const percent = usable > 0 ? Math.min(100, (used / usable) * 100) : 0;
  const threshold =
    usable > 0 && usage.compact_at_tokens
      ? Math.min(100, (usage.compact_at_tokens / usable) * 100)
      : 0;
  const percentLabel =
    percent > 0 && percent < 1 ? '<1%' : `${Math.round(percent)}%`;
  const stale = usage.freshness === 'stale' || usage.state === 'stale';
  const label =
    usage.status === 'compacting'
      ? 'Compacting context…'
      : usage.status === 'failed'
        ? `Context ${percentLabel} · compaction failed`
        : usable
          ? `Context ${stale ? '~' : ''}${percentLabel}`
          : 'Context unavailable';
  return { used, usable, percent, threshold, percentLabel, stale, label };
}

/** The largest window a local model may be given (its native 256K). */
const LARGEST_LOCAL_WINDOW = 262_144;

/**
 * A local model whose window Row-Bot sets (Ollama, Run on this computer) and
 * that this chat already half fills: its window is what limits the chat, so
 * the details say so, with the way to its settings (U3).
 */
function LocalWindowHint({ usage }: { usage: ContextUsageView }) {
  const routed = useInRouterContext();
  const ref = usage.model_ref ?? '';
  const window = usage.effective_limit_tokens;
  const changeable =
    (ref.startsWith('model:ollama:') && !ref.includes('row-bot-personal')) ||
    ref.startsWith('model:custom_openai_llamacpp:');
  const { percent } = measure(usage);
  if (
    !routed ||
    !changeable ||
    !window ||
    window >= LARGEST_LOCAL_WINDOW ||
    percent < 50
  )
    return null;
  return (
    <p className="context-local-hint">
      This model reads {windowWords(window)}.{' '}
      <Link to={modelSettingsPath(ref)}>Give it more in its settings</Link>
    </p>
  );
}

function ContextDetails({ usage }: { usage: ContextUsageView }) {
  const { used, stale, label } = measure(usage);
  const spent = describeConversationUsage(usage.conversation_usage);
  const summary = [
    `${stale ? 'Last measured: approximately' : 'Approximately'} ${tokens(used)} of ${tokens(usage.usable_input_tokens)} usable tokens.`,
    usage.compact_at_tokens
      ? `Automatic compaction starts around ${tokens(usage.compact_at_tokens)} tokens.`
      : 'Automatic compaction threshold is unavailable.',
  ].join(' ');
  const model = [
    `Model window: ${tokens(usage.effective_limit_tokens ?? usage.native_window_tokens)} tokens.`,
    `${usage.model_ref ?? 'Model unavailable'} · ${usage.scope === 'chat_only' ? 'Chat Only' : usage.scope === 'agent' ? 'Agent' : 'Unknown scope'}.`,
  ].join(' ');
  return (
    <>
      <strong className="context-meter-title">{label}</strong>
      <p>{summary}</p>
      {/* Prepared while the chat was idle, so reaching the point won't pause the reply (B6). */}
      {usage.summary_ready && (
        <p className="context-summary-ready">
          <Check size={12} aria-hidden />A summary of older messages is ready.
        </p>
      )}
      {usage.breakdown && (
        <ContextBreakdownSection
          breakdown={usage.breakdown}
          estimated={usage.estimated_input_tokens}
        />
      )}
      <LocalWindowHint usage={usage} />
      {spent?.cache && <p>{spent.cache}</p>}
      {spent && <p>{spent.detail}</p>}
      <Disclosure summary="Details" className="context-meter-more">
        <p>{model}</p>
        {usage.last_confirmed_input_tokens != null && (
          <p>
            Last provider-confirmed input:{' '}
            {tokens(usage.last_confirmed_input_tokens)} tokens.
          </p>
        )}
        <p>
          Capacity source state: {usage.capacity_state}. Freshness:{' '}
          {usage.freshness}.
        </p>
      </Disclosure>
    </>
  );
}
