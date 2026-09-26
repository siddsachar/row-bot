import { act, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  current: {
    status: 'loading',
    handshake: null as object | null,
    selectedConversationId: 'conversation-a' as string | null,
    connection: 'none',
  },
}));
vi.mock('./runtime', () => ({ useClientState: () => state.current }));

const { useShellSettled, useSettledIdentity } = await import('./shell-settled');

afterEach(() => vi.useRealTimers());

const handshake = (session: string) => ({
  instance_id: 'instance',
  server_epoch: 'epoch',
  client_session_id: session,
});

it('settles once the selected conversation snapshot is confirmed (B29)', () => {
  const first = handshake('session-1');
  state.current = {
    status: 'ready',
    handshake: first,
    selectedConversationId: 'conversation-a',
    connection: 'none',
  };
  const view = renderHook(() => [useShellSettled(), useSettledIdentity()]);
  expect(view.result.current).toEqual([false, '']);
  state.current = { ...state.current, connection: 'sse' };
  view.rerender();
  expect(view.result.current[0]).toBe(true);
  expect(view.result.current[1]).toBe(
    JSON.stringify(['instance', 'epoch', 'session-1']),
  );
  // Switching conversations on the same handshake stays settled.
  state.current = {
    ...state.current,
    selectedConversationId: 'conversation-b',
    connection: 'none',
  };
  view.rerender();
  expect(view.result.current[0]).toBe(true);
  // A new handshake (reconnect) waits for its own confirmation, while the
  // identity-scoped value stays until the new identity settles.
  state.current = { ...state.current, handshake: handshake('session-2') };
  view.rerender();
  expect(view.result.current).toEqual([
    false,
    JSON.stringify(['instance', 'epoch', 'session-1']),
  ]);
});

it('settles after a short fallback when no snapshot is confirmed', () => {
  vi.useFakeTimers();
  state.current = {
    status: 'ready',
    handshake: handshake('session-3'),
    selectedConversationId: 'conversation-a',
    connection: 'none',
  };
  const view = renderHook(() => useShellSettled());
  expect(view.result.current).toBe(false);
  act(() => vi.advanceTimersByTime(3000));
  expect(view.result.current).toBe(true);
});
