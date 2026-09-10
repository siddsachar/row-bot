import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import type {
  CommandReceipt,
  ResourceChoice,
  ResourceChoicePage,
} from '../../api/types';
import ResourceSetup from './ResourceSetup';
import type { HomeSetupEntry } from './Home';
import { setupSessions } from './setup-state';

const mock = vi.hoisted(() => ({
  controller: {
    getSnapshot: vi.fn(),
    getSelectionVersion: vi.fn(),
    deckSetup: vi.fn(),
    workspaceFor: vi.fn(),
    receipt: vi.fn(),
    intent: vi.fn(),
    library: vi.fn(),
    pickFolder: vi.fn(),
    selectConversation: vi.fn(),
  },
  navigate: vi.fn(),
  routeKey: 'opening-route',
  selectionVersion: 1,
  handshake: { instance_id: '', client_session_id: 'session' },
  overlay: { notify: vi.fn(), open: vi.fn(), close: vi.fn() },
}));
vi.mock('react-router-dom', async (original) => ({
  ...(await original<typeof import('react-router-dom')>()),
  useNavigate: () => mock.navigate,
  useLocation: () => ({ key: mock.routeKey }),
}));
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: mock.controller }),
  useClientState: () => ({ handshake: mock.handshake }),
}));
vi.mock('../../ui/overlays', () => ({ useOverlay: () => mock.overlay }));
const result = (id: string): CommandReceipt => ({
  command_id: id,
  status: 'completed',
  conversation_id: 'conversation-a',
  binding_id: 'binding-a',
  resource_id: 'deck-a',
  resource_kind: 'artifact',
  resource_revision: 'resource-1',
  setup_command_id: id,
  confirmed_stages: ['created', 'conversation', 'associated', 'bound'],
});

it('keeps an admitting result reconcilable and never offers a duplicate create', async () => {
  const key = setupSessions.scope(mock.handshake.instance_id, 'conversation-a');
  setupSessions.reserve(key, 'pending-command');
  mock.controller.receipt.mockResolvedValue({
    command_id: 'pending-command',
    status: 'admitting',
  });
  await act(async () => {
    view();
  });
  expect(
    screen.getByRole('button', { name: 'Check setup receipt' }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Create Deck' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Start another resource' }),
  ).not.toBeInTheDocument();
  expect(mock.controller.intent).not.toHaveBeenCalled();
});

it.each(['Home route', 'A-B-A selection', 'new session'])(
  'retains a late global setup receipt without presentation changes after %s',
  async (change) => {
    let finish!: (receipt: CommandReceipt) => void;
    mock.controller.intent.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const onPanel = vi.fn();
    const element = () => (
      <MemoryRouter>
        <ResourceSetup conversationId={null} onPanel={onPanel} />
      </MemoryRouter>
    );
    const rendered = render(element());
    await act(async () => {});
    await act(async () =>
      fireEvent.click(screen.getByRole('button', { name: 'Create Deck' })),
    );
    const commandId = mock.controller.intent.mock.calls[0][4];
    if (change === 'Home route') mock.routeKey = 'home-route';
    else if (change === 'A-B-A selection') mock.selectionVersion += 2;
    else mock.handshake.client_session_id = 'replacement-session';
    await act(async () => rendered.rerender(element()));
    await act(async () => finish(result(commandId)));
    expect(mock.navigate).not.toHaveBeenCalled();
    expect(onPanel).not.toHaveBeenCalled();
    expect(mock.overlay.notify).toHaveBeenCalledWith(
      'Resource setup completed. Open its conversation when ready.',
    );
    expect(
      setupSessions.read(setupSessions.scope(mock.handshake.instance_id, null))
        .receipt?.command_id,
    ).toBe(commandId);
    await act(async () =>
      fireEvent.click(
        screen.getByRole('button', { name: 'Open conversation' }),
      ),
    );
    expect(mock.navigate).toHaveBeenCalledWith('/conversations/conversation-a');
    expect(mock.controller.intent).toHaveBeenCalledTimes(1);
  },
);

