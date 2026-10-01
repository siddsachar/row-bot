import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { ClientController } from '../../api/controller';
import { FixtureTransport } from '../../api/fixtures';
import { createFakePlatform } from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import { AgentAvatar, agentSeed } from '../../ui/AgentAvatar';
import Navigation, { NavigationRail } from './Navigation';
import { createAuthenticatedEditorOwner } from '../settings/authenticated-editor-owner';
import {
  createGoalProfileSettingsSession,
  type ProfileSummary,
} from '../settings/GoalProfileSettings';
import userEvent from '@testing-library/user-event';

const clients: ClientController[] = [];
const owners: { dispose(): void }[] = [];
afterEach(() => {
  owners.splice(0).forEach((owner) => owner.dispose());
  clients.splice(0).forEach((controller) => controller.dispose());
  localStorage.removeItem('row-bot.sidebar-type.v1');
  localStorage.removeItem('row-bot.sidebar-agents.v1');
  localStorage.removeItem('row-bot.agent-favourites.v1');
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/**
 * The list's end marker under a fake IntersectionObserver: the returned
 * function scrolls it into view.
 */
function observeListEnd() {
  const observers = new Set<{
    callback: IntersectionObserverCallback;
    targets: Element[];
  }>();
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      private readonly entry: {
        callback: IntersectionObserverCallback;
        targets: Element[];
      };
      constructor(callback: IntersectionObserverCallback) {
        this.entry = { callback, targets: [] };
        observers.add(this.entry);
      }
      observe(target: Element) {
        this.entry.targets.push(target);
      }
      disconnect() {
        observers.delete(this.entry);
      }
    },
  );
  return () =>
    act(async () => {
      for (const { callback, targets } of [...observers])
        callback(
          targets.map((target) => ({ target, isIntersecting: true })) as never,
          {} as IntersectionObserver,
        );
    });
}

function CurrentRoute() {
  const location = useLocation();
  return (
    <output aria-label="Current route" data-history-key={location.key}>
      {location.pathname}
    </output>
  );
}

async function setup(
  count = 55,
  route = '/',
  prepare?: (transport: FixtureTransport) => void,
) {
  const onOpenConversation = vi.fn();
  const onNewChat = vi.fn();
  const transport = new FixtureTransport({ conversationCount: count });
  prepare?.(transport);
  const list = vi.spyOn(transport, 'listConversations');
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  render(
    <MemoryRouter initialEntries={[route]}>
      <CurrentRoute />
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <OverlayProvider>
          <Navigation
            onOpenConversation={onOpenConversation}
            onNewChat={onNewChat}
          />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  return {
    controller,
    transport,
    list,
    onOpenConversation,
    onNewChat,
  };
}

function rows() {
  return Array.from(
    document.querySelectorAll<HTMLButtonElement>(
      '.nav-conversations .nav-conversation-link',
    ),
  );
}

it('opens the grouped profile panel with counts and starts the selected profile chat', async () => {
  const transport = new FixtureTransport({ conversationCount: 1 });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  const profile = {
    id: 'builtin:general',
    slug: 'general',
    display_name: 'General Assistant',
    description: 'A safe default.',
    when_to_use: 'General work.',
    scope: 'system' as const,
    surface_scope: 'global' as const,
    source: 'builtin',
    group: 'Everyday',
    icon: 'auto_awesome',
    enabled: true,
    editable: false,
    revision: '1',
    capability: 'read_only' as const,
    allow_tools: [],
    skills: [],
    context_mode: 'auto' as const,
    workspace_mode: 'auto' as const,
    approval_mode: 'inherit' as const,
    instructions_preview: '',
    instructions_truncated: true,
  };
  controller.profiles = vi.fn().mockResolvedValue({
    schema_version: 1,
    scope: 'global',
    revision: 'a'.repeat(64),
    items: [profile],
    total: 1,
    next_cursor: null,
  });
  controller.profile = vi
    .fn()
    .mockResolvedValue({ schema_version: 1, profile });
  const owner = createAuthenticatedEditorOwner(
    controller,
    createGoalProfileSettingsSession,
  );
  const onStartProfileChat = vi.fn();
  render(
    <MemoryRouter>
      <RuntimeContext.Provider
        value={{
          controller,
          platform: createFakePlatform(),
          goalProfileOwner: owner,
        }}
      >
        <OverlayProvider>
          <Navigation
            showBuddy={false}
            onStartProfileChat={onStartProfileChat}
          />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  const entry = await screen.findByRole('button', {
    name: /^All agents \(1\).*1 built-in.*0 custom/,
  });
  fireEvent.click(entry);
  const dialog = await screen.findByRole('dialog', { name: 'Agent profiles' });
  expect(within(dialog).getByText('Everyday')).toBeInTheDocument();
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'View General Assistant' }),
  );
  await within(dialog).findByRole('region', { name: 'Profile details' });
  fireEvent.click(
    within(dialog).getByRole('button', {
      name: 'Start chat with General Assistant',
    }),
  );
  await waitFor(() => expect(onStartProfileChat).toHaveBeenCalledWith(profile));
  expect(screen.queryByRole('dialog', { name: 'Agent profiles' })).toBeNull();
  owner.dispose();
});

