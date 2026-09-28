import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { TranscriptRow } from '../../api/types';
import { TranscriptMessage } from './TranscriptMessage';

const mock = vi.hoisted(() => ({
  messageText: vi.fn(),
  getSnapshot: () => ({ selectedConversationId: 'conversation-a' }),
}));
vi.mock('../../runtime', () => ({ useRuntime: () => ({ controller: mock }) }));

function encode(text: string) {
  return btoa(String.fromCharCode(...new TextEncoder().encode(text)));
}

it('shows a paged content slice exactly as sent, not as Markdown', async () => {
  // A page can cut through Markdown: an open fence, a table row, emphasis.
  const page = '| a | b |\n```ts\n**not bold** _x_ é🙂\n';
  mock.messageText.mockResolvedValue({
    data: encode(page),
    next_cursor: null,
  });
  const row = {
    id: 'row-1',
    message_id: 'message-1',
    role: 'assistant',
    blocks: [],
    content_status: 'lazy',
    content_ref: 'content-1',
  } as unknown as TranscriptRow;
  const { container } = render(
    <TranscriptMessage row={row} conversationId="conversation-a" />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Load message content' }));
  await waitFor(() =>
    expect(container.querySelector('.message-text')?.textContent).toBe(page),
  );
  const text = container.querySelector('.message-text')!;
  expect(text).toHaveAttribute('data-paged', 'true');
  expect(text.querySelector('table, pre, strong, em')).toBeNull();
});

it('shows a follow-up as a quiet note, not as the person’s message', () => {
  const row = {
    id: 'user:submission:note-1',
    message_id: 'note-1',
    role: 'user',
    note: 'continuation',
    blocks: [{ id: 'b', type: 'markdown', text: 'Goal · turn 2 of 10' }],
    tool_call_ids: [],
    tool_call_id: '',
  } as unknown as TranscriptRow;
  render(<TranscriptMessage row={row} conversationId="conversation-a" />);
  expect(screen.getByRole('note')).toHaveTextContent('Goal · turn 2 of 10');
  expect(screen.queryByRole('article')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Copy message' })).toBeNull();
});

it('keeps Read aloud and says what is missing without a voice (U28)', () => {
  vi.stubGlobal('speechSynthesis', undefined);
  const row = {
    id: 'assistant:1',
    message_id: 'm1',
    role: 'assistant',
    blocks: [{ id: 'b', type: 'markdown', text: 'Tides rise twice a day.' }],
    tool_call_ids: [],
    tool_call_id: '',
  } as unknown as TranscriptRow;
  render(<TranscriptMessage row={row} conversationId="conversation-a" />);
  fireEvent.click(screen.getByRole('button', { name: 'Read aloud' }));
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Read aloud needs a voice installed on this device.',
  );
  vi.unstubAllGlobals();
});