it('never restores an opaque folder grant after reopening with a new handshake', async () => {
  mock.controller.pickFolder.mockResolvedValue({
    status: 'selected',
    grant_id: 'fixture-secret-grant',
    name: 'Selected fixture',
  });
  let rendered!: ReturnType<typeof view>;
  await act(async () => {
    rendered = view();
  });
  await act(async () => {
    fireEvent.change(screen.getByLabelText('Resource type'), {
      target: { value: 'workspace' },
    });
  });
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', { name: 'Choose existing folder' }),
    );
  });
  expect(screen.getByText('Selected: Selected fixture')).toBeInTheDocument();
  const key = setupSessions.scope(mock.handshake.instance_id, 'conversation-a');
  expect(sessionStorage.getItem(key)).not.toContain('fixture-secret-grant');
  rendered.unmount();
  mock.handshake.client_session_id = 'replacement-session';
  await act(async () => {
    view();
  });
  expect(
    screen.queryByText('Selected: Selected fixture'),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Register folder' }),
  ).toBeDisabled();
  expect(mock.controller.intent).not.toHaveBeenCalled();
});
function view(
  conversationId: string | null = 'conversation-a',
  initialEntry?: HomeSetupEntry,
) {
  return render(
    <MemoryRouter>
      <ResourceSetup
        conversationId={conversationId}
        onPanel={vi.fn()}
        initialEntry={initialEntry}
      />
    </MemoryRouter>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  mock.handshake.instance_id = crypto.randomUUID();
  mock.routeKey = 'opening-route';
  mock.selectionVersion = 1;
  mock.controller.getSelectionVersion.mockImplementation(
    () => mock.selectionVersion,
  );
  mock.navigate.mockReset();
  mock.controller.selectConversation.mockReset();
  mock.controller.selectConversation.mockResolvedValue(undefined);
  mock.controller.getSnapshot.mockReturnValue({
    selectedConversationId: 'conversation-a',
  });
  mock.controller.deckSetup.mockResolvedValue({
    templates: [{ id: 'blank_deck', label: 'Blank Deck' }],
    canvases: [{ id: '16:9', label: 'Wide' }],
    default_brand: 'Default brand',
  });
  mock.controller.workspaceFor.mockResolvedValue({
    conversation_id: 'conversation-a',
    revision: '4',
    controls: {
      model_selection: {
        provider_id: 'fixture',
        model_ref: 'fixture::shown-model',
      },
      profile_id: 'profile-a',
      runtime_mode: 'agent',
      approval_mode: 'ask',
    },
    profiles: [{ id: 'profile-a', label: 'Shown profile' }],
    resources: [
      {
        resource_ref: 'conversation-a:binding-a',
        resource_revision: 'resource-1',
        title: 'Saved Deck',
        available: true,
        binding: {
          binding_id: 'binding-a',
          kind: 'artifact',
          resource_id: 'deck-a',
          revision: '1',
          role: 'context',
        },
      },
    ],
    actions: [{ action: 'generate', ready: true }],
  });
  mock.controller.intent.mockImplementation(
    async (_target, _kind, _payload, _revision, id) => result(id),
  );
});

it('opens the exact Home resource through canonical setup after refreshing its library identity', async () => {
  const saved: ResourceChoice = {
    resource_id: 'home-deck',
    kind: 'artifact',
    name: 'Home Deck',
    revision: 'home-revision',
    origin_status: 'available',
    origin_conversation_id: 'home-origin',
    available: true,
  };
  mock.controller.library.mockResolvedValue({
    items: [saved],
    next_cursor: null,
  });
  mock.controller.intent.mockImplementation(
    async (_target, _kind, _payload, _revision, id) => ({
      ...result(id),
      conversation_id: 'home-origin',
      resource_id: saved.resource_id,
      resource_revision: saved.revision,
    }),
  );
  await act(async () =>
    view(null, { kind: 'artifact', mode: 'existing', resource: saved }),
  );
  expect(
    screen.getByRole('button', { name: 'Home Deck Resource ID: home-deck' }),
  ).toHaveAttribute('aria-pressed', 'true');
  expect(mock.controller.intent).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Open resource' })),
  );
  expect(mock.controller.intent).toHaveBeenCalledWith(
    null,
    'resource.setup',
    {
      kind: 'artifact',
      intent: 'open',
      resource_id: saved.resource_id,
      expected_resource_revision: saved.revision,
    },
    '0',
    expect.any(String),
  );
  expect(mock.navigate).toHaveBeenCalledWith('/conversations/home-origin');
});

