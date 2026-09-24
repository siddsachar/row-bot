import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { ClientController } from '../../api/controller';
import { FixtureTransport } from '../../api/fixtures';
import { OverlayProvider } from '../../ui/overlays';
import ConversationLibrary from './ConversationLibrary';

const clients: ClientController[] = [];
afterEach(() => {
  clients.splice(0).forEach((controller) => controller.dispose());
  vi.restoreAllMocks();
  sessionStorage.clear();
});

async function setup(count = 55) {
  const transport = new FixtureTransport({ conversationCount: count });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  render(
    <MemoryRouter>
      <OverlayProvider>
        <ConversationLibrary controller={controller} />
      </OverlayProvider>
    </MemoryRouter>,
  );
  await screen.findByRole('combobox', { name: 'Conversation type' });
  return { controller, transport };
}

it('reads beyond the sidebar page and selects every conversation in a type filter', async () => {
  const { transport } = await setup();
  transport.conversations.forEach((row) => {
    row.category = 'chat';
  });
  transport.conversations[52].category = 'code';
  transport.conversations[53].category = 'code';
  // Refresh explicitly because the fixture categories changed after the first read.
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Refresh library' })),
  );
  fireEvent.change(
    screen.getByRole('combobox', { name: 'Conversation type' }),
    {
      target: { value: 'code' },
    },
  );
  expect(screen.getByRole('option', { name: 'Code 2' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Select' }));
  fireEvent.click(screen.getByRole('button', { name: 'Select all in filter' }));
  expect(screen.getByText('2 selected')).toBeVisible();
  expect(transport.counters.commands).toBe(0);
  fireEvent.click(screen.getByRole('button', { name: 'Delete selected' }));
  const review = screen.getByRole('alertdialog');
  expect(
    within(review).getByText(/Bound designs and workspaces remain/),
  ).toBeVisible();
  expect(transport.counters.commands).toBe(0);
  fireEvent.click(
    within(review).getByRole('button', { name: 'Delete 2 conversations' }),
  );
  await waitFor(() => expect(transport.conversations).toHaveLength(53));
  expect(
    transport.conversations.some((row) => row.id === 'conversation-53'),
  ).toBe(false);
  expect(
    transport.conversations.some((row) => row.id === 'conversation-54'),
  ).toBe(false);
  expect(transport.counters.commands).toBe(2);
  expect(
    await screen.findByText('2 deleted; 0 need review; 0 not started.'),
  ).toBeVisible();
});

it('keeps stale failures selected and permits review again without repeating successful deletion', async () => {
  const { transport } = await setup(2);
  const original = transport.command.bind(transport);
  vi.spyOn(transport, 'command').mockImplementation(
    async (target, command, key, signal) => {
      if (target === 'conversation-2')
        throw { code: 'revision_conflict', status: 409 };
      return original(target, command, key, signal);
    },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Select' }));
  fireEvent.click(screen.getByRole('button', { name: 'Select all in filter' }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete selected' }));
  fireEvent.click(
    screen.getByRole('alertdialog').querySelector('button:last-child')!,
  );
  expect(
    await screen.findByText('1 deleted; 1 need review; 0 not started.'),
  ).toBeVisible();
  expect(transport.conversations.map((row) => row.id)).toEqual([
    'conversation-2',
  ]);
  expect(screen.getByText('1 selected')).toBeVisible();
  expect(screen.getByText(/Sample conversation 2:/)).toBeVisible();
});

it('retries an uncertain deletion with the original command identity', async () => {
  const { transport } = await setup(1);
  const original = transport.command.bind(transport);
  const send = vi
    .spyOn(transport, 'command')
    .mockRejectedValueOnce({ code: 'operation_uncertain' })
    .mockImplementation(original);
  fireEvent.click(screen.getByRole('button', { name: 'Select' }));
  fireEvent.click(screen.getByRole('button', { name: 'Select all in filter' }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete selected' }));
  fireEvent.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'Delete 1 conversation',
    }),
  );
  expect(
    await screen.findByText('0 deleted; 1 need review; 0 not started.'),
  ).toBeVisible();
  fireEvent.click(
    screen.getByRole('button', { name: 'Retry uncertain deletions' }),
  );
  await waitFor(() => expect(transport.conversations).toHaveLength(0));
  expect(send).toHaveBeenCalledTimes(2);
  expect(send.mock.calls[1][2]).toBe(send.mock.calls[0][2]);
  expect(
    await screen.findByText('1 deleted; 0 need review; 0 not started.'),
  ).toBeVisible();
});

it('stops before the next destructive command and retains unstarted selection', async () => {
  const { transport } = await setup(3);
  const original = transport.command.bind(transport);
  let release: (() => void) | undefined;
  const first = new Promise<void>((resolve) => {
    release = resolve;
  });
  const send = vi
    .spyOn(transport, 'command')
    .mockImplementation(async (target, command, key, signal) => {
      if (target === 'conversation-a') await first;
      return original(target, command, key, signal);
    });
  fireEvent.click(screen.getByRole('button', { name: 'Select' }));
  fireEvent.click(screen.getByRole('button', { name: 'Select all in filter' }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete selected' }));
  fireEvent.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'Delete 3 conversations',
    }),
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Stop after current deletion' }),
  );
  await act(async () => release?.());
  expect(
    await screen.findByText('1 deleted; 0 need review; 2 not started.'),
  ).toBeVisible();
  expect(transport.conversations).toHaveLength(2);
  expect(send).toHaveBeenCalledTimes(1);
  expect(screen.getByText('2 selected')).toBeVisible();
});

it('blocks deletion from a stale library after a failed refresh and recovers on retry', async () => {
  const { transport } = await setup(2);
  const list = vi.spyOn(transport, 'listConversations');
  list.mockRejectedValueOnce({ code: 'cursor_expired', status: 409 });
  fireEvent.click(screen.getByRole('button', { name: 'Select' }));
  fireEvent.click(screen.getByRole('button', { name: 'Select all in filter' }));
  fireEvent.click(screen.getByRole('button', { name: 'Refresh library' }));
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Delete selected' }),
  ).toBeDisabled();
  expect(transport.counters.commands).toBe(0);
  fireEvent.click(screen.getByRole('button', { name: 'Refresh library' }));
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Delete selected' }),
    ).toBeEnabled(),
  );
  expect(screen.queryByRole('alert')).toBeNull();
});

