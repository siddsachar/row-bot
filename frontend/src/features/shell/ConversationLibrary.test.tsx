import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { ClientController } from '../../api/controller';
import { FixtureTransport } from '../../api/fixtures';
import { createFakePlatform } from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import ConversationLibrary, {
  deleteOneConversation,
} from './ConversationLibrary';

const clients: ClientController[] = [];
afterEach(() => {
  clients.splice(0).forEach((controller) => controller.dispose());
  vi.restoreAllMocks();
  sessionStorage.clear();
});

function renderLibrary(
  controller: ClientController,
  initialSelectedId?: string,
) {
  const platform = createFakePlatform({
    save: { status: 'ok', value: { kind: 'file' } },
  });
  const save = vi.spyOn(platform, 'save');
  render(
    <MemoryRouter>
      <RuntimeContext.Provider value={{ controller, platform }}>
        <OverlayProvider>
          <ConversationLibrary initialSelectedId={initialSelectedId} />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  return { save };
}

async function setup(count = 55, initialSelectedId?: string) {
  const transport = new FixtureTransport({ conversationCount: count });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  const { save } = renderLibrary(controller, initialSelectedId);
  await screen.findAllByRole('checkbox', { name: /^Select / });
  return { controller, transport, save };
}

const bar = () =>
  screen.queryByRole('toolbar', { name: 'Selected conversations' });
const tick = (title: string) =>
  fireEvent.click(screen.getByRole('checkbox', { name: `Select ${title}` }));

/** Selecting starts by ticking any row; the bar then selects the filter. */
function selectAllInFilter() {
  fireEvent.click(screen.getAllByRole('checkbox', { name: /^Select / })[0]);
  const all = within(bar()!).getByRole('checkbox', {
    name: /^Select all \d+ in/,
  });
  if (!(all as HTMLInputElement).checked) fireEvent.click(all);
}
const deleteSelected = () =>
  fireEvent.click(within(bar()!).getByRole('button', { name: 'Delete…' }));

it('shows the selection bar above the list on the first tick; Esc and Clear selection clear it (B270)', async () => {
  await setup(5);
  expect(bar()).toBeNull();
  expect(screen.getByText('5 conversations · newest first')).toBeVisible();
  tick('Sample conversation 3');
  const toolbar = bar()!;
  expect(within(toolbar).getByText('1 selected')).toBeVisible();
  // The bar leads the list instead of following it.
  const firstRow = screen.getByRole('checkbox', {
    name: 'Select A place for your ideas',
  });
  expect(
    toolbar.compareDocumentPosition(firstRow) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  for (const name of ['Pin', 'Export', 'Delete…', 'Clear selection'])
    expect(within(toolbar).getByRole('button', { name })).toBeVisible();

  fireEvent.keyDown(
    screen.getByRole('checkbox', { name: 'Select Sample conversation 3' }),
    { key: 'Escape' },
  );
  expect(bar()).toBeNull();
  tick('Sample conversation 4');
  fireEvent.click(
    within(bar()!).getByRole('button', { name: 'Clear selection' }),
  );
  expect(bar()).toBeNull();
});

it('selects a range with Shift and ticks the focused row with Space (B270)', async () => {
  await setup(8);
  tick('Sample conversation 2');
  fireEvent.click(
    screen.getByRole('checkbox', { name: 'Select Sample conversation 5' }),
    { shiftKey: true },
  );
  expect(within(bar()!).getByText('4 selected')).toBeVisible();
  for (const index of [2, 3, 4, 5])
    expect(
      screen.getByRole('checkbox', {
        name: `Select Sample conversation ${index}`,
      }),
    ).toBeChecked();
  fireEvent.keyDown(
    screen.getByRole('link', { name: /^Sample conversation 7/ }),
    { key: ' ' },
  );
  expect(within(bar()!).getByText('5 selected')).toBeVisible();
  expect(
    screen.getByRole('checkbox', { name: 'Select Sample conversation 7' }),
  ).toBeChecked();
});

type Reviewed =
  'conversation.rename' | 'conversation.pin' | 'conversation.export';

function reviewedActions(controller: ClientController) {
  const load = vi
    .spyOn(controller, 'conversationActions')
    .mockImplementation(async (id) => ({
      schema_version: 1,
      conversation_id: id,
      revision: '4',
      checkpoint_revision: 'checkpoint-7',
      title: id,
      pinned: false,
      capabilities: {
        rename: { available: true, code: null },
        pin: { available: true, code: null },
        archive: { available: false, code: 'conversation_archive_unavailable' },
        export: { available: true, code: null },
      },
    }));
  const review = vi
    .spyOn(controller, 'reviewConversationAction')
    .mockImplementation(async (id, action, revision, fields) => ({
      schema_version: 1,
      conversation_id: id,
      action: action as Reviewed,
      revision,
      checkpoint_revision: 'checkpoint-7',
      fields: action === 'conversation.export' ? { title: id } : fields,
      action_digest: 'a'.repeat(64),
      summary: `Review ${action}`,
      disclosures: [],
      review_id: `review-${id}`,
    }));
  const execute = vi
    .spyOn(controller, 'executeConversationAction')
    .mockImplementation(async (id, command) => ({
      command_id: command.command_id,
      status: 'completed',
      action: command.type as Reviewed,
      code: undefined,
      conversation:
        command.type === 'conversation.pin'
          ? {
              conversation_id: id,
              revision: '5',
              title: id,
              pinned: Boolean(command.payload.pinned),
            }
          : undefined,
      export:
        command.type === 'conversation.export'
          ? {
              attachment_ref: `${id}:export`,
              file_name:
                command.payload.export_format === 'pdf'
                  ? ('conversation-export.pdf' as const)
                  : ('conversation-export.md' as const),
              size_bytes: 12,
              checkpoint_revision: 'checkpoint-7',
            }
          : undefined,
    }));
  return { load, review, execute };
}

it('pins the selected conversations one by one through the reviewed command (B270)', async () => {
  const { controller } = await setup(4);
  const { load, review, execute } = reviewedActions(controller);
  tick('Sample conversation 2');
  tick('Sample conversation 3');
  fireEvent.click(within(bar()!).getByRole('button', { name: 'Pin' }));
  expect(await screen.findByText('Pinned 2 conversations.')).toBeVisible();
  expect(load.mock.calls.map(([id]) => id)).toEqual([
    'conversation-2',
    'conversation-3',
  ]);
  expect(review).toHaveBeenCalledWith(
    'conversation-2',
    'conversation.pin',
    '4',
    { pinned: true },
    expect.any(AbortSignal),
  );
  expect(execute).toHaveBeenCalledTimes(2);
  expect(execute.mock.calls[1][1]).toMatchObject({
    type: 'conversation.pin',
    expected_revision: '4',
    payload: {
      pinned: true,
      checkpoint_revision: 'checkpoint-7',
      action_digest: 'a'.repeat(64),
    },
  });
});

it('exports each selected conversation and saves every file (B270)', async () => {
  const user = userEvent.setup();
  const { controller, save } = await setup(4);
  const { review } = reviewedActions(controller);
  tick('Sample conversation 2');
  tick('Sample conversation 4');
  await user.click(within(bar()!).getByRole('button', { name: 'Export' }));
  await user.click(await screen.findByRole('menuitem', { name: 'PDF' }));
  expect(await screen.findByText('Exported 2 conversations.')).toBeVisible();
  expect(review.mock.calls.map((call) => [call[0], call[3]])).toEqual([
    ['conversation-2', { format: 'pdf' }],
    ['conversation-4', { format: 'pdf' }],
  ]);
  expect(save.mock.calls).toEqual([
    ['conversation-2:export', 'conversation-export.pdf'],
    ['conversation-4:export', 'conversation-export.pdf'],
  ]);
});

it('searches titles and messages from the one field and groups hits by conversation (B270)', async () => {
  const user = userEvent.setup();
  const { transport } = await setup(3);
  const search = vi.fn().mockResolvedValue({
    items: [
      {
        conversation_id: 'conversation-2',
        title: 'Sample conversation 2',
        message_id: 'message-1',
        row_id: 'user:message-1',
        excerpt: 'First needle',
        checkpoint_revision: '1',
      },
      {
        conversation_id: 'conversation-2',
        title: 'Sample conversation 2',
        message_id: 'message-2',
        row_id: 'user:message-2',
        excerpt: 'Second needle',
        checkpoint_revision: '1',
      },
    ],
    has_more: false,
    next_cursor: null,
    revision: 'library-1',
    scanned_messages: 2,
  });
  Object.assign(transport, { search });
  const field = screen.getByRole('searchbox', {
    name: 'Search titles and messages',
  });
  await user.type(field, 'needle{Enter}');
  const results = await screen.findByRole('list', {
    name: 'Conversation search results',
  });
  expect(search).toHaveBeenCalledWith(
    'needle',
    undefined,
    undefined,
    expect.any(AbortSignal),
  );
  expect(within(results).getAllByText('Sample conversation 2')).toHaveLength(1);
  expect(
    within(results).getByRole('button', {
      name: 'Sample conversation 2 Second needle',
    }),
  ).toBeVisible();
  expect(screen.queryByRole('checkbox', { name: /^Select / })).toBeNull();
  await user.clear(field);
  expect(
    await screen.findByRole('checkbox', {
      name: 'Select Sample conversation 2',
    }),
  ).toBeVisible();
});

it('opens an exact older deletion target on its library page for review', async () => {
  const { transport } = await setup(150, 'conversation-120');
  expect(await screen.findByText('1 selected')).toBeVisible();
  expect(screen.getByText('Page 2 of 2')).toBeVisible();
  expect(
    screen.getByRole('checkbox', { name: 'Select Sample conversation 120' }),
  ).toBeChecked();
  deleteSelected();
  expect(
    within(screen.getByRole('alertdialog')).getByText(
      /Bound designs and workspaces remain/,
    ),
  ).toBeVisible();
  expect(transport.counters.commands).toBe(0);
});

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
  fireEvent.click(
    within(
      screen.getByRole('radiogroup', { name: 'Conversation type' }),
    ).getByRole('radio', { name: 'Code' }),
  );
  expect(screen.getByText('2 conversations · newest first')).toBeVisible();
  selectAllInFilter();
  expect(screen.getByText('2 selected')).toBeVisible();
  expect(transport.counters.commands).toBe(0);
  deleteSelected();
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
  selectAllInFilter();
  deleteSelected();
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
  selectAllInFilter();
  deleteSelected();
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
  selectAllInFilter();
  deleteSelected();
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
  selectAllInFilter();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh library' }));
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(
    within(bar()!).getByRole('button', { name: 'Delete…' }),
  ).toBeDisabled();
  expect(transport.counters.commands).toBe(0);
  fireEvent.click(screen.getByRole('button', { name: 'Refresh library' }));
  await waitFor(() =>
    expect(
      within(bar()!).getByRole('button', { name: 'Delete…' }),
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
  selectAllInFilter();
  deleteSelected();
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
  renderLibrary(controller);
  expect(
    await screen.findByText(/earlier deletion has an uncertain result/),
  ).toBeVisible();
  expect(transport.counters.commands).toBe(0);
  fireEvent.click(screen.getByRole('button', { name: 'Check deletion' }));
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
  selectAllInFilter();
  deleteSelected();
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
  deleteSelected();
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
  selectAllInFilter();
  deleteSelected();
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

describe('deleteOneConversation (sidebar single delete)', () => {
  const row = {
    id: 'conversation-7',
    revision: '3',
    title: 'Sample conversation 7',
    pinned: false,
  };
  function owner(command: (...args: unknown[]) => Promise<unknown>) {
    return {
      getSnapshot: () =>
        ({ handshake: { client_session_id: 'session-a' } }) as never,
      command: vi.fn(command) as never,
    };
  }

  it('deletes one conversation with its own revision and clears recovery', async () => {
    const controller = owner(async () => ({ status: 'DeleteCompleted' }));
    await expect(deleteOneConversation(controller, row)).resolves.toEqual({
      status: 'deleted',
      notice: '',
    });
    expect(controller.command).toHaveBeenCalledWith(
      'conversation-7',
      expect.objectContaining({
        type: 'conversation.delete',
        expected_revision: '3',
        client_session_id: 'session-a',
      }),
      expect.any(String),
    );
    expect(
      sessionStorage.getItem('row-bot.conversation-library.pending-delete.v1'),
    ).toBeNull();
  });

  it('reports running work and retained developer work in words', async () => {
    await expect(
      deleteOneConversation(
        owner(async () => ({ status: 'DeleteRequested' })),
        row,
      ),
    ).resolves.toMatchObject({ status: 'not_stopped' });
    await expect(
      deleteOneConversation(
        owner(async () => ({
          status: 'DeleteCompleted',
          retained_developer_work: true,
        })),
        row,
      ),
    ).resolves.toEqual({
      status: 'deleted',
      notice: 'Developer work with changes was retained.',
    });
  });

  it('keeps an unconfirmed outcome for the Library and never retries it', async () => {
    const controller = owner(async () => {
      throw { code: 'operation_uncertain' };
    });
    await expect(deleteOneConversation(controller, row)).resolves.toMatchObject(
      { status: 'uncertain' },
    );
    expect(controller.command).toHaveBeenCalledTimes(1);
    expect(
      JSON.parse(
        sessionStorage.getItem(
          'row-bot.conversation-library.pending-delete.v1',
        ) ?? 'null',
      ),
    ).toMatchObject({ target: 'conversation-7', revision: '3' });
  });

  it('clears recovery after a definite failure', async () => {
    await expect(
      deleteOneConversation(
        owner(async () => {
          throw { code: 'revision_conflict' };
        }),
        row,
      ),
    ).resolves.toMatchObject({ status: 'failed' });
    expect(
      sessionStorage.getItem('row-bot.conversation-library.pending-delete.v1'),
    ).toBeNull();
  });
});
