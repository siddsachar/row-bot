import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ChatEmpty from './ChatEmpty';

const suggestions = () =>
  within(screen.getByRole('group', { name: 'Example prompts' }))
    .getAllByRole('button')
    .map((button) => button.getAttribute('aria-label'));

it('suggests work on the design in a chat working on one', () => {
  const onChoose = vi.fn();
  render(
    <ChatEmpty
      conversationId="design-chat"
      disabled={false}
      onChoose={onChoose}
      recent={[]}
      onOpen={vi.fn()}
      design
    />,
  );
  expect(suggestions()).toEqual([
    'Tighten the copy',
    'Try another layout',
    'Check the review',
    'Polish the look',
  ]);
  expect(screen.queryByText('Summarize documents')).toBeNull();
  expect(screen.queryByRole('button', { name: 'More ideas' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Check the review' }));
  expect(onChoose).toHaveBeenCalledWith(
    'Check the design review and fix what it finds',
  );
});

it('keeps the general suggestions, and two more on request, elsewhere', () => {
  render(
    <ChatEmpty
      conversationId="chat"
      disabled={false}
      onChoose={vi.fn()}
      recent={[]}
      onOpen={vi.fn()}
    />,
  );
  expect(suggestions()).toEqual([
    'Summarize documents',
    'Plan a weekly brief',
    'Design a landing page',
    'Recall my projects',
  ]);
  fireEvent.click(screen.getByRole('button', { name: 'More ideas' }));
  expect(suggestions()).toHaveLength(6);
});