it('opens the folder starter without creating anything or launching a picker automatically', async () => {
  await act(async () => view(null, { kind: 'workspace', mode: 'create' }));
  expect(
    screen.getByRole('button', { name: 'Register folder' }),
  ).toBeDisabled();
  expect(mock.controller.pickFolder).not.toHaveBeenCalled();
  expect(mock.controller.intent).not.toHaveBeenCalled();
});

it('protects an unresolved setup receipt from a new Home starter', async () => {
  const scope = setupSessions.scope(mock.handshake.instance_id, null);
  setupSessions.reserve(scope, 'pending-before-home');
  mock.controller.receipt.mockResolvedValue({
    command_id: 'pending-before-home',
    status: 'admitting',
  });
  await act(async () => view(null, { kind: 'workspace', mode: 'create' }));
  expect(setupSessions.read(scope).commandId).toBe('pending-before-home');
  expect(setupSessions.read(scope).kind).toBe('artifact');
  expect(screen.getByText(/Review the earlier setup receipt/)).toBeVisible();
  expect(mock.controller.intent).not.toHaveBeenCalled();
});

it.each(['changed', 'missing', 'unavailable'])(
  'requires a fresh selection when the Home resource is %s',
  async (condition) => {
    const saved: ResourceChoice = {
      resource_id: 'home-stale',
      kind: 'artifact',
      name: 'Old Deck',
      revision: 'old-revision',
      origin_status: 'available',
      available: true,
    };
    mock.controller.library.mockResolvedValue({
      items:
        condition === 'missing'
          ? []
          : [
              {
                ...saved,
                revision:
                  condition === 'changed' ? 'new-revision' : saved.revision,
                available: condition !== 'unavailable',
              },
            ],
    });
    await act(async () =>
      view(null, { kind: 'artifact', mode: 'existing', resource: saved }),
    );
    expect(
      screen.getByRole('button', { name: 'Open resource' }),
    ).toBeDisabled();
    expect(
      screen.getByText(/This saved resource changed or is unavailable/),
    ).toBeVisible();
    expect(mock.controller.intent).not.toHaveBeenCalled();
  },
);

it('retains setup inputs when closed and restored without performing a mutation', async () => {
  let rendered!: ReturnType<typeof view>;
  await act(async () => {
    rendered = view();
  });
  fireEvent.change(screen.getByLabelText('Name (optional)'), {
    target: { value: 'Retained title' },
  });
  fireEvent.change(screen.getByLabelText('Brief (optional)'), {
    target: { value: 'Retained brief' },
  });
  rendered.unmount();
  await act(async () => {
    view();
  });
  expect(screen.getByLabelText('Name (optional)')).toHaveValue(
    'Retained title',
  );
  expect(screen.getByLabelText('Brief (optional)')).toHaveValue(
    'Retained brief',
  );
  expect(mock.controller.intent).not.toHaveBeenCalled();
});

it('reopens a lost setup response through its saved receipt without recreating the Deck', async () => {
  mock.controller.intent.mockRejectedValue(new TypeError('Lost response'));
  let rendered!: ReturnType<typeof view>;
  await act(async () => {
    rendered = view();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Create Deck' }));
  });
  const id = mock.controller.intent.mock.calls[0][4];
  const key = setupSessions.scope(mock.handshake.instance_id, 'conversation-a');
  expect(setupSessions.read(key).commandId).toBe(id);
  rendered.unmount();
  mock.controller.receipt.mockResolvedValue(result(id));
  await act(async () => {
    view();
  });
  expect(mock.controller.receipt).toHaveBeenCalledWith(
    id,
    expect.any(AbortSignal),
  );
  expect(screen.getByText('Resource ready')).toBeInTheDocument();
  expect(mock.controller.intent).toHaveBeenCalledTimes(1);
  expect(
    screen.getByRole('button', { name: 'Start another resource' }),
  ).toBeInTheDocument();
});

