import type { ConversationComposer, ReasoningView } from '../../api/types';

type SlashCommandSpec = ConversationComposer['commands'][number];
type Selection = ReasoningView['selection'];

/** Commands the composer runs itself when they carry an argument (B112). */
const WITH_ARGUMENT = new Set([
  'goal',
  'reasoning',
  'profile',
  'agent',
  'watch',
]);

export type SlashArgument = { command: SlashCommandSpec; argument: string };

/**
 * "/goal Ship the docs" → the goal command and "Ship the docs". Only the
 * palette's own commands that take an argument count; anything else is an
 * ordinary message. A bare "/reasoning" counts too: it shows the chat's
 * thinking level and its choices, as it does in channels.
 */
export function slashArgument(
  text: string,
  commands: readonly SlashCommandSpec[],
): SlashArgument | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith('/')) return null;
  const space = trimmed.search(/\s/);
  const token = (
    space < 0 ? trimmed : trimmed.slice(0, space)
  ).toLocaleLowerCase();
  const argument = space < 0 ? '' : trimmed.slice(space).trim();
  const command = commands.find((item) =>
    [item.token, ...item.aliases].some(
      (value) => value.toLocaleLowerCase() === token,
    ),
  );
  if (
    !command ||
    !WITH_ARGUMENT.has(command.handler_kind) ||
    (!argument && command.handler_kind !== 'reasoning')
  )
    return null;
  // "/watch the deploy and tell me when it's live" is a message: the model
  // sets the watch (B4).
  if (
    command.handler_kind === 'watch' &&
    watchRequest(argument).kind === 'model'
  )
    return null;
  return { command, argument };
}

export type WatchRequest =
  | {
      kind: 'direct';
      mode: 'page' | 'command';
      target: string;
      /** 0: it paces itself. */
      everyMinutes: number;
    }
  | { kind: 'model' }
  | { kind: 'invalid'; message: string };

const INTERVAL =
  /^(?:every\s+)?(\d+(?:\.\d+)?)\s*(m|min|mins|minutes?|h|hr|hrs|hours?)$/i;
const LEADING = /^((?:every\s+)?\d+(?:\.\d+)?\s*[a-z]+)\s+/i;
const TRAILING = /\s+((?:every\s+)?\d+(?:\.\d+)?\s*[a-z]+)$/i;

/**
 * "/watch 10m https://status.example.com" and "/watch 5m `gh pr checks 590`"
 * are set directly, with no model call (B4). The interval is optional and
 * may come first or last ("10m", "2h", "every 10 minutes"). Anything else
 * ("/watch the deploy", a question) goes to the model.
 */