function agentProfile(
  id: string,
  display_name: string,
  extra: Partial<ProfileSummary> = {},
): ProfileSummary {
  return {
    id,
    slug: id.replace(/^builtin:/, ''),
    display_name,
    description: `${display_name} profile.`,
    when_to_use: 'Synthetic work.',
    scope: 'system',
    surface_scope: 'global',
    source: 'builtin',
    group: 'Everyday',
    enabled: true,
    editable: false,
    revision: '1',
    capability: 'read_only',
    allow_tools: [],
    skills: [],
    context_mode: 'auto',
    workspace_mode: 'auto',
    approval_mode: 'inherit',
    instructions_preview: '',
    instructions_truncated: true,
    ...extra,
  };
}

const AGENTS = [
  agentProfile('builtin:row_bot_default', 'Row-Bot'),
  agentProfile('builtin:plan', 'Planner'),
  agentProfile('builtin:research', 'Researcher'),
  agentProfile('builtin:write', 'Writer'),
  agentProfile('builtin:ideas', 'Ideas'),
  agentProfile('builtin:knowledge', 'Knowledge'),
  agentProfile('builtin:data', 'Analyst'),
  agentProfile('profile-old', 'Old helper', {
    scope: 'user',
    source: 'user_created',
    group: undefined,
    enabled: false,
    editable: true,
  }),
];

