import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type {
  ApprovalView,
  CommandReceipt,
  ConversationView as ConversationRow,
  ConversationWorkspace,
  DelegatedActivityView,
  DelegatedRun,
  ModelChoice,
  SearchPage,
  Snapshot,
  TranscriptPage,
  TranscriptRow,
} from '../../api/types';
import { AgentAvatar, agentSeed } from '../../ui/AgentAvatar';
import { commandReceipts } from './command-receipts';
import { ContextHostContext, useContextHostOwner } from './context-host';
import ConversationView, { isNarrowChat, Media } from './Conversation';
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
    earlier: [] as TranscriptRow[],
    earlierAvailable: false,
    loadingEarlier: false,
    conversations: [] as ConversationRow[],
    status: 'ready',
    handshake: {
      instance_id: '',
      models: [] as ModelChoice[],
      application_capabilities: [] as string[],
    },
    activity: [],
    loadingConversation: false,
    search: null as SearchPage | null,
    searching: false,
    draftStatus: 'saved',
    suggestions: [],
  },
  version: 0,
  routeKey: 'conversation-route',
  navigate: vi.fn(),
  intent: vi.fn(),
  controlsSettled: vi.fn(),
  workspaceFor: vi.fn(),
  waitingMessages: vi.fn(),
  steering: vi.fn(),
  approval: vi.fn(),
  receipt: vi.fn(),
  showHistory: vi.fn(),
  showLatest: vi.fn(),
  loadEarlier: vi.fn(),
  selectConversation: vi.fn(),
  loadMoreConversations: vi.fn(),
  conversationActions: vi.fn(),
  reviewConversationAction: vi.fn(),
  executeConversationAction: vi.fn(),
  searchLibrary: vi.fn(),
  close: vi.fn(),
  open: vi.fn(),
  dismiss: vi.fn(),
  download: vi.fn(),
  writeClipboard: vi.fn(),
  platformDiscover: vi.fn(),
  drafts: new Map<string, { text: string; attachments: [] }>(),
  setDraft: vi.fn(),
  upload: vi.fn(),
  attachmentThumbnail: vi.fn(() => new Promise<Blob>(() => undefined)),
  composer: vi.fn(),
  refreshWorkspace: vi.fn(),
  refreshListedConversation: vi.fn().mockResolvedValue(undefined),
  notify: vi.fn(),
  goals: vi.fn(),
  reviewGoal: vi.fn(),
  executeGoal: vi.fn(),
  computerUse: vi.fn(),
  computerUsePreview: vi.fn(),
  computerUseCommand: vi.fn(),
  delegatedPage: vi.fn(),
  delegatedRun: vi.fn(),
  command: vi.fn(),
  forgetConversation: vi.fn(),
}));
vi.mock('react-router-dom', () => ({
  useNavigate: () => mock.navigate,
  useLocation: () => ({ key: mock.routeKey }),
  useInRouterContext: () => false,
}));
vi.mock('../../runtime', () => {
  const runtime = {
    controller: {
      getSnapshot: () => mock.state,
      getSelectionVersion: () => mock.version,
      dictationScope: () => null,
      dictationCapability: async () => ({
        schema_version: 1,
        browser_dictation_available: false,
        native_capture_available: false,
        reason: 'host_unavailable',
      }),
      getDraft: (id: string) =>
        mock.drafts.get(id) ?? { text: '', attachments: [] },
      setDraft: mock.setDraft,
      upload: mock.upload,
      attachmentThumbnail: mock.attachmentThumbnail,
      composer: mock.composer,
      refreshWorkspace: mock.refreshWorkspace,
      refreshListedConversation: mock.refreshListedConversation,
      goals: mock.goals,
      reviewGoal: mock.reviewGoal,
      executeGoal: mock.executeGoal,
      computerUse: mock.computerUse,
      computerUsePreview: mock.computerUsePreview,
      computerUseCommand: mock.computerUseCommand,
      intent: mock.intent,
      controlsSettled: mock.controlsSettled,
      workspaceFor: mock.workspaceFor,
      waitingMessages: mock.waitingMessages,
      steering: mock.steering,
      approval: mock.approval,
      receipt: mock.receipt,
      showHistory: mock.showHistory,
      showLatest: mock.showLatest,
      loadEarlier: mock.loadEarlier,
      selectConversation: mock.selectConversation,
      loadMoreConversations: mock.loadMoreConversations,
      conversationActions: mock.conversationActions,
      reviewConversationAction: mock.reviewConversationAction,
      executeConversationAction: mock.executeConversationAction,
      searchLibrary: mock.searchLibrary,
      download: mock.download,
      delegatedActivity: (conversationId: string) =>
        mock.delegatedPage(conversationId),
      delegatedRun: (conversationId: string, runId: string) =>
        mock.delegatedRun(conversationId, runId),
      command: mock.command,
      forgetConversation: mock.forgetConversation,
    },
    platform: {
      discover: mock.platformDiscover,
      save: vi.fn(),
      writeClipboard: mock.writeClipboard,
    },
    conversationActionsOwner: {
      get: () => ({ get: () => ({}) }),
    },
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
    dismiss: mock.dismiss,
    notify: mock.notify,
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
  mock.state.earlier = [];
  mock.state.earlierAvailable = false;
  mock.state.loadingEarlier = false;
  mock.state.conversations = [];
  mock.state.loadingConversation = false;
  mock.drafts.clear();
  mock.setDraft.mockImplementation((id, draft) => mock.drafts.set(id, draft));
  mock.state.search = null;
  mock.state.activity = [];
  mock.state.handshake.instance_id = crypto.randomUUID();
  mock.state.handshake.models = [];
  mock.state.handshake.application_capabilities = [];
  mock.platformDiscover.mockResolvedValue({
    status: 'ok',
    value: { kind: 'browser', platform: 'browser', capabilities: [] },
  });
  mock.workspaceFor.mockResolvedValue({ writer_status: '' });
  mock.controlsSettled.mockResolvedValue(undefined);
  mock.waitingMessages.mockImplementation(async (id: string) => ({
    conversation_id: id,
    generation_id: '',
    items: [],
    has_more: false,
  }));
  mock.selectConversation.mockImplementation(async (id: string) => {
    mock.version++;
    mock.state.selectedConversationId = id;
    mock.state.conversation = { id, title: id, revision: '1', pinned: false };
  });
  mock.delegatedPage.mockImplementation(
    async (conversationId: string): Promise<DelegatedActivityView> => ({
      conversation_id: conversationId,
      parent_conversation_id: null,
      items: [],
      next_cursor: null,
      has_more: false,
    }),
  );
  mock.delegatedRun.mockRejectedValue(
    new Error('No delegated run in this fixture'),
  );
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
      <button
        onClick={() =>
          void owner.newChat('What do you remember about my current projects?')
        }
      >
        New chat with example
      </button>
      <button
        onClick={() =>
          void owner.newChat('Create a design: ', undefined, { send: false })
        }
      >
        New chat with a draft
      </button>
      {owner.error && <p role="alert">{owner.error}</p>}
      {owner.canReview && (
        <button onClick={owner.reviewMissingReceipt}>Stop checking</button>
      )}
      <ConversationView
        {...props}
        focusConversationId={owner.focusConversationId}
        onComposerFocused={owner.onComposerFocused}
        firstPrompt={owner.firstPrompt}
        onFirstPromptConsumed={owner.onFirstPromptConsumed}
      />
    </>
  );
}
function conversation() {
  return render(<Conversation onPanel={vi.fn()} />);
}

it('offers earlier messages above the live window only once the selected conversation has opened', async () => {
  mock.state.selectedConversationId = 'conversation-b';
  mock.state.loadingConversation = true;
  mock.state.earlierAvailable = true;
  mock.loadEarlier.mockResolvedValue(undefined);
  let rendered!: ReturnType<typeof conversation>;
  await act(async () => {
    rendered = conversation();
  });
  expect(screen.queryByRole('button', { name: 'Earlier messages' })).toBeNull();

  mock.state.conversation = {
    id: 'conversation-b',
    title: 'B',
    revision: '1',
    pinned: false,
  };
  rendered.rerender(<Conversation onPanel={vi.fn()} />);
  expect(screen.queryByRole('button', { name: 'Earlier messages' })).toBeNull();

  mock.state.loadingConversation = false;
  rendered.rerender(<Conversation onPanel={vi.fn()} />);
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Earlier messages' }));
  });
  expect(mock.loadEarlier).toHaveBeenCalledOnce();
  expect(mock.showHistory).not.toHaveBeenCalled();
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
  const controls = ['Earlier messages', 'Later messages'].map((name) =>
    screen.getByRole('button', { name }),
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
    fireEvent.click(controls[0]);
    fireEvent.click(controls[1]);
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

it('morphs Send into Stop on one button that keeps its place and focus', () => {
  activeConversation();
  const rendered = render(<Conversation onPanel={vi.fn()} />);
  const stop = screen.getByRole('button', { name: 'Stop' });
  expect(stop).toHaveAttribute('data-state', 'stop');
  expect(stop).toHaveAttribute('type', 'button');
  stop.focus();
  mock.state.projection = {
    rows: [],
    generation: null,
  } as unknown as Snapshot;
  rendered.rerender(<Conversation onPanel={vi.fn()} />);
  const send = screen.getByRole('button', { name: 'Send' });
  expect(send).toBe(stop);
  expect(send).toHaveAttribute('data-state', 'send');
  expect(send).toHaveAttribute('type', 'submit');
});

it('anchors bounded approval context inline with canonical resolve controls', async () => {
  activeConversation();
  mock.state.projection = {
    ...mock.state.projection!,
    generation: {
      generation_id: 'run-a',
      quiesced: false,
      can_stop: false,
      status: 'waiting_approval',
      approval_id: 'approval-a',
    },
  } as unknown as Snapshot;
  mock.state.activity = [
    {
      cursor: '4',
      event: {
        event_id: 'event-approval',
        type: 'approval.required',
        conversation_id: 'conversation-a',
        projection_revision: '4',
        protocol_version: '1.0',
        server_epoch: 'epoch',
        source: 'runtime',
        source_epoch: 'epoch',
        source_stream_id: 'conversation-a',
        source_sequence_start: '4',
        source_sequence_end: '4',
        payload: {
          status: 'waiting_approval',
          approval_id: 'approval-a',
          action_label: 'fixture_tool',
          reason: 'Read a reviewed local value.',
          risk_class: 'low',
          scope: 'One local read.',
          safe_argument_summary: '{"limit":3}',
          requesting_trace_id: 'call-a',
        },
      },
    },
  ] as never[];
  mock.approval.mockResolvedValue({
    id: 'approval-a',
    status: 'pending',
    revision: '0',
    expires_at: '2030-01-01T00:00:00Z',
    summary: 'Read a reviewed local value.',
    action_label: 'fixture_tool',
    reason: 'Read a reviewed local value.',
    risk_class: 'low',
    scope: 'One local read.',
    safe_argument_summary: '{"limit":3}',
    requesting_trace_id: 'call-a',
    policy_revision: '1',
    nonce: 'n'.repeat(32),
  } satisfies ApprovalView);
  mock.intent.mockResolvedValue({ status: 'accepted' });

  await act(async () => conversation());
  const bar = await screen.findByRole('complementary', {
    name: 'Approval required for fixture_tool',
  });
  expect(bar).toHaveTextContent('Read a reviewed local value.');
  expect(bar).toHaveTextContent('Low risk');
  expect(bar).not.toHaveTextContent('One local read.');
  expect(within(bar).getByRole('button', { name: 'Deny' })).toBeVisible();
  expect(within(bar).getByRole('button', { name: 'Details' })).toBeVisible();
  await act(async () =>
    fireEvent.click(within(bar).getByRole('button', { name: 'Details' })),
  );
  expect(mock.open.mock.lastCall?.[0]).toMatchObject({
    title: 'Allow Fixture tool?',
  });
  await act(async () =>
    fireEvent.click(within(bar).getByRole('button', { name: 'Approve' })),
  );
  expect(mock.intent).toHaveBeenCalledWith(
    'approval-a',
    'approval.resolve',
    { decision: 'approve', nonce: 'n'.repeat(32) },
    '0',
  );
  expect(within(bar).getByRole('status')).toHaveTextContent(
    'Approval submitted.',
  );
});

function computerPaused() {
  activeConversation();
  mock.state.projection = {
    ...mock.state.projection!,
    generation: {
      generation_id: 'run-a',
      quiesced: true,
      can_stop: false,
      status: 'waiting_approval',
      approval_id: 'approval-a',
    },
  } as unknown as Snapshot;
  mock.state.activity = [
    {
      cursor: '4',
      event: {
        event_id: 'event-pause',
        type: 'approval.required',
        conversation_id: 'conversation-a',
        projection_revision: '4',
        protocol_version: '1.0',
        server_epoch: 'epoch',
        source: 'runtime',
        source_epoch: 'epoch',
        source_stream_id: 'conversation-a',
        source_sequence_start: '4',
        source_sequence_end: '4',
        payload: {
          status: 'waiting_approval',
          approval_id: 'approval-a',
          action_label: 'Computer activity',
          reason:
            'Computer control is paused. Use Resume or Stop in the live panel.',
        },
      },
    },
  ] as never[];
  mock.computerUse.mockResolvedValue({
    schema_version: 1,
    conversation_id: 'conversation-a',
    revision: 'c'.repeat(64),
    active: true,
    state: 'paused',
    app: 'Calculator',
    has_picture: false,
    approval_id: 'approval-a',
    can_pause: false,
    can_resume: true,
    can_stop: true,
  });
}

it('shows a paused computer as the computer card in place of the approval card', async () => {
  computerPaused();
  mock.state.handshake.application_capabilities = ['computer:interactive'];
  mock.computerUseCommand.mockResolvedValue({
    schema_version: 1,
    command_id: crypto.randomUUID(),
    action: 'computer_use.resume',
    conversation_id: 'conversation-a',
    status: 'completed',
    code: null,
    computer_use: null,
  });

  await act(async () => conversation());
  const card = await screen.findByRole('region', { name: 'Computer use' });
  expect(card).toHaveTextContent('Using your computer · Calculator');
  expect(within(card).getByRole('button', { name: 'Stop' })).toBeVisible();
  // Never both: Resume answers the pause, so no approval card asks again.
  expect(
    screen.queryByRole('complementary', { name: /Approval required/ }),
  ).toBeNull();
  expect(mock.approval).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(within(card).getByRole('button', { name: 'Resume' })),
  );
  expect(mock.computerUseCommand).toHaveBeenCalledWith(
    'conversation-a',
    'computer_use.resume',
  );
});

it('keeps the approval card on another device and never asks about the computer', async () => {
  computerPaused();
  mock.approval.mockResolvedValue({
    id: 'approval-a',
    status: 'pending',
    revision: '0',
    expires_at: '2030-01-01T00:00:00Z',
    summary: 'Computer control is paused.',
    action_label: 'Computer activity',
    reason: 'Computer control is paused. Use Resume or Stop in the live panel.',
    risk_class: 'unknown',
    scope: 'Only this requested action will be resolved.',
    safe_argument_summary: '',
    requesting_trace_id: '',
    policy_revision: '1',
    nonce: 'n'.repeat(32),
  } satisfies ApprovalView);

  await act(async () => conversation());
  expect(
    await screen.findByRole('complementary', { name: /Approval required/ }),
  ).toBeVisible();
  expect(mock.computerUse).not.toHaveBeenCalled();
  expect(screen.queryByRole('region', { name: 'Computer use' })).toBeNull();
});

it('renders assistant Markdown safely and copies only the visible canonical text', async () => {
  activeConversation();
  mock.state.projection = {
    ...mock.state.projection!,
    // Turn actions appear once the reply has finished streaming.
    generation: {
      generation_id: 'run-a',
      quiesced: true,
      can_stop: false,
      status: 'completed',
    },
    rows: [
      {
        id: 'row-a',
        message_id: 'message-a',
        role: 'assistant',
        blocks: [
          {
            type: 'text',
            text: '# Summary\n\n**Ready** with [docs](https://example.test).\n\n<script>never markup</script>',
          },
        ],
        tool_call_ids: ['tool-a'],
        content_status: 'lazy',
        content_ref: 'content-a',
      },
    ],
  } as unknown as Snapshot;
  mock.writeClipboard.mockResolvedValue({
    status: 'ok',
    value: null,
  });
  await act(async () => {
    conversation();
  });
  const message = screen.getByRole('article', { name: 'Row-Bot message' });
  expect(message.querySelector(':scope > .transcript-content')).not.toBeNull();
  expect(
    within(message).getByRole('heading', { name: 'Summary' }),
  ).toBeVisible();
  expect(within(message).getByText('Ready')).toHaveProperty(
    'tagName',
    'STRONG',
  );
  expect(
    within(message).getByText('<script>never markup</script>'),
  ).toBeVisible();
  expect(message.querySelector('script')).toBeNull();
  // No call count: the activity row carries tools. The author is in the
  // message's name; its turn marker is decorative (B271).
  expect(
    within(message).getByText('Row-Bot').closest('[aria-hidden="true"]'),
  ).not.toBeNull();
  expect(within(message).getByText('Paged content')).toBeVisible();
  await act(async () =>
    fireEvent.click(
      within(message).getByRole('button', { name: 'Copy message' }),
    ),
  );
  expect(mock.writeClipboard).toHaveBeenCalledExactlyOnceWith(
    '# Summary\n\n**Ready** with [docs](https://example.test).\n\n<script>never markup</script>',
  );
  expect(within(message).getByRole('status')).toHaveTextContent(
    'Visible message copied.',
  );
  expect(
    within(message).getByRole('button', { name: 'Copied message' }),
  ).toBeVisible();
});

it('opens the managed browser as a panel and closes the Context sheet it came from', async () => {
  activeConversation();
  const user = userEvent.setup();
  const onPanel = vi.fn();
  await act(async () => {
    render(<Conversation onPanel={onPanel} />);
  });
  await user.click(
    screen.getByRole('button', { name: 'Conversation actions' }),
  );
  await user.click(
    await screen.findByRole('menuitem', { name: 'Manage browser' }),
  );
  expect(onPanel).toHaveBeenCalledWith(
    expect.objectContaining({ panel_kind: 'browser.live' }),
  );
  // Below 1024px Context is a sheet: the panel must not open behind it.
  expect(mock.dismiss).toHaveBeenCalledWith('conversation-context');
});

it('opens reviewed conversation management from the existing action menu', async () => {
  activeConversation();
  const user = userEvent.setup();
  await act(async () => {
    conversation();
  });
  await user.click(
    screen.getByRole('button', { name: 'Conversation actions' }),
  );
  await user.click(
    await screen.findByRole('menuitem', { name: 'Manage conversation' }),
  );
  const options = mock.open.mock.lastCall?.[0];
  // Titled with the conversation's name; the meta line says its type (B237).
  expect(options.description).toMatch(/^Chat/);
  expect(options.content.props).toMatchObject({
    conversationId: 'conversation-a',
    load: mock.conversationActions,
    review: mock.reviewConversationAction,
    execute: mock.executeConversationAction,
    onDelete: expect.any(Function),
  });
});

it('publishes how far the composer reaches up from the bottom, so notices float above it', async () => {
  let top = 600;
  vi.spyOn(HTMLFormElement.prototype, 'getClientRects').mockImplementation(
    () => [{}] as unknown as DOMRectList,
  );
  vi.spyOn(
    HTMLFormElement.prototype,
    'getBoundingClientRect',
  ).mockImplementation(() => ({ top }) as DOMRect);
  activeConversation();
  let view!: ReturnType<typeof conversation>;
  await act(async () => {
    view = conversation();
  });
  const root = document.documentElement;
  const clearance = () => root.style.getPropertyValue('--composer-clearance');
  expect(clearance()).toBe(`${window.innerHeight - 600}px`);
  // It grows (a longer draft) or moves (the window, a bottom panel).
  top = 480;
  act(() => {
    window.dispatchEvent(new Event('resize'));
  });
  expect(clearance()).toBe(`${window.innerHeight - 480}px`);
  // Another composer on screen keeps it when this one goes.
  let other!: ReturnType<typeof conversation>;
  await act(async () => {
    other = conversation();
  });
  view.unmount();
  expect(clearance()).toBe(`${window.innerHeight - 480}px`);
  other.unmount();
  expect(clearance()).toBe('');
});

it('deletes from the details menu after confirmation: the chat closes at once and the delete waits for its Undo notice', async () => {
  activeConversation();
  const handshake = mock.state.handshake as { client_session_id?: string };
  handshake.client_session_id = 'session-a';
  try {
    mock.command.mockResolvedValue({ status: 'DeleteCompleted' });
    const user = userEvent.setup();
    await act(async () => {
      conversation();
    });
    await user.click(
      screen.getByRole('button', { name: 'Conversation actions' }),
    );
    await user.click(
      await screen.findByRole('menuitem', { name: 'Delete conversation' }),
    );
    const confirmation = mock.open.mock.lastCall?.[0];
    expect(confirmation).toMatchObject({
      kind: 'alert',
      confirmLabel: 'Delete conversation',
    });
    expect(mock.navigate).not.toHaveBeenCalledWith('/');
    act(() => confirmation.onConfirm());
    expect(mock.navigate).toHaveBeenCalledWith('/');
    expect(mock.forgetConversation).toHaveBeenCalledWith('conversation-a');
    const [message, , undo] = mock.notify.mock.lastCall!;
    expect(message).toBe("Deleted 'conversation-a'.");
    expect(undo).toMatchObject({ label: 'Undo' });
    // Nothing is deleted while Undo is on offer.
    expect(mock.command).not.toHaveBeenCalled();
    expect(mock.intent).not.toHaveBeenCalled();
    await act(async () => undo.onEnd());
    expect(mock.command).toHaveBeenCalledWith(
      'conversation-a',
      expect.objectContaining({
        type: 'conversation.delete',
        expected_revision: '1',
      }),
      expect.any(String),
    );
  } finally {
    delete handshake.client_session_id;
  }
});

it('keeps the context rail quiet: empty Working on and Agents sections stay hidden (B7)', async () => {
  idleConversation();
  await act(async () => conversation());

  const rail = screen.getByRole('complementary', {
    name: 'Conversation details',
  });
  // Delegated activity has loaded with nothing to show.
  await waitFor(() =>
    expect(
      within(rail).getByText('No delegated agents in this conversation.'),
    ).not.toBeVisible(),
  );
  expect(
    within(rail).queryByRole('heading', { name: 'Working on' }),
  ).not.toBeInTheDocument();
  expect(
    within(rail).getByText('Agents', { selector: 'summary' }),
  ).not.toBeVisible();
  // No goal: a small Set a goal, not a Goal row; Find and the terminal live
  // in the header (B223).
  expect(
    within(rail).queryByText('Goal', { selector: 'summary' }),
  ).not.toBeInTheDocument();
  expect(
    within(rail).getByRole('button', { name: 'Set a goal' }),
  ).toBeVisible();
  expect(
    within(rail).getByRole('button', { name: 'Add resource' }),
  ).toBeVisible();
  expect(
    within(rail).queryByRole('button', { name: 'Interactive terminal' }),
  ).not.toBeInTheDocument();
  expect(
    within(document.querySelector('.conversation-heading')!).queryByRole(
      'button',
      {
        name: 'Add resource',
      },
    ),
  ).not.toBeInTheDocument();
});

it('uses one persistent Conversation details entry point for the compact sheet', async () => {
  idleConversation();
  let rendered!: ReturnType<typeof render>;
  await act(async () => {
    rendered = render(<ConversationView onPanel={vi.fn()} compactContext />);
  });

  expect(
    screen.queryByRole('complementary', { name: 'Conversation details' }),
  ).not.toBeInTheDocument();
  mock.state.loadingConversation = true;
  rendered.rerender(<ConversationView onPanel={vi.fn()} compactContext />);
  const toggle = () =>
    screen.getByRole('button', { name: 'Conversation details' });
  expect(toggle()).toBeDisabled();
  fireEvent.click(toggle());
  expect(mock.open).not.toHaveBeenCalled();
  mock.state.loadingConversation = false;
  rendered.rerender(<ConversationView onPanel={vi.fn()} compactContext />);
  expect(toggle()).toBeEnabled();
  fireEvent.click(toggle());
  expect(mock.open.mock.lastCall?.[0]).toMatchObject({
    kind: 'sheet',
    key: 'conversation-context',
    title: 'Conversation details',
  });
});

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

it('stays on the latest row when the layout, not the reader, moves the transcript', async () => {
  activeConversation();
  let client = 100;
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(
    function (this: HTMLElement) {
      return this.classList.contains('transcript') ? 900 : 0;
    },
  );
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(
    function (this: HTMLElement) {
      return this.classList.contains('transcript') ? client : 0;
    },
  );
  await act(async () => {
    conversation();
  });
  const log = screen.getByRole('log');
  expect(log.scrollTop).toBe(800);
  // A panel opens and the composer grows: the transcript is 38px shorter and
  // the browser reports a scroll before any resize callback runs.
  client = 62;
  fireEvent.scroll(log);
  expect(log.scrollTop).toBe(838);
  expect(screen.queryByRole('button', { name: 'Latest messages' })).toBeNull();
  // The reader scrolling away still stops following.
  log.scrollTop = 300;
  fireEvent.scroll(log);
  expect(
    screen.getByRole('button', { name: 'Latest messages' }),
  ).toBeInTheDocument();
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
    observe: ReturnType<typeof vi.fn>;
  }[] = [];
  vi.stubGlobal(
    'ResizeObserver',
    class {
      disconnect = vi.fn();
      observe = vi.fn();
      constructor(callback: () => void) {
        observers.push({
          callback,
          disconnect: this.disconnect,
          observe: this.observe,
        });
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
  // The transcript's observer watches the log and its content.
  const watchesTranscript = (observer: (typeof observers)[number]) =>
    observer.observe.mock.calls.length === 2 &&
    observer.observe.mock.calls.some(([target]) => target === log);
  const first = observers.find(watchesTranscript)!;
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
  const latest = [...observers].reverse().find(watchesTranscript)!;
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

function waitingItem(text = 'Waiting follow-up') {
  return {
    id: 'submission-waiting',
    submission_id: 'submission-waiting',
    generation_id: 'generation-waiting',
    text,
    revision: '2',
    state: 'paused' as const,
    editable: true,
    removable: true,
  };
}

it('drops a send the server refused because a message waits, and offers Send now (B107)', async () => {
  const key = idleConversation();
  mock.state.projection = {
    rows: [
      {
        id: 'user:submission:stopped',
        message_id: 'stopped',
        role: 'user',
        blocks: [{ type: 'text', text: 'Stopped question' }],
      },
    ],
    generation: {
      generation_id: 'run-stopped',
      quiesced: true,
      can_stop: false,
      status: 'stopped',
    },
  } as unknown as Snapshot;
  mock.waitingMessages.mockResolvedValue({
    conversation_id: 'conversation-a',
    generation_id: '',
    items: [waitingItem()],
    has_more: false,
  });
  mock.workspaceFor.mockResolvedValue({ revision: '7', writer_status: '' });
  mock.intent.mockImplementation(async (_id, type) => {
    if (type === 'conversation.submit')
      throw { code: 'queue_pending', status: 409 };
    return { status: 'completed', conversation_id: 'conversation-a' };
  });
  await act(async () => {
    conversation();
  });
  const waiting = await screen.findByRole(
    'region',
    { name: 'Waiting messages' },
    { timeout: 2000 },
  );
  expect(waiting).toHaveTextContent('1 message waiting');
  // The stopped notice points at the waiting message instead of offering a
  // Send again the server would refuse.
  expect(screen.queryByRole('button', { name: 'Send again' })).toBeNull();
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  });
  const alert = screen.getByRole('alert');
  expect(alert).toHaveTextContent(
    'A message is waiting to be sent. Send it now, or discard it first.',
  );
  // Refused before anything ran: no claim blocks the composer, no pending
  // "awaiting confirmation" bubble, no check button.
  expect(commandReceipts.read(key)).toBeNull();
  expect(
    screen.queryByRole('article', {
      name: 'You message awaiting confirmation',
    }),
  ).toBeNull();
  expect(screen.queryByRole('button', { name: 'Check message' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled();
  await act(async () => {
    fireEvent.click(within(alert).getByRole('button', { name: 'Send now' }));
  });
  expect(mock.intent).toHaveBeenLastCalledWith(
    'conversation-a',
    'conversation.queue.dispatch',
    { submission_id: 'submission-waiting', expected_queue_revision: '2' },
    '7',
  );
});

it('drops a claim whose check reads a refusal instead of looping on Check (B107)', async () => {
  const key = idleConversation(),
    saved = { commandId: crypto.randomUUID(), steeringId: crypto.randomUUID() };
  commandReceipts.reserve(key, saved);
  mock.receipt.mockResolvedValue({
    command_id: saved.commandId,
    conversation_id: 'conversation-a',
    status: 'rejected',
    code: 'generation_active',
  });
  await act(async () => {
    conversation();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Check message' }));
  });
  expect(mock.receipt).toHaveBeenCalledWith(saved.commandId);
  expect(mock.intent).not.toHaveBeenCalled();
  expect(commandReceipts.read(key)).toBeNull();
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Row-Bot is still answering. Wait for it to finish, or stop it first.',
  );
  expect(screen.queryByRole('button', { name: 'Check message' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Stop checking' })).toBeNull();
});

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

it('offers interrupted recovery in the transcript with a cause and next steps, at any width', async () => {
  interruptedConversation();
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(private callback: ResizeObserverCallback) {}
      observe(target: Element) {
        if (target.classList.contains('composer'))
          this.callback(
            [{ contentRect: { width: 480 } } as ResizeObserverEntry],
            this as unknown as ResizeObserver,
          );
      }
      disconnect() {}
    },
  );
  mock.intent.mockImplementation(
    async (_id, _type, _payload, _revision, commandId) => ({
      command_id: commandId,
      conversation_id: 'conversation-a',
      status: 'accepted',
    }),
  );
  await act(async () => conversation());
  const log = screen.getByRole('log', { name: 'Conversation' });
  expect(log).toHaveTextContent('The response was interrupted');
  expect(
    within(log).getByRole('button', { name: 'Switch model' }),
  ).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Message actions' })).toBeNull();
  await userEvent
    .setup()
    .click(within(log).getByRole('button', { name: 'Resume' }));
  expect(mock.intent.mock.calls[0][1]).toBe('conversation.resume');
});

it('says when a stop left the last message unanswered and offers to send it again', async () => {
  idleConversation();
  mock.state.projection = {
    rows: [
      {
        id: 'user:guidance',
        message_id: 'guidance',
        role: 'user',
        blocks: [{ type: 'text', text: 'Keep it under five paragraphs.' }],
      },
    ],
    generation: {
      generation_id: 'stopped-run',
      quiesced: true,
      can_stop: false,
      status: 'stopped',
    },
  } as unknown as Snapshot;
  await act(async () => conversation());
  const log = screen.getByRole('log', { name: 'Conversation' });
  expect(log).toHaveTextContent('Stopped before a reply');
  expect(within(log).getByRole('button', { name: 'Send again' })).toBeVisible();
});

it('waits for a model change that is still saving, then sends with it (B109)', async () => {
  idleConversation();
  const other = { provider_id: 'fixture', model_ref: 'fixture/other' };
  let saved!: () => void;
  mock.controlsSettled.mockImplementation(
    () =>
      new Promise<void>((resolve) => {
        saved = () => {
          // What the save wrote back: the new model and a newer revision.
          mock.state.conversation = {
            ...mock.state.conversation!,
            revision: '2',
          };
          mock.state.workspace = {
            ...mock.state.workspace!,
            revision: '2',
            controls: {
              ...mock.state.workspace!.controls,
              model_selection: other,
            },
          } as ConversationWorkspace;
          resolve();
        };
      }),
  );
  mock.intent.mockImplementation(
    async (_conversation, _type, payload, _revision, commandId) => ({
      command_id: commandId,
      conversation_id: 'conversation-a',
      submission_id: payload.submission_id,
      status: 'accepted',
    }),
  );
  await act(async () => conversation());
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  });
  expect(mock.intent).not.toHaveBeenCalled();
  await act(async () => saved());
  expect(mock.intent).toHaveBeenCalledTimes(1);
  expect(mock.intent).toHaveBeenCalledWith(
    'conversation-a',
    'conversation.submit',
    expect.objectContaining({
      text: 'Original queued draft',
      model_selection: other,
    }),
    '2',
    expect.any(String),
  );
});

it('keeps only what was typed after sending once the message is accepted', async () => {
  // Found live: a follow-up typed while the send was confirmed joined the sent
  // text, and the whole of it stayed in the composer to be sent again.
  idleConversation();
  let accept!: () => void;
  mock.intent.mockImplementation(
    (_conversation, _type, payload, _revision, commandId) =>
      new Promise((resolve) => {
        accept = () =>
          resolve({
            command_id: commandId,
            conversation_id: 'conversation-a',
            submission_id: payload.submission_id,
            status: 'accepted',
          });
      }),
  );
  await act(async () => conversation());
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  });
  expect(mock.intent).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), {
    target: { value: 'Original queued draft Which one is the oldest?' },
  });
  await act(async () => accept());
  expect(mock.drafts.get('conversation-a')?.text).toBe(
    'Which one is the oldest?',
  );
});

