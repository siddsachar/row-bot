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
import Navigation from './Navigation';
import { createAuthenticatedEditorOwner } from '../settings/authenticated-editor-owner';
import { createGoalProfileSettingsSession } from '../settings/GoalProfileSettings';

const clients: ClientController[] = [];
afterEach(() => {
  clients.splice(0).forEach((controller) => controller.dispose());
  vi.restoreAllMocks();
  vi.useRealTimers();
});

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
  group?: 'pinned' | 'artifact' | 'workspace',
) {
  const onOpenConversation = vi.fn();
  const onOpenHome = vi.fn();
  const onNewChat = vi.fn();
  const transport = new FixtureTransport({ conversationCount: count });
  const list = vi.spyOn(transport, 'listConversations');
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  if (group) await controller.setConversationGroup(group);
  render(
    <MemoryRouter initialEntries={[route]}>
      <CurrentRoute />
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <OverlayProvider>
          <Navigation
            onOpenConversation={onOpenConversation}
            onOpenHome={onOpenHome}
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
    onOpenHome,
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
    name: /Agent profiles.*1 built-in.*0 custom/,
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

it('starts with ten ordered rows and expands without fetching or losing cursor access', async () => {
  const { list, transport } = await setup();
  expect(rows()).toHaveLength(10);
  expect(rows().map((row) => row.getAttribute('aria-label'))).toEqual(
    transport.conversations.slice(0, 10).map(({ title }) => title),
  );
  expect(
    screen.queryByRole('button', { name: 'Load more conversations' }),
  ).toBeNull();
  expect(list).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Show all' }));
  expect(rows()).toHaveLength(50);
  expect(list).toHaveBeenCalledTimes(1);
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Load more conversations' }),
    ),
  );
  expect(rows()).toHaveLength(55);
  expect(list).toHaveBeenCalledTimes(2);
  expect(rows().map((row) => row.getAttribute('aria-label'))).toEqual(
    transport.conversations.map(({ title }) => title),
  );
  expect(
    screen.queryByRole('button', { name: 'Load more conversations' }),
  ).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Show less' }));
  expect(rows()).toHaveLength(10);
  fireEvent.click(screen.getByRole('button', { name: 'Show all' }));
  expect(rows()).toHaveLength(55);
  expect(list).toHaveBeenCalledTimes(2);
  expect(transport.counters.commands).toBe(0);
});

it('removes the group selector and restores the unified list from an earlier group', async () => {
  const { controller, list } = await setup(2, '/', 'pinned');
  await waitFor(() =>
    expect(controller.getSnapshot().conversationGroup).toBe('all'),
  );
  expect(
    screen.queryByRole('combobox', { name: 'Conversation group' }),
  ).toBeNull();
  expect(rows()).toHaveLength(2);
  expect(list).toHaveBeenLastCalledWith(
    undefined,
    expect.any(AbortSignal),
    'all',
  );
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

it('opens Home without a creation, Stop, selection change or draft mutation', async () => {
  const { controller, transport, onOpenHome, onNewChat } = await setup(
    2,
    '/conversations/conversation-a',
  );
  await act(async () => controller.selectConversation('conversation-a'));
  const before = controller.getSnapshot();
  const commands = transport.counters.commands;
  fireEvent.click(screen.getByRole('link', { name: 'Home' }));
  expect(screen.getByLabelText('Current route')).toHaveTextContent('/');
  expect(screen.getByRole('link', { name: 'Home' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(rows()[0]).not.toHaveAttribute('aria-current');
  expect(controller.getSnapshot()).toBe(before);
  expect(transport.counters.commands).toBe(commands);
  expect(onOpenHome).toHaveBeenCalledTimes(1);
  expect(onNewChat).not.toHaveBeenCalled();
});

it('delegates sidebar New chat and navigates Settings to the persistent shell', async () => {
  const { transport, onNewChat } = await setup();
  fireEvent.click(screen.getByRole('button', { name: 'New chat' }));
  fireEvent.click(screen.getByRole('link', { name: 'Settings' }));
  expect(onNewChat).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText('Current route')).toHaveTextContent(
    '/settings/providers',
  );
  expect(transport.counters.commands).toBe(0);
});

it('groups primary actions, conversations and secondary destinations for compact navigation', async () => {
  await setup(2);
  expect(
    screen.getByRole('group', { name: 'Primary workspace actions' }),
  ).toBeVisible();
  expect(screen.getByRole('region', { name: 'Conversations' })).toBeVisible();
  expect(screen.getByText('About and developer utilities')).toBeVisible();
  expect(
    screen.getByText('About and developer utilities').closest('details'),
  ).not.toHaveAttribute('open');
  fireEvent.click(screen.getByText('About and developer utilities'));
  expect(
    screen.getByText('About and developer utilities').closest('details'),
  ).toHaveAttribute('open');
  expect(screen.getByRole('link', { name: 'Component gallery' })).toBeVisible();
});

it('supports an empty collapsible section without introducing commands or controls for nonexistent pages', async () => {
  const { transport } = await setup(0);
  expect(
    screen.getByText('Your conversations will appear here.'),
  ).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Show all' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Conversations' }));
  expect(screen.queryByText('Your conversations will appear here.')).toBeNull();
  expect(
    screen.getByRole('link', { name: 'Current application' }),
  ).toHaveAttribute('href', '/');
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