/** The expanded sidebar with the agent library's profiles loaded. */
async function setupAgents(prepare?: (transport: FixtureTransport) => void) {
  const transport = new FixtureTransport({ conversationCount: 2 });
  prepare?.(transport);
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  controller.profiles = vi.fn().mockResolvedValue({
    schema_version: 1,
    scope: 'global',
    revision: 'a'.repeat(64),
    items: AGENTS,
    total: AGENTS.length,
    next_cursor: null,
  });
  controller.profile = vi.fn().mockImplementation(async (id: string) => ({
    schema_version: 1,
    profile: AGENTS.find((item) => item.id === id),
  }));
  const owner = createAuthenticatedEditorOwner(
    controller,
    createGoalProfileSettingsSession,
  );
  owners.push(owner);
  const onNewChat = vi.fn();
  const onStartProfileChat = vi.fn();
  const view = render(
    <MemoryRouter>
      <RuntimeContext.Provider
        value={{
          controller,
          platform: createFakePlatform(),
          goalProfileOwner: owner,
        }}
      >
        <OverlayProvider>
          <Navigation
            showBuddy={false}
            onNewChat={onNewChat}
            onStartProfileChat={onStartProfileChat}
          />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  const nav = screen.getByRole('navigation', { name: 'Workspace navigation' });
  await within(nav).findByRole('button', { name: /^All agents \(8\)/ });
  return { controller, transport, nav, view, onNewChat, onStartProfileChat };
}

function avatarOf(seed: string) {
  const { container, unmount } = render(<AgentAvatar seed={seed} />);
  const id = container
    .querySelector('.agent-avatar')!
    .getAttribute('data-avatar');
  unmount();
  return id;
}

it('makes New chat the one primary button, with a ▾ for a chat with an agent (B268)', async () => {
  const user = userEvent.setup();
  const { nav, onNewChat, onStartProfileChat } = await setupAgents();
  // One New chat in the sidebar: the header keeps no second pencil.
  const newChat = within(nav).getByRole('button', { name: 'New chat' });
  expect(within(nav).getAllByRole('button', { name: 'New chat' })).toHaveLength(
    1,
  );
  expect(newChat).toHaveTextContent('New chat');
  expect(newChat).toHaveAttribute('aria-keyshortcuts', 'Control+Shift+O');
  await user.click(newChat);
  expect(onNewChat).toHaveBeenCalledTimes(1);
  await user.click(
    within(nav).getByRole('button', { name: 'New chat with an agent…' }),
  );
  const menu = await screen.findByRole('menu', {
    name: 'New chat with an agent…',
  });
  // Until a profile is pinned, the library's first five stand in; the
  // Default profile is plain New chat and a disabled one can't start.
  expect(
    within(menu)
      .getAllByRole('menuitem')
      .map((item) => item.textContent),
  ).toEqual([
    'Planner',
    'Researcher',
    'Writer',
    'Ideas',
    'Knowledge',
    'All agents…',
  ]);
  await user.click(within(menu).getByRole('menuitem', { name: 'Researcher' }));
  expect(onStartProfileChat).toHaveBeenCalledWith(AGENTS[2]);
  expect(onNewChat).toHaveBeenCalledTimes(1);
  await user.click(
    within(nav).getByRole('button', { name: 'New chat with an agent…' }),
  );
  await user.click(
    await screen.findByRole('menuitem', { name: 'All agents…' }),
  );
  expect(
    await screen.findByRole('dialog', { name: 'Agent profiles' }),
  ).toBeInTheDocument();
});

it('orders the sidebar: Home in the header, New chat, the conversations and their library, then Agents above the footer', async () => {
  const { nav } = await setupAgents();
  const order = [
    within(nav).getByRole('link', { name: 'Home' }),
    within(nav).getByRole('button', { name: 'New chat' }),
    within(nav).getByRole('region', { name: 'Conversations' }),
    within(nav).getByRole('link', { name: 'Conversation library' }),
    within(nav).getByRole('region', { name: 'Agents' }),
    within(nav).getByRole('link', { name: 'Settings' }),
  ];
  order
    .slice(1)
    .forEach((element, index) =>
      expect(
        order[index].compareDocumentPosition(element) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy(),
    );
  // Home's row is gone: the header icon is the only Home.
  expect(within(nav).getAllByRole('link', { name: 'Home' })).toHaveLength(1);
});

it('starts a chat with a favourite agent in one click and opens the library from the Agents section (B268)', async () => {
  const user = userEvent.setup();
  const { nav, onStartProfileChat } = await setupAgents();
  const agents = within(nav).getByRole('region', { name: 'Agents' });
  const favourites = within(agents).getByRole('group', {
    name: 'Favourite agents',
  });
  const buttons = within(favourites).getAllByRole('button');
  expect(buttons.map((button) => button.getAttribute('aria-label'))).toEqual([
    'New chat with Planner',
    'New chat with Researcher',
    'New chat with Writer',
    'New chat with Ideas',
    'New chat with Knowledge',
  ]);
  // A profile draws the same icon here as in the transcript and Agents.
  expect(
    buttons[1].querySelector('.agent-avatar')!.getAttribute('data-avatar'),
  ).toBe(avatarOf(agentSeed('builtin:research', 'run-elsewhere')));
  await user.click(buttons[1]);
  expect(onStartProfileChat).toHaveBeenCalledTimes(1);
  expect(onStartProfileChat).toHaveBeenCalledWith(AGENTS[2]);
  await user.click(
    within(agents).getByRole('button', {
      name: 'All agents (8): 7 built-in · 1 custom',
    }),
  );
  expect(
    await screen.findByRole('dialog', { name: 'Agent profiles' }),
  ).toBeInTheDocument();
});

it('shows pinned profiles as the favourites once one is pinned in the library (B268)', async () => {
  const user = userEvent.setup();
  const { nav } = await setupAgents();
  await user.click(within(nav).getByRole('button', { name: /^All agents/ }));
  const dialog = await screen.findByRole('dialog', { name: 'Agent profiles' });
  const pin = await within(dialog).findByRole('button', {
    name: 'Pin Analyst to the sidebar',
  });
  expect(pin).toHaveAttribute('aria-pressed', 'false');
  await user.click(pin);
  expect(
    within(dialog).getByRole('button', {
      name: 'Unpin Analyst from the sidebar',
    }),
  ).toHaveAttribute('aria-pressed', 'true');
  // Pinned profiles replace the stand-ins, in the order they were pinned.
  await user.click(
    within(dialog).getByRole('button', { name: 'Pin Writer to the sidebar' }),
  );
  await user.keyboard('{Escape}');
  await waitFor(() =>
    expect(screen.queryByRole('dialog', { name: 'Agent profiles' })).toBeNull(),
  );
  const favourites = within(nav).getByRole('group', {
    name: 'Favourite agents',
  });
  expect(
    within(favourites)
      .getAllByRole('button')
      .map((button) => button.getAttribute('aria-label')),
  ).toEqual(['New chat with Analyst', 'New chat with Writer']);
  await user.click(
    within(nav).getByRole('button', { name: 'New chat with an agent…' }),
  );
  const menu = await screen.findByRole('menu');
  expect(
    within(menu)
      .getAllByRole('menuitem')
      .map((item) => item.textContent),
  ).toEqual(['Analyst', 'Writer', 'All agents…']);
});

it('keeps the Agents section collapsed across a reload and lists no agent runs (B268)', async () => {
  const user = userEvent.setup();
  const { nav, view } = await setupAgents((transport) => {
    transport.conversations[1].activity_state = 'active';
    transport.conversations[1].activity_phase = 'background';
  });
  const agents = within(nav).getByRole('region', { name: 'Agents' });
  // Agents belong to their conversation: that row shows its own work, the
  // section holds only the favourites and the library.
  expect(within(agents).queryAllByRole('listitem')).toHaveLength(0);
  expect(within(agents).queryByText(/working/i)).toBeNull();
  expect(
    within(nav).getByRole('img', { name: 'Background agents working' }),
  ).toBeInTheDocument();
  const heading = within(agents).getByRole('button', { name: 'Agents' });
  expect(heading).toHaveAttribute('aria-expanded', 'true');
  await user.click(heading);
  expect(heading).toHaveAttribute('aria-expanded', 'false');
  expect(
    within(nav).queryByRole('group', { name: 'Favourite agents' }),
  ).toBeNull();
  expect(within(nav).queryByRole('button', { name: /^All agents/ })).toBeNull();
  view.unmount();
  render(
    <MemoryRouter>
      <RuntimeContext.Provider
        value={{
          controller: clients[0],
          platform: createFakePlatform(),
          goalProfileOwner: owners[0] as never,
        }}
      >
        <OverlayProvider>
          <Navigation showBuddy={false} />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  const again = screen.getByRole('button', { name: 'Agents' });
  expect(again).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('group', { name: 'Favourite agents' })).toBeNull();
  await user.click(again);
  expect(
    await screen.findByRole('group', { name: 'Favourite agents' }),
  ).toBeInTheDocument();
});

it('shows a scoped library failure with retry while retaining confirmed rows and selection', async () => {
  const { controller, list } = await setup(2);
  await act(async () => controller.selectConversation('conversation-a'));
  list.mockRejectedValueOnce({ status: 503 });
  await act(async () => controller.loadMoreConversations(true));
  expect(screen.getByRole('alert')).toBeVisible();
  expect(rows()).toHaveLength(2);
  expect(controller.getSnapshot().status).toBe('ready');
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Retry conversations' }),
    ),
  );
  expect(screen.queryByRole('alert')).toBeNull();
  expect(controller.getSnapshot().selectedConversationId).toBe(
    'conversation-a',
  );
});