it('sends the files again with Send again, not their names as text (B136)', async () => {
  idleConversation();
  mock.download.mockResolvedValue(new Blob(['notes'], { type: 'text/plain' }));
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:attachment');
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
  mock.state.projection = {
    rows: [
      {
        id: 'user:with-file',
        message_id: 'with-file',
        role: 'user',
        blocks: [
          { type: 'text', text: 'Summarise this file' },
          {
            id: 'attachment:one',
            type: 'attachment',
            attachment_ref: 'conversation-a:attachment-one',
            name: 'notes.txt',
            mime_type: 'application/octet-stream',
            size_bytes: 12,
            revision: 'rev-1',
          },
        ],
      },
    ],
    generation: {
      generation_id: 'stopped-run',
      quiesced: true,
      can_stop: false,
      status: 'stopped',
    },
  } as unknown as Snapshot;
  mock.intent.mockImplementation(
    async (_conversation, _type, payload, _revision, commandId) => ({
      command_id: commandId,
      conversation_id: 'conversation-a',
      submission_id: payload.submission_id,
      status: 'accepted',
    }),
  );
  await act(async () => conversation());
  const log = screen.getByRole('log', { name: 'Conversation' });
  await act(async () => {
    fireEvent.click(within(log).getByRole('button', { name: 'Send again' }));
  });
  expect(mock.intent).toHaveBeenCalledWith(
    'conversation-a',
    'conversation.submit',
    expect.objectContaining({
      text: 'Summarise this file',
      attachment_refs: ['conversation-a:attachment-one'],
      // Run again in place: the server sets the last turn aside, never a second copy of the message.
      retry: true,
    }),
    '1',
    expect.any(String),
  );
});

