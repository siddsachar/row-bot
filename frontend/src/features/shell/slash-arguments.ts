import type { ConversationComposer, ReasoningView } from '../../api/types';

type SlashCommandSpec = ConversationComposer['commands'][number];
type Selection = ReasoningView['selection'];

/** Commands the composer runs itself when they carry an argument (B112). */
const WITH_ARGUMENT = new Set(['goal', 'reasoning', 'profile', 'agent']);

export type SlashArgument = { command: SlashCommandSpec; argument: string };

/**
 * "/goal Ship the docs" → the goal command and "Ship the docs". Only the
 * palette's own commands that take an argument count; anything else is an
 * ordinary message.
 */
export function slashArgument(
  text: string,
  commands: readonly SlashCommandSpec[],
): SlashArgument | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith('/')) return null;
  const space = trimmed.search(/\s/);
  if (space < 0) return null;
  const token = trimmed.slice(0, space).toLocaleLowerCase();
  const argument = trimmed.slice(space).trim();
  const command = commands.find((item) =>
    [item.token, ...item.aliases].some(
      (value) => value.toLocaleLowerCase() === token,
    ),
  );
  if (!command || !argument || !WITH_ARGUMENT.has(command.handler_kind))
    return null;
  return { command, argument };
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
