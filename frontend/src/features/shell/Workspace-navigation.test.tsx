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
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import type { ReactNode } from 'react';
import { ClientController } from '../../api/controller';
import { FixtureTransport } from '../../api/fixtures';
import { createFakePlatform } from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider, useOverlay } from '../../ui/overlays';
import { deleteWithUndo } from './delete-with-undo';
import Workspace from './Workspace';
import { settingsReturnPath } from '../settings/return-path';

// JSDOM has no measured panes. Keep the production shell, navigation, router,
// composer and controller mounted while replacing only resize geometry.
vi.mock('react-resizable-panels', () => ({
  Group: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Panel: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Separator: () => <div />,
  usePanelRef: () => ({ current: null }),
}));

const clients: ClientController[] = [];
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});
afterEach(() => {
  clients.splice(0).forEach((controller) => controller.dispose());
  vi.unstubAllGlobals();
});

function HistoryControls() {
  const navigate = useNavigate();
  const location = useLocation();
  return (
    <>
      <output aria-label="Current route">{location.pathname}</output>
      <button onClick={() => navigate(-1)}>Browser Back</button>
      <button onClick={() => navigate(1)}>Browser Forward</button>
    </>
  );
}

it.each([1440, 900])(
  'Home adds one history entry and Back restores the same composer and draft at width %i',
  async (width) => {
    vi.stubGlobal('innerWidth', width);
    vi.stubGlobal('innerHeight', 900);
    const transport = new FixtureTransport({ conversationCount: 2 });
    const controller = new ClientController(transport, () => 1);
    clients.push(controller);
    await controller.start();
    await controller.selectConversation('conversation-a');
    const rendered = render(
      <MemoryRouter initialEntries={['/conversations/conversation-a']}>
        <HistoryControls />
        <RuntimeContext.Provider
          value={{ controller, platform: createFakePlatform() }}
        >
          <OverlayProvider>
            <Workspace />
          </OverlayProvider>
        </RuntimeContext.Provider>
      </MemoryRouter>,
    );
    expect(rendered.container.querySelector('.app-header')).toBeNull();
    // A conversation carries the workspace controls in its own single header
    // row (a phone goes back instead: see below).
    expect(rendered.container.querySelector('.compact-controls')).toBeNull();
    expect(
      screen.getByRole('button', { name: 'Workspace commands' }),
    ).toBeVisible();
    expect(screen.getByRole('button', { name: 'Open panel' })).toBeVisible();
    const composer = screen.getByRole('textbox', {
      name: 'Message',
    });
    await act(async () =>
      fireEvent.change(composer, {
        target: { value: 'Unsent draft retained across Home and Back' },
      }),
    );
    const commands = transport.counters.commands;
    const subscriptions = transport.counters.subscribes;
    const selectionVersion = controller.getSelectionVersion();
    if (width < 1024)
      fireEvent.click(
        screen.getByRole('button', { name: 'Toggle navigation' }),
      );
    expect(screen.getByRole('button', { name: 'New chat' })).toBeVisible();
    await act(async () =>
      fireEvent.click(screen.getByRole('link', { name: 'Home' })),
    );
    expect(screen.getByLabelText('Current route').textContent).toBe('/');
    expect(screen.getByRole('heading', { name: 'Home' })).toBeVisible();
    expect(composer).not.toBeVisible();
    expect(screen.queryByRole('dialog')).toBeNull();
    for (let cycle = 0; cycle < 2; cycle++) {
      await act(async () =>
        fireEvent.click(screen.getByRole('button', { name: 'Browser Back' })),
      );
      expect(screen.getByLabelText('Current route').textContent).toBe(
        '/conversations/conversation-a',
      );
      expect(screen.getByRole('textbox', { name: 'Message' })).toBe(composer);
      expect(composer).toBeVisible();
      expect(composer).toHaveValue(
        'Unsent draft retained across Home and Back',
      );
      expect(controller.getSnapshot().selectedConversationId).toBe(
        'conversation-a',
      );
      expect(controller.getSelectionVersion()).toBe(selectionVersion);
      expect(transport.counters.commands).toBe(commands);
      expect(transport.counters.subscribes).toBe(subscriptions);
      if (cycle === 0)
        await act(async () =>
          fireEvent.click(
            screen.getByRole('button', { name: 'Browser Forward' }),
          ),
        );
    }
  },
);