it('shows actual controls and target before one explicit generation and keeps its receipt separate', async () => {
  let rendered!: ReturnType<typeof view>;
  await act(async () => {
    rendered = view();
  });
  fireEvent.change(screen.getByLabelText('Brief (optional)'), {
    target: { value: 'First draft brief' },
  });
  fireEvent.click(screen.getByRole('checkbox'));
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', { name: 'Create and review first draft' }),
    );
  });
  expect(mock.controller.intent).toHaveBeenCalledTimes(1);
  expect(mock.controller.intent.mock.calls[0][1]).toBe('resource.setup');
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', { name: 'Review generation controls' }),
    );
  });
  expect(screen.getByText('Model: fixture::shown-model')).toBeInTheDocument();
  expect(screen.getByText(/Shown profile/)).toBeInTheDocument();
  expect(screen.getByText(/Target: Saved Deck/)).toBeInTheDocument();
  mock.controller.intent.mockImplementation(
    async (_target, _kind, _payload, _revision, id) => ({
      command_id: id,
      status: 'accepted',
    }),
  );
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', { name: 'Generate first draft' }),
    );
  });
  expect(mock.controller.intent).toHaveBeenCalledTimes(2);
  expect(mock.controller.intent.mock.calls[1][2]).toMatchObject({
    text: 'First draft brief',
    model_selection: { model_ref: 'fixture::shown-model' },
    write_targets: [
      {
        binding_id: 'binding-a',
        resource_id: 'deck-a',
        resource_revision: 'resource-1',
      },
    ],
  });
  const key = setupSessions.scope(mock.handshake.instance_id, 'conversation-a'),
    saved = setupSessions.read(key);
  expect(saved.receipt?.resource_id).toBe('deck-a');
  expect(saved.generationReceipt?.status).toBe('accepted');
  mock.controller.receipt.mockImplementation(async (id: string) =>
    id === saved.generationId ? saved.generationReceipt : saved.receipt,
  );
  rendered.unmount();
  await act(async () => {
    view();
  });
  expect(mock.controller.intent).toHaveBeenCalledTimes(2);
  expect(
    screen.queryByRole('button', { name: 'Generate first draft' }),
  ).not.toBeInTheDocument();
});

it.each([null, 'conversation-a'])(
  'distinguishes equal resource names and captures the second identity for %s',
  async (conversationId) => {
    const choices: ResourceChoice[] = ['first', 'second'].map((suffix) => ({
      resource_id: `deck-identical-prefix-${suffix}`,
      kind: 'artifact',
      name: 'Untitled Deck',
      revision: `revision-${suffix}`,
      origin_conversation_id: `origin-${suffix}`,
      origin_status: 'available',
      available: true,
    }));
    mock.controller.library.mockResolvedValue({
      items: choices,
      next_cursor: null,
    });
    mock.controller.intent.mockImplementation(
      async (_target, _kind, payload, _revision, id) => ({
        ...result(id),
        conversation_id: conversationId ?? 'origin-second',
        resource_id: payload.resource_id,
        resource_revision: payload.expected_resource_revision,
      }),
    );
    await act(async () => {
      view(conversationId);
    });
    await act(async () => {
      fireEvent.change(screen.getByLabelText('Choose resource'), {
        target: { value: 'existing' },
      });
    });
    const first = screen.getByRole('button', {
      name: 'Untitled Deck Resource ID: deck-identical-prefix-first',
    });
    const second = screen.getByRole('button', {
      name: 'Untitled Deck Resource ID: deck-identical-prefix-second',
    });
    expect(
      screen.getByText('Resource ID: deck-identical-prefix-second'),
    ).toBeVisible();
    fireEvent.click(second);
    expect(first).toHaveAttribute('aria-pressed', 'false');
    expect(second).toHaveAttribute('aria-pressed', 'true');
    // A later focus change cannot replace the captured Add destination.
    if (conversationId)
      mock.controller.getSnapshot.mockReturnValue({
        selectedConversationId: 'conversation-b',
      });
    await act(async () => {
      fireEvent.click(
        screen.getByRole('button', {
          name: conversationId ? 'Add to this conversation' : 'Open resource',
        }),
      );
    });
    expect(mock.controller.intent).toHaveBeenCalledExactlyOnceWith(
      conversationId,
      'resource.setup',
      {
        kind: 'artifact',
        intent: conversationId ? 'add' : 'open',
        resource_id: 'deck-identical-prefix-second',
        expected_resource_revision: 'revision-second',
      },
      conversationId ? '4' : '0',
      expect.any(String),
    );
    if (conversationId) {
      expect(mock.navigate).not.toHaveBeenCalled();
      expect(mock.controller.selectConversation).not.toHaveBeenCalled();
    } else {
      expect(
        mock.controller.selectConversation,
      ).toHaveBeenCalledExactlyOnceWith('origin-second');
      expect(mock.navigate).toHaveBeenCalledExactlyOnceWith(
        '/conversations/origin-second',
      );
    }
  },
);

