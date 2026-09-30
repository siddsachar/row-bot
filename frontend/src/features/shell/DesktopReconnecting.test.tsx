import { act, render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import type { NativeConnection } from '../../platform';
import { createFakePlatform } from '../../platform/fake';
import DesktopReconnecting from './DesktopReconnecting';

it('says desktop features are reconnecting only while they are (B231)', () => {
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
  render(<DesktopReconnecting platform={platform} />);
  const status = screen.getByRole('status');
  expect(status).toBeEmptyDOMElement();
  act(() => {
    connection = 'reconnecting';
    listeners.forEach((listener) => listener());
  });
  expect(status).toHaveTextContent('Desktop features are reconnecting…');
  act(() => {
    connection = 'ready';
    listeners.forEach((listener) => listener());
  });
  expect(status).toBeEmptyDOMElement();
});

it('says nothing in a browser', () => {
  render(<DesktopReconnecting platform={createFakePlatform()} />);
  expect(screen.getByRole('status')).toBeEmptyDOMElement();
});
