import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ClientQueueView } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import QueueControls from './QueueControls';

function page(conversation = 'A', ids = [0, 1, 2]): ClientQueueView {
  return {
    conversation_id: conversation,
    generation_id: `run-${conversation}`,
    next_cursor: null,
    has_more: false,
    items: ids.map((id) => ({
      id: `message-${id}`,
      submission_id: `message-${id}`,
      generation_id: `pending-${id}`,
      text: id === 0 || id === 2 ? 'Repeat A' : `Queued ${id}`,
      revision: `${id + 4}`,
      state: 'queued',
      editable: true,
      removable: true,
    })),
  };
}

it('edits exact IDs and revisions, preserves failed edits, and removes through the shared alert', async () => {
  const loadPage = vi.fn().mockResolvedValue(page());
  const onAction = vi
    .fn()
    .mockRejectedValueOnce(new Error('conflict'))
    .mockResolvedValue(undefined);
  await act(async () =>
    render(
      <OverlayProvider>
        <QueueControls
          conversationId="A"
          generationId="run-A"
          refreshKey="1"
          loadPage={loadPage}
          onAction={onAction}
        />
      </OverlayProvider>,
    ),
  );
  expect(screen.getAllByText('Repeat A')).toHaveLength(2);
  fireEvent.click(screen.getAllByRole('button', { name: 'Edit message' })[2]!);
  fireEvent.change(screen.getByLabelText('Edit queued message'), {
    target: { value: 'Edited third A' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save queued edit' })),
  );
  expect(onAction).toHaveBeenLastCalledWith(
    'edit',
    'message-2',
    '6',
    'Edited third A',
  );
  expect(screen.getByLabelText('Edit queued message')).toHaveValue(
    'Edited third A',
  );
  expect(screen.getByRole('alert')).toHaveTextContent('could not change');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel edit' }));
  fireEvent.click(
    screen.getAllByRole('button', { name: 'Remove message' })[0]!,
  );
  expect(onAction).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('alertdialog')).toHaveAccessibleName(
    'Remove queued message?',
  );
  await act(async () =>
    fireEvent.click(
      within(screen.getByRole('alertdialog')).getByRole('button', {
        name: 'Remove queued message',
      }),
    ),
  );
  expect(onAction).toHaveBeenLastCalledWith(
    'remove',
    'message-0',
    '4',
    undefined,
  );
});

it('honors authoritative edit/remove flags and explicitly continues only safe paused inputs', async () => {
  const value = page('A', [0, 1, 2]);
  value.items[0] = {
    ...value.items[0]!,
    state: 'consumed',
    editable: false,
    removable: false,
  };
  value.items[1] = {
    ...value.items[1]!,
    state: 'paused',
    editable: false,
    removable: false,
  };
  value.items[2] = { ...value.items[2]!, state: 'paused' };
  const onAction = vi.fn().mockResolvedValue(undefined);
  await act(async () =>
    render(
      <OverlayProvider>
        <QueueControls
          conversationId="A"
          generationId="run-A"
          refreshKey="1"
          loadPage={vi.fn().mockResolvedValue(value)}
          onAction={onAction}
        />
      </OverlayProvider>,
    ),
  );
  expect(screen.getAllByRole('button', { name: 'Edit message' })).toHaveLength(
    1,
  );
  expect(
    screen.getAllByRole('button', { name: 'Remove message' }),
  ).toHaveLength(1);
  expect(screen.getByText(/Use the conversation Resume action/)).toBeVisible();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Continue queued message' }),
    ),
  );
  expect(onAction).toHaveBeenCalledWith(
    'dispatch',
    'message-2',
    '6',
    undefined,
  );
});

it('holds one page and refetches exact event acknowledgements without owning a queue', async () => {
  const loadPage = vi.fn(async (_run: string, cursor: string | null) =>
    cursor === 'next'
      ? page('A', [3])
      : { ...page(), has_more: true, next_cursor: 'next' },
  );
  const props = {
    conversationId: 'A',
    generationId: 'run-A',
    loadPage,
    onAction: vi.fn(),
  };
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(
      <OverlayProvider>
        <QueueControls {...props} refreshKey="1" />
      </OverlayProvider>,
    );
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Next page' })),
  );
  expect(screen.getAllByRole('listitem')).toHaveLength(1);
  expect(loadPage.mock.calls[1]?.slice(0, 2)).toEqual(['run-A', 'next']);
  await act(async () =>
    view.rerender(
      <OverlayProvider>
        <QueueControls {...props} refreshKey="consumed" />
      </OverlayProvider>,
    ),
  );
  expect(loadPage).toHaveBeenCalledTimes(3);
  expect(props.onAction).not.toHaveBeenCalled();
});

it('aborts switched and unmounted loads and rejects late A/B responses after C', async () => {
  const resolves = new Map<string, (page: ClientQueueView) => void>();
  const loadPage = vi.fn(
    (run: string, _cursor: string | null, _signal: AbortSignal) =>
      new Promise<ClientQueueView>((resolve) => resolves.set(run, resolve)),
  );
  const node = (id: string) => (
    <OverlayProvider>
      <QueueControls
        conversationId={id}
        generationId={`run-${id}`}
        refreshKey="1"
        loadPage={loadPage}
        onAction={vi.fn()}
      />
    </OverlayProvider>
  );
  const view = render(node('A'));
  view.rerender(node('B'));
  view.rerender(node('C'));
  expect(loadPage.mock.calls[0]?.[2].aborted).toBe(true);
  expect(loadPage.mock.calls[1]?.[2].aborted).toBe(true);
  await act(async () => resolves.get('run-C')!(page('C', [30])));
  await act(async () => resolves.get('run-A')!(page('A', [10])));
  await act(async () => resolves.get('run-B')!(page('B', [20])));
  expect(screen.getByText(/Message ID: message-30/)).toBeVisible();
  expect(screen.queryByText(/Message ID: message-10/)).not.toBeInTheDocument();
  view.unmount();
  expect(loadPage.mock.calls[2]?.[2].aborted).toBe(true);
});

it('does not execute a stale removal after A/B/A navigation', async () => {
  const loadPage = vi.fn(async (run: string) => page(run.slice(4)));
  const onAction = vi.fn();
  const node = (id: string) => (
    <OverlayProvider>
      <QueueControls
        conversationId={id}
        generationId={`run-${id}`}
        refreshKey="1"
        loadPage={loadPage}
        onAction={onAction}
      />
    </OverlayProvider>
  );
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(node('A'));
  });
  fireEvent.click(
    screen.getAllByRole('button', { name: 'Remove message' })[0]!,
  );
  await act(async () => view.rerender(node('B')));
  await act(async () => view.rerender(node('A')));
  await act(async () =>
    fireEvent.click(
      within(screen.getByRole('alertdialog')).getByRole('button', {
        name: 'Remove queued message',
      }),
    ),
  );
  expect(onAction).not.toHaveBeenCalled();
});
