import { act, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { NativeConnection } from '../../platform';
import { createFakePlatform } from '../../platform/fake';
import DesktopReconnecting, {
  RECONNECTING_NOTICE_DELAY_MS,
} from './DesktopReconnecting';

afterEach(() => vi.useRealTimers());

const desktop = () => {
  let connection: NativeConnection = 'ready';
  const listeners = new Set<() => void>();
  const platform = {
    ...createFakePlatform(),
    nativeConnection: {
      get: () => connection,
      subscribe: (listener: () => void) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
    },
  };
  const become = (next: NativeConnection) =>
    act(() => {
      connection = next;
      listeners.forEach((listener) => listener());
    });
  return { platform, become };
};

it('says desktop features are reconnecting only while they still are after a moment (B231)', () => {
  vi.useFakeTimers();
  const { platform, become } = desktop();
  render(<DesktopReconnecting platform={platform} />);
  const status = screen.getByRole('status');
  expect(status).toBeEmptyDOMElement();
  become('reconnecting');
  act(() => vi.advanceTimersByTime(RECONNECTING_NOTICE_DELAY_MS));
  expect(status).toHaveTextContent('Desktop features are reconnecting…');
  become('ready');
  expect(status).toBeEmptyDOMElement();
});

it('never shows for a window that binds again within the moment', () => {
  vi.useFakeTimers();
  const { platform, become } = desktop();
  render(<DesktopReconnecting platform={platform} />);
  const status = screen.getByRole('status');
  become('reconnecting');
  act(() => vi.advanceTimersByTime(RECONNECTING_NOTICE_DELAY_MS - 1));
  expect(status).toBeEmptyDOMElement();
  become('ready');
  act(() => vi.advanceTimersByTime(RECONNECTING_NOTICE_DELAY_MS));
  expect(status).toBeEmptyDOMElement();
  // A later loss waits its own moment again.
  become('reconnecting');
  act(() => vi.advanceTimersByTime(RECONNECTING_NOTICE_DELAY_MS - 1));
  expect(status).toBeEmptyDOMElement();
  act(() => vi.advanceTimersByTime(1));
  expect(status).toHaveTextContent('Desktop features are reconnecting…');
});

it('says nothing in a browser', () => {
  render(<DesktopReconnecting platform={createFakePlatform()} />);
  expect(screen.getByRole('status')).toBeEmptyDOMElement();
});