it('shows welcome examples without a request and fills the composer with one instead of sending (U17)', async () => {
  idleConversation();
  mock.drafts.set('conversation-a', { text: '', attachments: [] });
  await act(async () => conversation());
  expect(mock.intent).not.toHaveBeenCalled();
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Plan a weekly brief',
      }),
    );
  });
  expect(mock.open).not.toHaveBeenCalled();
  expect(mock.intent).not.toHaveBeenCalled();
  expect(mock.drafts.get('conversation-a')?.text).toBe(
    'Create a disabled workflow for a weekly research briefing',
  );
});

it('creates a conversation and submits a Home example through one user action', async () => {
  idleConversation();
  mock.state.selectedConversationId = null;
  mock.state.conversation = null;
  mock.state.workspace!.conversation_id = 'first-chat';
  mock.state.workspace!.revision = '1';
  mock.intent.mockImplementation(
    async (_conversation, type, payload, _revision, commandId) =>
      type === 'conversation.create'
        ? {
            command_id: commandId,
            conversation_id: 'first-chat',
            status: 'completed',
          }
        : {
            command_id: commandId,
            conversation_id: 'first-chat',
            submission_id: payload.submission_id,
            status: 'accepted',
          },
  );
  let rendered!: ReturnType<typeof conversation>;
  await act(async () => {
    rendered = conversation();
  });
  expect(mock.intent).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'New chat with example' }),
    ),
  );
  await act(async () => rendered.rerender(<Conversation onPanel={vi.fn()} />));
  await waitFor(() => expect(mock.intent).toHaveBeenCalledTimes(2));
  expect(mock.intent.mock.calls[0][1]).toBe('conversation.create');
  expect(mock.intent.mock.calls[1][1]).toBe('conversation.submit');
  expect(mock.intent.mock.calls[1][2]).toMatchObject({
    text: 'What do you remember about my current projects?',
    attachment_refs: [],
    write_targets: [],
  });
  expect(mock.drafts.get('first-chat')?.text).toBe('');
});

