import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type {
  CommandReceipt,
  ConversationWorkspace,
  ModelChoice,
  SearchPage,
  Snapshot,
  TranscriptPage,
} from '../../api/types';
import { commandReceipts } from './command-receipts';
import ConversationView, { Media } from './Conversation';
import useNewChat from './useNewChat';
import SearchConversations from './SearchConversations';

const mock = vi.hoisted(() => ({
  state: {
    selectedConversationId: null as string | null,
    conversation: null as {
      id: string;
      title: string;
      revision: string;
      pinned: boolean;
    } | null,
    projection: null as Snapshot | null,
    workspace: null as ConversationWorkspace | null,
    history: null as TranscriptPage | null,
    historyFocus: null,
    status: 'ready',
    handshake: { instance_id: '', models: [] as ModelChoice[] },
    activity: [],
    loadingConversation: false,
    search: null as SearchPage | null,
    searching: false,
    draftStatus: 'saved',
  },
  version: 0,
  routeKey: 'conversation-route',
  navigate: vi.fn(),
  intent: vi.fn(),
  receipt: vi.fn(),
  showHistory: vi.fn(),
  showLatest: vi.fn(),
  selectConversation: vi.fn(),
  searchLibrary: vi.fn(),
  close: vi.fn(),
  open: vi.fn(),
  download: vi.fn(),
  drafts: new Map<string, { text: string; attachments: [] }>(),
  setDraft: vi.fn(),
}));
vi.mock('react-router-dom', () => ({
  useNavigate: () => mock.navigate,
  useLocation: () => ({ key: mock.routeKey }),
}));
vi.mock('../../runtime', () => {
  const runtime = {
    controller: {
      getSnapshot: () => mock.state,
      getSelectionVersion: () => mock.version,
      getDraft: (id: string) =>
        mock.drafts.get(id) ?? { text: '', attachments: [] },
      setDraft: mock.setDraft,
      intent: mock.intent,
      receipt: mock.receipt,
      showHistory: mock.showHistory,
      showLatest: mock.showLatest,
      selectConversation: mock.selectConversation,
      searchLibrary: mock.searchLibrary,
      download: mock.download,
      delegatedActivity: async (conversationId: string) => ({
        conversation_id: conversationId,
        parent_conversation_id: null,
        items: [],
        next_cursor: null,
        has_more: false,
      }),
      delegatedRun: async () => {
        throw new Error('No delegated run in this fixture');
      },
    },
    platform: {},
  };
  return {
    useClientState: () => mock.state,
    useClientSelector: <T,>(selector: (state: typeof mock.state) => T) =>
      selector(mock.state),
    useRuntime: () => runtime,
  };
});
vi.mock('../../ui/overlays', () => ({
  useOverlay: () => ({
    close: mock.close,
    open: mock.open,
    dismiss: vi.fn(),
    notify: vi.fn(),
  }),
}));
beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  mock.version = 0;
  mock.routeKey = 'conversation-route';
  mock.state.selectedConversationId = null;
  mock.state.conversation = null;
  mock.state.projection = null;
  mock.state.workspace = null;
  mock.state.history = null;
  mock.state.loadingConversation = false;
  mock.drafts.clear();
  mock.setDraft.mockImplementation((id, draft) => mock.drafts.set(id, draft));
  mock.state.search = null;
  mock.state.handshake.instance_id = crypto.randomUUID();
  mock.state.handshake.models = [];
  mock.selectConversation.mockImplementation(async (id: string) => {
    mock.version++;
    mock.state.selectedConversationId = id;
    mock.state.conversation = { id, title: id, revision: '1', pinned: false };
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
function Conversation(props: Parameters<typeof ConversationView>[0]) {
  const owner = useNewChat();
  return (
    <>
      <button
        onClick={() => void owner.newChat()}
        disabled={owner.creatingChat}
      >
        {owner.pending ? 'Check new chat' : 'New chat'}
      </button>
      {owner.error && <p role="alert">{owner.error}</p>}
      {owner.canReview && (
        <button onClick={owner.reviewMissingReceipt}>
          Review pending receipt
        </button>
      )}
      <ConversationView
        {...props}
        focusConversationId={owner.focusConversationId}
        onComposerFocused={owner.onComposerFocused}
      />
    </>
  );
}
function conversation() {
  return render(<Conversation onPanel={vi.fn()} />);
}

it('keeps Browse history disabled until the selected conversation finishes opening', async () => {
  mock.state.selectedConversationId = 'conversation-b';
  mock.state.loadingConversation = true;
  mock.showHistory.mockResolvedValue(undefined);
  let rendered!: ReturnType<typeof conversation>;
  await act(async () => {
    rendered = conversation();
  });
  const browse = screen.getByRole('button', { name: 'Browse history' });
  expect(browse).toBeDisabled();
  fireEvent.click(browse);
  expect(mock.showHistory).not.toHaveBeenCalled();

  mock.state.conversation = {
    id: 'conversation-b',
    title: 'B',
    revision: '1',
    pinned: false,
  };
  rendered.rerender(<Conversation onPanel={vi.fn()} />);
  expect(browse).toBeDisabled();
  fireEvent.click(browse);
  expect(mock.showHistory).not.toHaveBeenCalled();

  mock.state.loadingConversation = false;
  rendered.rerender(<Conversation onPanel={vi.fn()} />);
  expect(browse).toBeEnabled();
  await act(async () => {
    fireEvent.click(browse);
  });
  expect(mock.showHistory).toHaveBeenCalledExactlyOnceWith();
});

it('fences history navigation for a missing or mismatched loaded conversation and during loading', async () => {
  mock.state.selectedConversationId = 'conversation-b';
  mock.state.history = {
    rows: [],
    previous_cursor: 'before',
    next_cursor: 'after',
  } as unknown as TranscriptPage;
  mock.showHistory.mockResolvedValue(undefined);
  let rendered!: ReturnType<typeof conversation>;
  await act(async () => {
    rendered = conversation();
  });
  const controls = ['Browse history', 'Earlier messages', 'Later messages'].map(
    (name) => screen.getByRole('button', { name }),
  );
  const expectDisabled = () => {
    for (const button of controls) {
      expect(button).toBeDisabled();
      fireEvent.click(button);
    }
    expect(mock.showHistory).not.toHaveBeenCalled();
  };
  expectDisabled();
  mock.state.conversation = {
    id: 'conversation-a',
    title: 'A',
    revision: '1',
    pinned: false,
  };
  rendered.rerender(<Conversation onPanel={vi.fn()} />);
  expectDisabled();
  mock.state.conversation.id = 'conversation-b';
  mock.state.loadingConversation = true;
  rendered.rerender(<Conversation onPanel={vi.fn()} />);
  expectDisabled();
  mock.state.loadingConversation = false;
  rendered.rerender(<Conversation onPanel={vi.fn()} />);
  for (const button of controls) expect(button).toBeEnabled();
  await act(async () => {
    fireEvent.click(controls[1]);
    fireEvent.click(controls[2]);
  });
  expect(mock.showHistory.mock.calls).toEqual([
    [undefined, 'before'],
    [undefined, 'after'],
  ]);
});

function activeConversation(id = 'conversation-a') {
  mock.state.selectedConversationId = id;
  mock.state.conversation = { id, title: id, revision: '1', pinned: false };
  mock.state.projection = {
    rows: [],
    generation: {
      generation_id: 'run-a',
      quiesced: false,
      can_stop: true,
      status: 'running',
    },
  } as unknown as Snapshot;
  mock.drafts.set(id, { text: 'Original queued draft', attachments: [] });
  return commandReceipts.scope(mock.state.handshake.instance_id, id);
}

function transcriptGeometry(initialHeight = 900) {
  let height = initialHeight;
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(
    function (this: HTMLElement) {
      return this.classList.contains('transcript') ? height : 0;
    },
  );
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(
    function (this: HTMLElement) {
      return this.classList.contains('transcript') ? 100 : 0;
    },
  );
  return (next: number) => {
    height = next;
  };
}

it('follows live rows until the reader scrolls away and resumes only on explicit Latest', async () => {
  activeConversation();
  const height = transcriptGeometry();
  let view!: ReturnType<typeof conversation>;
  await act(async () => {
    view = conversation();
  });
  const log = screen.getByRole('log');
  expect(log.scrollTop).toBe(800);
  height(1100);
  mock.state.projection = { ...mock.state.projection!, rows: [] };
  view.rerender(<Conversation onPanel={vi.fn()} />);
  expect(log.scrollTop).toBe(1000);

  log.scrollTop = 250;
  fireEvent.scroll(log);
  height(1400);
  mock.state.projection = { ...mock.state.projection!, rows: [] };
  view.rerender(<Conversation onPanel={vi.fn()} />);
  expect(log.scrollTop).toBe(250);
  fireEvent.click(screen.getByRole('button', { name: 'Latest messages' }));
  expect(log.scrollTop).toBe(1300);
  expect(screen.queryByRole('button', { name: 'Latest messages' })).toBeNull();
  expect(mock.drafts.get('conversation-a')?.text).toBe('Original queued draft');
  expect(mock.showLatest).not.toHaveBeenCalled();
});

it('preserves an older history position and follows the newest window after leaving history or switching conversation', async () => {
  activeConversation();
  transcriptGeometry();
  let view!: ReturnType<typeof conversation>;
  await act(async () => {
    view = conversation();
  });
  const log = screen.getByRole('log');
  log.scrollTop = 200;
  mock.state.history = { rows: [] } as unknown as TranscriptPage;
  view.rerender(<Conversation onPanel={vi.fn()} />);
  fireEvent.scroll(log);
  mock.state.projection = { ...mock.state.projection!, rows: [] };
  view.rerender(<Conversation onPanel={vi.fn()} />);
  expect(log.scrollTop).toBe(200);
  fireEvent.click(screen.getByRole('button', { name: 'Latest messages' }));
  expect(mock.showLatest).toHaveBeenCalledOnce();
  mock.state.history = null;
  view.rerender(<Conversation onPanel={vi.fn()} />);
  expect(log.scrollTop).toBe(800);
  log.scrollTop = 200;
  fireEvent.scroll(log);
  activeConversation('conversation-b');
  await act(async () => {
    view.rerender(<Conversation onPanel={vi.fn()} />);
  });
  expect(log.scrollTop).toBe(800);
  expect(screen.queryByRole('button', { name: 'Latest messages' })).toBeNull();
});

it('follows media and pane size changes without moving older readers or accepting disposed observers', async () => {
  const observers: {
    callback: () => void;
    disconnect: ReturnType<typeof vi.fn>;
  }[] = [];
  vi.stubGlobal(
    'ResizeObserver',
    class {
      disconnect = vi.fn();
      observe = vi.fn();
      constructor(callback: () => void) {
        observers.push({ callback, disconnect: this.disconnect });
      }
    },
  );
  activeConversation();
  const height = transcriptGeometry();
  let view!: ReturnType<typeof conversation>;
  await act(async () => {
    view = conversation();
  });
  const log = screen.getByRole('log');
  const first = observers.at(-1)!;
  height(1200);
  act(() => first.callback());
  expect(log.scrollTop).toBe(1100);
  log.scrollTop = 300;
  fireEvent.scroll(log);
  height(1600);
  act(() => first.callback());
  expect(log.scrollTop).toBe(300);
  activeConversation('conversation-b');
  await act(async () => {
    view.rerender(<Conversation onPanel={vi.fn()} />);
  });
  expect(first.disconnect).toHaveBeenCalledOnce();
  log.scrollTop = 123;
  act(() => first.callback());
  expect(log.scrollTop).toBe(123);
  const latest = observers.at(-1)!;
  view.unmount();
  act(() => latest.callback());
  expect(latest.disconnect).toHaveBeenCalledOnce();
  expect(log.scrollTop).toBe(123);
});

function idleConversation() {
  activeConversation();
  mock.state.projection = null;
  mock.state.workspace = {
    conversation_id: 'conversation-a',
    revision: '1',
    controls: {
      model_selection: { provider_id: 'fixture', model_ref: 'fixture/model' },
      runtime_mode: 'chat_only',
      profile_id: '',
      approval_mode: 'approve',
    },
    resources: [],
    profiles: [],
    actions: [{ action: 'send', ready: true }],
  } as unknown as ConversationWorkspace;
  return commandReceipts.scope(
    mock.state.handshake.instance_id,
    'conversation-a',
    'submit',
  );
}

function interruptedConversation() {
  idleConversation();
  mock.state.projection = {
    rows: [],
    generation: {
      generation_id: 'interrupted-run',
      quiesced: true,
      can_stop: false,
      status: 'interrupted',
    },
  } as unknown as Snapshot;
  return commandReceipts.scope(
    mock.state.handshake.instance_id,
    'conversation-a',
    'resume',
  );
}

it('changes the model through the compact composer menu and preserves the current draft', async () => {
  idleConversation();
  mock.state.handshake.models = [
    {
      provider_id: 'fixture',
      model_ref: 'fixture/model',
      label: 'Current model',
      available: true,
    },
    {
      provider_id: 'other',
      model_ref: 'other::chosen',
      label: 'Chosen model',
      available: true,
    },
  ];
  mock.intent.mockResolvedValue({ status: 'completed' });
  mock.drafts.set('conversation-a', { text: 'Keep my draft', attachments: [] });
  await act(async () => conversation());
  expect(screen.queryByRole('combobox', { name: 'Model' })).toBeNull();
  await act(async () =>
    fireEvent.keyDown(screen.getByRole('button', { name: 'Model' }), {
      key: 'Enter',
    }),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('menuitem', { name: 'Chosen model' })),
  );
  expect(mock.intent).toHaveBeenCalledWith(
    'conversation-a',
    'conversation.controls',
    {
      model_selection: { provider_id: 'other', model_ref: 'other::chosen' },
      runtime_mode: 'chat_only',
      profile_id: '',
      approval_mode: 'approve',
    },
    '1',
  );
  expect(mock.drafts.get('conversation-a')?.text).toBe('Keep my draft');
});

it('shows a failed compact approval save in the conversation without changing its draft', async () => {
  idleConversation();
  mock.intent.mockRejectedValue({ code: 'revision_conflict' });
  mock.drafts.set('conversation-a', { text: 'Keep my draft', attachments: [] });
  await act(async () => conversation());
  await act(async () =>
    fireEvent.keyDown(screen.getByRole('button', { name: 'Approvals' }), {
      key: 'Enter',
    }),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('menuitem', { name: 'Block' })),
  );
  expect(mock.intent.mock.calls[0][2].approval_mode).toBe('block');
  expect(screen.getByRole('alert')).toHaveTextContent(/review|changed|retry/i);
  expect(mock.drafts.get('conversation-a')?.text).toBe('Keep my draft');
  expect(screen.getByRole('button', { name: 'Approvals' })).toHaveTextContent(
    'Ask',
  );
});

