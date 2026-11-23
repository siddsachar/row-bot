import { expect, it } from 'vitest';
import type { ReasoningView } from '../../api/types';
import {
  goalRequest,
  profileChoice,
  reasoningChoice,
  reasoningStatus,
  slashArgument,
  watchRequest,
} from './slash-arguments';

const commands = [
  'goal',
  'reasoning',
  'profile',
  'agent',
  'watch',
  'status',
].map((id) => ({
  id,
  token: `/${id}`,
  aliases: id === 'agent' ? ['/subagent'] : id === 'watch' ? ['/loop'] : [],
  label: id,
  description: id,
  icon: '',
  category: 'Chat',
  argument_mode: 'prefix' as const,
  argument_hint: '',
  handler_kind: id,
  skill_id: null,
})) as unknown as Parameters<typeof slashArgument>[1];

it('recognises a palette command that carries an argument', () => {
  expect(slashArgument('/goal Ship the docs', commands)).toMatchObject({
    command: { id: 'goal' },
    argument: 'Ship the docs',
  });
  expect(slashArgument('  /SubAgent  dig into tides ', commands)).toMatchObject(
    { command: { id: 'agent' }, argument: 'dig into tides' },
  );
  expect(slashArgument('/goal', commands)).toBeNull();
  // A bare /reasoning shows the level and its choices (docs: Reasoning Controls).
  expect(slashArgument(' /reasoning ', commands)).toMatchObject({
    command: { id: 'reasoning' },
    argument: '',
  });
  expect(slashArgument('/status now', commands)).toBeNull();
  expect(slashArgument('/unknown thing', commands)).toBeNull();
  expect(slashArgument('Tell me about /goal', commands)).toBeNull();
});

it('reads thinking levels by label, effort and default', () => {
  const reasoning = {
    model_ref: 'm',
    capability_revision: 'c',
    available: true,
    selection: { kind: 'provider_default' },
    choices: [
      { selection: { kind: 'effort', effort: 'xhigh' }, label: 'Extra high' },
      { selection: { kind: 'off' }, label: 'Off' },
    ],
    supports_budget: false,
    budget_min: 0,
    budget_max: 0,
  } as ReasoningView;
  expect(reasoningChoice('Extra high', reasoning)?.selection).toEqual({
    kind: 'effort',
    effort: 'xhigh',
  });
  expect(reasoningChoice('xhigh', reasoning)?.label).toBe('Extra high');
  expect(reasoningChoice('off', reasoning)?.selection).toEqual({ kind: 'off' });
  expect(reasoningChoice('default', reasoning)?.selection).toEqual({
    kind: 'provider_default',
  });
  expect(reasoningChoice('turbo', reasoning)).toBeNull();
  expect(reasoningStatus(reasoning)).toBe(
    'Thinking: Provider default. Choices: Extra high, Off.',
  );
  expect(
    reasoningStatus({
      ...reasoning,
      selection: { kind: 'budget', budget: 4096 },
      supports_budget: true,
    }),
  ).toBe('Thinking: 4096 tokens. Choices: Extra high, Off, budget <tokens>.');
  expect(reasoningStatus({ ...reasoning, available: false })).toBeNull();
  expect(
    reasoningChoice('high', { ...reasoning, available: false }),
  ).toBeNull();
});

it('finds a profile by name or a unique start, and Default by its words', () => {
  const profiles = [
    { id: 'writer_pro', label: 'Writer' },
    { id: 'research', label: 'Research assistant' },
    { id: 'reviewer', label: 'Reviewer' },
  ];
  expect(profileChoice('writer', profiles)?.id).toBe('writer_pro');
  expect(profileChoice('research', profiles)?.id).toBe('research');
  expect(profileChoice('re', profiles)).toBeNull();
  expect(profileChoice('clear', profiles)).toEqual({
    id: '',
    label: 'Default',
  });
});

it('controls a goal with a word and starts one with anything else', () => {
  expect(goalRequest('pause')).toEqual({ operation: 'pause' });
  expect(goalRequest('stop')).toEqual({ operation: 'clear' });
  expect(goalRequest('done')).toEqual({ operation: 'complete' });
  expect(goalRequest('Pause the newsletter drafts')).toEqual({
    operation: 'start',
    objective: 'Pause the newsletter drafts',
  });
});

it('reads the check a goal must pass after "check:" (B3)', () => {
  expect(goalRequest('Ship check: npm test')).toEqual({
    operation: 'start',
    objective: 'Ship',
    check: 'npm test',
  });
  expect(goalRequest('Ship the docs CHECK:  npm run build ')).toEqual({
    operation: 'start',
    objective: 'Ship the docs',
    check: 'npm run build',
  });
  // Only the word on its own is the marker; an empty check is no check.
  expect(goalRequest('Recheck: the links')).toEqual({
    operation: 'start',
    objective: 'Recheck: the links',
  });
  expect(goalRequest('Ship check:')).toEqual({
    operation: 'start',
    objective: 'Ship',
  });
});

it('sets a page or a backticked command watch directly, and leaves anything else to the model (B4)', () => {
  expect(watchRequest('10m https://status.example.com')).toEqual({
    kind: 'direct',
    mode: 'page',
    target: 'https://status.example.com',
    everyMinutes: 10,
  });
  expect(watchRequest('`gh pr checks 590` every 5 minutes')).toEqual({
    kind: 'direct',
    mode: 'command',
    target: 'gh pr checks 590',
    everyMinutes: 5,
  });
  expect(watchRequest('2h https://example.com/price')).toMatchObject({
    everyMinutes: 120,
  });
  // No interval: it paces itself.
  expect(watchRequest('https://example.com')).toMatchObject({
    everyMinutes: 0,
  });
  expect(watchRequest('the deploy and tell me when it is live')).toEqual({
    kind: 'model',
  });
  expect(watchRequest('"is the PR ready to merge?" every 30m')).toEqual({
    kind: 'model',
  });
  expect(watchRequest('0m https://example.com')).toMatchObject({
    kind: 'invalid',
  });
  expect(watchRequest('3000m https://example.com')).toMatchObject({
    kind: 'invalid',
  });
});

it('runs a direct /watch or /loop here and sends a described one as a message (B4)', () => {
  expect(
    slashArgument('/watch 10m https://status.example.com', commands),
  ).toMatchObject({ command: { id: 'watch' } });
  expect(slashArgument('/loop `npm test`', commands)).toMatchObject({
    command: { id: 'watch' },
    argument: '`npm test`',
  });
  expect(
    slashArgument('/watch the deploy until it is live', commands),
  ).toBeNull();
  expect(
    slashArgument('/watch 0m https://example.com', commands),
  ).toMatchObject({ command: { id: 'watch' } });
});