it('opens the chat New chat made and nobody used instead of making another', async () => {
  idleConversation();
  mock.state.selectedConversationId = null;
  mock.state.conversation = null;
  const listed = (id: string, title: string, updated_at = '2026-10-08') =>
    ({
      id,
      title,
      revision: '0',
      pinned: false,
      updated_at,
    }) as ConversationRow;
  let made = 0;
  mock.intent.mockImplementation(
    async (_target, _type, _payload, _revision, commandId) => ({
      command_id: commandId,
      conversation_id: `made-${++made}`,
      status: 'completed',
    }),
  );
  const newChat = () =>
    act(async () =>
      fireEvent.click(screen.getByRole('button', { name: 'New chat' })),
    );
  await act(async () => conversation());
  await newChat();
  expect(mock.navigate).toHaveBeenLastCalledWith('/conversations/made-1');
  // An empty chat from before (it may hold a draft saved elsewhere) is left alone.
  mock.state.conversations = [
    listed('used', 'Trip plans'),
    listed('made-1', 'New conversation', '2026-10-01'),
    listed('from-before', 'New conversation', '2026-10-07'),
  ];

  await newChat();
  expect(mock.intent).toHaveBeenCalledTimes(1);
  expect(mock.selectConversation).toHaveBeenLastCalledWith('made-1');
  expect(mock.navigate).toHaveBeenLastCalledWith('/conversations/made-1');

  // A draft, or a first message, makes it used: New chat makes another.
  mock.drafts.set('made-1', { text: 'Half a thought', attachments: [] });
  await newChat();
  expect(mock.intent).toHaveBeenCalledTimes(2);
  expect(mock.intent.mock.calls[1][1]).toBe('conversation.create');
  expect(mock.navigate).toHaveBeenLastCalledWith('/conversations/made-2');
  mock.state.conversations = [
    listed('made-1', 'Half a thought'),
    listed('made-2', 'New conversation'),
  ];
  mock.state.selectedConversationId = 'made-2';
  mock.state.projection = {
    rows: [row('made-2-first', 'user', 'Hello')],
  } as unknown as Snapshot;
  await newChat();
  expect(mock.intent).toHaveBeenCalledTimes(3);
  expect(mock.navigate).toHaveBeenLastCalledWith('/conversations/made-3');
});