it('reserves Resume before dispatch and recovers a lost accepted response after reload without another admission', async () => {
  const key = interruptedConversation();
  mock.intent.mockImplementation((_id, _type, _payload, _revision, command) => {
    expect(commandReceipts.read(key)).toEqual({
      commandId: command,
      steeringId: null,
    });
    throw new TypeError('lost accepted response');
  });
  let rendered!: ReturnType<typeof conversation>;
  await act(async () => {
    rendered = conversation();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
  });
  const saved = commandReceipts.read(key)!;
  expect(mock.intent).toHaveBeenCalledExactlyOnceWith(
    'conversation-a',
    'conversation.resume',
    { model_selection: { provider_id: 'fixture', model_ref: 'fixture/model' } },
    '1',
    saved.commandId,
  );
  expect(screen.getByRole('button', { name: 'Resume' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  rendered.unmount();
  mock.state.projection = null;
  mock.drafts.set('conversation-a', {
    text: 'Keep the edited unsent draft',
    attachments: [],
  });
  mock.receipt.mockResolvedValue({
    command_id: saved.commandId,
    conversation_id: 'conversation-a',
    status: 'accepted',
  });
  await act(async () => {
    conversation();
  });
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', { name: 'Check resume receipt' }),
    );
  });
  expect(mock.receipt).toHaveBeenCalledWith(saved.commandId);
  expect(mock.intent).toHaveBeenCalledTimes(1);
  expect(commandReceipts.read(key)).toBeNull();
  expect(mock.setDraft).not.toHaveBeenCalled();
  expect(mock.drafts.get('conversation-a')?.text).toBe(
    'Keep the edited unsent draft',
  );
});

