import { act, cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { OpenConversationRequests, publishBuddyTarget } from './BuddyFollow';

const navigate = vi.hoisted(() => vi.fn());
vi.mock('react-router-dom', () => ({ useNavigate: () => navigate }));

function controller() {
  let selected: string | null = null;
  const listeners = new Set<() => void>();
  return {
    getSnapshot: () => ({ selectedConversationId: selected }) as never,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    select(id: string | null) {
      selected = id;
      listeners.forEach((listener) => listener());
    },
  };
}
const flush = async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};
afterEach(cleanup);

it('publishes each newly selected conversation once and keeps the last one', async () => {
  const value = controller();
  const publish = vi.fn().mockResolvedValue({
    status: 'ok',
    value: { conversationId: 'c1', revision: 1 },
  });
  const unbind = publishBuddyTarget(value, { publishBuddyTarget: publish });
  value.select('c1');
  value.select('c1');
  // Leaving for Home or Settings keeps Buddy on the last conversation.
  value.select(null);
  value.select('c2');
  await flush();
  expect(publish.mock.calls).toEqual([['c1'], ['c2']]);
  unbind();
  value.select('c3');
  expect(publish).toHaveBeenCalledTimes(2);
});

it('stops in a browser and retries after a transient failure', async () => {
  const value = controller();
  const browser = vi.fn().mockResolvedValue({
    status: 'unavailable',
    reason: 'buddy_target_requires_native',
  });
  publishBuddyTarget(value, { publishBuddyTarget: browser });
  value.select('c1');
  await flush();
  value.select('c2');
  await flush();
  expect(browser).toHaveBeenCalledTimes(1);

  const other = controller();
  const flaky = vi
    .fn()
    .mockResolvedValueOnce({
      status: 'unavailable',
      reason: 'native_operation_failed',
    })
    .mockResolvedValue({
      status: 'ok',
      value: { conversationId: 'c1', revision: 2 },
    });
  publishBuddyTarget(other, { publishBuddyTarget: flaky });
  other.select('c1');
  await flush();
  other.select(null);
  other.select('c1');
  await flush();
  expect(flaky.mock.calls).toEqual([['c1'], ['c1']]);
});

it('opens a conversation the desktop Buddy asks for, ignoring malformed ids', () => {
  render(<OpenConversationRequests />);
  act(() => {
    window.dispatchEvent(
      new CustomEvent('row-bot-open-conversation', { detail: 'c1' }),
    );
    window.dispatchEvent(
      new CustomEvent('row-bot-open-conversation', { detail: '../settings' }),
    );
    window.dispatchEvent(
      new CustomEvent('row-bot-open-conversation', { detail: { id: 'c2' } }),
    );
  });
  expect(navigate.mock.calls).toEqual([['/conversations/c1']]);
});