it.each(['/primitives', '/settings/appearance'])(
  'returns to the conversation when selecting a row from %s',
  async (route) => {
    const { controller, transport, onOpenConversation } = await setup(2, route);
    expect(screen.getByLabelText('Current route')).toHaveTextContent(route);
    await act(async () => fireEvent.click(rows()[0]));
    expect(screen.getByLabelText('Current route').textContent).toBe(
      `/conversations/${transport.conversations[0].id}`,
    );
    expect(controller.getSnapshot().selectedConversationId).toBe(
      transport.conversations[0].id,
    );
    expect(transport.counters.commands).toBe(0);
    expect(onOpenConversation).toHaveBeenCalledTimes(1);
  },
);

it('preserves the history entry when selecting the current canonical conversation', async () => {
  const { controller, transport, onOpenConversation } = await setup(
    2,
    '/conversations/conversation-a',
  );
  const historyKey = screen
    .getByLabelText('Current route')
    .getAttribute('data-history-key');
  for (let repeat = 0; repeat < 2; repeat++) {
    await act(async () => fireEvent.click(rows()[0]));
    expect(screen.getByLabelText('Current route')).toHaveAttribute(
      'data-history-key',
      historyKey,
    );
  }
  expect(controller.getSnapshot().selectedConversationId).toBe(
    transport.conversations[0].id,
  );
  expect(transport.counters.commands).toBe(0);
  expect(onOpenConversation).toHaveBeenCalledTimes(2);
});

it('starts with ten ordered rows and reads the next page when Show all scrolls to its end', async () => {
  const scrollToEnd = observeListEnd();
  const { list, transport } = await setup();
  expect(rows()).toHaveLength(10);
  expect(rows().map((row) => row.getAttribute('aria-label'))).toEqual(
    transport.conversations.slice(0, 10).map(({ title }) => title),
  );
  await scrollToEnd();
  expect(list).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Show all' }));
  expect(rows()).toHaveLength(50);
  expect(list).toHaveBeenCalledTimes(1);
  // No second button: the end of the list reads the next page.
  expect(
    screen.queryByRole('button', { name: 'Load more conversations' }),
  ).toBeNull();
  await scrollToEnd();
  expect(rows()).toHaveLength(55);
  expect(list).toHaveBeenCalledTimes(2);
  expect(list).toHaveBeenLastCalledWith('50', expect.any(AbortSignal), 'all');
  expect(rows().map((row) => row.getAttribute('aria-label'))).toEqual(
    transport.conversations.map(({ title }) => title),
  );
  await scrollToEnd();
  expect(list).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole('button', { name: 'Show less' }));
  expect(rows()).toHaveLength(10);
  fireEvent.click(screen.getByRole('button', { name: 'Show all' }));
  expect(rows()).toHaveLength(55);
  expect(list).toHaveBeenCalledTimes(2);
  expect(transport.counters.commands).toBe(0);
});

