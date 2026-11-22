import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { TranscriptRow } from '../../api/types';
import { AgentAvatar } from '../../ui/AgentAvatar';
import { TranscriptMessage } from './TranscriptMessage';
import { TeachContext } from '../learning/chat-learning';
import { SpeakersContext, type Speakers } from './TurnMarker';

const mock = vi.hoisted(() => ({
  messageText: vi.fn(),
  getSnapshot: () => ({ selectedConversationId: 'conversation-a' }),
  writeClipboard: vi.fn(async () => ({ status: 'ok' as const })),
  dictationCapability: vi.fn(async () => ({
    read_aloud_reason: 'available',
  })),
  readAloud: vi.fn(),
}));
const runtime = vi.hoisted(() => ({ controller: null as unknown }));
vi.mock('../../runtime', () => ({
  useRuntime: () => ({
    controller: runtime.controller ?? mock,
    platform: { writeClipboard: mock.writeClipboard },
  }),
}));

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

it('shows no empty words for a step that only used a tool', () => {
  const row = {
    id: 'assistant:checkpoint:step-3',
    message_id: 'step-3',
    role: 'assistant',
    blocks: [{ id: 'b', type: 'markdown', text: '' }],
  } as unknown as TranscriptRow;
  const { container } = render(
    <TranscriptMessage row={row} conversationId="conversation-a" />,
  );
  expect(container.querySelector('.message-text')).toBeNull();
  expect(screen.getByRole('article')).toHaveAttribute('data-empty', 'true');
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

it('shows what other chats left for a workflow run under its line, and why it didn’t finish', () => {
  const row = {
    id: 'user:submission:run-1',
    message_id: 'run-1',
    role: 'user',
    note: 'continuation',
    blocks: [
      { id: 'b', type: 'markdown', text: 'Scheduled run · 6 Nov, 08:31' },
    ],
    held: [
      {
        id: 'results-chat',
        title: '⚡ Growth | Daily results',
        text: 'Downloads rose 8% this week.',
      },
    ],
    run_failure: "The run didn't finish: The provider is busy.",
    tool_call_ids: [],
    tool_call_id: '',
  } as unknown as TranscriptRow;
  render(
    <MemoryRouter>
      <TranscriptMessage row={row} conversationId="conversation-a" />
    </MemoryRouter>,
  );
  const [line, held, failure] = screen.getAllByRole('note');
  expect(line).toHaveTextContent('Scheduled run · 6 Nov, 08:31');
  expect(held).toHaveTextContent(
    'From ⚡ Growth | Daily results' + 'Downloads rose 8% this week.',
  );
  expect(
    within(held).getByRole('link', { name: '⚡ Growth | Daily results' }),
  ).toHaveAttribute('href', '/conversations/results-chat');
  expect(failure).toHaveTextContent(
    "The run didn't finish" + 'The provider is busy.',
  );
  expect(screen.queryByRole('article')).toBeNull();
});

/** Audio the test ends by hand, standing in for the browser's player. */
class FakeAudio {
  static playing: FakeAudio[] = [];
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  paused = true;
  currentTime = 0;
  constructor(public src: string) {}
  play() {
    this.paused = false;
    FakeAudio.playing.push(this);
    return Promise.resolve();
  }
  pause() {
    this.paused = true;
  }
  end() {
    this.paused = true;
    this.onended?.();
  }
}

function speech(part: number, parts: number) {
  return { audio: new Blob(['RIFF'], { type: 'audio/wav' }), part, parts };
}

const tides = {
  id: 'assistant:1',
  message_id: 'm1',
  role: 'assistant',
  blocks: [{ id: 'b', type: 'markdown', text: 'Tides rise twice a day.' }],
  tool_call_ids: [],
  tool_call_id: '',
} as unknown as TranscriptRow;

function withAudio(run: () => Promise<void>) {
  return async () => {
    FakeAudio.playing = [];
    vi.stubGlobal('Audio', FakeAudio);
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: () => 'blob:part',
      revokeObjectURL: () => {},
    });
    try {
      await run();
    } finally {
      vi.unstubAllGlobals();
      mock.readAloud.mockReset();
    }
  };
}