it('opens Find with Ctrl or Cmd+F, so the words meant for it never reach the composer', async () => {
  idleConversation();
  mock.drafts.set('conversation-a', { text: '', attachments: [] });
  await act(async () => conversation());
  const composer = screen.getByRole('textbox', { name: 'Message' });
  composer.focus();
  expect(fireEvent.keyDown(composer, { key: 'f', ctrlKey: true })).toBe(false);
  expect(mock.open).toHaveBeenCalledWith(
    expect.objectContaining({ title: 'Find in conversation' }),
  );
  expect(fireEvent.keyDown(document.body, { key: 'F', metaKey: true })).toBe(
    false,
  );
  expect(mock.open).toHaveBeenCalledTimes(2);

  // Another text field (a panel's editor) or a dialog keeps its own Ctrl+F.
  const editor = document.createElement('textarea');
  const dialog = document.createElement('div');
  dialog.setAttribute('role', 'dialog');
  dialog.tabIndex = -1;
  document.body.append(editor, dialog);
  expect(fireEvent.keyDown(editor, { key: 'f', ctrlKey: true })).toBe(true);
  expect(fireEvent.keyDown(dialog, { key: 'f', ctrlKey: true })).toBe(true);
  editor.remove();
  dialog.remove();
  expect(mock.open).toHaveBeenCalledTimes(2);
  expect(composer).toHaveValue('');
  expect(mock.intent).not.toHaveBeenCalled();
});

it('starts a chat with a draft that waits in the composer and is never sent', async () => {
  idleConversation();
  mock.state.selectedConversationId = null;
  mock.state.conversation = null;
  mock.state.workspace!.conversation_id = 'first-chat';
  mock.intent.mockImplementation(
    async (_target, _type, _payload, _revision, commandId) => ({
      command_id: commandId,
      conversation_id: 'first-chat',
      status: 'completed',
    }),
  );
  let rendered!: ReturnType<typeof conversation>;
  await act(async () => {
    rendered = conversation();
  });
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'New chat with a draft' }),
    ),
  );
  await act(async () => rendered.rerender(<Conversation onPanel={vi.fn()} />));
  expect(mock.intent).toHaveBeenCalledTimes(1);
  expect(mock.intent.mock.calls[0][1]).toBe('conversation.create');
  expect(mock.drafts.get('first-chat')?.text).toBe('Create a design: ');
});

it('keeps a Home example as a local draft until a model becomes ready', async () => {
  idleConversation();
  mock.state.selectedConversationId = null;
  mock.state.conversation = null;
  mock.state.workspace!.conversation_id = 'first-chat';
  mock.state.workspace!.actions = [{ action: 'send', ready: false }];
  mock.intent.mockImplementation(
    async (_target, type, payload, _revision, commandId) =>
      type === 'conversation.create'
        ? {
            command_id: commandId,
            conversation_id: 'first-chat',
            status: 'completed',
          }
        : {
            command_id: commandId,
            conversation_id: 'first-chat',
            submission_id: payload.submission_id,
            status: 'accepted',
          },
  );
  let rendered!: ReturnType<typeof conversation>;
  await act(async () => {
    rendered = conversation();
  });
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'New chat with example' }),
    ),
  );
  expect(mock.intent).toHaveBeenCalledTimes(1);
  expect(mock.drafts.get('first-chat')?.text).toBe(
    'What do you remember about my current projects?',
  );
  mock.state.workspace!.actions = [{ action: 'send', ready: true }];
  await act(async () => rendered.rerender(<Conversation onPanel={vi.fn()} />));
  await waitFor(() => expect(mock.intent).toHaveBeenCalledTimes(2));
  expect(mock.intent.mock.calls[1][1]).toBe('conversation.submit');
});

it('sends to the selected workspace from one click with the target fixed in the command', async () => {
  idleConversation();
  mock.state.workspace!.resources = [
    {
      resource_ref: 'conversation-a:binding',
      conversation_revision: '1',
      binding: {
        binding_id: 'binding',
        kind: 'workspace',
        resource_id: 'workspace',
        role: 'primary',
        revision: '2',
      },
      title: 'Project',
      resource_revision: '3',
      available: true,
    },
  ];
  mock.drafts.set('conversation-a', {
    text: 'Update the summary',
    attachments: [],
  });
  mock.intent.mockImplementation(
    async (_conversation, _type, payload, _revision, commandId) => ({
      command_id: commandId,
      conversation_id: 'conversation-a',
      submission_id: payload.submission_id,
      status: 'accepted',
    }),
  );
  await act(async () => conversation());
  await act(async () =>
    fireEvent.keyDown(screen.getByRole('button', { name: 'Folder target' }), {
      key: 'Enter',
    }),
  );
  fireEvent.click(
    within(screen.getByRole('menu')).getByRole('menuitem', { name: 'Project' }),
  );
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  });
  expect(mock.open).not.toHaveBeenCalled();
  expect(mock.intent).toHaveBeenCalledTimes(1);
  expect(mock.intent.mock.calls[0][2]).toMatchObject({
    text: 'Update the summary',
    write_targets: [
      {
        kind: 'workspace',
        binding_id: 'binding',
        resource_id: 'workspace',
        binding_revision: '2',
        resource_revision: '3',
      },
    ],
  });
});

it('names the composer and explains why sending is unavailable', async () => {
  idleConversation();
  mock.state.workspace!.actions = [{ action: 'send', ready: false }];
  mock.drafts.set('conversation-a', {
    text: 'Keep this draft',
    attachments: [],
  });
  await act(async () => conversation());
  const composer = screen.getByRole('form', { name: 'Message composer' });
  const reason = screen.getByText(/choose a model to send/i);
  expect(composer).toContainElement(reason);
  expect(
    screen.getByRole('textbox', { name: 'Message' }),
  ).toHaveAccessibleDescription(reason.textContent ?? '');
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
});

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
    fireEvent.click(screen.getByRole('button', { name: 'Model' })),
  );
  const picker = screen.getByRole('dialog', { name: 'Choose a model' });
  expect(
    within(picker).getByRole('combobox', { name: 'Search models' }),
  ).toHaveFocus();
  await act(async () =>
    fireEvent.click(
      within(picker).getByRole('option', { name: 'Chosen model' }),
    ),
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
    fireEvent.click(screen.getByRole('menuitem', { name: /^Block/ })),
  );
  expect(mock.intent.mock.calls[0][2].approval_mode).toBe('block');
  expect(screen.getByRole('alert')).toHaveTextContent(/review|changed|retry/i);
  expect(mock.drafts.get('conversation-a')?.text).toBe('Keep my draft');
  // The shield's glyph shows the mode; its description names it.
  expect(screen.getByRole('button', { name: 'Approvals' })).toHaveAttribute(
    'aria-description',
    'Approvals: Ask',
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
    fireEvent.click(screen.getByRole('button', { name: 'Check Resume' }));
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
      "This browser can't store what Row-Bot needs",
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
    fireEvent.click(screen.getByRole('button', { name: 'Check Resume' }));
  });
  expect(commandReceipts.read(key)).toEqual(saved);
  expect(mock.intent).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Stop checking' }));
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
    screen.queryByRole('button', { name: 'Check Resume' }),
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
    fireEvent.click(screen.getByRole('button', { name: 'Check Resume' }));
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
    fireEvent.click(screen.getByRole('button', { name: 'Check message' }));
  });
  expect(mock.receipt).toHaveBeenCalledWith(saved.commandId);
  expect(mock.intent).toHaveBeenCalledTimes(1);
  expect(commandReceipts.read(key)).toBeNull();
  expect(mock.drafts.get('conversation-a')?.text).toBe('Edited current draft');
  expect(mock.setDraft).not.toHaveBeenCalled();
});