/** Sixty conversations; only the given indexes (0-based) are Code. */
function codeAt(...indexes: number[]) {
  return (transport: FixtureTransport) =>
    transport.conversations.forEach((row, index) => {
      row.pinned = false;
      row.category = indexes.includes(index) ? 'code' : 'chat';
      row.updated_at = new Date(
        Date.now() - (index < 50 ? index : 30 + index) * 3_600_000 * 24,
      ).toISOString();
    });
}

it('lists a type from the server, so an older match beyond the loaded pages shows under Older', async () => {
  const { controller, list, transport } = await setup(60, '/', codeAt(1, 57));
  expect(list).toHaveBeenCalledTimes(1);
  expect(
    screen.queryByRole('button', { name: 'Sample conversation 58' }),
  ).toBeNull();
  await act(async () =>
    fireEvent.click(screen.getByRole('radio', { name: 'Code' })),
  );
  expect(list).toHaveBeenLastCalledWith(
    undefined,
    expect.any(AbortSignal),
    'workspace',
  );
  expect(rows().map((row) => row.getAttribute('aria-label'))).toEqual([
    'Sample conversation 2',
    'Sample conversation 58',
  ]);
  const older = screen
    .getByRole('button', { name: 'Sample conversation 58' })
    .closest('li')!;
  expect(within(older).getByRole('heading', { level: 4 })).toHaveTextContent(
    'Older',
  );
  expect(screen.queryByRole('button', { name: 'Show all' })).toBeNull();
  // Deleted elsewhere, it leaves the typed list too.
  act(() => controller.forgetConversation(transport.conversations[57].id));
  expect(rows()).toHaveLength(1);
});

it('pages a typed list in as it scrolls and says none only for the whole library', async () => {
  const scrollToEnd = observeListEnd();
  const code = Array.from({ length: 55 }, (_, index) => index + 3);
  const { list } = await setup(60, '/', codeAt(...code));
  await act(async () =>
    fireEvent.click(screen.getByRole('radio', { name: 'Code' })),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Show all' }));
  expect(rows()).toHaveLength(50);
  await scrollToEnd();
  expect(list).toHaveBeenLastCalledWith(
    '50',
    expect.any(AbortSignal),
    'workspace',
  );
  expect(rows()).toHaveLength(55);
  expect(rows().at(-1)).toHaveAccessibleName('Sample conversation 58');
  await act(async () =>
    fireEvent.click(screen.getByRole('radio', { name: 'Workflows' })),
  );
  expect(list).toHaveBeenLastCalledWith(
    undefined,
    expect.any(AbortSignal),
    'workflow',
  );
  expect(screen.getByText('No workflow conversations yet.')).toBeVisible();
  // Back to All: the shared list, no new read.
  const reads = list.mock.calls.length;
  fireEvent.click(screen.getByRole('radio', { name: 'All' }));
  expect(rows()).toHaveLength(50);
  expect(list).toHaveBeenCalledTimes(reads);
});

it('retains the selected older row through Show less and section collapse', async () => {
  const { controller, transport } = await setup();
  fireEvent.click(screen.getByRole('button', { name: 'Show all' }));
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Sample conversation 13' }),
    ),
  );
  expect(controller.getSnapshot().selectedConversationId).toBe(
    'conversation-13',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Show less' }));
  expect(rows()).toHaveLength(11);
  expect(rows().at(-1)).toHaveAttribute('aria-current', 'page');
  const heading = screen.getByRole('button', {
    name: 'Conversations',
  });
  fireEvent.click(heading);
  expect(heading).toHaveAttribute('aria-expanded', 'false');
  expect(
    document.getElementById(heading.getAttribute('aria-controls')!),
  ).not.toBeVisible();
  expect(
    screen.queryByRole('list', { name: 'Recent conversations' }),
  ).toBeNull();
  const selected = within(
    screen.getByRole('list', { name: 'Current conversation' }),
  ).getAllByRole('button', { name: 'Sample conversation 13' });
  expect(selected).toHaveLength(1);
  expect(selected[0]).toHaveAccessibleName('Sample conversation 13');
  fireEvent.click(heading);
  expect(rows()).toHaveLength(11);
  expect(controller.getSnapshot().selectedConversationId).toBe(
    'conversation-13',
  );
  expect(transport.counters.commands).toBe(0);
});

it('keeps confirmed selection visible when refreshing the list no longer includes it', async () => {
  const { controller, transport } = await setup(
    15,
    '/conversations/conversation-15',
  );
  await act(async () => controller.selectConversation('conversation-15'));
  transport.conversations.splice(0);
  await act(async () => controller.loadMoreConversations(true));
  expect(rows()).toHaveLength(1);
  expect(rows()[0]).toHaveAccessibleName('Sample conversation 15');
  expect(rows()[0]).toHaveAttribute('aria-current', 'page');
  expect(screen.queryByText('Your conversations will appear here.')).toBeNull();
});