it('shows retained Developer work and cleanup warnings in the deletion receipt', async () => {
  const { transport } = await setup(1);
  const original = transport.command.bind(transport);
  vi.spyOn(transport, 'command').mockImplementation(async (...args) => ({
    ...(await original(...args)),
    retained_developer_work: true,
    deletion_warnings: [
      'A Developer sandbox with unimported changes was kept.',
    ],
  }));
  fireEvent.click(screen.getByRole('button', { name: 'Select' }));
  fireEvent.click(screen.getByRole('button', { name: 'Select all in filter' }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete selected' }));
  fireEvent.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'Delete 1 conversation',
    }),
  );
  expect(
    await screen.findByText(
      /A Developer sandbox with unimported changes was kept/,
    ),
  ).toBeVisible();
  expect(screen.getByText('Retained work and cleanup warnings:')).toBeVisible();
});

it('checks an interrupted deletion receipt after remount before allowing another batch', async () => {
  const transport = new FixtureTransport({ conversationCount: 1 });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  const session = controller.getSnapshot().handshake!.client_session_id;
  const key = crypto.randomUUID();
  sessionStorage.setItem(
    'row-bot.conversation-library.pending-delete.v1',
    JSON.stringify({
      target: 'conversation-a',
      key,
      revision: '0',
      session,
    }),
  );
  const receipt = vi.spyOn(transport, 'receipt').mockResolvedValue({
    command_id: key,
    conversation_id: 'conversation-a',
    status: 'DeleteCompleted',
  });
  transport.conversations.splice(0, 1);
  render(
    <MemoryRouter>
      <OverlayProvider>
        <ConversationLibrary controller={controller} />
      </OverlayProvider>
    </MemoryRouter>,
  );
  expect(
    await screen.findByText(/earlier deletion has an uncertain result/),
  ).toBeVisible();
  expect(transport.counters.commands).toBe(0);
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original deletion receipt' }),
  );
  expect(
    await screen.findByText(
      'The earlier deletion completed. The library has been refreshed.',
    ),
  ).toBeVisible();
  expect(receipt).toHaveBeenCalledWith(key, expect.any(AbortSignal));
  expect(
    sessionStorage.getItem('row-bot.conversation-library.pending-delete.v1'),
  ).toBeNull();
  expect(transport.counters.commands).toBe(0);
});

it('keeps a blocked active conversation for a fresh user review and later retry', async () => {
  const { transport } = await setup(1);
  const original = transport.command.bind(transport);
  const send = vi
    .spyOn(transport, 'command')
    .mockResolvedValueOnce({
      command_id: crypto.randomUUID(),
      conversation_id: 'conversation-a',
      status: 'DeleteBlocked',
    })
    .mockImplementation(original);
  fireEvent.click(screen.getByRole('button', { name: 'Select' }));
  fireEvent.click(screen.getByRole('button', { name: 'Select all in filter' }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete selected' }));
  fireEvent.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'Delete 1 conversation',
    }),
  );
  expect(
    await screen.findByText('0 deleted; 1 need review; 0 not started.'),
  ).toBeVisible();
  expect(transport.conversations).toHaveLength(1);
  expect(send).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Delete selected' }));
  fireEvent.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'Delete 1 conversation',
    }),
  );
  await waitFor(() => expect(transport.conversations).toHaveLength(0));
  expect(send).toHaveBeenCalledTimes(2);
});

it('reports an authorization denial without deleting or automatically retrying', async () => {
  const { transport } = await setup(1);
  const send = vi
    .spyOn(transport, 'command')
    .mockRejectedValue({ code: 'action_denied', status: 403 });
  fireEvent.click(screen.getByRole('button', { name: 'Select' }));
  fireEvent.click(screen.getByRole('button', { name: 'Select all in filter' }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete selected' }));
  fireEvent.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'Delete 1 conversation',
    }),
  );
  expect(
    await screen.findByText('0 deleted; 1 need review; 0 not started.'),
  ).toBeVisible();
  expect(transport.conversations).toHaveLength(1);
  expect(send).toHaveBeenCalledTimes(1);
  expect(
    screen.queryByRole('button', { name: 'Retry uncertain deletions' }),
  ).toBeNull();
});