it.each(['global completion', 'explicit open'])(
  'selects the draft owner before navigation while %s selection is still loading',
  async (entry) => {
    let selected = 'conversation-a';
    const drafts = new Map([['conversation-a', 'Keep draft A']]);
    const order: string[] = [];
    let finishSelection!: () => void;
    const pending = new Promise<void>((resolve) => {
      finishSelection = resolve;
    });
    mock.controller.getSnapshot.mockImplementation(() => ({
      selectedConversationId: selected,
    }));
    mock.controller.selectConversation.mockImplementation((target: string) => {
      order.push(`select:${target}`);
      selected = target;
      return pending;
    });
    mock.navigate.mockImplementation((path: string) => {
      order.push(`navigate:${path}`);
      // Model an immediate composer edit at route change, before /open resolves.
      drafts.set(
        mock.controller.getSnapshot().selectedConversationId,
        'Draft B while loading',
      );
    });
    if (entry === 'global completion') {
      mock.controller.intent.mockImplementation(
        async (_target, _kind, _payload, _revision, id) => ({
          ...result(id),
          conversation_id: 'conversation-b',
        }),
      );
      await act(async () => {
        view(null);
      });
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: 'Create Deck' }));
      });
    } else {
      const key = setupSessions.scope(
        mock.handshake.instance_id,
        'conversation-a',
      );
      setupSessions.reserve(key, 'saved-command');
      mock.controller.receipt.mockResolvedValue({
        ...result('saved-command'),
        conversation_id: 'conversation-b',
      });
      await act(async () => {
        view();
      });
      fireEvent.click(
        screen.getByRole('button', { name: 'Open conversation' }),
      );
    }
    expect(order).toEqual([
      'select:conversation-b',
      'navigate:/conversations/conversation-b',
    ]);
    expect(drafts.get('conversation-a')).toBe('Keep draft A');
    expect(drafts.get('conversation-b')).toBe('Draft B while loading');
    await act(async () => {
      finishSelection();
      await pending;
    });
    expect(mock.controller.selectConversation).toHaveBeenCalledTimes(1);
    expect(mock.navigate).toHaveBeenCalledTimes(1);
  },
);

it('opens an already selected outcome without selecting it again', async () => {
  await act(async () => {
    view(null);
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Create Deck' }));
  });
  expect(mock.navigate).toHaveBeenCalledWith('/conversations/conversation-a');
  fireEvent.click(screen.getByRole('button', { name: 'Open conversation' }));
  expect(mock.controller.selectConversation).not.toHaveBeenCalled();
});