it('keeps the admitted user row visible until the exact durable row is observed', async () => {
  idleConversation();
  mock.intent.mockImplementation(
    async (_id, _type, payload, _revision, commandId) => ({
      command_id: commandId,
      submission_id: payload.submission_id,
      conversation_id: 'conversation-a',
      status: 'accepted',
    }),
  );
  let rendered!: ReturnType<typeof conversation>;
  await act(async () => {
    rendered = conversation();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  });
  const submissionId = mock.intent.mock.calls[0][2].submission_id;
  expect(
    screen.getByRole('article', { name: 'You message awaiting confirmation' }),
  ).toHaveTextContent('Original queued draft');

  rendered.rerender(<Conversation onPanel={vi.fn()} />);
  expect(
    screen.getByRole('article', { name: 'You message awaiting confirmation' }),
  ).toBeVisible();

  mock.state.projection = {
    rows: [
      {
        id: `user:submission:${submissionId}`,
        message_id: submissionId,
        role: 'user',
        blocks: [{ type: 'text', text: 'Original queued draft' }],
      },
    ],
    generation: null,
  } as unknown as Snapshot;
  await act(async () => {
    rendered.rerender(<Conversation onPanel={vi.fn()} />);
  });
  expect(
    screen.queryByRole('article', {
      name: 'You message awaiting confirmation',
    }),
  ).not.toBeInTheDocument();
  expect(screen.getAllByRole('article', { name: 'You message' })).toHaveLength(
    1,
  );
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
    "This browser can't store what Row-Bot needs",
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
    screen.queryByRole('button', { name: 'Check message' }),
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
    fireEvent.click(screen.getByRole('button', { name: 'Check message' }));
  });
  expect(commandReceipts.read(key)).toEqual(saved);
  expect(mock.intent).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Stop checking' }));
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
      "This browser can't store what Row-Bot needs",
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
  fireEvent.click(screen.getByRole('button', { name: 'Stop checking' }));
  expect(mock.open.mock.calls.at(-1)?.[0].confirmLabel).toBe('Stop checking');
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
    "This browser can't store what Row-Bot needs",
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
      screen.getByRole('button', { name: 'Check waiting message' }),
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
      screen.getByRole('button', { name: 'Check waiting message' }),
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
    screen.queryByRole('button', { name: 'Check waiting message' }),
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
      screen.getByRole('button', { name: 'Check waiting message' }),
    );
  });
  expect(mock.intent).not.toHaveBeenCalled();
  expect(commandReceipts.read(key)).toEqual(saved);
  fireEvent.click(screen.getByRole('button', { name: 'Stop checking' }));
  expect(mock.open.mock.calls.at(-1)?.[0].description).toContain(
    'sending it again could make it appear twice',
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
  expect(
    screen.getByRole('group', { name: 'Generated result' }),
  ).toBeInTheDocument();
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
  expect(
    screen.getByRole('list', { name: 'Conversation search results' }),
  ).toBeVisible();
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

it('renders a folded create_chart result with its parent message (B4)', async () => {
  activeConversation();
  mock.state.projection = {
    ...mock.state.projection!,
    rows: [
      {
        id: 'assistant:checkpoint:a',
        message_id: 'a',
        role: 'assistant',
        blocks: [{ type: 'text', text: 'Charting the numbers now.' }],
        tool_call_ids: ['call-chart'],
      },
      {
        id: 'tool:checkpoint:b',
        message_id: 'b',
        role: 'tool',
        tool_call_id: 'call-chart',
        trace_parent_id: 'assistant:checkpoint:a',
        blocks: [
          {
            id: 'block:chart',
            type: 'chart',
            figure_json: '{"data":[],"layout":{}}',
            text: 'Sample revenue vs costs',
          },
        ],
      },
    ],
  } as unknown as Snapshot;
  await act(async () => {
    conversation();
  });
  const message = screen.getByRole('article', { name: 'Row-Bot message' });
  expect(
    within(message).getByText('Sample revenue vs costs', {
      selector: 'figcaption',
    }),
  ).toBeVisible();
  expect(
    screen.queryByRole('article', { name: 'Tool result message' }),
  ).toBeNull();
});

it('floats Context below 740px and docks it again only from 780px', () => {
  expect(isNarrowChat(718, false)).toBe(true);
  expect(isNarrowChat(760, false)).toBe(false);
  // Hysteresis: a floating chat stays floating until it is clearly wide.
  expect(isNarrowChat(760, true)).toBe(true);
  expect(isNarrowChat(780, true)).toBe(false);
});

it('says so when an attached image cannot be seen and offers a vision model (decision 11)', async () => {
  idleConversation();
  mock.state.workspace!.model_status = {
    state: 'ready',
    local: false,
    sees_images: false,
  };
  mock.drafts.set('conversation-a', {
    text: 'What is in this picture?',
    attachments: [
      {
        attachment_ref: 'attachment-photo',
        name: 'photo.png',
        mime_type: 'image/png',
        size_bytes: 1024,
        revision: '1',
      },
    ],
  } as never);
  await act(async () => conversation());
  expect(screen.getByText(/can't see images\./)).toBeVisible();
  fireEvent.click(
    screen.getByRole('button', { name: 'Choose a vision model' }),
  );
  expect(mock.navigate).toHaveBeenCalledWith('/settings/models#vision');
});

it('offers the fix that matches why the model cannot answer', async () => {
  idleConversation();
  mock.state.workspace!.actions = [{ action: 'send', ready: false }];
  mock.state.workspace!.model_status = {
    state: 'unavailable',
    reason: "Ollama isn't running",
    fix: 'reconnect',
    local: true,
  };
  await act(async () => conversation());
  expect(
    screen.getByText(/The model is unavailable: Ollama isn't running\./),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }));
  expect(mock.navigate).toHaveBeenCalledWith('/settings/providers');
});

function uploaded(file: File) {
  return {
    attachment_ref: `conversation-a:${file.name}`,
    name: file.name,
    mime_type: file.type,
    size_bytes: file.size,
    revision: '1',
  };
}

it('attaches a pasted screenshot with a readable name (parity row 1)', async () => {
  idleConversation();
  mock.upload.mockImplementation(async (_id: string, file: File) =>
    uploaded(file),
  );
  conversation();
  const composer = screen.getByRole('textbox', { name: 'Message' });
  const shot = new File([new Uint8Array([137, 80, 78, 71])], 'image.png', {
    type: 'image/png',
  });
  await act(async () => {
    fireEvent.paste(composer, {
      clipboardData: { files: [shot], getData: () => '' },
    });
  });
  expect(mock.upload).toHaveBeenCalledOnce();
  const sent = mock.upload.mock.calls[0][1] as File;
  expect(sent.name).toMatch(/^Pasted image \d{4}-\d{2}-\d{2} [\d.]+\.png$/);
  expect(mock.drafts.get('conversation-a')?.attachments).toHaveLength(1);
});

it('attaches several dropped files and names the one over the limit (U18)', async () => {
  idleConversation();
  mock.upload.mockImplementation(async (_id: string, file: File) =>
    uploaded(file),
  );
  const { container } = conversation();
  const field = container.querySelector('.composer-field')!;
  const small = new File(['notes'], 'notes.txt', { type: 'text/plain' });
  const other = new File(['more'], 'more.txt', { type: 'text/plain' });
  const huge = new File(['x'], 'film.mov', { type: 'video/quicktime' });
  Object.defineProperty(huge, 'size', { value: 40 * 1024 * 1024 });
  const dataTransfer = { types: ['Files'], files: [small, other, huge] };
  fireEvent.dragEnter(field, { dataTransfer });
  expect(field).toHaveAttribute('data-dragging', 'true');
  expect(container.querySelector('.composer-drop')).toHaveTextContent(
    'Drop to attach · up to 25 MB each',
  );
  await act(async () => {
    fireEvent.drop(field, { dataTransfer });
  });
  expect(field).not.toHaveAttribute('data-dragging');
  expect(mock.upload.mock.calls.map((call) => (call[1] as File).name)).toEqual([
    'notes.txt',
    'more.txt',
  ]);
  expect(
    screen.getByText(/“film.mov” is 40 MB; files can be up to 25 MB./),
  ).toBeVisible();
});

it('shows each dropped file uploading on its own tile, a failure there, and Retry (B232)', async () => {
  idleConversation();
  let report!: (sent: number) => void;
  let finish!: (value: ReturnType<typeof uploaded>) => void;
  const notes = new File(['notes'], 'notes.txt', { type: 'text/plain' });
  const draft = new File(['draft'], 'draft.txt', { type: 'text/plain' });
  mock.upload
    .mockImplementationOnce(
      (_id: string, _file: File, _signal: AbortSignal, progress) => {
        report = progress;
        return new Promise((resolve) => (finish = resolve));
      },
    )
    .mockRejectedValueOnce(new TypeError('Failed to fetch'))
    .mockImplementationOnce(async (_id: string, file: File) => uploaded(file));
  const { container } = conversation();
  await act(async () => {
    fireEvent.drop(container.querySelector('.composer-field')!, {
      dataTransfer: { types: ['Files'], files: [notes, draft] },
    });
  });
  const list = screen.getByRole('list', { name: 'Attachments' });
  act(() => report(3));
  expect(
    within(list).getByRole('progressbar', { name: 'Uploading notes.txt' }),
  ).toHaveAttribute('aria-valuenow', '60');
  await act(async () => finish(uploaded(notes)));
  expect(await within(list).findByRole('alert')).toHaveTextContent(
    /^Couldn’t upload draft\.txt\./,
  );
  expect(
    within(list).getByRole('button', { name: 'Preview notes.txt' }),
  ).toBeVisible();
  expect(mock.drafts.get('conversation-a')?.attachments).toMatchObject([
    { name: 'notes.txt' },
  ]);
  const retry = within(list).getByRole('button', { name: 'Retry draft.txt' });
  await waitFor(() => expect(retry).toBeEnabled());
  await act(async () => {
    fireEvent.click(retry);
  });
  await waitFor(() =>
    expect(mock.drafts.get('conversation-a')?.attachments).toMatchObject([
      { name: 'notes.txt' },
      { name: 'draft.txt' },
    ]),
  );
  expect(within(list).queryByRole('alert')).toBeNull();
});

it('cancels an upload when its tile is removed (B232)', async () => {
  idleConversation();
  let signal!: AbortSignal;
  mock.upload.mockImplementationOnce(
    (_id: string, _file: File, current: AbortSignal) => {
      signal = current;
      return new Promise((_resolve, reject) =>
        current.addEventListener('abort', () =>
          reject(new DOMException('Cancelled', 'AbortError')),
        ),
      );
    },
  );
  const { container } = conversation();
  const notes = new File(['notes'], 'notes.txt', { type: 'text/plain' });
  await act(async () => {
    fireEvent.drop(container.querySelector('.composer-field')!, {
      dataTransfer: { types: ['Files'], files: [notes] },
    });
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Remove notes.txt' }));
  });
  expect(signal.aborted).toBe(true);
  expect(screen.queryByRole('list', { name: 'Attachments' })).toBeNull();
  expect(mock.drafts.get('conversation-a')?.attachments ?? []).toEqual([]);
});

function slashCommands() {
  const command = (id: string, label: string) => ({
    id,
    token: `/${id}`,
    aliases: [],
    label,
    description: label,
    icon: 'flag',
    category: 'Chat',
    argument_mode: 'prefix',
    argument_hint: '',
    handler_kind: id,
    skill_id: null,
  });
  return [
    command('goal', 'Goal'),
    command('reasoning', 'Reasoning'),
    command('profile', 'Agent Profile'),
    command('agent', 'Start Agent'),
  ];
}

function withCommands() {
  idleConversation();
  const composer = {
    conversation_id: 'conversation-a',
    composer_revision: 'composer-1',
    library: { availability: 'available', revision: 'library-1' },
    active_skills: [],
    suggestions: [],
    commands: slashCommands(),
  };
  Object.assign(mock.state.workspace!, {
    composer,
    profiles: [{ id: 'writer-profile', label: 'Writer' }],
    reasoning: {
      model_ref: 'fixture/model',
      capability_revision: 'caps-1',
      available: true,
      selection: { kind: 'provider_default' },
      choices: [
        { selection: { kind: 'effort', effort: 'low' }, label: 'Low' },
        { selection: { kind: 'effort', effort: 'high' }, label: 'High' },
      ],
      supports_budget: false,
      budget_min: 0,
      budget_max: 0,
    },
  });
  mock.composer.mockResolvedValue(composer);
  mock.intent.mockResolvedValue({ status: 'completed' });
}

async function sendText(text: string) {
  mock.drafts.set('conversation-a', { text, attachments: [] });
  conversation();
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  });
}

it('sets the thinking level from /reasoning high instead of sending it (B112)', async () => {
  withCommands();
  await sendText('/reasoning high');
  expect(mock.intent).toHaveBeenCalledWith(
    'conversation-a',
    'conversation.controls',
    expect.objectContaining({
      reasoning: {
        model_ref: 'fixture/model',
        capability_revision: 'caps-1',
        selection: { kind: 'effort', effort: 'high' },
      },
    }),
    '1',
  );
  expect(mock.intent).not.toHaveBeenCalledWith(
    'conversation-a',
    'conversation.submit',
    expect.anything(),
    expect.anything(),
    expect.anything(),
  );
  expect(mock.drafts.get('conversation-a')?.text).toBe('');
});

it('says which levels exist when /reasoning names none, and keeps the text', async () => {
  withCommands();
  await sendText('/reasoning turbo');
  expect(mock.intent).not.toHaveBeenCalled();
  expect(
    screen.getByText(/isn't a thinking level for this model. Try Low, High./),
  ).toBeVisible();
  expect(mock.drafts.get('conversation-a')?.text).toBe('/reasoning turbo');
});

it('switches the profile with /profile', async () => {
  withCommands();
  await sendText('/profile writer');
  expect(mock.intent).toHaveBeenCalledWith(
    'conversation-a',
    'conversation.controls',
    expect.objectContaining({ profile_id: 'writer-profile' }),
    '1',
  );
});

it('starts a delegated agent with /agent and its task', async () => {
  withCommands();
  await sendText('/agent Summarise tide tables');
  expect(mock.intent).toHaveBeenCalledWith(
    'conversation-a',
    'agent.start',
    { text: 'Summarise tide tables' },
    '1',
  );
});

it('starts a goal at once with /goal and no turn limit unless Agent runtime sets one (B243)', async () => {
  withCommands();
  mock.goals.mockResolvedValue({
    conversation_id: 'conversation-a',
    current_goal_id: null,
    current_revision: 'none',
    default_max_turns: 0,
    items: [],
  });
  mock.reviewGoal.mockImplementation(async (_id: string, payload) => ({
    ...payload,
    review_id: 'review-1',
  }));
  mock.executeGoal.mockResolvedValue({ status: 'completed' });
  await sendText('/goal Draft three posts about tides');
  expect(mock.reviewGoal).toHaveBeenCalledWith(
    'conversation-a',
    expect.objectContaining({
      operation: 'start',
      objective: 'Draft three posts about tides',
      max_turns: null,
    }),
  );
  expect(mock.executeGoal.mock.calls[0][1]).toMatchObject({
    type: 'goal.control',
    payload: { operation: 'start', review_id: 'review-1' },
  });
  expect(mock.drafts.get('conversation-a')?.text).toBe('');
});

it('removes a default skill from this chat with Undo in the notice (B236)', async () => {
  withCommands();
  const skill = {
    id: 'proactive_agent',
    display_name: 'Proactive Agent',
    icon: '✨',
    description: 'Plans ahead.',
    library_source: 'bundled',
    source: 'default',
    removable: true,
  };
  const before = {
    ...mock.state.workspace!.composer!,
    active_skills: [skill],
  };
  const after = {
    ...before,
    composer_revision: 'composer-2',
    active_skills: [],
  };
  mock.state.workspace!.composer = before as never;
  mock.composer.mockResolvedValue(before);
  mock.intent.mockImplementation(
    async (_id, _type, _payload, _revision, commandId: string) => {
      mock.composer.mockResolvedValue(after);
      return { status: 'completed', command_id: commandId };
    },
  );
  conversation();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Remove Proactive Agent from this chat',
      }),
    ),
  );
  expect(mock.intent).toHaveBeenCalledWith(
    'conversation-a',
    'conversation.skills',
    expect.objectContaining({
      action: 'remove',
      composer_revision: 'composer-1',
      skill_id: 'proactive_agent',
    }),
    '1',
    expect.any(String),
  );
  expect(mock.notify).toHaveBeenCalledWith(
    'Removed from this chat.',
    undefined,
    expect.objectContaining({ label: 'Undo' }),
  );
  // Undo brings the skill back against the chat's fresh composer revision.
  const undo = mock.notify.mock.calls.at(-1)![2] as { onAction(): void };
  await act(async () => undo.onAction());
  expect(mock.intent).toHaveBeenLastCalledWith(
    'conversation-a',
    'conversation.skills',
    expect.objectContaining({
      action: 'activate',
      composer_revision: 'composer-2',
      skill_id: 'proactive_agent',
    }),
    '1',
    expect.any(String),
  );
});