it.each(['throw', 'discard'] as const)(
  'does not Resume when identity storage writes %s',
  async (mode) => {
    interruptedConversation();
    await act(async () => {
      conversation();
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      if (mode === 'throw') throw new Error('private');
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
    });
    expect(mock.intent).not.toHaveBeenCalled();
    expect(mock.setDraft).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Receipt storage is unavailable',
    );
  },
);

it('retains a missing Resume receipt until explicit review without replaying it', async () => {
  const key = interruptedConversation(),
    saved = { commandId: crypto.randomUUID(), steeringId: null };
  commandReceipts.reserve(key, saved);
  mock.receipt.mockRejectedValue({ code: 'not_found' });
  await act(async () => {
    conversation();
  });
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', { name: 'Check resume receipt' }),
    );
  });
  expect(commandReceipts.read(key)).toEqual(saved);
  expect(mock.intent).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Review pending receipt' }),
  );
  await act(async () => {
    mock.open.mock.calls.at(-1)?.[0].onConfirm();
  });
  expect(commandReceipts.read(key)).toBeNull();
  expect(mock.intent).not.toHaveBeenCalled();
  expect(mock.setDraft).not.toHaveBeenCalled();
});

it('coalesces Resume clicks and fences its late success after A to B to C navigation', async () => {
  const key = interruptedConversation();
  let finish!: (receipt: CommandReceipt) => void;
  mock.intent.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  let rendered!: ReturnType<typeof conversation>;
  await act(async () => {
    rendered = conversation();
  });
  await act(async () => {
    const button = screen.getByRole('button', { name: 'Resume' });
    fireEvent.click(button);
    fireEvent.click(button);
  });
  const saved = commandReceipts.read(key)!;
  expect(mock.intent).toHaveBeenCalledTimes(1);
  await mock.selectConversation('conversation-b');
  await mock.selectConversation('conversation-c');
  mock.drafts.set('conversation-c', { text: 'C draft', attachments: [] });
  await act(async () => {
    rendered.rerender(<Conversation onPanel={vi.fn()} />);
  });
  await act(async () => {
    finish({
      command_id: saved.commandId,
      conversation_id: 'conversation-a',
      status: 'accepted',
    });
  });
  expect(commandReceipts.read(key)).toBeNull();
  expect(mock.setDraft).not.toHaveBeenCalled();
  expect(mock.showLatest).not.toHaveBeenCalled();
  expect(mock.navigate).not.toHaveBeenCalled();
  expect(
    screen.queryByRole('button', { name: 'Check resume receipt' }),
  ).not.toBeInTheDocument();
  expect(mock.drafts.get('conversation-c')?.text).toBe('C draft');
});