it('does not navigate or select when a late global completion belongs to an earlier focus', async () => {
  let finish!: (receipt: CommandReceipt) => void;
  mock.controller.intent.mockImplementation(
    () =>
      new Promise<CommandReceipt>((resolve) => {
        finish = resolve;
      }),
  );
  await act(async () => {
    view(null);
  });
  fireEvent.click(screen.getByRole('button', { name: 'Create Deck' }));
  const commandId = mock.controller.intent.mock.calls[0][4];
  mock.controller.getSnapshot.mockReturnValue({
    selectedConversationId: 'conversation-c',
  });
  await act(async () => {
    finish({ ...result(commandId), conversation_id: 'conversation-b' });
  });
  expect(mock.controller.selectConversation).not.toHaveBeenCalled();
  expect(mock.navigate).not.toHaveBeenCalled();
  expect(mock.overlay.notify).toHaveBeenCalledWith(
    'Resource setup completed. Open its conversation when ready.',
  );
});

const savedWorkspace: ResourceChoice = {
  kind: 'workspace',
  resource_id: 'workspace-saved',
  name: 'Saved workspace',
  revision: 'workspace-revision',
  available: true,
  origin_status: 'available',
  origin_conversation_id: 'original-history',
};
const newWorkspaceConversation = (id: string): CommandReceipt => ({
  ...result(id),
  conversation_id: 'separate-history',
  resource_id: savedWorkspace.resource_id,
  resource_kind: 'workspace',
  resource_revision: savedWorkspace.revision,
  setup_intent: 'new_conversation',
  confirmed_stages: ['conversation', 'bound'],
});
async function savedWorkspaceView(conversationId: string | null = null) {
  setupSessions.update(
    setupSessions.scope(mock.handshake.instance_id, conversationId),
    { kind: 'workspace', mode: 'existing', selected: savedWorkspace },
  );
  mock.controller.library.mockResolvedValue({ items: [savedWorkspace] });
  let rendered!: ReturnType<typeof view>;
  await act(async () => {
    rendered = view(conversationId);
  });
  return rendered;
}
const newWorkspaceButton = () =>
  screen.getByRole('button', {
    name: 'New conversation with this workspace',
  });

