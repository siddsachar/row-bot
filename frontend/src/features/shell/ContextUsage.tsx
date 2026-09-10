import type { ContextUsageView } from '../../api/types';

export default function ContextUsage({
  usage,
}: {
  usage?: ContextUsageView | null;
}) {
  if (!usage || usage.state === 'unknown')
    return <small>Context usage is available after a response.</small>;
  return (
    <details className="context-usage">
      <summary>
        Last saved context:{' '}
        {usage.estimated_input_tokens?.toLocaleString('en-GB') ?? 'unknown'}{' '}
        estimated tokens{usage.state === 'stale' ? ' · out of date' : ''}
      </summary>
      <p>
        {usage.model_ref ?? 'Model unavailable'}. This is the saved response
        snapshot; the next request may use a different amount.
      </p>
      <p>
        Usable input capacity:{' '}
        {usage.usable_input_tokens?.toLocaleString('en-GB') ?? 'unknown'}{' '}
        tokens. Model window:{' '}
        {usage.native_window_tokens?.toLocaleString('en-GB') ?? 'unknown'}{' '}
        tokens.
      </p>
      {usage.last_confirmed_input_tokens !== null &&
        usage.last_confirmed_input_tokens !== undefined && (
          <p>
            Last provider-confirmed input:{' '}
            {usage.last_confirmed_input_tokens.toLocaleString('en-GB')} tokens.
          </p>
        )}
    </details>
  );
}