it(
  'reads a reply aloud with the chosen voice, part after part, then offers it again',
  withAudio(async () => {
    mock.readAloud.mockImplementation(
      async (_c: string, _m: string, part: number) => speech(part, 2),
    );
    render(<TranscriptMessage row={tides} conversationId="conversation-a" />);
    fireEvent.click(screen.getByRole('button', { name: 'Read aloud' }));
    // The server reads the reply's words: the browser names the message, never the text.
    await waitFor(() => expect(FakeAudio.playing).toHaveLength(1));
    expect(mock.readAloud.mock.calls.map((call) => call.slice(0, 3))).toEqual([
      ['conversation-a', 'm1', 0],
      ['conversation-a', 'm1', 1],
    ]);
    const stop = screen.getByRole('button', { name: 'Stop reading' });
    expect(stop).toHaveAttribute('aria-pressed', 'true');
    FakeAudio.playing[0].end();
    await waitFor(() => expect(FakeAudio.playing).toHaveLength(2));
    FakeAudio.playing[1].end();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Read aloud' })).toBeVisible(),
    );
    expect(mock.readAloud).toHaveBeenCalledTimes(2);
  }),
);

it(
  'stops reading at once and asks for no more of the reply',
  withAudio(async () => {
    mock.readAloud.mockImplementation(
      async (_c: string, _m: string, part: number) => speech(part, 5),
    );
    render(<TranscriptMessage row={tides} conversationId="conversation-a" />);
    fireEvent.click(screen.getByRole('button', { name: 'Read aloud' }));
    await waitFor(() => expect(FakeAudio.playing).toHaveLength(1));
    fireEvent.click(screen.getByRole('button', { name: 'Stop reading' }));
    expect(FakeAudio.playing[0].paused).toBe(true);
    expect(screen.getByRole('button', { name: 'Read aloud' })).toBeVisible();
    await Promise.resolve();
    expect(mock.readAloud).toHaveBeenCalledTimes(2);
    expect(FakeAudio.playing).toHaveLength(1);
    expect(screen.queryByRole('alert')).toBeNull();
  }),
);

it(
  'says Read aloud needs the Kokoro voice and opens its setting, never using the device voice (U28)',
  withAudio(async () => {
    const synthesis = { speak: vi.fn(), getVoices: () => [] };
    vi.stubGlobal('speechSynthesis', synthesis);
    // A new connection: its first check says the voice isn't installed.
    const readAloud = vi
      .fn()
      .mockRejectedValue({ code: 'kokoro_model_missing' });
    runtime.controller = {
      ...mock,
      dictationCapability: async () => ({
        read_aloud_reason: 'kokoro_model_missing',
      }),
      readAloud,
    };
    render(
      <MemoryRouter>
        <TranscriptMessage row={tides} conversationId="conversation-a" />
      </MemoryRouter>,
    );
    const button = screen.getByRole('button', { name: 'Read aloud' });
    await waitFor(() =>
      expect(button).toHaveAttribute('data-unavailable', 'true'),
    );
    expect(button).toHaveAttribute(
      'title',
      'Read aloud needs the Kokoro voice',
    );
    fireEvent.click(button);
    // A setup step, not an error.
    const alert = await screen.findByRole('status');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(alert).toHaveTextContent(
      'Read aloud needs the Kokoro voice. Install it in Voice settings.',
    );
    expect(
      within(alert).getByRole('button', { name: 'Open Voice settings' }),
    ).toBeVisible();
    expect(readAloud).toHaveBeenCalledTimes(1);
    expect(synthesis.speak).not.toHaveBeenCalled();
    expect(FakeAudio.playing).toHaveLength(0);
    runtime.controller = null;
  }),
);

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

it('says quietly when a reply used a lesson in use (Learning)', () => {
  const { rerender } = render(
    <TranscriptMessage
      row={{ ...reply, used_lesson: true }}
      conversationId="conversation-a"
    />,
  );
  expect(screen.getByText('Used a lesson')).toBeVisible();
  rerender(<TranscriptMessage row={reply} conversationId="conversation-a" />);
  expect(screen.queryByText('Used a lesson')).toBeNull();
});

