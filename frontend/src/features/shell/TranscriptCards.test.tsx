import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { EventRecord, ResourceView } from '../../api/types';
import type { ContextType } from 'react';
import { RuntimeContext } from '../../runtime';
import {
  CardActionsContext,
  liveCards,
  TranscriptCards,
  tracedCards,
  type CardActions,
  type TranscriptCard,
} from './TranscriptCards';
import { OverlayProvider } from '../../ui/overlays';
import { buildTranscript, foldLiveVideos } from './transcript-model';

vi.mock('../../api/errors', () => ({
  clientError: (cause: { code?: string }) => ({
    message:
      cause?.code === 'resource_not_discardable'
        ? 'Not yours.'
        : cause?.code === 'resource_not_empty'
          ? 'It has files now.'
          : 'x',
  }),
}));

const design: TranscriptCard = {
  kind: 'resource',
  resourceKind: 'design',
  name: 'Harbour cleanup deck',
  bindingId: 'binding-a',
  resourceId: 'design-a',
};

const view = {
  resource_ref: 'conversation-a:binding-a',
  conversation_revision: '4',
  binding: {
    binding_id: 'binding-a',
    kind: 'artifact',
    resource_id: 'design-a',
    role: 'context',
    revision: '1',
  },
  title: 'Harbour cleanup deck',
  resource_revision: 'r1',
  available: true,
} as ResourceView;

function actions(resource: ResourceView | null = view): CardActions {
  return {
    resource: vi.fn(() => resource ?? undefined),
    open: vi.fn(),
    rename: vi.fn(async () => {}),
    undo: vi.fn(async () => {}),
    remove: vi.fn(async () => {}),
    connect: vi.fn(),
    continueWith: vi.fn(),
  };
}

function renderCards(value: CardActions, cards = [design], live = false) {
  return render(
    <OverlayProvider>
      <CardActionsContext.Provider value={value}>
        <TranscriptCards cards={cards} live={live} />
      </CardActionsContext.Provider>
    </OverlayProvider>,
  );
}

it('shows a created design with Open, Rename and Undo', async () => {
  const value = actions();
  renderCards(value);
  const card = screen.getByRole('group', {
    name: 'Created design Harbour cleanup deck',
  });
  expect(card).toHaveTextContent('Created design Harbour cleanup deck');
  fireEvent.click(screen.getByRole('button', { name: 'Open' }));
  expect(value.open).toHaveBeenCalledWith(view);
  // Undo deletes the design with whatever was made in it since: it asks first.
  fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
  let dialog = await screen.findByRole('alertdialog', {
    name: 'Delete design?',
  });
  expect(dialog).toHaveTextContent(
    'including changes made since it was created',
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
  expect(value.undo).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
  dialog = await screen.findByRole('alertdialog', { name: 'Delete design?' });
  await act(async () => {
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Delete design' }),
    );
  });
  expect(value.undo).toHaveBeenCalledOnce();
  expect(value.undo).toHaveBeenCalledWith('binding-a');
});

it('renames in place: Enter saves, Escape keeps the name', async () => {
  const value = actions();
  renderCards(value);
  fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
  const field = screen.getByRole('textbox', { name: 'Name of the design' });
  expect(field).toHaveValue('Harbour cleanup deck');
  fireEvent.change(field, { target: { value: 'Tides deck' } });
  await act(async () => {
    fireEvent.keyDown(field, { key: 'Enter' });
  });
  expect(value.rename).toHaveBeenCalledWith('binding-a', 'Tides deck');
  expect(value.rename).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
  fireEvent.keyDown(
    screen.getByRole('textbox', { name: 'Name of the design' }),
    { key: 'Escape' },
  );
  expect(value.rename).toHaveBeenCalledTimes(1);
});

it('says why an Undo was refused', async () => {
  const value = actions();
  value.undo = vi.fn(async () => {
    throw { code: 'resource_not_discardable' };
  });
  renderCards(value);
  fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
  const dialog = await screen.findByRole('alertdialog', {
    name: 'Delete design?',
  });
  await act(async () => {
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Delete design' }),
    );
  });
  expect(screen.getByRole('alert')).toHaveTextContent('Not yours.');
});

