import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ConversationComposer } from '../../api/types';
import ComposerSkills, { ComposerSkillChips } from './ComposerSkills';

const composer: ConversationComposer = {
  schema_version: 1,
  conversation_id: 'conversation-a',
  conversation_revision: '4',
  composer_revision: 'composer-4',
  library: { availability: 'available', revision: 'skills-2' },
  smart_skills_off: false,
  active_skills: [
    {
      id: 'review',
      display_name: 'Careful review',
      icon: '🔎',
      description: 'Review carefully.',
      library_source: 'bundled',
      source: 'auto',
      removable: true,
    },
  ],
  suggestions: [
    {
      id: 'suggest-write',
      skill_id: 'write',
      display_name: 'Clear writing',
      icon: '✍️',
      description: 'Write clearly.',
      reason: 'The draft asks for a rewrite.',
    },
  ],
  commands: [
    {
      id: 'skill:write',
      token: '/clear-writing',
      aliases: [],
      label: 'Clear writing',
      description: 'Write clearly.',
      icon: '✍️',
      category: 'Skills',
      argument_mode: 'none',
      argument_hint: '',
      handler_kind: 'activate_skill',
      skill_id: 'write',
    },
  ],
  command_total: 1,
  commands_truncated: false,
};

it('shows skill provenance and performs activate, dismiss, and remove actions', async () => {
  const action = vi.fn().mockResolvedValue(undefined);
  render(
    <ComposerSkillChips composer={composer} disabled={false} action={action} />,
  );
  expect(screen.getByText('auto')).toBeVisible();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: /Use Clear writing/ })),
  );
  expect(action).toHaveBeenCalledWith('activate', 'write');
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Dismiss Clear writing suggestion' }),
    ),
  );
  expect(action).toHaveBeenCalledWith('dismiss', 'write');
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Remove Careful review from this chat',
      }),
    ),
  );
  expect(action).toHaveBeenCalledWith('remove', 'review');
});

it('filters the picker and mutates only the current conversation', async () => {
  const action = vi.fn().mockResolvedValue(undefined);
  render(
    <ComposerSkills composer={composer} disabled={false} action={action} />,
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Skills: 1 active' })),
  );
  const menu = within(screen.getByRole('dialog', { name: 'Smart Skills' }));
  fireEvent.change(
    menu.getByRole('textbox', { name: 'Search available skills' }),
    {
      target: { value: 'clear' },
    },
  );
  await act(async () =>
    fireEvent.click(menu.getByRole('button', { name: /Clear writing/ })),
  );
  expect(action).toHaveBeenCalledWith('activate', 'write');

  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Skills: 1 active' })),
  );
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Reset Skills for this chat' }),
    ),
  );
  expect(action).toHaveBeenCalledWith('reset');
});

it('reports unavailable libraries truthfully', async () => {
  render(
    <ComposerSkills
      composer={{
        ...composer,
        library: { availability: 'unavailable', revision: 'unavailable' },
      }}
      disabled={false}
      action={vi.fn()}
      open
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent(
    'The Skills library is unavailable.',
  );
});