function HostedConversation() {
  const { host, parking } = useContextHostOwner();
  return (
    <ContextHostContext.Provider value={host}>
      <ConversationView onPanel={vi.fn()} />
      <div ref={parking} hidden />
    </ContextHostContext.Provider>
  );
}

it('pins Conversation details open in a wide chat, beside the column, until its toggle hides it (B221)', async () => {
  idleConversation();
  localStorage.removeItem('row-bot.context-hidden.v1');
  await act(async () => {
    render(<HostedConversation />);
  });
  const workspace = document.querySelector('.chat-workspace')!;
  const details = screen.getByRole('complementary', {
    name: 'Conversation details',
  });
  // It floats pinned open (no column of its own) and the column moves aside.
  expect(details.closest('.context-card')).toHaveClass('context-card-pinned');
  expect(workspace).toHaveClass('details-wide', 'details-open');
  const toggle = screen.getByRole('button', { name: 'Conversation details' });
  expect(toggle).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(toggle);
  expect(workspace).toHaveClass('details-wide');
  expect(workspace).not.toHaveClass('details-open');
  expect(details).not.toBeVisible();
  expect(localStorage.getItem('row-bot.context-hidden.v1')).toBe('1');
  fireEvent.click(toggle);
  expect(workspace).toHaveClass('details-open');
  expect(details).toBeVisible();
  expect(localStorage.getItem('row-bot.context-hidden.v1')).toBeNull();
});