it('a created code folder’s Undo deletes it only while empty, and says when it kept the files', async () => {
  const folder: TranscriptCard = {
    kind: 'resource',
    resourceKind: 'code',
    name: 'tour-test-math',
    bindingId: 'binding-c',
    resourceId: 'workspace-c',
  };
  const value = actions({ ...view, title: 'tour-test-math' });
  value.undo = vi.fn(async () => {
    throw { code: 'resource_not_empty' };
  });
  renderCards(value, [folder]);
  const undo = screen.getByRole('button', { name: 'Undo' });
  expect(undo).toHaveAttribute(
    'title',
    'Delete this code folder while it is still empty; files added to it are never deleted',
  );
  await act(async () => {
    fireEvent.click(undo);
  });
  expect(value.undo).toHaveBeenCalledWith('binding-c');
  expect(screen.getByRole('alert')).toHaveTextContent('It has files now.');
  expect(
    screen.getByRole('group', { name: 'Created code folder tour-test-math' }),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Open' })).toBeEnabled();
});

it('reads as removed once the binding is gone, but not while it is live', () => {
  const gone = actions(null);
  const first = renderCards(gone);
  expect(
    screen.getByRole('group', {
      name: 'Removed design Harbour cleanup deck',
    }),
  ).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
  first.unmount();
  renderCards(gone, [design], true);
  expect(
    screen.getByRole('group', {
      name: 'Created design Harbour cleanup deck',
    }),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Open' })).toBeDisabled();
});

it('a renamed card that is undone reads removed under the new name', () => {
  const renamed = actions({ ...view, title: 'Tides deck' });
  const shown = renderCards(renamed);
  expect(
    screen.getByRole('group', { name: 'Created design Tides deck' }),
  ).toBeVisible();
  renamed.resource = vi.fn(() => undefined);
  shown.rerender(
    <OverlayProvider>
      <CardActionsContext.Provider value={{ ...renamed }}>
        <TranscriptCards cards={[design]} live={false} />
      </CardActionsContext.Provider>
    </OverlayProvider>,
  );
  expect(
    screen.getByRole('group', { name: 'Removed design Tides deck' }),
  ).toBeVisible();
});

it('a folder the person had reads Using, and Undo only takes it out of the chat (B277)', async () => {
  const folder: TranscriptCard = {
    kind: 'resource',
    resourceKind: 'code',
    name: 'tide-app',
    bindingId: 'binding-c',
    resourceId: 'workspace-c',
    bound: true,
  };
  const value = actions({ ...view, title: 'tide-app' });
  const shown = renderCards(value, [folder]);
  expect(
    screen.getByRole('group', { name: 'Using code folder tide-app' }),
  ).toHaveTextContent('Using code folder tide-app');
  expect(screen.queryByRole('button', { name: 'Rename' })).toBeNull();
  const undo = screen.getByRole('button', { name: 'Undo' });
  expect(undo).toHaveAttribute(
    'title',
    'Stop using this folder in this conversation; its files stay',
  );
  await act(async () => {
    fireEvent.click(undo);
  });
  expect(value.remove).toHaveBeenCalledWith('binding-c');
  expect(value.undo).not.toHaveBeenCalled();
  value.resource = vi.fn(() => undefined);
  shown.rerender(
    <OverlayProvider>
      <CardActionsContext.Provider value={{ ...value }}>
        <TranscriptCards cards={[folder]} />
      </CardActionsContext.Provider>
    </OverlayProvider>,
  );
  expect(
    screen.getByRole('group', { name: 'No longer using code folder tide-app' }),
  ).toBeVisible();
});

it('offers Connect for an account the work needs', () => {
  const value = actions();
  renderCards(value, [
    { kind: 'connect', target: 'google', label: 'Google', page: 'accounts' },
  ]);
  fireEvent.click(screen.getByRole('button', { name: 'Connect Google' }));
  expect(value.connect).toHaveBeenCalledWith('accounts', 'google');
});

const specialization = {
  kind: 'resource_created' as const,
  display_name: 'Tiny date app',
  resource_kind: 'code' as const,
  resource_id: 'workspace-a',
  binding_id: 'binding-b',
};

it('hoists cards from a turn’s traces and from live tool activity', () => {
  const group = {
    group_id: 'g',
    name: 'create_code_folder',
    kind: 'generic' as const,
    group_order: 0,
    status: 'succeeded' as const,
    counts: { succeeded: 1 },
    items: [
      {
        item_id: 'i',
        group_id: 'g',
        call_id: 'c',
        result_message_id: '',
        call_order: 0,
        group_order: 0,
        canonical_name: 'create_code_folder',
        group_name: 'create_code_folder',
        group_kind: 'generic' as const,
        status: 'succeeded' as const,
        safe_input: '',
        safe_summary: '',
        summary_truncated: false,
        content_ref: '',
        specialization,
      },
    ],
  };
  expect(tracedCards([group])).toEqual([
    {
      kind: 'resource',
      resourceKind: 'code',
      name: 'Tiny date app',
      bindingId: 'binding-b',
      resourceId: 'workspace-a',
    },
  ]);
  const items = buildTranscript([
    {
      id: 'assistant:1',
      role: 'assistant',
      blocks: [],
      tool_call_ids: [],
      tool_call_id: '',
      traces: [group],
    },
  ]);
  expect(items[0].cards).toHaveLength(1);
  const activity = [
    {
      event: {
        type: 'tool.activity',
        payload: { state: 'tool_done', specialization },
      },
    },
  ] as unknown as EventRecord[];
  expect(liveCards(activity, new Set())).toHaveLength(1);
  expect(liveCards(activity, new Set(['resource:binding-b']))).toHaveLength(0);
  const bound = [
    {
      event: {
        type: 'tool.activity',
        payload: {
          state: 'tool_done',
          specialization: { ...specialization, kind: 'resource_bound' },
        },
      },
    },
  ] as unknown as EventRecord[];
  expect(liveCards(bound, new Set())).toEqual([
    {
      kind: 'resource',
      resourceKind: 'code',
      name: 'Tiny date app',
      bindingId: 'binding-b',
      resourceId: 'workspace-a',
      bound: true,
    },
  ]);
});

function videoRow(
  id: string,
  role: 'user' | 'assistant',
  specs: Record<string, unknown>[],
  text = '',
) {
  return {
    id,
    role,
    blocks: text ? [{ type: 'text', text }] : [],
    tool_call_ids: [],
    tool_call_id: '',
    traces: specs.length
      ? [
          {
            group_id: `${id}:g`,
            name: 'edit_video',
            kind: 'generic' as const,
            group_order: 0,
            status: 'succeeded' as const,
            counts: { succeeded: specs.length },
            items: specs.map((specialization, index) => ({
              item_id: `${id}:${index}`,
              group_id: `${id}:g`,
              call_id: `${id}:${index}`,
              result_message_id: '',
              call_order: index,
              group_order: 0,
              canonical_name: 'edit_video',
              group_name: 'edit_video',
              group_kind: 'generic' as const,
              status: 'succeeded' as const,
              safe_input: '',
              safe_summary: '',
              summary_truncated: false,
              content_ref: '',
              specialization,
            })),
          },
        ]
      : [],
  } as unknown as Parameters<typeof buildTranscript>[0][number];
}

const recording = { kind: 'recording', recording_id: 'rec-00000001' };
const edit = (id: string, media_kind = '') => ({
  kind: 'video_edit',
  edit_id: id,
  media_kind,
});

it('shows only a reply’s newest video in full and folds the earlier ones under it', () => {
  const items = buildTranscript([
    videoRow('user:1', 'user', [], 'Make a demo video'),
    videoRow('assistant:1', 'assistant', [recording]),
    // Text between the steps splits the reply into items; the fold spans them.
    videoRow('assistant:2', 'assistant', [edit('edit-0000000a')], 'Trimmed.'),
    videoRow('assistant:3', 'assistant', [
      edit('edit-0000000b'),
      // Stills taken to check the video are no newer version of it.
      edit('edit-0000000c', 'frames'),
    ]),
    videoRow('user:2', 'user', [], 'Thanks'),
    // The next reply's one video stands alone.
    videoRow('assistant:4', 'assistant', [edit('edit-0000000d')], 'Here.'),
  ]);
  const keys = (cards: TranscriptCard[] | undefined) =>
    (cards ?? []).map((card) =>
      card.kind === 'video_edit'
        ? card.editId
        : card.kind === 'recording'
          ? card.recordingId
          : card.kind,
    );
  expect(items.map((item) => keys(item.cards))).toEqual([
    [],
    [],
    [],
    ['edit-0000000b', 'edit-0000000c'],
    [],
    ['edit-0000000d'],
  ]);
  expect(items.map((item) => keys(item.earlier))).toEqual([
    [],
    [],
    [],
    ['rec-00000001', 'edit-0000000a'],
    [],
    [],
  ]);
});

it('folds what a turn in flight already shows under its newer live video', () => {
  const items = buildTranscript([
    videoRow('user:1', 'user', [], 'Make a demo video'),
    videoRow('assistant:1', 'assistant', [recording, edit('edit-0000000a')]),
  ]);
  const live: TranscriptCard[] = [
    { kind: 'video_edit', editId: 'edit-0000000b' },
    { kind: 'page', pageId: 'page-00000001' },
  ];
  const shown = foldLiveVideos(items, live);
  expect(shown.items[1].cards).toEqual([]);
  expect(shown.items[1].earlier).toBeUndefined();
  expect(shown.pending).toEqual(live);
  expect(shown.earlier).toEqual([
    { kind: 'recording', recordingId: 'rec-00000001' },
    { kind: 'video_edit', editId: 'edit-0000000a' },
  ]);
  // Live cards without a video leave the saved ones as they are.
  const quiet = foldLiveVideos(items, [live[1]]);
  expect(quiet.items).toBe(items);
  expect(quiet.earlier).toEqual([]);
});

it('keeps earlier versions behind one quiet toggle', () => {
  render(
    <OverlayProvider>
      <CardActionsContext.Provider value={actions()}>
        <TranscriptCards
          cards={[design]}
          earlier={[
            { ...design, bindingId: 'binding-x', name: 'Raw take' },
            { ...design, bindingId: 'binding-y', name: 'Trimmed take' },
          ]}
        />
      </CardActionsContext.Provider>
    </OverlayProvider>,
  );
  const toggle = screen.getByText('2 earlier versions');
  const fold = toggle.closest('details')!;
  expect(fold).not.toHaveAttribute('open');
  fireEvent.click(toggle);
  expect(fold).toHaveAttribute('open');
  expect(within(fold).getAllByRole('button', { name: 'Open' })).toHaveLength(2);
});

const settingCard: TranscriptCard = {
  kind: 'setting',
  callId: 'call-a',
  messageId: 'message-a',
  change: {
    label: 'Dream Cycle',
    where: 'Settings › Preferences › Dream Cycle',
    href: '/settings/preferences#dream-cycle',
    before: '1:00–5:00',
    after: '2:00–6:00',
  },
};

function renderSettingCard(
  undoSettingChange: ReturnType<typeof vi.fn>,
  card: TranscriptCard = settingCard,
) {
  return render(
    <RuntimeContext.Provider
      value={
        { controller: { undoSettingChange } } as unknown as NonNullable<
          ContextType<typeof RuntimeContext>
        >
      }
    >
      <OverlayProvider>
        <TranscriptCards cards={[card]} conversation="conversation-a" />
      </OverlayProvider>
    </RuntimeContext.Provider>,
  );
}

it('shows a setting Row-Bot changed, its row, and undoes it with one click', async () => {
  const undoSettingChange = vi.fn(async () => ({
    status: 'undone',
    message: 'Undone: Dream Cycle is back to 1:00–5:00.',
  }));
  renderSettingCard(undoSettingChange);

  const card = screen.getByRole('group', { name: 'Changed Dream Cycle' });
  expect(card).toHaveTextContent('Changed Dream Cycle to 2:00–6:00');
  expect(
    within(card).getByRole('link', { name: 'Show in Settings' }),
  ).toHaveAttribute('href', '/app-v2/settings/preferences#dream-cycle');

  await act(async () => {
    fireEvent.click(
      within(card).getByRole('button', { name: 'Undo: back to 1:00–5:00' }),
    );
  });

  expect(undoSettingChange).toHaveBeenCalledWith('conversation-a', 'message-a');
  const undone = screen.getByRole('group', { name: 'Undone: Dream Cycle' });
  expect(undone).toHaveTextContent('Undone: Dream Cycle is back to 1:00–5:00');
  expect(within(undone).queryByRole('button')).toBeNull();
});

it('says when a setting changed since and leaves it alone', async () => {
  const undoSettingChange = vi.fn(async () => ({
    status: 'changed_since',
    message:
      "Dream Cycle has changed since, so it wasn't undone. Change it in Settings.",
  }));
  renderSettingCard(undoSettingChange);

  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', { name: 'Undo: back to 1:00–5:00' }),
    );
  });

  expect(
    screen.getByRole('button', {
      name: 'Changed since: open Settings to change it',
    }),
  ).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent('has changed since');
});