it('opens Home from its header icon without a creation, Stop, selection change or draft mutation', async () => {
  const user = userEvent.setup();
  const { controller, transport, onNewChat } = await setup(
    2,
    '/conversations/conversation-a',
  );
  await act(async () => controller.selectConversation('conversation-a'));
  const before = controller.getSnapshot();
  const commands = transport.counters.commands;
  // An icon in the header, not a row: named, first in the tab order, and
  // not current while a conversation shows.
  const home = screen.getByRole('link', { name: 'Home' });
  expect(home.textContent).toBe('');
  expect(home).not.toHaveAttribute('aria-current');
  await user.tab();
  expect(home).toHaveFocus();
  await user.tab();
  expect(screen.getByRole('button', { name: 'New chat' })).toHaveFocus();
  await user.click(home);
  expect(screen.getByLabelText('Current route')).toHaveTextContent(/^\/$/);
  expect(home).toHaveAttribute('aria-current', 'page');
  expect(rows()[0]).not.toHaveAttribute('aria-current');
  expect(controller.getSnapshot()).toBe(before);
  expect(transport.counters.commands).toBe(commands);
  expect(onNewChat).not.toHaveBeenCalled();
});

it('opens Home from the logo too, a pointer shortcut outside the tab order', async () => {
  await setup(2, '/conversations/conversation-a');
  const logo = screen.getByText('Row-Bot').closest('a')!;
  expect(logo).toHaveAttribute('tabindex', '-1');
  fireEvent.click(logo);
  expect(screen.getByLabelText('Current route')).toHaveTextContent(/^\/$/);
  expect(screen.getByRole('link', { name: 'Home' })).toHaveAttribute(
    'aria-current',
    'page',
  );
});

it('delegates sidebar New chat and navigates Settings to the persistent shell', async () => {
  const { transport, onNewChat } = await setup();
  fireEvent.click(screen.getByRole('button', { name: 'New chat' }));
  // Settings is its own labelled row under Buddy (B225).
  expect(screen.getByRole('link', { name: 'Settings' })).toHaveTextContent(
    'Settings',
  );
  fireEvent.click(screen.getByRole('link', { name: 'Settings' }));
  expect(onNewChat).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText('Current route')).toHaveTextContent(
    '/settings/providers',
  );
  expect(transport.counters.commands).toBe(0);
});

it('lists the conversations with no developer utilities for users (B267)', async () => {
  await setup(2);
  expect(screen.getByRole('region', { name: 'Conversations' })).toBeVisible();
  expect(screen.queryByText('About and developer utilities')).toBeNull();
  expect(screen.queryByRole('link', { name: 'Component gallery' })).toBeNull();
});

it('supports an empty collapsible section without introducing commands or controls for nonexistent pages', async () => {
  const { transport } = await setup(0);
  expect(
    screen.getByText('Your conversations will appear here.'),
  ).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Show all' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Conversations' }));
  expect(screen.queryByText('Your conversations will appear here.')).toBeNull();
  expect(transport.counters.commands).toBe(0);
});

it('preserves full accessible titles and safe untitled labels', async () => {
  const { controller, transport } = await setup(2);
  const longTitle = 'A long synthetic conversation title '.repeat(5);
  transport.conversations[0].title = longTitle;
  transport.conversations[1].title = '';
  await act(async () => controller.loadMoreConversations(true));
  expect(rows()[0]).toHaveAttribute('aria-label', longTitle);
  expect(rows()[1]).toHaveAccessibleName('Untitled conversation');
});

it('groups capped pinned and recent rows with canonical dates and active activity only', async () => {
  const { controller, transport } = await setup(14);
  transport.conversations.forEach((row, index) => {
    row.pinned = index < 7;
    row.updated_at = '2026-09-25T09:30:00Z';
  });
  transport.conversations[0].generation_state = [
    {
      execution_id: 'execution-a',
      conversation_id: 'conversation-a',
      generation_id: 'generation-a',
      pass_id: 'pass-a',
      segment_id: null,
      status: 'running',
      revision: '1',
      cancel_requested: false,
      quiesced: false,
      cleanup_complete: false,
      external_outcome: 'not_applicable',
      approval_id: null,
      can_stop: true,
    },
  ];
  transport.conversations[1].generation_state = [
    {
      ...transport.conversations[0].generation_state[0],
      conversation_id: 'conversation-2',
      status: 'completed',
      quiesced: true,
    },
  ];
  await act(async () => controller.loadMoreConversations(true));
  expect(
    within(
      screen.getByRole('list', { name: 'Pinned conversations' }),
    ).getAllByRole('listitem'),
  ).toHaveLength(5);
  expect(
    within(
      screen.getByRole('list', { name: 'Recent conversations' }),
    ).getAllByRole('listitem'),
  ).toHaveLength(5);
  expect(rows()).toHaveLength(10);
  expect(screen.getByRole('img', { name: 'Generating response' })).toHaveClass(
    'nav-activity-spin',
  );
  expect(screen.queryByRole('img', { name: 'Completed response' })).toBeNull();
  expect(rows()[0].querySelector('time')).toHaveAttribute(
    'dateTime',
    '2026-09-25T09:30:00Z',
  );
  expect(
    screen.getByRole('button', { name: 'Pin Sample conversation 8' }),
  ).toHaveAttribute('aria-pressed', 'false');
  expect(
    screen.getByRole('button', { name: 'Unpin A place for your ideas' }),
  ).toHaveAttribute('aria-pressed', 'true');
});

