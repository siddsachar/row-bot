import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ConversationView } from '../../api/types';
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

it('lists recent chats without the never-used ones', () => {
  // Found live: three empty "New conversation" chats filled the list.
  const row = (id: string, title: string) =>
    ({ id, title, updated_at: '2026-10-10T07:00:00Z' }) as ConversationView;
  render(
    <ChatEmpty
      conversationId="chat"
      disabled={false}
      onChoose={vi.fn()}
      recent={[
        row('empty-1', 'New conversation'),
        row('tea', 'Your Favourite Tea Is Jasmine'),
        row('empty-2', 'New conversation'),
        row('desk', 'Three Tips for a Tidy Desk'),
      ]}
      onOpen={vi.fn()}
    />,
  );
  expect(
    within(screen.getByRole('navigation', { name: 'Recent conversations' }))
      .getAllByRole('link')
      .map(
        (link) => link.querySelector('.chat-empty-recent-title')?.textContent,
      ),
  ).toEqual(['Your Favourite Tea Is Jasmine', 'Three Tips for a Tidy Desk']);
});

it('leaves helpers’ own threads out of Recent, as the sidebar does (U12)', () => {
  const row = (id: string, title: string, parent: string | null = null) =>
    ({
      id,
      title,
      updated_at: '2026-10-10T07:00:00Z',
      parent_conversation_id: parent,
    }) as ConversationView;
  render(
    <ChatEmpty
      conversationId="chat"
      disabled={false}
      onChoose={vi.fn()}
      recent={[
        row('venus', 'Venus facts', 'planets'),
        row('planets', 'Planet research'),
        row('mars', 'Mars facts', 'planets'),
      ]}
      onOpen={vi.fn()}
    />,
  );
  expect(
    within(screen.getByRole('navigation', { name: 'Recent conversations' }))
      .getAllByRole('link')
      .map((link) => link.textContent),
  ).toEqual([expect.stringContaining('Planet research')]);
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

it('suggests one first message on a hosted Row-Bot, sent with one tap (U3)', () => {
  const onSend = vi.fn();
  const onChoose = vi.fn();
  render(
    <ChatEmpty
      conversationId="first-chat"
      disabled={false}
      onChoose={onChoose}
      recent={[]}
      onOpen={vi.fn()}
      first={{ prompt: 'What can you do for me?', onSend }}
    />,
  );
  expect(screen.queryByRole('group', { name: 'Example prompts' })).toBeNull();
  fireEvent.click(
    screen.getByRole('button', { name: 'What can you do for me?' }),
  );
  expect(onSend).toHaveBeenCalledWith('What can you do for me?');
  expect(onChoose).not.toHaveBeenCalled();
});
