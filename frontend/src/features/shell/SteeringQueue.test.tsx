import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ParentSteeringView } from '../../api/types';
import SteeringQueue from './SteeringQueue';

function page(
  conversation = 'A',
  ids = [0, 1, 2],
  consumed: number[] = [],
): ParentSteeringView {
  return {
    conversation_id: conversation,
    generation_id: `run-${conversation}`,
    orchestration_id: `orchestration-${conversation}`,
    items: ids.map((id) => ({
      id: `steering-${id}`,
      event_id: `event-${id}`,
      text: id === 0 || id === 2 ? 'Repeat A' : `Guidance ${id}`,
      state: consumed.includes(id) ? 'consumed' : 'queued',
      editable: false,
    })),
    next_cursor: null,
    has_more: false,
  };
}

it('retains seven distinct IDs and updates only confirmed consumed messages', async () => {
  const loadPage = vi.fn().mockResolvedValue(page('A', [0, 1, 2, 3, 4, 5, 6]));
  const props = { conversationId: 'A', generationId: 'run-A', loadPage };
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(<SteeringQueue {...props} refreshKey="queued" />);
  });
  expect(screen.getAllByRole('listitem')).toHaveLength(7);
  expect(screen.getAllByText('Repeat A')).toHaveLength(2);
  expect(screen.getAllByText('Queued')).toHaveLength(7);
  loadPage.mockResolvedValue(page('A', [0, 1, 2, 3, 4, 5, 6], [0, 1, 2, 3, 4]));
  await act(async () =>
    view.rerender(<SteeringQueue {...props} refreshKey="consumed-five" />),
  );
  expect(screen.getAllByText('Consumed')).toHaveLength(5);
  expect(screen.getAllByText('Queued')).toHaveLength(2);
  expect(
    within(screen.getAllByRole('listitem')[5]!).getByText(
      'Message ID: steering-5',
    ),
  ).toBeVisible();
  expect(
    screen.queryByRole('button', { name: /edit|remove/i }),
  ).not.toBeInTheDocument();
});

it('keeps one bounded page and follows the exact continuation', async () => {
  const loadPage = vi.fn(async (_generation: string, cursor: string | null) =>
    cursor === 'cursor-next'
      ? page('A', [3, 4])
      : { ...page(), next_cursor: 'cursor-next', has_more: true },
  );
  await act(async () =>
    render(
      <SteeringQueue
        conversationId="A"
        generationId="run-A"
        refreshKey="1"
        loadPage={loadPage}
      />,
    ),
  );
  expect(screen.getByRole('button', { name: 'First page' })).toBeDisabled();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Next page' })),
  );
  expect(loadPage.mock.calls[1]?.slice(0, 2)).toEqual(['run-A', 'cursor-next']);
  expect(screen.getAllByRole('listitem')).toHaveLength(2);
  expect(screen.queryByText('Message ID: steering-0')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'First page' })),
  );
  expect(loadPage.mock.calls[2]?.[1]).toBeNull();
  expect(screen.getAllByRole('listitem')).toHaveLength(3);
});

it('aborts A and B and ignores their responses after C becomes current', async () => {
  const finish = new Map<string, (value: ParentSteeringView) => void>();
  const loadPage = vi.fn(
    (generation: string, _cursor: string | null, _signal: AbortSignal) =>
      new Promise<ParentSteeringView>((resolve) =>
        finish.set(generation, resolve),
      ),
  );
  const view = render(
    <SteeringQueue
      conversationId="A"
      generationId="run-A"
      refreshKey="1"
      loadPage={loadPage}
    />,
  );
  view.rerender(
    <SteeringQueue
      conversationId="B"
      generationId="run-B"
      refreshKey="1"
      loadPage={loadPage}
    />,
  );
  view.rerender(
    <SteeringQueue
      conversationId="C"
      generationId="run-C"
      refreshKey="1"
      loadPage={loadPage}
    />,
  );
  expect(loadPage.mock.calls[0]?.[2].aborted).toBe(true);
  expect(loadPage.mock.calls[1]?.[2].aborted).toBe(true);
  await act(async () => finish.get('run-C')!(page('C', [30])));
  await act(async () => finish.get('run-B')!(page('B', [20])));
  await act(async () => finish.get('run-A')!(page('A', [10])));
  expect(screen.getByText('Message ID: steering-30')).toBeVisible();
  expect(screen.queryByText('Message ID: steering-10')).not.toBeInTheDocument();
  view.unmount();
  expect(loadPage.mock.calls[2]?.[2].aborted).toBe(true);
});

it('rejects a mismatched run and reloads without sending a command', async () => {
  const loadPage = vi
    .fn()
    .mockResolvedValueOnce({ ...page(), generation_id: 'wrong-run' })
    .mockResolvedValueOnce(page());
  await act(async () =>
    render(
      <SteeringQueue
        conversationId="A"
        generationId="run-A"
        refreshKey="1"
        loadPage={loadPage}
      />,
    ),
  );
  expect(screen.getByRole('alert')).toHaveTextContent('queue changed');
  expect(screen.queryByRole('listitem')).not.toBeInTheDocument();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Reload queue' })),
  );
  expect(screen.getAllByRole('listitem')).toHaveLength(3);
  expect(loadPage.mock.calls[1]?.[1]).toBeNull();
});

it('clears inaccessible content and recovers from a failed refresh', async () => {
  const loadPage = vi
    .fn()
    .mockResolvedValueOnce(page())
    .mockRejectedValueOnce(new Error('synthetic error'))
    .mockResolvedValueOnce(page('A', []));
  await act(async () =>
    render(
      <SteeringQueue
        conversationId="A"
        generationId="run-A"
        refreshKey="1"
        loadPage={loadPage}
      />,
    ),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Refresh queue' })),
  );
  expect(screen.getByRole('alert')).toHaveTextContent('could not load');
  expect(screen.queryByRole('listitem')).not.toBeInTheDocument();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Reload queue' })),
  );
  expect(screen.getByText('No steering messages')).toBeVisible();
});
