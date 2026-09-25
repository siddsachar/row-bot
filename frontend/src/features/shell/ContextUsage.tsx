import * as Popover from '@radix-ui/react-popover';
import type { ContextUsageView } from '../../api/types';
import { Hint } from '../../ui/primitives';

function tokens(value: number | null | undefined) {
  return value == null ? 'unknown' : value.toLocaleString('en-GB');
}

const RADIUS = 7;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

function Ring({ percent, threshold }: { percent: number; threshold: number }) {
  const angle = (threshold / 100) * 2 * Math.PI - Math.PI / 2;
  return (
    <svg className="context-ring-svg" viewBox="0 0 20 20" aria-hidden>
      <circle className="context-ring-track" cx="10" cy="10" r={RADIUS} />
      <circle
        className="context-ring-fill"
        cx="10"
        cy="10"
        r={RADIUS}
        strokeDasharray={`${(percent / 100) * CIRCUMFERENCE} ${CIRCUMFERENCE}`}
        transform="rotate(-90 10 10)"
      />
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
 * Context usage as a small ring beside the send button. The tooltip reads
 * "43% · compacts at 80%"; the full measurement opens on click.
 */
export default function ContextUsage({
  usage,
}: {
  usage?: ContextUsageView | null;
}) {
  if (!usage || usage.state === 'unknown')
    return (
      <Hint label="Context will appear after the next response">
        <span
          className="context-meter context-ring is-unavailable"
          role="status"
          aria-label="Context not measured; usage will appear after a response"
          tabIndex={0}
        >
          <Ring percent={0} threshold={0} />
          <span className="visually-hidden">Context ready</span>
        </span>
      </Hint>
    );
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
  const warning =
    usage.status === 'failed' ||
    usage.status === 'compacting' ||
    (threshold > 0 && percent >= threshold);
  const label =
    usage.status === 'compacting'
      ? 'Compacting context…'
      : usage.status === 'failed'
        ? `Context ${percentLabel} · compaction failed`
        : usable
          ? `Context ${stale ? '~' : ''}${percentLabel}`
          : 'Context unavailable';
  const hint =
    usage.status === 'compacting'
      ? 'Compacting context…'
      : usable
        ? `${stale ? '~' : ''}${percentLabel} of context${threshold ? ` · compacts at ${Math.round(threshold)}%` : ''}`
        : 'Context unavailable';
  const tooltip = [
    `${stale ? 'Last measured: approximately' : 'Approximately'} ${tokens(used)} of ${tokens(usage.usable_input_tokens)} usable tokens.`,
    usage.compact_at_tokens
      ? `Automatic compaction starts around ${tokens(usage.compact_at_tokens)} tokens.`
      : 'Automatic compaction threshold is unavailable.',
    `Model window: ${tokens(usage.effective_limit_tokens ?? usage.native_window_tokens)} tokens.`,
    `${usage.model_ref ?? 'Model unavailable'} · ${usage.scope === 'chat_only' ? 'Chat Only' : usage.scope === 'agent' ? 'Agent' : 'Unknown scope'}.`,
  ].join(' ');
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
            <strong className="context-meter-title">{label}</strong>
            <p>{tooltip}</p>
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
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
    </span>
  );
}