export function watchRequest(argument: string): WatchRequest {
  let rest = argument.trim();
  let everyMinutes = 0;
  for (const piece of [LEADING, TRAILING]) {
    const found = piece.exec(rest);
    const interval = found ? INTERVAL.exec(found[1].trim()) : null;
    if (!found || !interval) continue;
    const amount = Number(interval[1]);
    everyMinutes = interval[2].toLowerCase().startsWith('h')
      ? amount * 60
      : amount;
    rest = (
      rest.slice(0, found.index) + rest.slice(found.index + found[0].length)
    ).trim();
    if (everyMinutes < 1 || everyMinutes > 1440)
      return {
        kind: 'invalid',
        message:
          'A watch checks at most once a minute and at least once a day.',
      };
    break;
  }
  if (/^https?:\/\/\S+$/i.test(rest))
    return { kind: 'direct', mode: 'page', target: rest, everyMinutes };
  const command = /^`([^`]+)`$/.exec(rest);
  if (command && command[1].trim())
    return {
      kind: 'direct',
      mode: 'command',
      target: command[1].trim(),
      everyMinutes,
    };
  return { kind: 'model' };
}

const words = (value: string) =>
  value
    .toLocaleLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();

/** "/reasoning high" → the model's High level; "default" → provider default. */
export function reasoningChoice(
  argument: string,
  reasoning: ReasoningView | null | undefined,
): { selection: Selection; label: string } | null {
  if (!reasoning?.available) return null;
  const wanted = words(argument);
  if (['default', 'provider default', 'auto', 'reset'].includes(wanted))
    return {
      selection: { kind: 'provider_default' },
      label: 'Provider default',
    };
  const choice = reasoning.choices.find(
    (item) =>
      words(item.label) === wanted ||
      words(item.selection.effort ?? '') === wanted ||
      (wanted === item.selection.kind &&
        ['on', 'off'].includes(item.selection.kind)),
  );
  return choice ? { selection: choice.selection, label: choice.label } : null;
}

/** The chat's thinking level as the Thinking control names it. */
export function currentThinkingLabel(
  reasoning: ReasoningView | null | undefined,
): string {
  return (
    reasoning?.choices.find(
      (item) =>
        JSON.stringify(item.selection) === JSON.stringify(reasoning.selection),
    )?.label ??
    (reasoning?.selection.kind === 'budget'
      ? `${reasoning.selection.budget} tokens`
      : 'Provider default')
  );
}

/** "/reasoning" or "/reasoning status": the chat's level and its choices. */
export function reasoningStatus(
  reasoning: ReasoningView | null | undefined,
): string | null {
  if (!reasoning?.available) return null;
  const choices = reasoning.choices.map((item) => item.label);
  if (reasoning.supports_budget) choices.push('budget <tokens>');
  return `Thinking: ${currentThinkingLabel(reasoning)}. Choices: ${choices.join(', ')}.`;
}

/**
 * "/agent --reasoning low …" → "low", and whether --model chose another
 * model (whose levels only the server knows). Null without --reasoning.
 */
export function agentReasoningOption(
  argument: string,
): { value: string; otherModel: boolean } | null {
  const parts = argument.trim().split(/\s+/);
  let value: string | null = null;
  let otherModel = false;
  parts.forEach((part, index) => {
    if (part === '--model' || part.startsWith('--model=')) otherModel = true;
    if (part.startsWith('--reasoning='))
      value = part.slice('--reasoning='.length);
    else if (part === '--reasoning') value = parts[index + 1] ?? '';
  });
  return value === null ? null : { value, otherModel };
}

/** "/profile writer" → the profile whose name or id starts with it. */
export function profileChoice(
  argument: string,
  profiles: readonly { id: string; label: string }[],
): { id: string; label: string } | null {
  const wanted = words(argument);
  if (['default', 'clear', 'none', 'off', 'reset'].includes(wanted))
    return { id: '', label: 'Default' };
  const exact = profiles.find(
    (item) => words(item.label) === wanted || words(item.id) === wanted,
  );
  if (exact) return exact;
  const starting = profiles.filter(
    (item) =>
      words(item.label).startsWith(wanted) || words(item.id).startsWith(wanted),
  );
  return starting.length === 1 ? starting[0] : null;
}

export type GoalRequest =
  | { operation: 'start'; objective: string; check?: string }
  | { operation: 'pause' | 'resume' | 'clear' | 'complete' };

/**
 * "/goal pause" controls the goal; anything else starts one, and
 * "/goal Ship the docs check: npm run build" names the command that must
 * pass before it counts as done (B3).
 */
export function goalRequest(argument: string): GoalRequest {
  const control: Record<string, GoalRequest['operation']> = {
    pause: 'pause',
    resume: 'resume',
    continue: 'resume',
    stop: 'clear',
    clear: 'clear',
    done: 'complete',
    complete: 'complete',
  };
  const operation = control[words(argument)];
  if (operation && operation !== 'start') return { operation } as GoalRequest;
  const marker = /(?:^|\s)check:\s*/i.exec(argument);
  if (!marker) return { operation: 'start', objective: argument.trim() };
  const check = argument.slice(marker.index + marker[0].length).trim();
  return {
    operation: 'start',
    objective: argument.slice(0, marker.index).trim(),
    ...(check ? { check } : {}),
  };
}
