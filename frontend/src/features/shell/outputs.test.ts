import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { OutputFilter } from '../../api/types';
import { REFRESH_SETTLE_MS, outputTitle, useOutputs } from './outputs';

const runtime = vi.hoisted(() => ({ outputs: vi.fn() }));
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: runtime }),
}));

const page = (name: string) => ({ items: [], counts: null, name });

beforeEach(() => {
  vi.useFakeTimers();
  runtime.outputs
    .mockReset()
    .mockImplementation(async (filter: OutputFilter) =>
      page(filter.conversation_id ?? ''),
    );
});
afterEach(() => vi.useRealTimers());

it('reads a chat’s list at once, again only once a refresh holds still, and never another chat’s', async () => {
  const { result, rerender } = renderHook(
    ({ chat, refresh, enabled }) =>
      useOutputs({ conversation_id: chat }, refresh, enabled),
    { initialProps: { chat: 'chat-a', refresh: '0', enabled: false } },
  );
  // Nothing before the open settles.
  expect(runtime.outputs).not.toHaveBeenCalled();
  rerender({ chat: 'chat-a', refresh: '0', enabled: true });
  await act(async () => undefined);
  expect(runtime.outputs).toHaveBeenCalledTimes(1);
  expect(result.current.page).toMatchObject({ name: 'chat-a' });

  // A burst of changes reads once, after it holds still; the list stays meanwhile.
  rerender({ chat: 'chat-a', refresh: '1', enabled: true });
  rerender({ chat: 'chat-a', refresh: '2', enabled: true });
  expect(result.current.page).toMatchObject({ name: 'chat-a' });
  await act(async () => vi.advanceTimersByTimeAsync(REFRESH_SETTLE_MS));
  expect(runtime.outputs).toHaveBeenCalledTimes(2);

  // Another chat starts empty and is read at once.
  rerender({ chat: 'chat-b', refresh: '2', enabled: true });
  expect(result.current.page).toBeNull();
  await act(async () => undefined);
  expect(runtime.outputs).toHaveBeenCalledTimes(3);
  expect(result.current.page).toMatchObject({ name: 'chat-b' });
});

it('names a made output by its words and a file the person added by its own name', () => {
  const output = { file_name: 'x.mp4', role: 'final' as const };
  expect(outputTitle({ ...output, name: 'waves-at-dusk' })).toBe(
    'Waves at dusk',
  );
  expect(outputTitle({ ...output, name: 'Row-Bot demo' })).toBe('Row-Bot demo');
  expect(outputTitle({ ...output, name: 'Q3 budget £480' })).toBe(
    'Q3 budget £480',
  );
  expect(
    outputTitle({ name: 'holiday', file_name: 'holiday.png', role: 'upload' }),
  ).toBe('holiday.png');
});
