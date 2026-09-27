import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ConversationView } from '../api/types';
import type { ClientPlatform } from '../platform';
import { mostRecent, useBuddyTarget } from './use-buddy-target';

const view = (
  id: string,
  updated_at: string,
  parent: string | null = null,
): ConversationView =>
  ({ id, updated_at, parent_conversation_id: parent }) as ConversationView;

afterEach(() => vi.useRealTimers());

it('picks the latest top-level conversation', () => {
  expect(
    mostRecent([
      view('old', '2026-09-01T00:00:00Z'),
      view('child', '2026-09-30T00:00:00Z', 'old'),
      view('new', '2026-09-20T00:00:00Z'),
    ]),
  ).toBe('new');
  expect(mostRecent([])).toBeNull();
});

it('ignores stale answers and keeps the last conversation when the host has none', async () => {
  let resolveSlow!: (value: unknown) => void;
  const readBuddyTarget = vi
    .fn()
    .mockResolvedValueOnce({
      status: 'ok',
      value: { conversationId: 'c2', revision: 2 },
    })
    .mockReturnValueOnce(new Promise((resolve) => (resolveSlow = resolve)))
    .mockResolvedValueOnce({
      status: 'ok',
      value: { conversationId: null, revision: 3 },
    });
  const platform = { readBuddyTarget } as unknown as ClientPlatform;
  const { result } = renderHook(() =>
    useBuddyTarget(platform, null, [view('recent', '2026-09-27T00:00:00Z')]),
  );
  // Before the host answers, the latest conversation stands in.
  expect(result.current).toEqual({
    conversationId: 'recent',
    source: 'recent',
  });
  await waitFor(() => expect(result.current.conversationId).toBe('c2'));
  act(() => {
    window.dispatchEvent(new Event('row-bot-native-changed'));
  });
  act(() => {
    window.dispatchEvent(new Event('focus'));
  });
  await act(async () => {
    resolveSlow({ status: 'ok', value: { conversationId: 'c1', revision: 1 } });
    await Promise.resolve();
  });
  // Revision 1 arrived after revision 3: ignored; a null target keeps c2.
  expect(result.current).toEqual({ conversationId: 'c2', source: 'host' });
});

it('re-reads on a timer and prefers a valid explicit conversation', async () => {
  vi.useFakeTimers();
  const readBuddyTarget = vi
    .fn()
    .mockResolvedValue({ status: 'unavailable', reason: 'x' });
  const platform = { readBuddyTarget } as unknown as ClientPlatform;
  renderHook(() => useBuddyTarget(platform, null, []));
  expect(readBuddyTarget).toHaveBeenCalledTimes(1);
  await act(async () => {
    vi.advanceTimersByTime(5000);
  });
  expect(readBuddyTarget).toHaveBeenCalledTimes(2);
  const explicit = renderHook(() => useBuddyTarget(platform, 'c9', []));
  expect(explicit.result.current).toEqual({
    conversationId: 'c9',
    source: 'explicit',
  });
  const invalid = renderHook(() => useBuddyTarget(platform, '../x', []));
  expect(invalid.result.current.source).toBe('recent');
});
