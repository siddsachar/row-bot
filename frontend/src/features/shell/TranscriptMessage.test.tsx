import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { TranscriptRow } from '../../api/types';
import { AgentAvatar } from '../../ui/AgentAvatar';
import { TranscriptMessage } from './TranscriptMessage';
import { TeachContext } from '../learning/chat-learning';
import { SpeakersContext, type Speakers } from './TurnMarker';

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

const reply = {
  id: 'assistant:2',
  message_id: 'm2',
  role: 'assistant',
  blocks: [{ id: 'b', type: 'markdown', text: 'Here is the plan.' }],
  tool_call_ids: [],
  tool_call_id: '',
} as unknown as TranscriptRow;
const ask = {
  ...reply,
  id: 'user:1',
  message_id: 'm1',
  role: 'user',
} as unknown as TranscriptRow;

it('says when the output limit cut a reply short', () => {
  const { rerender } = render(
    <TranscriptMessage
      row={{ ...reply, cut_short: true }}
      conversationId="conversation-a"
    />,
  );
  expect(screen.getByText('Here is the plan.')).toBeInTheDocument();
  expect(screen.getByText('Cut short by the output limit')).toHaveAttribute(
    'title',
    'Ask to continue, or for a shorter answer.',
  );
  rerender(<TranscriptMessage row={reply} conversationId="conversation-a" />);
  expect(screen.queryByText('Cut short by the output limit')).toBeNull();
});

function speaking(speakers: Speakers, row: TranscriptRow, turnStart: boolean) {
  return (
    <SpeakersContext.Provider value={speakers}>
      <TranscriptMessage
        row={row}
        conversationId="conversation-a"
        turnStart={turnStart}
      />
    </SpeakersContext.Provider>
  );
}

it('marks only a turn’s first row, decoratively, keeping the author in its name (B271)', () => {
  const speakers = { buddy: 'blob:buddy-a', agent: null };
  const { rerender } = render(speaking(speakers, reply, false));
  const message = screen.getByRole('article', { name: 'Row-Bot message' });
  expect(message.querySelector('.turn-marker')).toBeNull();
  rerender(speaking(speakers, reply, true));
  const lead = message.querySelector('.turn-lead')!;
  expect(lead).toHaveAttribute('aria-hidden', 'true');
  expect(lead.querySelector('img')).toHaveAttribute('src', 'blob:buddy-a');
  rerender(speaking(speakers, ask, true));
  const mine = screen.getByRole('article', { name: 'You message' });
  expect(mine.querySelector('.turn-marker-user svg')).not.toBeNull();
  expect(mine.querySelector('img')).toBeNull();
});

it('draws the selected Buddy’s still, following a change of pack (B271)', () => {
  const { rerender } = render(
    speaking({ buddy: 'blob:pack-a', agent: null }, reply, true),
  );
  const image = () =>
    screen
      .getByRole('article', { name: 'Row-Bot message' })
      .querySelector('.turn-marker img');
  expect(image()).toHaveAttribute('src', 'blob:pack-a');
  rerender(speaking({ buddy: 'blob:pack-b', agent: null }, reply, true));
  expect(image()).toHaveAttribute('src', 'blob:pack-b');
});

it('gives an agent’s replies its own icon in its conversation (B271)', () => {
  render(
    speaking(
      { buddy: 'blob:pack-a', agent: { seed: 'profile-7', name: 'Scan' } },
      reply,
      true,
    ),
  );
  const marker = screen
    .getByRole('article', { name: 'Row-Bot message' })
    .querySelector('.turn-marker')!;
  expect(marker.querySelector('img')).toBeNull();
  const expected = render(<AgentAvatar seed="profile-7" />)
    .container.querySelector('.agent-avatar')!
    .getAttribute('data-avatar');
  expect(
    marker.querySelector('.agent-avatar')?.getAttribute('data-avatar'),
  ).toBe(expected);
});

it('offers Teach Row-Bot after Copy, Read aloud and Retry while Learning uses the chat', () => {
  const teach = vi.fn();
  const row = {
    id: 'assistant:teach',
    message_id: 'reply-7',
    role: 'assistant',
    blocks: [{ id: 'b', type: 'markdown', text: 'It is the draft budget.' }],
    tool_call_ids: [],
    tool_call_id: '',
  } as unknown as TranscriptRow;
  render(
    <TeachContext.Provider value={teach}>
      <TranscriptMessage
        row={row}
        conversationId="conversation-a"
        onRetry={vi.fn()}
      />
    </TeachContext.Provider>,
  );
  const toolbar = screen.getByRole('toolbar', {
    name: 'Row-Bot message actions',
  });
  expect(
    within(toolbar)
      .getAllByRole('button')
      .map((button) => button.getAttribute('aria-label')),
  ).toEqual(['Copy message', 'Read aloud', 'Retry', 'Teach Row-Bot']);
  fireEvent.click(
    within(toolbar).getByRole('button', { name: 'Teach Row-Bot' }),
  );
  expect(teach).toHaveBeenCalledWith({ messageId: 'reply-7' });
});

it('has no Teach without Learning, nor on your own messages', () => {
  const reply = {
    id: 'assistant:plain',
    message_id: 'reply-8',
    role: 'assistant',
    blocks: [{ id: 'b', type: 'markdown', text: 'Done.' }],
    tool_call_ids: [],
    tool_call_id: '',
  } as unknown as TranscriptRow;
  const mine = {
    ...reply,
    id: 'user:plain',
    message_id: 'mine-1',
    role: 'user',
  } as unknown as TranscriptRow;
  const { rerender } = render(
    <TranscriptMessage row={reply} conversationId="conversation-a" />,
  );
  expect(screen.queryByRole('button', { name: 'Teach Row-Bot' })).toBeNull();
  rerender(
    <TeachContext.Provider value={vi.fn()}>
      <TranscriptMessage row={mine} conversationId="conversation-a" />
    </TeachContext.Provider>,
  );
  expect(screen.queryByRole('button', { name: 'Teach Row-Bot' })).toBeNull();
});
