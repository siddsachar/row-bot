import { expect, it } from 'vitest';
import type { ReasoningView } from '../../api/types';
import {
  goalRequest,
  profileChoice,
  reasoningChoice,
  slashArgument,
} from './slash-arguments';

const commands = ['goal', 'reasoning', 'profile', 'agent', 'status'].map(
  (id) => ({
    id,
    token: `/${id}`,
    aliases: id === 'agent' ? ['/subagent'] : [],
    label: id,
    description: id,
    icon: '',
    category: 'Chat',
    argument_mode: 'prefix' as const,
    argument_hint: '',
    handler_kind: id,
    skill_id: null,
  }),
) as unknown as Parameters<typeof slashArgument>[1];

it('recognises a palette command that carries an argument', () => {
  expect(slashArgument('/goal Ship the docs', commands)).toMatchObject({
    command: { id: 'goal' },
    argument: 'Ship the docs',
  });
  expect(slashArgument('  /SubAgent  dig into tides ', commands)).toMatchObject(
    { command: { id: 'agent' }, argument: 'dig into tides' },
  );
  expect(slashArgument('/goal', commands)).toBeNull();
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