it('keeps pending Resume on a foreign receipt and blocks Queue while unresolved', async () => {
  const key = interruptedConversation(),
    saved = { commandId: crypto.randomUUID(), steeringId: null };
  commandReceipts.reserve(key, saved);
  activeConversation();
  mock.receipt.mockResolvedValue({
    command_id: saved.commandId,
    conversation_id: 'conversation-b',
    status: 'accepted',
  });
  await act(async () => {
    conversation();
  });
  expect(screen.getByRole('button', { name: 'Queue message' })).toBeDisabled();
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', { name: 'Check resume receipt' }),
    );
  });
  expect(commandReceipts.read(key)).toEqual(saved);
  expect(mock.intent).not.toHaveBeenCalled();
  expect(mock.setDraft).not.toHaveBeenCalled();
});

it('blocks fresh Resume while an ordinary submission receipt remains unresolved', async () => {
  interruptedConversation();
  commandReceipts.reserve(
    commandReceipts.scope(
      mock.state.handshake.instance_id,
      'conversation-a',
      'submit',
    ),
    { commandId: crypto.randomUUID(), steeringId: crypto.randomUUID() },
  );
  await act(async () => {
    conversation();
  });
  expect(screen.getByRole('button', { name: 'Resume' })).toBeDisabled();
  expect(mock.intent).not.toHaveBeenCalled();
});

