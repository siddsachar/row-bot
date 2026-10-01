import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { TERMINAL_MIN_HEIGHT, useTerminalDock } from './terminal-dock';

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

it('remembers the terminal height on this device, but opens only when asked (B249)', () => {
  const first = renderHook(() => useTerminalDock());
  expect(first.result.current.open).toBe(false);
  act(() => first.result.current.show());
  act(() => first.result.current.resize(333.6));
  expect(first.result.current.height).toBe(334);
  first.unmount();

  const next = renderHook(() => useTerminalDock());
  expect(next.result.current.height).toBe(334);
  expect(next.result.current.open).toBe(false);
});

it('keeps the terminal tall enough to use', () => {
  const { result } = renderHook(() => useTerminalDock());
  act(() => result.current.resize(10));
  expect(result.current.height).toBe(TERMINAL_MIN_HEIGHT);
});

it('still resizes for this page when the browser blocks storage', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  const { result } = renderHook(() => useTerminalDock());
  expect(result.current.height).toBeGreaterThanOrEqual(TERMINAL_MIN_HEIGHT);
  act(() => result.current.resize(400));
  expect(result.current.height).toBe(400);
});

it('moves the focus into the terminal each time it opens', () => {
  const { result } = renderHook(() => useTerminalDock());
  act(() => result.current.show());
  const first = result.current.focusKey;
  act(() => result.current.hide());
  act(() => result.current.show());
  expect(result.current.focusKey).toBeGreaterThan(first);
});
