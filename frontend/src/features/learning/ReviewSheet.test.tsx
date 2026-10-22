import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import ReviewSheet, { type ReviewChange } from './ReviewSheet';

// Fixture data: the sheet is shown nowhere real until L3b fills it.
const change: ReviewChange = {
  title: 'Pick documents by ID, not title',
  kind: 'Lesson for document lookups',
  why: 'In 4 chats this month Row-Bot opened the wrong document when two had the same title. You corrected it twice.',
  change:
    'When two documents share a title, choose by ID and say which one you picked.',
  results: {
    rows: [
      {
        label: 'Document tasks it hadn’t seen',
        total: 24,
        without: 15,
        with: 21,
      },
      { label: 'Other tasks', total: 80, without: 80, with: 80 },
    ],
    note: 'Testing used 85K tokens · Small test, so expect some variation.',
  },
};

function show(value: ReviewChange = change) {
  const actions = {
    onUse: vi.fn(),
    onTestAgain: vi.fn(),
    onNotNow: vi.fn(),
    onEdit: vi.fn(),
    onSeeChats: vi.fn(),
    onDontSuggest: vi.fn(),
    onDetails: vi.fn(),
  };
  render(
    <ReviewSheet open onOpenChange={vi.fn()} change={value} {...actions} />,
  );
  return actions;
}

it('shows why, what changes and the test result with real headers, then one primary action', async () => {
  const actions = show();
  const sheet = screen.getByRole('dialog', {
    name: 'Pick documents by ID, not title',
  });
  expect(sheet).toHaveAccessibleDescription('Lesson for document lookups');
  for (const section of ['Why', 'What changes', 'Test result'])
    expect(within(sheet).getByRole('region', { name: section })).toBeVisible();
  const table = within(sheet).getByRole('table');
  expect(
    within(table)
      .getAllByRole('columnheader')
      .map((cell) => cell.textContent),
  ).toEqual(['Tasks', 'Without', 'With']);
  expect(
    within(table).getByRole('rowheader', {
      name: 'Document tasks it hadn’t seen (24)',
    }),
  ).toBeVisible();
  expect(within(table).getByText('21 passed')).toBeVisible();
  expect(
    within(sheet).getByText(
      'Testing used 85K tokens · Small test, so expect some variation.',
    ),
  ).toBeVisible();

  const user = userEvent.setup();
  await user.click(
    within(sheet).getByRole('button', { name: 'Use this change' }),
  );
  expect(actions.onUse).toHaveBeenCalledOnce();
  await user.click(
    within(sheet).getByRole('button', { name: 'More for this change' }),
  );
  await user.click(
    await screen.findByRole('menuitem', { name: 'Experiment details' }),
  );
  expect(actions.onDetails).toHaveBeenCalledOnce();
});

it('an edited change must be tested again before it can be used', () => {
  const actions = show({ ...change, edited: true });
  expect(screen.getByText('Edited: test again before using.')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Use this change' })).toBeNull();
  screen.getByRole('button', { name: 'Test again' }).click();
  expect(actions.onTestAgain).toHaveBeenCalledOnce();
});