const row = (
  id: string,
  role: 'user' | 'assistant',
  text: string,
  extra: Partial<TranscriptRow> = {},
) =>
  ({
    id,
    message_id: id,
    role,
    blocks: [{ type: 'text', text }],
    ...extra,
  }) as unknown as TranscriptRow;

function settledRows(rows: TranscriptRow[]) {
  mock.state.projection = {
    rows,
    generation: {
      generation_id: 'run-a',
      quiesced: true,
      can_stop: false,
      status: 'completed',
    },
  } as unknown as Snapshot;
}

const avatarOf = (seed: string) =>
  render(<AgentAvatar seed={seed} />)
    .container.querySelector('.agent-avatar')!
    .getAttribute('data-avatar');

it('marks each turn’s start with its speaker, never a follow-up (B271)', async () => {
  idleConversation();
  settledRows([
    row('u1', 'user', 'Plan the launch.'),
    row('a1', 'assistant', 'Here is a plan.'),
    row('a2', 'assistant', 'And a timeline.'),
    row('u2', 'user', 'Thanks.'),
    row('a3', 'assistant', 'Glad to help.'),
  ]);
  await act(async () => {
    conversation();
  });
  const marked = screen
    .getAllByRole('article')
    .map((article) => [
      article.getAttribute('data-row-id'),
      article.querySelector('.turn-marker-user')
        ? 'you'
        : article.querySelector('.turn-marker-buddy')
          ? 'buddy'
          : '',
    ]);
  expect(marked).toEqual([
    ['u1', 'you'],
    ['a1', 'buddy'],
    ['a2', ''],
    ['u2', 'you'],
    ['a3', 'buddy'],
  ]);
});

it('links an agent’s own conversation back to its parent and marks its replies with its icon (B242, B271)', async () => {
  idleConversation();
  mock.state.conversation = {
    id: 'conversation-a',
    title: 'Pricing scan',
    revision: '1',
    pinned: false,
    parent_conversation_id: 'parent-a',
  } as typeof mock.state.conversation;
  settledRows([
    row('u1', 'user', 'Also cover euros.'),
    row('a1', 'assistant', 'Adding an EU column.'),
  ]);
  const own: DelegatedRun = {
    run_id: 'run-own',
    parent_conversation_id: 'parent-a',
    child_conversation_id: 'conversation-a',
    name: 'Pricing scan',
    status: 'running',
    summary: '',
    profile_id: 'profile-7',
  };
  mock.delegatedPage.mockImplementation(async (conversationId: string) => ({
    conversation_id: conversationId,
    parent_conversation_id: 'parent-a',
    parent_title: 'Q4 launch plan',
    own_run: own,
    items: [],
    next_cursor: null,
    has_more: false,
  }));
  await act(async () => {
    conversation();
  });
  const back = await screen.findByRole('link', {
    name: 'Back to Q4 launch plan',
  });
  expect(back).toHaveAttribute('href', '/app-v2/conversations/parent-a');
  const reply = screen.getByRole('article', { name: 'Row-Bot message' });
  const icon = reply.querySelector('.turn-marker .agent-avatar');
  // The same icon as the card's "This agent" row and the breadcrumb.
  expect(icon?.getAttribute('data-avatar')).toBe(
    avatarOf(agentSeed('profile-7', 'run-own')),
  );
  expect(
    document
      .querySelector('header.conversation-heading .agent-avatar')
      ?.getAttribute('data-avatar'),
  ).toBe(icon?.getAttribute('data-avatar'));
  // The person's own follow-up keeps the person marker.
  expect(
    screen
      .getByRole('article', { name: 'You message' })
      .querySelector('.turn-marker-user'),
  ).not.toBeNull();
});

it('shows the agents a turn started as stubs that update in place and open each agent (B241)', async () => {
  idleConversation();
  const started = {
    group_id: 'group-agents',
    name: 'delegate_work',
    kind: 'generic',
    group_order: 0,
    status: 'succeeded',
    counts: { succeeded: 2 },
    items: ['run-1', 'run-2'].map((runId, index) => ({
      item_id: `call-${index}`,
      group_id: 'group-agents',
      call_id: `call-${index}`,
      result_message_id: `result-${index}`,
      call_order: index,
      group_order: 0,
      canonical_name: 'delegate_work',
      group_name: 'delegate_work',
      group_kind: 'generic',
      status: 'succeeded',
      safe_input: '',
      safe_summary: '',
      summary_truncated: false,
      content_ref: '',
      specialization: {
        kind: 'delegated_agent',
        agent_runs: [
          {
            run_id: runId,
            display_name: index ? 'Launch email' : 'Pricing scan',
            status: 'queued',
            profile_id: index ? '' : 'profile-7',
          },
        ],
      },
    })),
  } as unknown as NonNullable<TranscriptRow['traces']>[number];
  settledRows([
    row('u1', 'user', 'Get the launch moving.'),
    row('a1', 'assistant', 'I started two agents.', { traces: [started] }),
  ]);
  const feed = (pricing: string, email: string): DelegatedActivityView => ({
    conversation_id: 'conversation-a',
    parent_conversation_id: null,
    items: [
      {
        run_id: 'run-1',
        parent_conversation_id: 'conversation-a',
        child_conversation_id: 'child-1',
        name: 'Pricing scan',
        status: pricing,
        summary: '',
        profile_id: 'profile-7',
      },
      {
        run_id: 'run-2',
        parent_conversation_id: 'conversation-a',
        child_conversation_id: 'child-2',
        name: 'Launch email',
        status: email,
        summary: 'The model could not be reached.',
        profile_id: '',
      },
    ],
    next_cursor: null,
    has_more: false,
  });
  mock.delegatedPage.mockResolvedValue(feed('running', 'running'));
  let view!: ReturnType<typeof conversation>;
  await act(async () => {
    view = conversation();
  });
  const stubs = screen.getByRole('list', { name: 'Agents started' });
  expect(
    await within(stubs).findByRole('button', { name: 'Pricing scan, Working' }),
  ).toBeVisible();
  expect(
    within(stubs).getByRole('button', { name: 'Launch email, Working' }),
  ).toBeVisible();
  // One row for the agents started together, each with the panel's icon.
  expect(
    within(stubs)
      .getByRole('button', { name: /^Pricing scan/ })
      .querySelector('.agent-avatar')
      ?.getAttribute('data-avatar'),
  ).toBe(avatarOf(agentSeed('profile-7', 'run-1')));

  // The feed moves on: the same stubs change in place.
  mock.delegatedPage.mockResolvedValue(feed('completed', 'failed'));
  mock.state.activity = [
    {
      event: {
        type: 'agent.activity',
        event_id: 'agent-event-1',
        payload: { run_id: 'run-2', status: 'failed' },
      },
    },
  ] as unknown as typeof mock.state.activity;
  mock.version++;
  await act(async () => {
    view.rerender(<Conversation onPanel={vi.fn()} />);
  });
  expect(
    await within(stubs).findByRole('button', { name: 'Pricing scan, Done' }),
  ).toBeVisible();
  const failed = within(stubs).getByRole('button', {
    name: 'Launch email, Failed',
  });
  // A failed stub says why.
  expect(failed).toHaveAttribute(
    'aria-description',
    'The model could not be reached.',
  );
  expect(within(stubs).getAllByRole('listitem')).toHaveLength(2);
  await act(async () => fireEvent.click(failed));
  expect(mock.selectConversation).toHaveBeenCalledWith('child-2');
});

it('re-reads its sidebar row when its agents report in (B302)', async () => {
  await mock.selectConversation('conversation-a');
  const view = conversation();
  mock.refreshListedConversation.mockClear();
  mock.state.activity = [
    {
      event: {
        type: 'agent.activity',
        event_id: 'agent-event-done',
        payload: { run_id: 'run-1', status: 'completed' },
      },
    },
  ] as unknown as typeof mock.state.activity;
  mock.version++;
  await act(async () => {
    view.rerender(<Conversation onPanel={vi.fn()} />);
  });
  expect(mock.refreshListedConversation).toHaveBeenCalledWith(
    'conversation-a',
    expect.any(AbortSignal),
  );
});
