import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createFakePlatform } from '../../platform/fake';
import type { ClientPlatform } from '../../platform/types';
import NativeTerminal from './NativeTerminal';

const mock = vi.hoisted(() => ({
  controller: {
    terminalRead: vi.fn(),
    terminalInput: vi.fn(),
    terminalResize: vi.fn(),
    terminalDisconnect: vi.fn(),
  },
  platform: null as unknown as ClientPlatform,
  conversationId: 'conversation-a' as string | null,
}));
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: mock.controller, platform: mock.platform }),
  useClientSelector: (
    selector: (state: { selectedConversationId: string | null }) => unknown,
  ) => selector({ selectedConversationId: mock.conversationId }),
}));

const opened = {
  status: 'ok' as const,
  value: { terminalId: 'terminal-1' },
};

function output(cursor: number, ...data: string[]) {
  return {
    cursor: cursor + data.length,
    latest: cursor + data.length,
    truncated: false,
    frames: data.map((text, index) => ({
      sequence: cursor + index + 1,
      data: text,
    })),
    status: 'running' as const,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  mock.conversationId = 'conversation-a';
  mock.platform = createFakePlatform({ openTerminal: opened });
  mock.controller.terminalResize.mockResolvedValue({ ok: true });
  mock.controller.terminalInput.mockResolvedValue({ ok: true });
  mock.controller.terminalDisconnect.mockResolvedValue({ disconnected: true });
  mock.controller.terminalRead.mockImplementation(async (_id, cursor) =>
    output(cursor),
  );
});
afterEach(() => {
  vi.useRealTimers();
});

async function view() {
  await act(async () => {
    render(<NativeTerminal visible />);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

it('sends Ctrl+C to the running command from the Stop button', async () => {
  await view();
  const stop = screen.getByRole('button', {
    name: 'Stop the running command',
  });
  expect(stop).toHaveAttribute('aria-keyshortcuts', 'Control+C');
  await act(async () => fireEvent.click(stop));
  expect(mock.controller.terminalInput).toHaveBeenCalledWith(
    'terminal-1',
    '\x03',
  );
});

it('sends Ctrl+C from the input only when no text is selected', async () => {
  await view();
  const input = screen.getByRole('textbox', { name: 'Terminal input' });
  fireEvent.change(input, { target: { value: 'npm run dev' } });
  (input as HTMLInputElement).setSelectionRange(0, 3);
  await act(async () => fireEvent.keyDown(input, { key: 'c', ctrlKey: true }));
  expect(mock.controller.terminalInput).not.toHaveBeenCalled();

  (input as HTMLInputElement).setSelectionRange(11, 11);
  await act(async () => fireEvent.keyDown(input, { key: 'c', ctrlKey: true }));
  expect(mock.controller.terminalInput).toHaveBeenCalledTimes(1);
  expect(mock.controller.terminalInput).toHaveBeenCalledWith(
    'terminal-1',
    '\x03',
  );
  // The typed line is kept; only Send runs it.
  expect(input).toHaveValue('npm run dev');
  await act(async () => fireEvent.keyDown(input, { key: 'c', metaKey: true }));
  expect(mock.controller.terminalInput).toHaveBeenCalledTimes(1);
});

it('clears the shown output without reading old output again', async () => {
  mock.controller.terminalRead.mockImplementationOnce(async (_id, cursor) =>
    output(cursor, 'PS C:\\> ', 'dir\r\n', 'fixture listing'),
  );
  await view();
  expect(screen.getByText(/fixture listing/)).toBeInTheDocument();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Clear' })),
  );
  expect(screen.queryByText(/fixture listing/)).not.toBeInTheDocument();
  expect(screen.getByText('Terminal output will appear here.')).toBeVisible();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(100);
  });
  const calls = mock.controller.terminalRead.mock.calls;
  expect(calls.at(-1)?.[1]).toBe(3);
  expect(screen.queryByText(/fixture listing/)).not.toBeInTheDocument();
  expect(mock.controller.terminalInput).not.toHaveBeenCalled();
});

it('says where the terminal runs in plain words', async () => {
  await view();
  expect(screen.getByText('Terminal on this computer')).toBeVisible();
  expect(screen.queryByText(/PTY/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
});