it('explains disconnected conversation state, preserves the local draft, and restores send eligibility after reconnect', async () => {
  vi.stubGlobal('innerWidth', 1440);
  vi.stubGlobal('innerHeight', 900);
  class ReadyWorkspaceTransport extends FixtureTransport {
    workspace = vi.fn(async (conversationId: string) => ({
      conversation_id: conversationId,
      revision: '1',
      controls: {},
      profiles: [],
      resources: [],
      actions: [{ action: 'send' as const, ready: true }],
    }));
  }
  const transport = new ReadyWorkspaceTransport({ conversationCount: 2 });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  await controller.selectConversation('conversation-a');
  const rendered = render(
    <MemoryRouter initialEntries={['/conversations/conversation-a']}>
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <OverlayProvider>
          <Workspace />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  const composer = screen.getByRole('textbox', { name: 'Message' });
  fireEvent.change(composer, { target: { value: 'Local reconnect draft' } });
  const send = screen.getByRole('button', { name: 'Send' });
  expect(send).toBeEnabled();
  await act(async () => controller.setOnline(false));
  const connectionStatus =
    rendered.container.querySelector('.connection-status');
  expect(connectionStatus).toHaveAttribute('role', 'status');
  expect(connectionStatus).toHaveTextContent('disconnected');
  const connectionAlert = screen
    .getByText('Connection interrupted', { exact: true })
    .closest('[role="alert"]');
  expect(connectionAlert).toHaveTextContent(
    'Disconnected. What you last saw is kept. Sending and live updates resume when Row-Bot reconnects.',
  );
  expect(screen.getByRole('button', { name: 'Reconnect' })).toBeEnabled();
  const composerReason = screen.getByText(
    'Reconnect to send. Your draft remains on this device.',
  );
  expect(composerReason).toHaveAttribute('role', 'status');
  expect(send).toBeDisabled();
  expect(send).toHaveAttribute('aria-describedby', composerReason.id);
  expect(composer).toHaveValue('Local reconnect draft');
  const commands = transport.counters.commands;
  fireEvent.keyDown(composer, { key: 'Enter' });
  expect(transport.counters.commands).toBe(commands);
  await act(async () => controller.setOnline(true));
  await waitFor(() => expect(connectionStatus).toHaveTextContent('Connected'));
  expect(
    screen.queryByText('Connection interrupted', { exact: true }),
  ).toBeNull();
  expect(screen.queryByText(/Reconnect to send/)).toBeNull();
  expect(composer).toHaveValue('Local reconnect draft');
  expect(send).toBeEnabled();
  expect(send).not.toHaveAttribute('aria-describedby');
});

function GoToSettings() {
  const navigate = useNavigate();
  return (
    <button onClick={() => navigate('/settings/buddy')}>Route elsewhere</button>
  );
}

it('closes the tablet navigation drawer when something in it changes the route', async () => {
  vi.stubGlobal('innerWidth', 900);
  vi.stubGlobal('innerHeight', 1180);
  const transport = new FixtureTransport({ conversationCount: 2 });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  await controller.selectConversation('conversation-a');
  render(
    <MemoryRouter initialEntries={['/conversations/conversation-a']}>
      <HistoryControls />
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <OverlayProvider>
          <Workspace />
          <GoToSettings />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Toggle navigation' }));
  expect(
    await screen.findByRole('dialog', { name: 'Conversations' }),
  ).toBeVisible();
  // Like Buddy settings in the drawer's footer: a route change from inside.
  await act(async () => {
    fireEvent.click(screen.getByText('Route elsewhere'));
  });
  await waitFor(() =>
    expect(screen.queryByRole('dialog', { name: 'Conversations' })).toBeNull(),
  );
  expect(screen.getByLabelText('Current route')).toHaveTextContent(
    '/settings/buddy',
  );
});

it('keeps Search out of a phone chat menu', async () => {
  vi.stubGlobal('innerWidth', 390);
  vi.stubGlobal('innerHeight', 844);
  const user = userEvent.setup();
  const transport = new FixtureTransport({ conversationCount: 2 });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  await controller.selectConversation('conversation-a');
  render(
    <MemoryRouter initialEntries={['/conversations/conversation-a']}>
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <OverlayProvider>
          <Workspace />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  const menu = screen.getByRole('button', { name: 'Conversation menu' });
  await user.click(menu);
  expect(
    await screen.findByRole('menuitem', { name: 'Find in conversation' }),
  ).toBeVisible();
  expect(screen.queryByRole('menuitem', { name: 'Search' })).toBeNull();
});

it('goes Home and says so when the open conversation is deleted in another window', async () => {
  vi.stubGlobal('innerWidth', 390);
  vi.stubGlobal('innerHeight', 844);
  const transport = new FixtureTransport({ conversationCount: 2 });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  await controller.selectConversation('conversation-a');
  render(
    <MemoryRouter initialEntries={['/conversations/conversation-a']}>
      <HistoryControls />
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <OverlayProvider>
          <Workspace />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  expect(screen.getByRole('textbox', { name: 'Message' })).toBeVisible();
  // Another window deleted it: the controller closes it.
  await act(async () => controller.forgetConversation('conversation-a'));
  await waitFor(() =>
    expect(screen.getByLabelText('Current route')).toHaveTextContent(/^\/$/),
  );
  expect(
    await screen.findByText('That conversation was deleted.'),
  ).toBeVisible();
});

it('remembers the chat Settings was opened from, for Close settings', async () => {
  const transport = new FixtureTransport({ conversationCount: 2 });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  await controller.selectConversation('conversation-a');
  render(
    <MemoryRouter initialEntries={['/conversations/conversation-a']}>
      <HistoryControls />
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <OverlayProvider>
          <Workspace />
          <GoToSettings />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  await act(async () => {
    fireEvent.click(screen.getByText('Route elsewhere'));
  });
  expect(screen.getByLabelText('Current route')).toHaveTextContent(
    '/settings/buddy',
  );
  expect(settingsReturnPath()).toBe('/conversations/conversation-a');
});

it('goes Home without "deleted elsewhere" when the person deletes the open chat with Undo', async () => {
  const transport = new FixtureTransport({ conversationCount: 2 });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  await controller.selectConversation('conversation-a');
  render(
    <MemoryRouter initialEntries={['/conversations/conversation-a']}>
      <HistoryControls />
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <OverlayProvider>
          <Workspace />
          <DeleteOpenChat controller={controller} />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  await act(async () => {
    fireEvent.click(screen.getByText('Delete the open chat'));
  });
  await waitFor(() =>
    expect(screen.getByLabelText('Current route')).toHaveTextContent(/^\/$/),
  );
  expect(await screen.findByText(/^Deleted '/)).toBeVisible();
  expect(screen.queryByText('That conversation was deleted.')).toBeNull();
});

function DeleteOpenChat({ controller }: { controller: ClientController }) {
  const overlay = useOverlay();
  return (
    <button
      onClick={() => {
        const row = controller
          .getSnapshot()
          .conversations.find((item) => item.id === 'conversation-a')!;
        deleteWithUndo(controller, overlay.notify, row);
      }}
    >
      Delete the open chat
    </button>
  );
}

it('stays on the chat while a sign-in is replaced after a restart', async () => {
  const transport = new FixtureTransport({ conversationCount: 2 });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  await controller.selectConversation('conversation-a');
  render(
    <MemoryRouter initialEntries={['/conversations/conversation-a']}>
      <HistoryControls />
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <OverlayProvider>
          <Workspace />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  // A restart drops the session: the chat closes for a moment, then the app is ready again.
  const update = (patch: object) =>
    (controller as unknown as { update(patch: object): void }).update(patch);
  await act(async () =>
    update({ selectedConversationId: null, status: 'unauthorized' }),
  );
  await act(async () => update({ status: 'ready' }));
  expect(screen.getByLabelText('Current route')).toHaveTextContent(
    '/conversations/conversation-a',
  );
  expect(screen.queryByText('That conversation was deleted.')).toBeNull();
});

it('says whether the sidebar is open, and leaves out the conversation skip link in Settings', async () => {
  vi.stubGlobal('innerWidth', 1440);
  vi.stubGlobal('innerHeight', 900);
  const transport = new FixtureTransport({ conversationCount: 2 });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  render(
    <MemoryRouter initialEntries={['/conversations/conversation-a']}>
      <HistoryControls />
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <OverlayProvider>
          <Workspace />
          <GoToSettings />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  expect(
    screen.getAllByRole('button', { name: 'Toggle navigation' })[0],
  ).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByText('Skip to conversation')).toBeInTheDocument();
  await act(async () => {
    fireEvent.click(screen.getByText('Route elsewhere'));
  });
  expect(screen.queryByText('Skip to conversation')).toBeNull();
});

it("opens one new chat from the installed app's New chat shortcut", async () => {
  vi.stubGlobal('innerWidth', 390);
  vi.stubGlobal('innerHeight', 844);
  const transport = new FixtureTransport({ conversationCount: 2 });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  const commands = transport.counters.commands;
  render(
    <MemoryRouter initialEntries={['/?new=chat']}>
      <HistoryControls />
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <OverlayProvider>
          <Workspace />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  await waitFor(
    () =>
      expect(screen.getByLabelText('Current route').textContent).toMatch(
        /^\/conversations\/fixture-/,
      ),
    { timeout: 3000 },
  );
  expect(screen.getByRole('textbox', { name: 'Message' })).toBeVisible();
  expect(transport.counters.commands).toBe(commands + 1);
});

it('keeps what was typed on Home while a slow New chat opens (M12)', async () => {
  vi.stubGlobal('innerWidth', 1440);
  vi.stubGlobal('innerHeight', 900);
  const transport = new FixtureTransport({ conversationCount: 2 });
  // Over a slow link the new chat takes a while to be made.
  let arrive!: () => void;
  const slow = new Promise<void>((resolve) => (arrive = resolve));
  const command = transport.command.bind(transport);
  vi.spyOn(transport, 'command').mockImplementation(async (...args) => {
    if (args[1].type === 'conversation.create') await slow;
    return command(...args);
  });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  render(
    <MemoryRouter initialEntries={['/']}>
      <HistoryControls />
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <OverlayProvider>
          <Workspace />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );

  const ask = await screen.findByRole(
    'textbox',
    { name: 'Ask Row-Bot' },
    { timeout: 3000 },
  );
  fireEvent.click(screen.getByRole('button', { name: 'New chat' }));
  fireEvent.change(ask, {
    target: { value: 'Suggest a name for a second cat' },
  });
  await act(async () => arrive());

  await waitFor(
    () =>
      expect(screen.getByLabelText('Current route').textContent).toMatch(
        /^\/conversations\/fixture-/,
      ),
    { timeout: 3000 },
  );
  expect(screen.getByRole('textbox', { name: 'Message' })).toHaveValue(
    'Suggest a name for a second cat',
  );
});

async function onPhone(entries: string[]) {
  vi.stubGlobal('innerWidth', 390);
  vi.stubGlobal('innerHeight', 844);
  const transport = new FixtureTransport({ conversationCount: 2 });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  render(
    <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
      <HistoryControls />
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <OverlayProvider>
          <Workspace />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
}

const route = () => screen.getByLabelText('Current route').textContent;

function swipeFromLeftEdge() {
  for (const [type, x] of [
    ['touchstart', 6],
    ['touchmove', 70],
    ['touchmove', 140],
    ['touchend', 140],
  ] as const) {
    const event = new Event(type, { bubbles: true });
    Object.defineProperty(event, 'touches', {
      value: type === 'touchend' ? [] : [{ clientX: x, clientY: 300 }],
    });
    act(() => {
      document.body.dispatchEvent(event);
    });
  }
}

it('goes back from a phone chat to the tab it came from, by â€¹ or a swipe from the left edge, and has no drawer', async () => {
  await onPhone(['/library', '/conversations/conversation-a']);
  expect(
    screen.queryByRole('button', { name: 'Toggle navigation' }),
  ).toBeNull();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Back' })),
  );
  expect(route()).toBe('/library');
  // On a tab the swipe does nothing: there is nowhere further back.
  swipeFromLeftEdge();
  expect(route()).toBe('/library');
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Browser Forward' })),
  );
  expect(route()).toBe('/conversations/conversation-a');
  swipeFromLeftEdge();
  await waitFor(() => expect(route()).toBe('/library'));
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('goes back to Chats from a phone chat opened straight from a link', async () => {
  await onPhone(['/conversations/conversation-a']);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Back' })),
  );
  expect(route()).toBe('/library');
});

// Back from what Search opened is covered in phone-shell.spec.ts: the
// palette's history step lives in the browser's history, not a MemoryRouter's.
it('searches chats and settings on a phone, with no commands', async () => {
  const user = userEvent.setup();
  await onPhone(['/library']);
  await user.click(await screen.findByRole('button', { name: 'Search' }));
  const search = await screen.findByRole('dialog', { name: 'Search' });
  expect(search).toBeVisible();
  expect(screen.queryByRole('group', { name: 'Commands' })).toBeNull();
  const field = screen.getByRole('searchbox', {
    name: 'Search chats, files and settings',
  });
  await user.type(field, 'models');
  expect(
    within(screen.getByRole('group', { name: 'Settings' })).getByRole(
      'option',
      {
        name: /^Models settings/,
      },
    ),
  ).toBeVisible();
  await user.clear(field);
  await user.type(field, 'place for your ideas');
  await user.click(
    within(screen.getByRole('group', { name: 'Chats' })).getByRole('option', {
      name: 'A place for your ideas',
    }),
  );
  await waitFor(() => expect(route()).toBe('/conversations/conversation-a'));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
});

it('closes phone Search with its back arrow', async () => {
  const user = userEvent.setup();
  await onPhone(['/']);
  const opener = await screen.findByRole('button', { name: 'Search' });
  await user.click(opener);
  await user.click(
    within(await screen.findByRole('dialog', { name: 'Search' })).getByRole(
      'button',
      { name: 'Close search' },
    ),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(route()).toBe('/');
  await waitFor(() => expect(opener).toHaveFocus());
});
