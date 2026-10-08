import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import type { ReactNode } from 'react';
import { ClientController } from '../../api/controller';
import { FixtureTransport } from '../../api/fixtures';
import { createFakePlatform } from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
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

it.each([1440, 900, 390])(
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
    // row; phones fold them into the header's ⋯.
    expect(rendered.container.querySelector('.compact-controls')).toBeNull();
    if (width >= 768) {
      expect(
        screen.getByRole('button', { name: 'Workspace commands' }),
      ).toBeVisible();
      expect(screen.getByRole('button', { name: 'Open panel' })).toBeVisible();
    } else {
      expect(
        screen.getByRole('button', { name: 'Conversation menu' }),
      ).toBeVisible();
    }
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

it('closes the phone navigation drawer when something in it changes the route', async () => {
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

it('returns focus to the phone header menu after Workspace commands opened from it', async () => {
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
  await user.click(
    await screen.findByRole('menuitem', { name: 'Workspace commands' }),
  );
  expect(
    await screen.findByRole('dialog', { name: 'Workspace commands' }),
  ).toBeVisible();
  await user.keyboard('{Escape}');
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Workspace commands' }),
    ).toBeNull(),
  );
  // The menu item that opened it is gone; its trigger takes focus back.
  await waitFor(() => expect(menu).toHaveFocus());
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
