import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { EventRecord, ResourceView } from '../../api/types';
import {
  CardActionsContext,
  liveCards,
  TranscriptCards,
  tracedCards,
  type CardActions,
  type TranscriptCard,
} from './TranscriptCards';
import { buildTranscript } from './transcript-model';

vi.mock('../../api/errors', () => ({
  clientError: (cause: { code?: string }) => ({
    message: cause?.code === 'resource_not_discardable' ? 'Not yours.' : 'x',
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
  };
}

function renderCards(value: CardActions, cards = [design], live = false) {
  return render(
    <CardActionsContext.Provider value={value}>
      <TranscriptCards cards={cards} live={live} />
    </CardActionsContext.Provider>,
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
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
  });
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
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
  });
  expect(screen.getByRole('alert')).toHaveTextContent('Not yours.');
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
    <CardActionsContext.Provider value={{ ...renamed }}>
      <TranscriptCards cards={[design]} live={false} />
    </CardActionsContext.Provider>,
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
    <CardActionsContext.Provider value={{ ...value }}>
      <TranscriptCards cards={[folder]} />
    </CardActionsContext.Provider>,
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
