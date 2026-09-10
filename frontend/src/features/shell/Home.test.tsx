import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type {
  ConversationPage,
  ResourceChoice,
  ResourceChoicePage,
} from '../../api/types';
import Home from './Home';

const mock = vi.hoisted(() => ({
  state: {
    status: 'ready',
    handshake: { instance_id: 'server-a', client_session_id: 'session-a' },
  },
  controller: { recentConversations: vi.fn(), library: vi.fn() },
}));
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: mock.controller }),
  useClientState: () => mock.state,
}));

const props = () => ({
  onNewChat: vi.fn(),
  onSetup: vi.fn(),
  onOpenConversation: vi.fn(),
  onSearch: vi.fn(),
});
const resource = (
  id: string,
  kind: ResourceChoice['kind'] = 'artifact',
): ResourceChoice => ({
  resource_id: id,
  kind,
  name: 'Synthetic resource',
  revision: `revision-${id}`,
  origin_status: 'available',
  origin_conversation_id: `conversation-${id}`,
  available: true,
});
beforeEach(() => {
  vi.resetAllMocks();
  mock.state.status = 'ready';
  mock.state.handshake = {
    instance_id: 'server-a',
    client_session_id: 'session-a',
  };
  mock.controller.recentConversations.mockResolvedValue({
    items: [],
    has_more: false,
  });
  mock.controller.library.mockResolvedValue({ items: [] });
});

it('reads three bounded real libraries without creating or selecting a conversation', async () => {
  const callbacks = props();
  await act(async () => render(<Home {...callbacks} />));
  expect(mock.controller.recentConversations).toHaveBeenCalledTimes(1);
  expect(
    mock.controller.library.mock.calls.map(([kind, cursor]) => [kind, cursor]),
  ).toEqual([
    ['artifact', undefined],
    ['workspace', undefined],
  ]);
  expect(
    screen.getByText('Your conversations will appear here.'),
  ).toBeVisible();
  expect(screen.getByText('No saved Decks yet.')).toBeVisible();
  expect(screen.getByText('No saved workspaces yet.')).toBeVisible();
  for (const callback of Object.values(callbacks))
    expect(callback).not.toHaveBeenCalled();
  expect(
    screen.getByRole('link', { name: 'Open in current app' }),
  ).toHaveAttribute('href', '/');
  expect(screen.queryByRole('tab', { name: 'Monitor' })).toBeNull();
});

it('shows six recent rows in service order with full titles and routes search to the full library', async () => {
  const items: ConversationPage['items'] = Array.from(
    { length: 20 },
    (_, index) => ({
      id: `conversation-${index}`,
      revision: '1',
      title: index ? `Conversation ${index}` : 'A full long title '.repeat(20),
      pinned: index === 1,
    }),
  );
  mock.controller.recentConversations.mockResolvedValue({
    items,
    has_more: true,
    next_cursor: 'next',
  });
  const callbacks = props();
  await act(async () => render(<Home {...callbacks} />));
  const section = within(
    screen.getByRole('region', { name: 'Recent conversations' }),
  );
  const buttons = within(section.getByRole('list')).getAllByRole('button');
  expect(buttons).toHaveLength(6);
  expect(buttons.map((button) => button.getAttribute('aria-label'))).toEqual(
    items.slice(0, 6).map((item) => `Open ${item.title}`),
  );
  fireEvent.click(buttons[2]);
  expect(callbacks.onOpenConversation).toHaveBeenCalledWith('conversation-2');
  fireEvent.click(
    section.getByRole('button', { name: 'Search all conversations' }),
  );
  expect(callbacks.onSearch).toHaveBeenCalledTimes(1);
  expect(mock.controller.recentConversations).toHaveBeenCalledTimes(1);
});

it('delegates New chat and distinct domain starters to their shared owners', async () => {
  const callbacks = props();
  await act(async () => render(<Home {...callbacks} />));
  fireEvent.click(screen.getByRole('button', { name: 'New chat' }));
  fireEvent.click(screen.getByRole('button', { name: 'Create Deck' }));
  fireEvent.click(screen.getByRole('button', { name: 'Open folder' }));
  fireEvent.click(
    screen.getByRole('button', { name: 'Browse all workspaces' }),
  );
  expect(callbacks.onNewChat).toHaveBeenCalledTimes(1);
  expect(callbacks.onSetup.mock.calls).toEqual([
    [{ kind: 'artifact', mode: 'create' }],
    [{ kind: 'workspace', mode: 'create' }],
    [{ kind: 'workspace', mode: 'existing' }],
  ]);
});