it('explicitly starts separate workspace history with the saved identity and selects before navigating', async () => {
  const order: string[] = [];
  mock.controller.selectConversation.mockImplementation(async (id) => {
    order.push(`select:${id}`);
  });
  mock.navigate.mockImplementation((path) => {
    order.push(`navigate:${path}`);
  });
  mock.controller.intent.mockImplementation(
    async (_target, _type, _payload, _revision, id) => {
      expect(
        setupSessions.read(
          setupSessions.scope(mock.handshake.instance_id, null),
        ).commandId,
      ).toBe(id);
      return newWorkspaceConversation(id);
    },
  );
  await savedWorkspaceView();
  expect(
    screen.getByText(/original conversation stays unchanged/),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Open resource' })).toBeEnabled();
  await act(async () => {
    fireEvent.click(newWorkspaceButton());
  });
  expect(mock.controller.intent).toHaveBeenCalledExactlyOnceWith(
    null,
    'resource.setup',
    {
      kind: 'workspace',
      intent: 'new_conversation',
      resource_id: 'workspace-saved',
      expected_resource_revision: 'workspace-revision',
    },
    '0',
    expect.any(String),
  );
  expect(mock.controller.workspaceFor).not.toHaveBeenCalled();
  expect(order).toEqual([
    'select:separate-history',
    'navigate:/conversations/separate-history',
  ]);
});

it.each(['add', 'deck', 'register'] as const)(
  'does not offer separate workspace history for %s',
  async (entry) => {
    if (entry === 'add') await savedWorkspaceView('conversation-a');
    else {
      if (entry === 'register')
        setupSessions.update(
          setupSessions.scope(mock.handshake.instance_id, null),
          { kind: 'workspace' },
        );
      await act(async () => {
        view(null);
      });
    }
    expect(
      screen.queryByRole('button', {
        name: 'New conversation with this workspace',
      }),
    ).not.toBeInTheDocument();
    expect(mock.controller.intent).not.toHaveBeenCalled();
  },
);

it('recovers a lost separate-history response on remount without creating another conversation', async () => {
  mock.controller.intent.mockRejectedValueOnce(new TypeError('Lost response'));
  const rendered = await savedWorkspaceView();
  await act(async () => {
    fireEvent.click(newWorkspaceButton());
  });
  const id = mock.controller.intent.mock.calls[0][4];
  expect(
    screen.getByRole('button', { name: 'Check setup receipt' }),
  ).toBeVisible();
  rendered.unmount();
  mock.controller.receipt.mockResolvedValue(newWorkspaceConversation(id));
  await act(async () => {
    view(null);
  });
  expect(mock.controller.receipt).toHaveBeenCalledWith(
    id,
    expect.any(AbortSignal),
  );
  expect(screen.getByText('Resource ready')).toBeVisible();
  expect(mock.controller.intent).toHaveBeenCalledTimes(1);
  expect(mock.navigate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Open conversation' }));
  expect(mock.navigate).toHaveBeenCalledWith('/conversations/separate-history');
});

it('continues a partial separate-history binding using its confirmed conversation and setup receipt', async () => {
  let partial!: CommandReceipt;
  mock.controller.intent.mockImplementation(
    async (_target, type, _payload, _revision, id) => {
      if (type === 'resource.setup') {
        partial = {
          ...newWorkspaceConversation(id),
          status: 'partial',
          binding_id: undefined,
          confirmed_stages: ['conversation'],
        };
        return partial;
      }
      return {
        ...newWorkspaceConversation(id),
        setup_command_id: partial.setup_command_id,
      };
    },
  );
  mock.controller.receipt.mockImplementation(async () => partial);
  mock.controller.workspaceFor.mockResolvedValue({
    conversation_id: 'separate-history',
    revision: 'separate-revision',
  });
  await savedWorkspaceView();
  await act(async () => {
    fireEvent.click(newWorkspaceButton());
  });
  expect(mock.navigate).not.toHaveBeenCalled();
  expect(screen.getByText('Setup partially completed')).toBeVisible();
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Continue setup' }));
  });
  expect(mock.controller.intent.mock.calls[1]).toEqual([
    'separate-history',
    'resource.continue',
    {
      setup_command_id: partial.setup_command_id,
      expected_resource_revision: 'workspace-revision',
    },
    'separate-revision',
    expect.any(String),
  ]);
  expect(mock.controller.intent).toHaveBeenCalledTimes(2);
  expect(mock.navigate).toHaveBeenCalledWith('/conversations/separate-history');
});

it('keeps a late separate-history completion attributed to its receipt after A to B to C navigation', async () => {
  let finish!: (receipt: CommandReceipt) => void;
  mock.controller.intent.mockImplementation(
    () =>
      new Promise<CommandReceipt>((resolve) => {
        finish = resolve;
      }),
  );
  await savedWorkspaceView();
  fireEvent.click(newWorkspaceButton());
  const id = mock.controller.intent.mock.calls[0][4];
  mock.controller.getSnapshot.mockReturnValue({
    selectedConversationId: 'conversation-b',
  });
  mock.controller.getSnapshot.mockReturnValue({
    selectedConversationId: 'conversation-c',
  });
  await act(async () => {
    finish(newWorkspaceConversation(id));
  });
  expect(mock.navigate).not.toHaveBeenCalled();
  expect(mock.controller.selectConversation).not.toHaveBeenCalled();
  expect(
    setupSessions.read(setupSessions.scope(mock.handshake.instance_id, null))
      .receipt?.conversation_id,
  ).toBe('separate-history');
  expect(mock.overlay.notify).toHaveBeenCalledWith(
    'Resource setup completed. Open its conversation when ready.',
  );
});

it('fails closed when separate-history recovery identity cannot be persisted', async () => {
  await savedWorkspaceView();
  vi.spyOn(Storage.prototype, 'setItem').mockImplementationOnce(() => {
    throw new Error('Storage unavailable');
  });
  await act(async () => {
    fireEvent.click(newWorkspaceButton());
  });
  expect(mock.controller.intent).not.toHaveBeenCalled();
  expect(mock.navigate).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toBeVisible();
});

