import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { When } from './When';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it('keeps a near time current on its own: in 2 minutes reaches in under a minute', () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-11-03T09:00:00Z'));
  render(<When value="2026-11-03T09:02:00Z" />);
  expect(screen.getByText('in 2 minutes')).toBeInTheDocument();
  act(() => vi.advanceTimersByTime(90_000));
  expect(screen.getByText('in under a minute')).toBeInTheDocument();
});