it('shows every pinned row when the complete list fits the compact cap', async () => {
  const { controller, transport } = await setup(7);
  transport.conversations.forEach((row) => {
    row.pinned = true;
  });
  await act(async () => controller.loadMoreConversations(true));
  expect(
    within(
      screen.getByRole('list', { name: 'Pinned conversations' }),
    ).getAllByRole('listitem'),
  ).toHaveLength(7);
  expect(screen.queryByRole('button', { name: 'Show all' })).toBeNull();
});

it('keeps canonical children under their parent and a direct child route active', async () => {
  const { controller, transport } = await setup(
    4,
    '/conversations/conversation-4',
  );
  transport.conversations[3].parent_conversation_id = 'conversation-a';
  await act(async () => {
    await controller.loadMoreConversations(true);
    await controller.selectConversation('conversation-4');
  });
  expect(rows()).toHaveLength(3);
  expect(
    screen.getByRole('button', { name: 'Sample conversation 4' }),
  ).toHaveAttribute('aria-current', 'page');
  expect(
    screen.getByRole('button', { name: 'Sample conversation 4' }).closest('li'),
  ).toContainElement(rows()[0]);
  expect(
    screen.getByRole('button', { name: 'A place for your ideas' }),
  ).not.toHaveAttribute('aria-current');
});

it('shows background work but clears terminal, quiesced, and selected stale states', async () => {
  const { controller, transport } = await setup(2);
  const running = {
    execution_id: 'execution-a',
    conversation_id: 'conversation-2',
    generation_id: 'generation-a',
    pass_id: 'pass-a',
    segment_id: null,
    status: 'running' as const,
    revision: '1',
    cancel_requested: false,
    quiesced: false,
    cleanup_complete: false,
    external_outcome: 'not_applicable' as const,
    approval_id: null,
    can_stop: true,
  };
  transport.conversations[1].generation_state = [running];
  await act(async () => controller.loadMoreConversations(true));
  expect(screen.getByRole('img', { name: 'Generating response' })).toHaveClass(
    'nav-activity-spin',
  );
  transport.conversations[1].generation_state = [
    { ...running, status: 'interrupted' },
  ];
  await act(async () => controller.loadMoreConversations(true));
  expect(screen.queryByRole('img', { name: 'Generating response' })).toBeNull();
  transport.conversations[1].generation_state = [
    { ...running, quiesced: true },
  ];
  await act(async () => controller.loadMoreConversations(true));
  expect(screen.queryByRole('img', { name: 'Generating response' })).toBeNull();
  transport.conversations[0].generation_state = [
    { ...running, conversation_id: 'conversation-a' },
  ];
  await act(async () => {
    await controller.loadMoreConversations(true);
    await controller.selectConversation('conversation-a');
  });
  expect(screen.queryByRole('img', { name: 'Generating response' })).toBeNull();
});

it('uses canonical orchestration activity and stops spinning after failure or completion', async () => {
  const { controller, transport } = await setup(2);
  const row = transport.conversations[1];
  row.activity_state = 'active';
  row.activity_phase = 'background';
  await act(async () => controller.loadMoreConversations(true));
  expect(
    screen.getByRole('img', { name: 'Background agents working' }),
  ).toHaveClass('nav-activity-spin');
  row.activity_state = 'attention';
  row.activity_phase = 'resume_required';
  await act(async () => controller.loadMoreConversations(true));
  expect(
    screen.getByRole('img', { name: 'Agent work needs attention' }),
  ).not.toHaveClass('nav-activity-spin');
  // A turn paused on its own approval needs the user too, in its own words.
  row.activity_phase = 'waiting_approval';
  await act(async () => controller.loadMoreConversations(true));
  expect(
    screen.getByRole('img', { name: 'Waiting for approval' }),
  ).not.toHaveClass('nav-activity-spin');
  row.activity_state = 'terminal';
  row.activity_phase = 'failed';
  await act(async () => controller.loadMoreConversations(true));
  expect(
    screen.queryByRole('img', { name: 'Agent work needs attention' }),
  ).toBeNull();
  row.activity_phase = 'completed';
  await act(async () => controller.loadMoreConversations(true));
  expect(
    screen.queryByRole('img', { name: 'Background agents working' }),
  ).toBeNull();
});