async function pagedWorkspacePicker() {
  setupSessions.update(setupSessions.scope(mock.handshake.instance_id, null), {
    kind: 'workspace',
    mode: 'existing',
    selected: null,
  });
  mock.controller.library.mockResolvedValue({
    items: [savedWorkspace],
    next_cursor: 'page-two',
  });
  let rendered!: ReturnType<typeof view>;
  await act(async () => {
    rendered = view(null);
  });
  return rendered;
}
function holdResourcePage() {
  let resolve!: (page: ResourceChoicePage) => void;
  let reject!: (error: unknown) => void;
  let signal!: AbortSignal;
  mock.controller.library.mockImplementationOnce(
    (_kind, _cursor, currentSignal) => {
      signal = currentSignal;
      return new Promise<ResourceChoicePage>((done, fail) => {
        resolve = done;
        reject = fail;
      });
    },
  );
  fireEvent.click(screen.getByRole('button', { name: 'More saved resources' }));
  return { resolve, reject, signal };
}
const freshChoice: ResourceChoice = {
  ...savedWorkspace,
  name: 'Fresh query choice',
  resource_id: 'workspace-fresh',
};
const obsoletePage: ResourceChoicePage = {
  items: [
    {
      ...savedWorkspace,
      name: 'Obsolete page choice',
      resource_id: 'workspace-obsolete',
    },
  ],
  next_cursor: null,
};

it.each(['success', 'failure'] as const)(
  'ignores an old continuation %s after returning to the same picker query',
  async (outcome) => {
    await pagedWorkspacePicker();
    const held = holdResourcePage();
    expect(held.signal.aborted).toBe(false);
    await act(async () => {
      fireEvent.change(screen.getByLabelText('Choose resource'), {
        target: { value: 'create' },
      });
    });
    expect(held.signal.aborted).toBe(true);
    mock.controller.library.mockResolvedValue({
      items: [freshChoice],
      next_cursor: null,
    });
    await act(async () => {
      fireEvent.change(screen.getByLabelText('Choose resource'), {
        target: { value: 'existing' },
      });
    });
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Fresh query choice Resource ID: workspace-fresh',
      }),
    );
    await act(async () => {
      if (outcome === 'success') held.resolve(obsoletePage);
      else held.reject({ status: 503 });
    });
    expect(
      screen.getByRole('button', {
        name: 'Fresh query choice Resource ID: workspace-fresh',
      }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByText('Obsolete page choice')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(mock.controller.intent).not.toHaveBeenCalled();
  },
);

it.each(['success', 'failure'] as const)(
  'aborts an unmounted picker continuation and contains its ignored-abort %s',
  async (outcome) => {
    const rendered = await pagedWorkspacePicker();
    const held = holdResourcePage();
    rendered.unmount();
    expect(held.signal.aborted).toBe(true);
    mock.controller.library.mockResolvedValue({
      items: [freshChoice],
      next_cursor: null,
    });
    await act(async () => {
      view(null);
    });
    await act(async () => {
      if (outcome === 'success') held.resolve(obsoletePage);
      else held.reject({ status: 503 });
    });
    expect(
      screen.getByRole('button', {
        name: 'Fresh query choice Resource ID: workspace-fresh',
      }),
    ).toBeVisible();
    expect(screen.queryByText('Obsolete page choice')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  },
);

it('keeps the latest continuation when an earlier request completes out of order', async () => {
  await pagedWorkspacePicker();
  const earlier = holdResourcePage();
  const latest = holdResourcePage();
  expect(earlier.signal.aborted).toBe(true);
  expect(latest.signal.aborted).toBe(false);
  await act(async () => {
    latest.resolve({ items: [freshChoice], next_cursor: null });
  });
  await act(async () => {
    earlier.resolve(obsoletePage);
  });
  expect(
    screen.getByRole('button', {
      name: 'Fresh query choice Resource ID: workspace-fresh',
    }),
  ).toBeVisible();
  expect(screen.queryByText('Obsolete page choice')).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'More saved resources' }),
  ).not.toBeInTheDocument();
});
