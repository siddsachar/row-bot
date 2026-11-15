import { expect, it, vi } from 'vitest';
import type { BrowserInputEvent } from '../../api/types';
import { createInputQueue, pageKey, pagePoint, paneSize } from './live';

it('maps a point on the shown picture to the page’s CSS pixels, whatever its size', () => {
  const rect = { left: 10, top: 20, width: 320, height: 200 };
  const page = { cssWidth: 1280, cssHeight: 800 };
  expect(pagePoint(170, 120, rect, page)).toEqual({ x: 640, y: 400 });
  expect(pagePoint(0, 0, rect, page)).toEqual({ x: 0, y: 0 }); // Outside the picture: its edge.
  expect(pagePoint(999, 999, rect, page)).toEqual({ x: 1280, y: 800 });
});

it('gives the page the pane’s area in CSS pixels and the screen’s pixel ratio, up to 2×', () => {
  expect(paneSize({ width: 419.6, height: 700.2 }, 2)).toEqual({
    width: 420,
    height: 700,
    scale: 2,
  });
  expect(paneSize({ width: 390, height: 700 }, 3)).toEqual({
    width: 390,
    height: 700,
    scale: 2,
  });
  expect(paneSize({ width: 900, height: 640 }, 1.25)).toEqual({
    width: 900,
    height: 640,
    scale: 1.25,
  });
  // Not laid out (hidden): no size to give, so the page stays a desktop window.
  expect(paneSize({ width: 0, height: 0 }, 2)).toEqual({ width: 1280 });
});

it('sends only keys a page understands', () => {
  expect(pageKey('a')).toBe('a');
  expect(pageKey('Enter')).toBe('Enter');
  expect(pageKey('ArrowLeft')).toBe('ArrowLeft');
  expect(pageKey('Unidentified')).toBe('');
  expect(pageKey('Process')).toBe('');
});

it('sends input in order and lets a slow connection skip stale pointer moves', async () => {
  const batches: BrowserInputEvent[][] = [];
  let release!: () => void;
  const send = vi.fn(
    (events: BrowserInputEvent[]) =>
      new Promise<void>((resolve) => {
        batches.push(events);
        release = resolve;
      }),
  );
  const queue = createInputQueue(send);
  queue.push({
    type: 'mouse',
    action: 'down',
    x: 1,
    y: 1,
  } as BrowserInputEvent);
  queue.push({
    type: 'mouse',
    action: 'move',
    x: 2,
    y: 2,
  } as BrowserInputEvent);
  queue.push({
    type: 'mouse',
    action: 'move',
    x: 3,
    y: 3,
  } as BrowserInputEvent);
  queue.push({ type: 'mouse', action: 'up', x: 3, y: 3 } as BrowserInputEvent);
  release();
  await vi.waitFor(() => expect(batches).toHaveLength(2));
  release();
  expect(batches[0]).toEqual([{ type: 'mouse', action: 'down', x: 1, y: 1 }]);
  expect(batches[1]).toEqual([
    { type: 'mouse', action: 'move', x: 3, y: 3 },
    { type: 'mouse', action: 'up', x: 3, y: 3 },
  ]);
});

it('sends a refused batch once more before anything after it, so a busy moment drops no keys', async () => {
  const sent: BrowserInputEvent[][] = [];
  let calls = 0;
  const send = vi.fn(async (events: BrowserInputEvent[]) => {
    calls += 1;
    if (calls === 1) throw new Error('rate_limited');
    sent.push(events);
  });
  const queue = createInputQueue(send);
  queue.push({ type: 'key', action: 'down', key: 'a' } as BrowserInputEvent);
  queue.push({ type: 'key', action: 'down', key: 'b' } as BrowserInputEvent);
  await vi.waitFor(() =>
    expect(sent.flat()).toEqual([
      { type: 'key', action: 'down', key: 'a' },
      { type: 'key', action: 'down', key: 'b' },
    ]),
  );
});
