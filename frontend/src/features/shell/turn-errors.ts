/**
 * The runtime writes provider failures into the transcript as one line such
 * as "⚠️ Billing limit reached — please review your plan…" (agent.py
 * `_friendly_api_error`, optionally wrapped in "⚠️ An error occurred: …").
 * Those rows render as a callout with the cause and a next step.
 */

export type RecoveryAction = 'retry' | 'model' | 'providers' | 'new';

export type TurnError = {
  /** The cause in a few words, e.g. "Billing limit reached". */
  cause: string;
  /** The rest of the runtime's sentence, if any. */
  detail: string;
  /** Next steps, most useful first. */
  actions: RecoveryAction[];
};

const WARNING = /^\s*(?:⚠️?\s*)+/;
const WRAPPER = /^An error occurred:\s*/i;

const KINDS: Array<[RegExp, RecoveryAction[]]> = [
  [/^API quota exceeded|^Billing limit reached/i, ['model', 'providers']],
  [/^Authentication failed/i, ['providers', 'model']],
  [/does not support tool calling/i, ['model']],
  [/^Context too long/i, ['new', 'model']],
  [/^Rate limit reached/i, ['retry', 'model']],
  [/^Request timed out/i, ['retry', 'model']],
  [/^The AI provider/i, ['retry', 'model']],
  [/^I got stuck in a tool loop/i, ['retry']],
  [/^API error:/i, ['retry', 'model']],
];

export function turnError(text: string): TurnError | null {
  const trimmed = text.trim();
  if (!WARNING.test(trimmed) || trimmed.includes('\n\n')) return null;
  const message = trimmed
    .replace(WARNING, '')
    .replace(WRAPPER, '')
    .replace(WARNING, '')
    .trim();
  const kind = KINDS.find(([pattern]) => pattern.test(message));
  if (!kind && !WRAPPER.test(trimmed.replace(WARNING, ''))) return null;
  const [cause, ...rest] = message.split(/\s+[—–-]\s+/);
  const detail = rest.join(' — ').trim();
  return {
    cause: (cause || 'The response could not finish').replace(/[.:]$/, ''),
    detail: detail ? detail.charAt(0).toUpperCase() + detail.slice(1) : '',
    actions: kind?.[1] ?? ['retry', 'model'],
  };
}

export const RECOVERY_LABELS: Record<RecoveryAction, string> = {
  retry: 'Retry',
  model: 'Switch model',
  providers: 'Open providers',
  new: 'New chat',
};
