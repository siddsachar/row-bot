import type { ContextUsageView } from '../../api/types';

function tokens(value: number | null | undefined) {
  return value == null ? 'unknown' : value.toLocaleString('en-GB');
}

export default function ContextUsage({
  usage,
}: {
  usage?: ContextUsageView | null;
}) {
  if (!usage || usage.state === 'unknown')
    return (
      <div
        className="context-meter is-unavailable"
        role="status"
        aria-label="Context not measured; usage will appear after a response"
        title="Context will appear after the next validated model preparation."
      >
        <span className="context-meter-label">Context ready</span>
        <span className="context-meter-track" aria-hidden="true" />
      </div>
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
  const tooltip = [
    `${stale ? 'Last measured: approximately' : 'Approximately'} ${tokens(used)} of ${tokens(usage.usable_input_tokens)} usable tokens.`,
    usage.compact_at_tokens
      ? `Automatic compaction starts around ${tokens(usage.compact_at_tokens)} tokens.`
      : 'Automatic compaction threshold is unavailable.',
    `Model window: ${tokens(usage.effective_limit_tokens ?? usage.native_window_tokens)} tokens.`,
    `${usage.model_ref ?? 'Model unavailable'} · ${usage.scope === 'chat_only' ? 'Chat Only' : usage.scope === 'agent' ? 'Agent' : 'Unknown scope'}.`,
  ].join(' ');
  return (
    <details
      className={`context-meter${warning ? ' is-warning' : ''}${stale ? ' is-stale' : ''}`}
      data-context-status={usage.status}
    >
      <summary
        className="context-meter-summary"
        aria-label={`${label}; automatic compaction threshold marker`}
        title={tooltip}
      >
        <span className="context-meter-label">{label}</span>
        <span
          className="context-meter-track"
          role="meter"
          aria-label="Context usage"
          aria-valuemin={0}
          aria-valuemax={usable || undefined}
          aria-valuenow={usable ? used : undefined}
          aria-valuetext={usable ? `${percentLabel} used` : 'Unavailable'}
        >
          <span
            className="context-meter-fill"
            style={{ inlineSize: `${percent}%` }}
          />
          {threshold > 0 && (
            <span
              className="context-meter-threshold"
              style={{ insetInlineStart: `${threshold}%` }}
              aria-hidden="true"
            />
          )}
        </span>
      </summary>
      <div className="context-meter-detail">
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
      </div>
    </details>
  );
}