it('persists ordinary submit identity before dispatch and recovers a lost response after reload without sending again', async () => {
  const key = idleConversation();
  mock.intent.mockImplementation((_id, _type, payload, _revision, command) => {
    expect(commandReceipts.read(key)).toEqual({
      commandId: command,
      steeringId: payload.submission_id,
    });
    throw new TypeError('lost accepted response');
  });
  let rendered!: ReturnType<typeof conversation>;
  await act(async () => {
    rendered = conversation();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  });
  const saved = commandReceipts.read(key)!;
  expect(mock.intent).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  rendered.unmount();
  mock.drafts.set('conversation-a', {
    text: 'Edited current draft',
    attachments: [],
  });
  mock.receipt.mockResolvedValue({
    command_id: saved.commandId,
    submission_id: saved.steeringId,
    conversation_id: 'conversation-a',
    status: 'accepted',
  });
  await act(async () => {
    conversation();
  });
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', { name: 'Check request receipt' }),
    );
  });
  expect(mock.receipt).toHaveBeenCalledWith(saved.commandId);
  expect(mock.intent).toHaveBeenCalledTimes(1);
  expect(commandReceipts.read(key)).toBeNull();
  expect(mock.drafts.get('conversation-a')?.text).toBe('Edited current draft');
  expect(mock.setDraft).not.toHaveBeenCalled();
});

it('does not submit when its persisted recovery identity write fails', async () => {
  idleConversation();
  await act(async () => {
    conversation();
  });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('private');
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  });
  expect(mock.intent).not.toHaveBeenCalled();
  expect(mock.setDraft).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Receipt storage is unavailable',
  );
});

it('fences a late submit result and protects B draft after switching away from A', async () => {
  const key = idleConversation();
  let finish!: (receipt: CommandReceipt) => void;
  mock.intent.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  let rendered!: ReturnType<typeof conversation>;
  await act(async () => {
    rendered = conversation();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  });
  const saved = commandReceipts.read(key)!;
  mock.drafts.set('conversation-a', { text: 'A edited', attachments: [] });
  await mock.selectConversation('conversation-b');
  mock.drafts.set('conversation-b', { text: 'B draft', attachments: [] });
  await act(async () => {
    rendered.rerender(<Conversation onPanel={vi.fn()} />);
  });
  await act(async () => {
    finish({
      command_id: saved.commandId,
      submission_id: saved.steeringId!,
      conversation_id: 'conversation-a',
      status: 'accepted',
    });
  });
  expect(commandReceipts.read(key)).toBeNull();
  expect(mock.setDraft).not.toHaveBeenCalled();
  expect(mock.showLatest).not.toHaveBeenCalled();
  expect(mock.drafts.get('conversation-b')?.text).toBe('B draft');
  expect(
    screen.queryByRole('button', { name: 'Check request receipt' }),
  ).not.toBeInTheDocument();
});

it('keeps an absent ordinary submit receipt until explicit review without resend', async () => {
  const key = idleConversation(),
    saved = { commandId: crypto.randomUUID(), steeringId: crypto.randomUUID() };
  commandReceipts.reserve(key, saved);
  mock.receipt.mockRejectedValue({ code: 'not_found' });
  await act(async () => {
    conversation();
  });
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', { name: 'Check request receipt' }),
    );
  });
  expect(commandReceipts.read(key)).toEqual(saved);
  expect(mock.intent).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Review pending receipt' }),
  );
  await act(async () => {
    mock.open.mock.calls.at(-1)?.[0].onConfirm();
  });
  expect(commandReceipts.read(key)).toBeNull();
  expect(mock.intent).not.toHaveBeenCalled();
  expect(mock.drafts.get('conversation-a')?.text).toBe('Original queued draft');
});

it.each(['throw', 'discard'] as const)(
  'does not create New chat if receipt storage writes %s',
  async (mode) => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      if (mode === 'throw') throw new Error('Private storage detail');
    });
    await act(async () => {
      conversation();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'New chat' }));
    });
    expect(mock.intent).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Receipt storage is unavailable',
    );
    expect(screen.getByRole('alert')).not.toHaveTextContent(
      'Private storage detail',
    );
  },
);

