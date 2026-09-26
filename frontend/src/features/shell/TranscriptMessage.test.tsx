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