it('preserves identical saved names and exact resource revisions for canonical setup and repair', async () => {
  const first = resource('workspace-first', 'workspace');
  const second = {
    ...resource('workspace-second', 'workspace'),
    origin_status: 'repair_required' as const,
  };
  mock.controller.library.mockImplementation(async (kind: string) => ({
    items: kind === 'workspace' ? [first, second] : [],
  }));
  const callbacks = props();
  await act(async () => render(<Home {...callbacks} />));
  const section = within(
    screen.getByRole('region', { name: 'Developer library' }),
  );
  const buttons = section.getAllByRole('button', {
    name: 'Open Synthetic resource',
  });
  expect(
    section.getByText('Saved workspace ID: workspace-first'),
  ).toBeInTheDocument();
  expect(
    section.getByText('Saved workspace ID: workspace-second'),
  ).toBeInTheDocument();
  expect(section.getByText('Review missing conversation')).toBeVisible();
  fireEvent.click(buttons[1]);
  expect(callbacks.onSetup).toHaveBeenCalledWith({
    kind: 'workspace',
    mode: 'existing',
    resource: second,
  });
  expect(callbacks.onOpenConversation).not.toHaveBeenCalled();
});

it('keeps unavailable entries truthful and bounds cards while preserving full-library access', async () => {
  const items = Array.from({ length: 10 }, (_, index) => ({
    ...resource(`deck-${index}`),
    available: index !== 0,
  }));
  mock.controller.library.mockImplementation(async (kind: string) => ({
    items: kind === 'artifact' ? items : [],
    next_cursor: 'next',
  }));
  const callbacks = props();
  await act(async () => render(<Home {...callbacks} />));
  const section = within(
    screen.getByRole('region', { name: 'Designer library' }),
  );
  const buttons = section.getAllByRole('button', {
    name: 'Open Synthetic resource',
  });
  expect(buttons).toHaveLength(6);
  expect(buttons[0]).toBeDisabled();
  expect(section.getByText('Unavailable')).toBeVisible();
  fireEvent.click(buttons[0]);
  expect(callbacks.onSetup).not.toHaveBeenCalled();
  fireEvent.click(section.getByRole('button', { name: 'Browse all Decks' }));
  expect(callbacks.onSetup).toHaveBeenCalledWith({
    kind: 'artifact',
    mode: 'existing',
  });
});

it('retries a failed section independently without misrepresenting failure as an empty library', async () => {
  mock.controller.library.mockImplementationOnce(async () => {
    throw { status: 503 };
  });
  const callbacks = props();
  await act(async () => render(<Home {...callbacks} />));
  const section = within(
    screen.getByRole('region', { name: 'Designer library' }),
  );
  expect(section.getByRole('alert')).toBeVisible();
  expect(section.queryByText('No saved Decks yet.')).toBeNull();
  await act(async () =>
    fireEvent.click(section.getByRole('button', { name: 'Retry Decks' })),
  );
  expect(section.queryByRole('alert')).toBeNull();
  expect(section.getByText('No saved Decks yet.')).toBeVisible();
  expect(mock.controller.library).toHaveBeenCalledTimes(3);
  expect(mock.controller.recentConversations).toHaveBeenCalledTimes(1);
});

it('aborts old session reads and never installs late resource data into a replacement session', async () => {
  let resolveOld!: (page: ResourceChoicePage) => void;
  let oldSignal!: AbortSignal;
  mock.controller.library.mockImplementationOnce(
    (_kind, _cursor, signal: AbortSignal) => {
      oldSignal = signal;
      return new Promise<ResourceChoicePage>((resolve) => {
        resolveOld = resolve;
      });
    },
  );
  const callbacks = props();
  const view = render(<Home {...callbacks} />);
  mock.state.handshake = {
    instance_id: 'server-b',
    client_session_id: 'session-b',
  };
  await act(async () => view.rerender(<Home {...callbacks} />));
  expect(oldSignal.aborted).toBe(true);
  await act(async () => resolveOld({ items: [resource('obsolete')] }));
  expect(screen.queryByText('Deck ID: obsolete')).toBeNull();
  expect(screen.getByText('No saved Decks yet.')).toBeVisible();
  expect(mock.controller.recentConversations).toHaveBeenCalledTimes(2);
});

it('does no library work while disconnected and hides previous-session content', async () => {
  mock.state.status = 'disconnected';
  const callbacks = props();
  await act(async () => render(<Home {...callbacks} />));
  expect(mock.controller.library).not.toHaveBeenCalled();
  expect(mock.controller.recentConversations).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'New chat' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Open folder' })).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent('Connect to open');
});
