import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { expect, it, vi } from 'vitest';
import type { ConversationComposer } from '../../api/types';
import ComposerSkills, {
  ComposerSkillChips,
  SkillsAnchor,
} from './ComposerSkills';

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

it('performs activate, dismiss, and remove actions', async () => {
  const action = vi.fn().mockResolvedValue(undefined);
  render(
    <ComposerSkillChips composer={composer} disabled={false} action={action} />,
  );
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

it('gives a default skill a chip with only its name, whose × removes it from this chat (B236)', async () => {
  const action = vi.fn().mockResolvedValue(undefined);
  const seeded = {
    ...composer,
    suggestions: [],
    active_skills: composer.active_skills.map((skill) => ({
      ...skill,
      source: 'default' as const,
    })),
  };
  render(
    <ComposerSkillChips composer={seeded} disabled={false} action={action} />,
  );
  const chips = screen.getByLabelText('Smart Skills');
  expect(chips).toHaveTextContent('Careful review');
  expect(chips).not.toHaveTextContent('default');
  expect(screen.getByTitle('Review carefully.')).toHaveTextContent(
    'Careful review',
  );
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Remove Careful review from this chat',
      }),
    ),
  );
  expect(action).toHaveBeenCalledExactlyOnceWith('remove', 'review');
});

it("shows no remove button on a skill the chat can't drop (an agent profile's)", () => {
  const profiled = {
    ...composer,
    suggestions: [],
    active_skills: composer.active_skills.map((skill) => ({
      ...skill,
      source: 'thread' as const,
      removable: false,
    })),
  };
  render(
    <ComposerSkillChips
      composer={profiled}
      disabled={false}
      action={vi.fn()}
    />,
  );
  expect(screen.getByLabelText('Smart Skills')).toHaveTextContent(
    'Careful review',
  );
  expect(screen.queryByRole('button', { name: /Remove/ })).toBeNull();
});

function Anchored(
  props: Omit<Parameters<typeof ComposerSkills>[0], 'children'>,
) {
  const [open, setOpen] = useState(false);
  return (
    <ComposerSkills {...props} open={open} onOpenChange={setOpen}>
      <SkillsAnchor asChild>
        <button type="button" onClick={() => setOpen(true)}>
          Add files and more
        </button>
      </SkillsAnchor>
    </ComposerSkills>
  );
}

it('filters the picker and mutates only the current conversation', async () => {
  const action = vi.fn().mockResolvedValue(undefined);
  render(<Anchored composer={composer} disabled={false} action={action} />);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Add files and more' })),
  );
  expect(
    screen.getByRole('dialog', { name: 'Smart Skills' }),
  ).toHaveTextContent('1 active');
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
    fireEvent.click(screen.getByRole('button', { name: 'Add files and more' })),
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
    >
      <SkillsAnchor asChild>
        <button type="button">Add files and more</button>
      </SkillsAnchor>
    </ComposerSkills>,
  );
  expect(screen.getByRole('status')).toHaveTextContent(
    'The Skills library is unavailable.',
  );
});

it('leads to the Skills library to find more', async () => {
  const findMore = vi.fn();
  render(
    <Anchored
      composer={composer}
      disabled={false}
      action={vi.fn().mockResolvedValue(undefined)}
      onFindMore={findMore}
    />,
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Add files and more' })),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Find more skills' })),
  );
  expect(findMore).toHaveBeenCalledOnce();
  expect(screen.queryByRole('dialog', { name: 'Smart Skills' })).toBeNull();
});

it('says what a suggested skill does, never why it matched', () => {
  render(
    <ComposerSkillChips
      composer={{
        ...composer,
        suggestions: [
          {
            ...composer.suggestions[0],
            reason: 'matched heading terms check',
          },
        ],
      }}
      disabled={false}
      action={vi.fn()}
    />,
  );
  const suggestion = screen.getByRole('button', { name: /Use Clear writing/ });
  expect(suggestion).toHaveAttribute('title', 'Write clearly.');
  expect(document.body.innerHTML).not.toContain('matched heading terms');
});