it('rechecks background work without resetting the list and marks failed reads stale', async () => {
  const { controller, transport, list } = await setup(55);
  vi.useFakeTimers();
  transport.conversations[1].activity_state = 'active';
  transport.conversations[1].activity_phase = 'background';
  await act(async () => controller.loadMoreConversations(true));
  const listReads = list.mock.calls.length;
  vi.spyOn(transport, 'getConversation').mockRejectedValueOnce(
    Error('offline'),
  );
  await act(async () => vi.advanceTimersByTimeAsync(15000));
  expect(
    screen.getByRole('img', { name: 'Activity status unavailable' }),
  ).not.toHaveClass('nav-activity-spin');
  expect(list).toHaveBeenCalledTimes(listReads);
  transport.conversations[1].activity_state = 'terminal';
  transport.conversations[1].activity_phase = 'completed';
  await act(async () => vi.advanceTimersByTimeAsync(15000));
  expect(
    screen.queryByRole('img', { name: 'Activity status unavailable' }),
  ).toBeNull();
  expect(controller.getSnapshot().hasMoreConversations).toBe(true);
  expect(list).toHaveBeenCalledTimes(listReads);
});

it('filters by conversation type and labels recency runs inside the recent list', async () => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date(2026, 8, 26, 12));
  const { controller, transport } = await setup(6);
  const days = [0, 0, 1, 3, 9, 9];
  const categories = [
    'chat',
    'designer',
    'code',
    'workflow',
    'designer',
    'chat',
  ] as const;
  transport.conversations.forEach((row, index) => {
    row.pinned = false;
    row.updated_at = new Date(2026, 8, 26 - days[index], 10).toISOString();
    row.category = categories[index];
  });
  const titles = transport.conversations.map((row) => row.title);
  await act(async () => controller.loadMoreConversations(true));
  const recent = screen.getByRole('list', { name: 'Recent conversations' });
  // Headings sit inside the first row of each run, so the list still has
  // exactly one item per conversation, in server order.
  expect(within(recent).getAllByRole('listitem')).toHaveLength(6);
  expect(
    within(recent)
      .getAllByRole('heading', { level: 4 })
      .map((heading) => heading.textContent),
  ).toEqual(['Today', 'Yesterday', 'This week', 'Older']);
  expect(rows().map((row) => row.getAttribute('aria-label'))).toEqual(titles);
  expect(
    screen.getByRole('radiogroup', { name: 'Filter conversations' }),
  ).toBeVisible();
  await act(async () =>
    fireEvent.click(screen.getByRole('radio', { name: 'Designs' })),
  );
  expect(rows().map((row) => row.getAttribute('aria-label'))).toEqual([
    titles[1],
    titles[4],
  ]);
  expect(localStorage.getItem('row-bot.sidebar-type.v1')).toBe('designer');
  await act(async () =>
    fireEvent.click(screen.getByRole('radio', { name: 'Chats' })),
  );
  expect(rows().map((row) => row.getAttribute('aria-label'))).toEqual([
    titles[0],
    titles[5],
  ]);
  fireEvent.click(screen.getByRole('radio', { name: 'All' }));
  expect(rows()).toHaveLength(6);
});

it('keeps Home, New chat, commands and Settings reachable on the collapsed rail (B11)', async () => {
  const transport = new FixtureTransport({ conversationCount: 3 });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  const onNewChat = vi.fn();
  const onCommands = vi.fn();
  render(
    <MemoryRouter initialEntries={['/conversations/conversation-a']}>
      <CurrentRoute />
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <OverlayProvider>
          <NavigationRail
            onNewChat={onNewChat}
            railActions={
              <button type="button" onClick={onCommands}>
                Workspace commands
              </button>
            }
          />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  const rail = screen.getByRole('navigation', { name: 'Collapsed navigation' });
  expect(
    screen.queryByRole('navigation', { name: 'Workspace navigation' }),
  ).toBeNull();
  // One New chat on the rail, with its shortcut; the ▾ lives in the sidebar.
  expect(
    within(rail).getAllByRole('button', { name: /New chat/ }),
  ).toHaveLength(1);
  expect(
    within(rail).getByRole('button', { name: 'New chat' }),
  ).toHaveAttribute('aria-keyshortcuts', 'Control+Shift+O');
  fireEvent.click(within(rail).getByRole('button', { name: 'New chat' }));
  expect(onNewChat).toHaveBeenCalledTimes(1);
  fireEvent.click(
    within(rail).getByRole('button', { name: 'Workspace commands' }),
  );
  expect(onCommands).toHaveBeenCalledTimes(1);
  expect(within(rail).getByRole('link', { name: 'Settings' })).toHaveAttribute(
    'href',
    '/settings/providers',
  );
  fireEvent.click(within(rail).getByRole('link', { name: 'Home' }));
  expect(
    screen.getByRole('status', { name: 'Current route' }),
  ).toHaveTextContent('/');
});