it('checks an absent New chat receipt without replay and requires explicit review to clear', async () => {
  const identity = crypto.randomUUID();
  sessionStorage.setItem(
    `row-bot.new-chat.${mock.state.handshake.instance_id}`,
    identity,
  );
  mock.receipt.mockRejectedValue({ code: 'not_found' });
  await act(async () => {
    conversation();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Check new chat' }));
  });
  expect(mock.intent).not.toHaveBeenCalled();
  expect(
    sessionStorage.getItem(
      `row-bot.new-chat.${mock.state.handshake.instance_id}`,
    ),
  ).toBe(identity);
  fireEvent.click(
    screen.getByRole('button', { name: 'Review pending receipt' }),
  );
  expect(mock.open.mock.calls.at(-1)?.[0].confirmLabel).toBe(
    'Clear pending receipt',
  );
  await act(async () => {
    mock.open.mock.calls.at(-1)?.[0].onConfirm();
  });
  expect(mock.intent).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'New chat' })).toBeInTheDocument();
});

it('fails closed before queue dispatch if its recovery identity cannot be saved', async () => {
  activeConversation();
  await act(async () => {
    conversation();
  });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('private');
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Queue message' }));
  });
  expect(mock.intent).not.toHaveBeenCalled();
  expect(mock.setDraft).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Receipt storage is unavailable',
  );
});

it('fails closed if existing receipt storage cannot be read', async () => {
  activeConversation();
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('private');
  });
  await act(async () => {
    conversation();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Queue message' }));
  });
  expect(mock.intent).not.toHaveBeenCalled();
  expect(mock.receipt).not.toHaveBeenCalled();
  expect(mock.setDraft).not.toHaveBeenCalled();
});

it('reserves before dispatch, coalesces synchronous clicks and clears only the unchanged draft on acceptance', async () => {
  const key = activeConversation();
  let finish!: (receipt: CommandReceipt) => void;
  mock.intent.mockImplementation((_id, _type, payload, _revision, command) => {
    expect(commandReceipts.read(key)).toEqual({
      commandId: command,
      steeringId: payload.steering_id,
    });
    return new Promise((resolve) => {
      finish = resolve;
    });
  });
  await act(async () => {
    conversation();
  });
  await act(async () => {
    const button = screen.getByRole('button', { name: 'Queue message' });
    fireEvent.click(button);
    fireEvent.click(button);
  });
  const saved = commandReceipts.read(key)!;
  expect(mock.intent).toHaveBeenCalledTimes(1);
  await act(async () => {
    finish({
      command_id: saved.commandId,
      conversation_id: 'conversation-a',
      status: 'accepted',
    });
  });
  expect(commandReceipts.read(key)).toBeNull();
  expect(mock.setDraft).toHaveBeenCalledExactlyOnceWith('conversation-a', {
    text: '',
    attachments: [],
  });
});

it('retains the exact pending identity when a queue receipt belongs to another conversation', async () => {
  const key = activeConversation(),
    saved = { commandId: crypto.randomUUID(), steeringId: crypto.randomUUID() };
  commandReceipts.reserve(key, saved);
  mock.receipt.mockResolvedValue({
    command_id: saved.commandId,
    conversation_id: 'conversation-b',
    status: 'accepted',
  });
  await act(async () => {
    conversation();
  });
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', { name: 'Check queued message' }),
    );
  });
  expect(commandReceipts.read(key)).toEqual(saved);
  expect(mock.setDraft).not.toHaveBeenCalled();
  expect(mock.intent).not.toHaveBeenCalled();
});

it('checks the exact lost queue receipt after remount and run completion, preserving a current draft', async () => {
  const key = activeConversation();
  mock.intent.mockRejectedValue(new TypeError('Lost response'));
  let rendered!: ReturnType<typeof conversation>;
  await act(async () => {
    rendered = conversation();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Queue message' }));
  });
  const saved = commandReceipts.read(key)!;
  expect(mock.intent).toHaveBeenCalledWith(
    'conversation-a',
    'conversation.steer',
    { text: 'Original queued draft', steering_id: saved.steeringId },
    '1',
    saved.commandId,
  );
  expect(screen.getByRole('button', { name: 'Queue message' })).toBeDisabled();
  rendered.unmount();
  mock.state.projection = null;
  mock.drafts.set('conversation-a', {
    text: 'New edited draft',
    attachments: [],
  });
  mock.receipt.mockResolvedValue({
    command_id: saved.commandId,
    conversation_id: 'conversation-a',
    submission_id: saved.steeringId,
    status: 'accepted',
  });
  await act(async () => {
    conversation();
  });
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', { name: 'Check queued message' }),
    );
  });
  expect(mock.intent).toHaveBeenCalledTimes(1);
  expect(mock.receipt).toHaveBeenCalledWith(saved.commandId);
  expect(commandReceipts.read(key)).toBeNull();
  expect(mock.drafts.get('conversation-a')?.text).toBe('New edited draft');
  expect(mock.setDraft).not.toHaveBeenCalled();
});