it('words a tool Row-Bot turned on, with Turn it off again', () => {
  renderSettingCard(vi.fn(), {
    ...settingCard,
    change: {
      label: 'Web Search',
      where: 'Settings › Tools',
      href: '/settings/tools#search-tools',
      before: 'Off',
      after: 'On',
    },
  } as TranscriptCard);

  expect(screen.getByRole('group')).toHaveTextContent('Turned on Web Search');
  expect(
    screen.getByRole('button', { name: 'Turn it off again' }),
  ).toBeEnabled();
});

it('reads the change and its result message from a settled setting step', () => {
  const cards = tracedCards([
    {
      group_id: 'group-a',
      name: 'Row-Bot Status',
      kind: 'generic',
      group_order: 0,
      status: 'succeeded',
      counts: {},
      items: [
        {
          item_id: 'item-a',
          group_id: 'group-a',
          call_id: 'call-a',
          result_message_id: 'message-a',
          call_order: 0,
          group_order: 0,
          canonical_name: 'Update Setting',
          status: 'succeeded',
          safe_input: '',
          safe_summary: '',
          summary_truncated: false,
          content_ref: '',
          specialization: {
            kind: 'setting_change',
            display_name: 'Dream Cycle',
            setting_change: (settingCard as { change: unknown }).change,
          },
        },
      ],
    },
  ] as never);

  expect(cards).toEqual([settingCard]);
});