it('says in one quiet line when the backup model took over a turn (#140)', () => {
  const handover = {
    ...ask,
    id: 'user:handover',
    message_id: 'handover',
    note: 'backup_model',
    blocks: [
      {
        id: 'b',
        type: 'markdown',
        text: "qwen3:8b didn't answer (it couldn't be reached), so GPT-5 mini took over.",
      },
    ],
  } as unknown as TranscriptRow;
  render(<TranscriptMessage row={handover} conversationId="conversation-a" />);
  // Never the person's bubble or the hand-over written for the model.
  expect(screen.getByRole('note')).toHaveTextContent(
    "qwen3:8b didn't answer (it couldn't be reached), so GPT-5 mini took over.",
  );
  expect(screen.queryByRole('article')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Copy message' })).toBeNull();
});

it('shows a watch that ran out as a quiet line, not a reply (B4)', () => {
  const line = {
    ...reply,
    id: 'assistant:checkpoint:ran-out',
    message_id: 'ran-out',
    note: 'watch',
    blocks: [
      {
        id: 'b',
        type: 'markdown',
        text: 'Stopped watching “the deploy”: its 24 hours ran out.',
      },
    ],
  } as unknown as TranscriptRow;
  render(<TranscriptMessage row={line} conversationId="conversation-a" />);
  expect(screen.getByRole('note')).toHaveTextContent(
    'Stopped watching “the deploy”: its 24 hours ran out.',
  );
  expect(screen.queryByRole('article')).toBeNull();
});

it('says quietly which replies the personal model gave, and when the main model finished one (Learning)', () => {
  const { rerender } = render(
    <TranscriptMessage
      row={{ ...reply, answered_by: 'personal_model' }}
      conversationId="conversation-a"
    />,
  );
  expect(screen.getByText('Here is the plan.')).toBeInTheDocument();
  expect(screen.getByText('Answered by your personal model')).toBeVisible();
  rerender(<TranscriptMessage row={reply} conversationId="conversation-a" />);
  expect(screen.queryByText('Answered by your personal model')).toBeNull();

  // The hand-over is never shown as the person's message or the model's words.
  const fallback = {
    ...ask,
    id: 'user:handover',
    message_id: 'handover',
    note: 'personal_model_fallback',
    blocks: [
      {
        id: 'b',
        type: 'markdown',
        text: 'Your personal model got stuck; GPT-6.1 Sol finished this.',
      },
    ],
  } as unknown as TranscriptRow;
  rerender(
    <TranscriptMessage row={fallback} conversationId="conversation-a" />,
  );
  expect(screen.getByRole('note')).toHaveTextContent(
    'Your personal model got stuck; GPT-6.1 Sol finished this.',
  );
  expect(screen.queryByRole('article')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Copy message' })).toBeNull();
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

it('opens a message’s actions on a long-press, and Select text makes its text selectable (D10)', async () => {
  const onEditResend = vi.fn();
  const row = {
    id: 'user:message-9',
    message_id: 'message-9',
    role: 'user',
    blocks: [{ id: 'b', type: 'markdown', text: 'Book the hall for Friday' }],
  } as unknown as TranscriptRow;
  render(
    <TranscriptMessage
      row={row}
      conversationId="conversation-a"
      onEditResend={onEditResend}
    />,
  );
  const article = screen.getByRole('article');
  const press = new Event('touchstart', { bubbles: true });
  Object.defineProperty(press, 'touches', {
    value: [{ clientX: 40, clientY: 40 }],
  });
  article.dispatchEvent(press);

  const menu = await screen.findByRole('menu', {}, { timeout: 2000 });
  expect(
    within(menu)
      .getAllByRole('menuitem')
      .map((item) => item.textContent),
  ).toEqual(['Copy message', 'Select text', 'Edit and resend']);
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Select text' }));
  await waitFor(() =>
    expect(article).toHaveAttribute('data-selecting', 'true'),
  );
  await waitFor(() =>
    expect(window.getSelection()?.toString()).toBe('Book the hall for Friday'),
  );
});

it('a phone’s older reply has no icon row; a right-click or the long-press opens its actions (D14)', async () => {
  const row = {
    id: 'assistant:older',
    message_id: 'reply-older',
    role: 'assistant',
    blocks: [{ id: 'b', type: 'markdown', text: 'The deck is ready.' }],
    tool_call_ids: [],
    tool_call_id: '',
  } as unknown as TranscriptRow;
  const onFork = vi.fn(async () => {});
  const { rerender } = render(
    <TranscriptMessage
      row={row}
      conversationId="conversation-a"
      onFork={onFork}
      pressOnly
    />,
  );
  expect(screen.queryByRole('toolbar')).toBeNull();
  const article = screen.getByRole('article', { name: 'Row-Bot message' });
  fireEvent.contextMenu(article);
  const menu = await screen.findByRole('menu');
  expect(
    within(menu)
      .getAllByRole('menuitem')
      .map((item) => item.textContent),
  ).toEqual(['Copy message', 'Select text', 'Read aloud', 'Fork from here']);
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Copy message' }));
  await waitFor(() =>
    expect(mock.writeClipboard).toHaveBeenCalledWith('The deck is ready.'),
  );

  // The newest reply keeps its icons, and the browser keeps its own menu.
  rerender(
    <TranscriptMessage row={row} conversationId="conversation-a" latest />,
  );
  await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
  expect(
    screen.getByRole('toolbar', { name: 'Row-Bot message actions' }),
  ).toBeVisible();
  expect(fireEvent.contextMenu(article)).toBe(true);
  expect(screen.queryByRole('menu')).toBeNull();
});

const mineToEdit = {
  id: 'user:mine-2',
  message_id: 'mine-2',
  role: 'user',
  blocks: [{ id: 'b', type: 'markdown', text: 'Book the hall for Friday' }],
} as unknown as TranscriptRow;

it('Edit and resend turns the message into its field; Escape puts it back unchanged (B5)', async () => {
  const onEditResend = vi.fn();
  render(
    <TranscriptMessage
      row={mineToEdit}
      conversationId="conversation-a"
      onEditResend={onEditResend}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Edit and resend' }));
  const field = screen.getByRole('textbox', { name: 'Edit your message' });
  expect(field).toHaveValue('Book the hall for Friday');
  expect(field).toHaveFocus();
  expect(
    screen.getByText('Sends in a new conversation. This one stays as it is.'),
  ).toBeInTheDocument();
  // The message's own actions wait until the edit ends.
  expect(screen.queryByRole('button', { name: 'Copy message' })).toBeNull();

  fireEvent.keyDown(field, { key: 'Escape' });

  expect(screen.queryByRole('textbox')).toBeNull();
  expect(screen.getByText('Book the hall for Friday')).toBeInTheDocument();
  expect(onEditResend).not.toHaveBeenCalled();
});

it('Enter sends the edited text once, with a spinner until the fork answers (B5)', async () => {
  let finish!: () => void;
  const onEditResend = vi.fn(
    () => new Promise<void>((done) => (finish = done)),
  );
  render(
    <TranscriptMessage
      row={mineToEdit}
      conversationId="conversation-a"
      onEditResend={onEditResend}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Edit and resend' }));
  const field = screen.getByRole('textbox', { name: 'Edit your message' });
  fireEvent.change(field, { target: { value: 'Book the hall for Saturday' } });
  fireEvent.keyDown(field, { key: 'Enter' });
  fireEvent.keyDown(field, { key: 'Enter' });

  expect(onEditResend).toHaveBeenCalledTimes(1);
  expect(onEditResend).toHaveBeenCalledWith(
    'mine-2',
    'Book the hall for Saturday',
  );
  expect(
    screen.getByRole('button', { name: 'Send in a new conversation' }),
  ).toBeDisabled();
  finish();
  await waitFor(() => expect(screen.queryByRole('textbox')).toBeNull());
});

it('a refused edit stays open and says why (B5)', async () => {
  const onEditResend = vi.fn(async () => {
    throw { code: 'generation_active' };
  });
  render(
    <TranscriptMessage
      row={mineToEdit}
      conversationId="conversation-a"
      onEditResend={onEditResend}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Edit and resend' }));
  fireEvent.click(
    screen.getByRole('button', { name: 'Send in a new conversation' }),
  );

  expect(await screen.findByRole('alert')).toHaveTextContent(
    "Couldn't fork: the conversation is busy. Try when the reply ends.",
  );
  expect(
    screen.getByRole('textbox', { name: 'Edit your message' }),
  ).toHaveValue('Book the hall for Friday');
});

it('Fork from here sits in a reply’s actions and its long-press menu, only when offered (B5)', async () => {
  const onFork = vi.fn(async () => undefined);
  const reply = {
    id: 'assistant:reply-4',
    message_id: 'reply-4',
    role: 'assistant',
    blocks: [
      { id: 'b', type: 'markdown', text: 'The hall is free on Friday.' },
    ],
  } as unknown as TranscriptRow;
  const { rerender } = render(
    <TranscriptMessage row={reply} conversationId="conversation-a" />,
  );
  expect(screen.queryByRole('button', { name: 'Fork from here' })).toBeNull();

  rerender(
    <TranscriptMessage
      row={reply}
      conversationId="conversation-a"
      onFork={onFork}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Fork from here' }));
  await waitFor(() => expect(onFork).toHaveBeenCalledWith('reply-4'));

  const article = screen.getByRole('article');
  const press = new Event('touchstart', { bubbles: true });
  Object.defineProperty(press, 'touches', {
    value: [{ clientX: 40, clientY: 40 }],
  });
  article.dispatchEvent(press);
  const menu = await screen.findByRole('menu', {}, { timeout: 2000 });
  expect(
    within(menu).getByRole('menuitem', { name: 'Fork from here' }),
  ).toBeInTheDocument();
  // A person's message is edited, never forked after; a reply is never edited.
  expect(
    within(menu).queryByRole('menuitem', { name: 'Edit and resend' }),
  ).toBeNull();
});
