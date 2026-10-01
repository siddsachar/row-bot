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
  | { operation: 'start'; objective: string }
  | { operation: 'pause' | 'resume' | 'clear' | 'complete' };

/** "/goal pause" controls the goal; anything else starts one. */
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
  return operation && operation !== 'start'
    ? ({ operation } as GoalRequest)
    : { operation: 'start', objective: argument.trim() };
}