it('fences A queue completion after switching through B to C and preserves edits', async () => {
  const key = activeConversation();
  let finish!: (receipt: CommandReceipt) => void;
  mock.intent.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  let rendered!: ReturnType<typeof conversation>;
  await act(async () => {
    rendered = conversation();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Queue message' }));
  });
  const saved = commandReceipts.read(key)!;
  mock.drafts.set('conversation-a', {
    text: 'A edited while pending',
    attachments: [],
  });
  await mock.selectConversation('conversation-b');
  await mock.selectConversation('conversation-c');
  mock.drafts.set('conversation-c', { text: 'C draft', attachments: [] });
  await act(async () => {
    rendered.rerender(<Conversation onPanel={vi.fn()} />);
  });
  await act(async () => {
    finish({
      command_id: saved.commandId,
      conversation_id: 'conversation-a',
      submission_id: saved.steeringId!,
      status: 'accepted',
    });
  });
  expect(commandReceipts.read(key)).toBeNull();
  expect(mock.setDraft).not.toHaveBeenCalled();
  expect(mock.drafts.get('conversation-a')?.text).toBe(
    'A edited while pending',
  );
  expect(mock.drafts.get('conversation-c')?.text).toBe('C draft');
  expect(
    screen.queryByRole('button', { name: 'Check queued message' }),
  ).not.toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('retains an absent queue receipt until an explicit review, without creating another command', async () => {
  const key = activeConversation(),
    saved = { commandId: crypto.randomUUID(), steeringId: crypto.randomUUID() };
  commandReceipts.reserve(key, saved);
  mock.receipt.mockRejectedValue({ code: 'not_found' });
  await act(async () => {
    conversation();
  });
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', { name: 'Check queued message' }),
    );
  });
  expect(mock.intent).not.toHaveBeenCalled();
  expect(commandReceipts.read(key)).toEqual(saved);
  fireEvent.click(
    screen.getByRole('button', { name: 'Review pending receipt' }),
  );
  expect(mock.open.mock.calls.at(-1)?.[0].description).toContain(
    'sending again could create a duplicate',
  );
  await act(async () => {
    mock.open.mock.calls.at(-1)?.[0].onConfirm();
  });
  expect(commandReceipts.read(key)).toBeNull();
  expect(mock.intent).not.toHaveBeenCalled();
  expect(mock.drafts.get('conversation-a')?.text).toBe('Original queued draft');
});

it('offers media retry after a failed download and releases its object URL on unmount', async () => {
  mock.download
    .mockRejectedValueOnce(new TypeError('fixture disconnected'))
    .mockResolvedValueOnce(new Blob(['image']));
  const create = vi
    .spyOn(URL, 'createObjectURL')
    .mockReturnValue('blob:synthetic-result');
  const revoke = vi
    .spyOn(URL, 'revokeObjectURL')
    .mockImplementation(() => undefined);
  const rendered = render(<Media reference="fixture-media" mime="image/png" />);
  expect(
    await screen.findByRole('button', { name: 'Retry generated result' }),
  ).toBeInTheDocument();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Retry generated result' }),
    ),
  );
  expect(screen.getByAltText('Generated result')).toHaveAttribute(
    'src',
    'blob:synthetic-result',
  );
  expect(mock.download).toHaveBeenCalledTimes(2);
  rendered.unmount();
  expect(revoke).toHaveBeenCalledWith('blob:synthetic-result');
  create.mockRestore();
  revoke.mockRestore();
});

it('recovers lost New chat response using the saved command ID across remount without creating twice', async () => {
  mock.intent.mockRejectedValue(new TypeError('Fixture lost response'));
  let rendered!: ReturnType<typeof conversation>;
  await act(async () => {
    rendered = conversation();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'New chat' }));
  });
  const identity = mock.intent.mock.calls[0][4];
  expect(
    sessionStorage.getItem(
      `row-bot.new-chat.${mock.state.handshake.instance_id}`,
    ),
  ).toBe(identity);
  rendered.unmount();
  mock.receipt.mockResolvedValue({
    command_id: identity,
    status: 'completed',
    conversation_id: 'created-conversation',
  });
  await act(async () => {
    conversation();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Check new chat' }));
  });
  expect(mock.receipt).toHaveBeenCalledWith(identity);
  expect(mock.intent).toHaveBeenCalledTimes(1);
  expect(mock.navigate).toHaveBeenCalledWith(
    '/conversations/created-conversation',
  );
  expect(
    sessionStorage.getItem(
      `row-bot.new-chat.${mock.state.handshake.instance_id}`,
    ),
  ).toBeNull();
});

