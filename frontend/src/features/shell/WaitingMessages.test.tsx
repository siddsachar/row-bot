import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  within,
} from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ClientQueueItem, ClientQueueView } from '../../api/types';
import WaitingMessages, {
  sendable,
  useWaitingMessages,
  waitingFrom,
  type WaitingMessage,
} from './WaitingMessages';

const open = vi.fn();
vi.mock('../../ui/overlays', () => ({
  useOverlay: () => ({ open, close: vi.fn(), notify: vi.fn() }),
}));

function item(
  submission: string,
  state: ClientQueueItem['state'],
  text = `Text ${submission}`,
  editable = state === 'queued' || state === 'paused',
): ClientQueueItem {
  return {
    id: submission,
    submission_id: submission,
    generation_id: 'generation-a',
    text,
    revision: '1',
    state,
    editable,
    removable: editable,
  };
}
function view(...items: ClientQueueItem[]): ClientQueueView {
  return {
    conversation_id: 'conversation-a',
    generation_id: '',
    items,
    has_more: false,
  };
}

beforeEach(() => open.mockReset());

it('lists only messages that are still waiting, then follow-ups for agents', () => {
  const items = waitingFrom(
    view(
      item('sent', 'consumed'),
      item('dropped', 'cancelled'),
      item('next', 'paused'),
      item('later', 'queued'),
    ),
    {
      conversation_id: 'conversation-a',
      generation_id: 'generation-a',
      orchestration_id: 'orchestration-a',
      items: [
        {
          id: 'agent-1',
          event_id: 'e1',
          text: 'For the child',
          state: 'queued',
        },
        { id: 'agent-0', event_id: 'e0', text: 'Consumed', state: 'consumed' },
      ],
      has_more: false,
    },
  );
  expect(items.map((value) => [value.id, value.forAgents ?? false])).toEqual([
    ['next', false],
    ['later', false],
    ['steering:agent-1', true],
  ]);
});

it('offers Send now for the first message only when nothing is running', () => {
  const items = waitingFrom(
    view(item('a', 'paused'), item('b', 'paused')),
    null,
  );
  expect(sendable(items, false)?.id).toBe('a');
  expect(sendable(items, true)).toBeNull();
  expect(
    sendable(waitingFrom(view(item('c', 'dispatching')), null), false),
  ).toBeNull();
});

function list(items: WaitingMessage[], running = false) {
  const onAction = vi.fn(async () => undefined);
  render(
    <WaitingMessages
      items={items}
      running={running}
      busy={false}
      onAction={onAction}
    />,
  );
  return onAction;
}

it('renders nothing while no message waits', () => {
  const { container } = render(
    <WaitingMessages
      items={[]}
      running={false}
      busy={false}
      onAction={vi.fn()}
    />,
  );
  expect(container).toBeEmptyDOMElement();
});

it('says how many wait and offers Send now, Edit and Discard in plain words', async () => {
  const onAction = list(
    waitingFrom(view(item('a', 'paused', 'Also add a summary')), null),
  );
  const region = screen.getByRole('region', { name: 'Waiting messages' });
  expect(region).toHaveTextContent('1 message waiting');
  expect(region).not.toHaveTextContent(/queue|admitted|Run input|Active run/i);
  await act(async () =>
    fireEvent.click(within(region).getByRole('button', { name: 'Send now' })),
  );
  expect(onAction).toHaveBeenCalledWith(
    'dispatch',
    expect.objectContaining({ id: 'a', revision: '1' }),
    undefined,
  );
});

it('edits a waiting message in place', async () => {
  const onAction = list(
    waitingFrom(view(item('a', 'queued', 'Old text')), null),
    true,
  );
  expect(
    screen.getByRole('region', { name: 'Waiting messages' }),
  ).toHaveTextContent('1 message waiting · sends when Row-Bot finishes');
  expect(screen.queryByRole('button', { name: 'Send now' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Edit waiting message' }),
    {
      target: { value: 'New text' },
    },
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save' })),
  );
  expect(onAction).toHaveBeenCalledWith(
    'edit',
    expect.objectContaining({ id: 'a' }),
    'New text',
  );
  expect(
    screen.queryByRole('textbox', { name: 'Edit waiting message' }),
  ).toBeNull();
});

it('asks before discarding, because a discarded message is gone', async () => {
  const onAction = list(waitingFrom(view(item('a', 'paused')), null));
  fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
  expect(onAction).not.toHaveBeenCalled();
  const dialog = open.mock.calls[0][0];
  expect(dialog).toMatchObject({
    kind: 'alert',
    title: 'Discard this message?',
    confirmLabel: 'Discard message',
  });
  await act(async () => dialog.onConfirm());
  expect(onAction).toHaveBeenCalledWith(
    'remove',
    expect.objectContaining({ id: 'a' }),
    undefined,
  );
});

it('shows agent follow-ups and sending messages without actions', () => {
  list([
    {
      id: 'x',
      text: 'Sending text',
      revision: '2',
      state: 'dispatching',
      editable: false,
    },
    {
      id: 'steering:y',
      text: 'Child guidance',
      revision: '0',
      state: 'queued',
      editable: false,
      forAgents: true,
    },
  ]);
  expect(screen.getByText('2 messages waiting')).toBeVisible();
  expect(screen.getByText('Sending…')).toBeVisible();
  expect(screen.getByText('For the running agents')).toBeVisible();
  expect(screen.queryByRole('button')).toBeNull();
});

it('reads the server list on each change and never counts events (B108)', async () => {
  vi.useFakeTimers();
  try {
    const readWaiting = vi
      .fn()
      .mockResolvedValueOnce(view(item('a', 'queued')))
      .mockResolvedValueOnce(view(item('a', 'consumed')))
      .mockRejectedValueOnce(new TypeError('offline'));
    const readSteering = vi.fn();
    const { result, rerender } = renderHook(
      ({ refreshKey }) =>
        useWaitingMessages({
          conversationId: 'conversation-a',
          refreshKey,
          steeringGeneration: '',
          readWaiting,
          readSteering,
        }),
      { initialProps: { refreshKey: 'event-1' } },
    );
    await act(async () => vi.advanceTimersByTimeAsync(200));
    expect(result.current.items.map((value) => value.id)).toEqual(['a']);
    rerender({ refreshKey: 'event-2' });
    await act(async () => vi.advanceTimersByTimeAsync(200));
    // Consumed: gone, whatever the event said.
    expect(result.current.items).toEqual([]);
    rerender({ refreshKey: 'event-3' });
    await act(async () => vi.advanceTimersByTimeAsync(200));
    expect(result.current.items).toEqual([]);
    expect(readWaiting).toHaveBeenCalledTimes(3);
    expect(readSteering).not.toHaveBeenCalled();
  } finally {
    vi.useRealTimers();
  }
});