it('owns the new draft before navigation while the initial conversation read is pending', async () => {
  mock.state.selectedConversationId = 'conversation-a';
  mock.state.conversation = {
    id: 'conversation-a',
    title: 'Conversation A',
    revision: '1',
    pinned: false,
  };
  mock.drafts.set('conversation-a', {
    text: 'Retained A draft',
    attachments: [],
  });
  mock.intent.mockImplementation(async (...args: unknown[]) => ({
    command_id: args[4],
    status: 'completed',
    conversation_id: 'conversation-b',
  }));
  let finishOpen!: () => void;
  mock.selectConversation.mockImplementation((id: string) => {
    mock.version++;
    mock.state.selectedConversationId = id;
    mock.state.conversation = null;
    mock.state.loadingConversation = true;
    return new Promise<void>((resolve) => {
      finishOpen = () => {
        mock.state.conversation = {
          id,
          title: id,
          revision: '1',
          pinned: false,
        };
        mock.state.loadingConversation = false;
        resolve();
      };
    });
  });
  const navigationOwners: (string | null)[] = [];
  mock.navigate.mockImplementationOnce(() => {
    navigationOwners.push(mock.state.selectedConversationId);
  });
  let rendered!: ReturnType<typeof conversation>;
  await act(async () => {
    rendered = conversation();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'New chat' }));
  });
  expect(navigationOwners).toEqual(['conversation-b']);
  expect(mock.state.loadingConversation).toBe(true);
  const input = screen.getByRole('textbox', { name: 'Message' });
  fireEvent.change(input, {
    target: { value: 'Typed before B finishes opening' },
  });
  expect(mock.drafts.get('conversation-b')?.text).toBe(
    'Typed before B finishes opening',
  );
  expect(mock.drafts.get('conversation-a')?.text).toBe('Retained A draft');
  await act(async () => {
    finishOpen();
    rendered.rerender(<Conversation onPanel={vi.fn()} />);
  });
  expect(input).toHaveValue('Typed before B finishes opening');
  expect(mock.intent).toHaveBeenCalledTimes(1);
});

it('retains late successful New chat receipt without stealing a newer selection', async () => {
  let finish!: (result: CommandReceipt) => void;
  mock.intent.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await act(async () => {
    conversation();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'New chat' }));
  });
  const identity = mock.intent.mock.calls[0][4];
  mock.version += 2;
  mock.state.selectedConversationId = 'conversation-c';
  await act(async () => {
    finish({
      command_id: identity,
      status: 'completed',
      conversation_id: 'created-conversation',
    });
  });
  expect(mock.navigate).not.toHaveBeenCalled();
  expect(mock.intent).toHaveBeenCalledTimes(1);
  expect(
    screen.getByRole('button', { name: 'Check new chat' }),
  ).toBeInTheDocument();
  expect(
    sessionStorage.getItem(
      `row-bot.new-chat.${mock.state.handshake.instance_id}`,
    ),
  ).toBe(identity);
});

it('retains a late New chat receipt after going Home even when conversation selection stays unchanged', async () => {
  let finish!: (result: CommandReceipt) => void;
  mock.intent.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  let rendered!: ReturnType<typeof conversation>;
  await act(async () => {
    rendered = conversation();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'New chat' }));
  });
  const identity = mock.intent.mock.calls[0][4];
  mock.routeKey = 'home-route';
  await act(async () => rendered.rerender(<Conversation onPanel={vi.fn()} />));
  await act(async () =>
    finish({
      command_id: identity,
      status: 'completed',
      conversation_id: 'created-conversation',
    }),
  );
  expect(mock.navigate).not.toHaveBeenCalled();
  expect(mock.selectConversation).not.toHaveBeenCalled();
  expect(
    commandReceipts.read(`row-bot.new-chat.${mock.state.handshake.instance_id}`)
      ?.commandId,
  ).toBe(identity);
  expect(screen.getByRole('button', { name: 'Check new chat' })).toBeVisible();
});

it('admits only one New chat action across rapid shared-owner requests', async () => {
  let finish!: (result: CommandReceipt) => void;
  mock.intent.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await act(async () => conversation());
  await act(async () => {
    const button = screen.getByRole('button', { name: 'New chat' });
    fireEvent.click(button);
    fireEvent.click(button);
  });
  expect(mock.intent).toHaveBeenCalledTimes(1);
  await act(async () =>
    finish({
      command_id: mock.intent.mock.calls[0][4],
      status: 'completed',
      conversation_id: 'created-once',
    }),
  );
  expect(mock.intent).toHaveBeenCalledTimes(1);
  expect(mock.navigate).toHaveBeenCalledWith('/conversations/created-once');
});

it('fences a search hit when A history resolves after selection has moved through B to C', async () => {
  mock.state.search = {
    items: [
      {
        conversation_id: 'conversation-a',
        title: 'A hit',
        message_id: 'message-a',
        row_id: 'user:message-a',
        excerpt: 'Needle',
        checkpoint_revision: '1',
      },
    ],
    has_more: false,
    next_cursor: null,
    revision: 'library-1',
    scanned_messages: 1,
  };
  let finish!: () => void;
  mock.showHistory.mockImplementation(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  await act(async () => {
    render(<SearchConversations />);
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'A hit Needle' }));
  });
  expect(mock.showHistory).toHaveBeenCalledWith('message-a');
  await mock.selectConversation('conversation-b');
  await mock.selectConversation('conversation-c');
  await act(async () => {
    finish();
  });
  expect(mock.navigate).not.toHaveBeenCalled();
  expect(mock.close).not.toHaveBeenCalled();
  expect(mock.state.selectedConversationId).toBe('conversation-c');
});
