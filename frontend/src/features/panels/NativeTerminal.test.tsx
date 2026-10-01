import { act, fireEvent, render, screen } from '@testing-library/react';
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  expect,
  it,
  vi,
} from 'vitest';
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

// xterm measures its characters with the layout jsdom does not have: give
// every element an 8×16 px cell, a sized box for the fit addon, and the
// media-query and resize observers a browser has.
const resizeObservers: (() => void)[] = [];
const originals = {
  matchMedia: window.matchMedia,
  getContext: HTMLCanvasElement.prototype.getContext,
  ResizeObserver: window.ResizeObserver,
  offsetWidth: Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    'offsetWidth',
  )!,
  offsetHeight: Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    'offsetHeight',
  )!,
};
beforeAll(() => {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
  HTMLCanvasElement.prototype.getContext = (() =>
    null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
    configurable: true,
    get(this: HTMLElement) {
      return 8 * (this.textContent?.length ?? 0);
    },
  });
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
    configurable: true,
    get: () => 16,
  });
  window.ResizeObserver = class {
    constructor(private callback: () => void) {}
    observe() {
      resizeObservers.push(this.callback);
    }
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});
afterAll(() => {
  window.matchMedia = originals.matchMedia;
  HTMLCanvasElement.prototype.getContext = originals.getContext;
  window.ResizeObserver = originals.ResizeObserver;
  Object.defineProperty(
    HTMLElement.prototype,
    'offsetWidth',
    originals.offsetWidth,
  );
  Object.defineProperty(
    HTMLElement.prototype,
    'offsetHeight',
    originals.offsetHeight,
  );
});

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

/** Everything typed so far, in the order it reached the terminal. */
function typed() {
  return mock.controller.terminalInput.mock.calls
    .map(([, data]) => data as string)
    .join('');
}

function rows() {
  return [...document.querySelectorAll('.xterm-rows > div')].map((row) =>
    (row.textContent ?? '').trimEnd(),
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  resizeObservers.length = 0;
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

async function settle(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function view(onClose = vi.fn()) {
  let result!: ReturnType<typeof render>;
  await act(async () => {
    result = render(<NativeTerminal onClose={onClose} />);
  });
  await settle(20);
  return result;
}

function input() {
  return screen.getByRole('textbox', { name: 'Terminal input' });
}

function press(key: string, init: KeyboardEventInit & { keyCode: number }) {
  fireEvent.keyDown(input(), { key, ...init });
}

it('says the real reason the terminal did not open and reconnects (B238)', async () => {
  const openTerminal = vi
    .fn()
    .mockResolvedValueOnce({
      status: 'unavailable',
      reason: 'native_reconnecting',
    })
    .mockResolvedValue(opened);
  mock.platform = { ...createFakePlatform(), openTerminal };
  await view();
  expect(
    screen.getByRole('heading', { name: 'Terminal unavailable' }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      'Desktop features are reconnecting. Try again in a moment.',
    ),
  ).toBeInTheDocument();
  expect(screen.queryByText(/needs the Row-Bot desktop app/)).toBeNull();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' })),
  );
  await settle(20);
  expect(openTerminal).toHaveBeenCalledTimes(2);
  expect(
    screen.queryByRole('heading', { name: 'Terminal unavailable' }),
  ).toBeNull();
  expect(input()).toBeInTheDocument();
});

it('says only a browser needs the desktop app, with nothing to reconnect', async () => {
  mock.platform = createFakePlatform({
    openTerminal: { status: 'unavailable', reason: 'terminal_requires_native' },
  });
  await view();
  expect(
    screen.getByText('The terminal needs the Row-Bot desktop app.'),
  ).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Reconnect' })).toBeNull();
});

it('sends every key to the terminal as typed: arrows, Tab and Enter included (B248)', async () => {
  let release = () => {};
  mock.controller.terminalInput.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        release = () => resolve({ ok: true });
      }),
  );
  await view();
  press('d', { keyCode: 68 });
  // Keys typed while one is on its way go together, in order.
  press('i', { keyCode: 73 });
  press('r', { keyCode: 82 });
  press('Tab', { keyCode: 9 });
  press('ArrowUp', { keyCode: 38 });
  press('Enter', { keyCode: 13 });
  expect(mock.controller.terminalInput).toHaveBeenCalledTimes(1);
  await act(async () => release());
  await settle();
  expect(mock.controller.terminalInput).toHaveBeenCalledTimes(2);
  expect(mock.controller.terminalInput.mock.calls[0][0]).toBe('terminal-1');
  expect(typed()).toBe('dir\t\x1b[A\r');
});

it('renders output with its colours and cursor, even when a code is split across reads (B248)', async () => {
  mock.controller.terminalRead.mockImplementationOnce(async (_id, cursor) =>
    output(
      cursor,
      '\x1b]0;Windows PowerShell\x07Copyright. All rights reserved.\r\n',
      '\x1b[3',
      '1mred\x1b[0m plain\r\nPS C:\\> ',
    ),
  );
  await view();
  await settle(50);
  expect(rows().slice(0, 3)).toEqual([
    'Copyright. All rights reserved.',
    'red plain',
    'PS C:\\>',
  ]);
  const spans = [...document.querySelectorAll<HTMLElement>('.xterm-rows span')];
  const coloured = (text: string) =>
    spans.find((span) => span.textContent === text)?.style.color;
  expect(coloured('red')).toMatch(/^rgb\(/);
  expect(coloured(' plain')).toBe('');
  input().focus();
  await settle(50);
  // The cursor waits after the prompt.
  const cursor = document.querySelector('.xterm-rows .xterm-cursor');
  expect(cursor?.parentElement?.textContent).toBe('PS C:\\>  ');
});

it('reads the echo of a key at once, and only slowly while nothing happens', async () => {
  await view();
  await settle(3000);
  const idle = mock.controller.terminalRead.mock.calls.length;
  await settle(1000);
  // Idle: a few reads a second.
  expect(
    mock.controller.terminalRead.mock.calls.length - idle,
  ).toBeLessThanOrEqual(4);
  const before = mock.controller.terminalRead.mock.calls.length;
  press('a', { keyCode: 65 });
  await settle();
  // The key's echo is read as soon as the key has gone, then every frame.
  expect(mock.controller.terminalRead.mock.calls.length).toBe(before + 1);
  await settle(20);
  expect(mock.controller.terminalRead.mock.calls.length).toBeGreaterThan(
    before + 1,
  );
});

it('stops the running command with Ctrl+C sent on its own from the Stop button', async () => {
  await view();
  const stop = screen.getByRole('button', {
    name: 'Stop the running command',
  });
  expect(stop).toHaveAttribute('aria-keyshortcuts', 'Control+C');
  await act(async () => fireEvent.click(stop));
  await settle();
  expect(mock.controller.terminalInput).toHaveBeenCalledWith(
    'terminal-1',
    '\x03',
  );
});

it('Ctrl+C copies selected text, and stops the running command when nothing is selected', async () => {
  mock.controller.terminalRead.mockImplementationOnce(async (_id, cursor) =>
    output(cursor, 'hello world'),
  );
  await view();
  await settle(50);
  const screenArea = document.querySelector('.xterm-screen')!;
  // A double-click selects the word under the pointer.
  fireEvent.mouseDown(screenArea, { clientX: 4, clientY: 4, detail: 2 });
  fireEvent.mouseUp(screenArea, { clientX: 4, clientY: 4, detail: 2 });
  const copy = new KeyboardEvent('keydown', {
    key: 'c',
    keyCode: 67,
    ctrlKey: true,
    bubbles: true,
    cancelable: true,
  });
  input().dispatchEvent(copy);
  await settle();
  // Left to the browser, which copies the selection.
  expect(copy.defaultPrevented).toBe(false);
  expect(mock.controller.terminalInput).not.toHaveBeenCalled();

  fireEvent.mouseDown(screenArea, { clientX: 60, clientY: 4, detail: 1 });
  fireEvent.mouseUp(screenArea, { clientX: 60, clientY: 4, detail: 1 });
  press('c', { keyCode: 67, ctrlKey: true });
  await settle();
  expect(mock.controller.terminalInput.mock.calls).toEqual([
    ['terminal-1', '\x03'],
  ]);
});

it('leaves Ctrl+V to the browser, which pastes', async () => {
  await view();
  const paste = new KeyboardEvent('keydown', {
    key: 'v',
    keyCode: 86,
    ctrlKey: true,
    bubbles: true,
    cancelable: true,
  });
  input().dispatchEvent(paste);
  await settle();
  expect(paste.defaultPrevented).toBe(false);
  expect(mock.controller.terminalInput).not.toHaveBeenCalled();
});

it('sends a long paste in order, in pieces the terminal accepts', async () => {
  await view();
  const text = 'a'.repeat(4095) + '😀' + 'b'.repeat(6000);
  fireEvent(
    input(),
    new InputEvent('input', { data: text, inputType: 'insertText' }),
  );
  await settle();
  const pieces = mock.controller.terminalInput.mock.calls.map(
    ([, data]) => data as string,
  );
  expect(pieces.length).toBeGreaterThan(1);
  expect(pieces.every((piece) => piece.length <= 4096)).toBe(true);
  expect(pieces.join('')).toBe(text);
});

it('fits the terminal to its panel and tells the shell the new size', async () => {
  await view();
  const host = document.querySelector<HTMLElement>('.native-terminal-screen')!;
  host.style.width = '814px';
  host.style.height = '320px';
  await act(async () => resizeObservers.forEach((notify) => notify()));
  await settle(200);
  // 814 px less the 14 px scrollbar is 100 columns of 8 px; 320 px is 20 rows.
  expect(mock.controller.terminalResize).toHaveBeenLastCalledWith(
    'terminal-1',
    100,
    20,
  );
});

it('keeps the scrollback while the terminal is closed and reads on from where it was', async () => {
  mock.controller.terminalRead.mockImplementationOnce(async (_id, cursor) =>
    output(cursor, 'first command output\r\n'),
  );
  const first = await view();
  await settle(50);
  first.unmount();
  mock.controller.terminalRead.mockClear();
  mock.controller.terminalRead.mockImplementationOnce(async (_id, cursor) =>
    output(cursor, 'printed while closed\r\n'),
  );
  await view();
  await settle(50);
  expect(rows().slice(0, 2)).toEqual([
    'first command output',
    'printed while closed',
  ]);
  expect(mock.controller.terminalRead.mock.calls[0][1]).toBe(1);
  expect(
    (mock.platform as ClientPlatform & { calls: string[] }).calls.filter(
      (call) => call === 'openTerminal',
    ),
  ).toHaveLength(1);
  expect(mock.controller.terminalDisconnect).not.toHaveBeenCalled();
});

it('clears the screen without reading old output again', async () => {
  mock.controller.terminalRead.mockImplementationOnce(async (_id, cursor) =>
    output(cursor, 'PS C:\\> ', 'dir\r\n', 'fixture listing\r\nPS C:\\> '),
  );
  await view();
  await settle(50);
  expect(rows()).toContain('fixture listing');
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Clear' })),
  );
  await settle(300);
  expect(rows()).not.toContain('fixture listing');
  expect(mock.controller.terminalRead.mock.calls.at(-1)?.[1]).toBe(3);
  expect(mock.controller.terminalInput).not.toHaveBeenCalled();
});

it('offers to start again when the shell has ended', async () => {
  const openTerminal = vi
    .fn()
    .mockResolvedValueOnce(opened)
    .mockResolvedValue({ status: 'ok', value: { terminalId: 'terminal-2' } });
  mock.platform = { ...createFakePlatform(), openTerminal };
  mock.controller.terminalRead.mockImplementationOnce(async (_id, cursor) => ({
    ...output(cursor, 'exit\r\n'),
    status: 'stopped' as const,
  }));
  await view();
  await settle(50);
  expect(screen.getByRole('status')).toHaveTextContent(
    'The terminal session ended.',
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Start again' })),
  );
  await settle(20);
  expect(openTerminal).toHaveBeenCalledTimes(2);
  expect(mock.controller.terminalDisconnect).toHaveBeenCalledWith('terminal-1');
  expect(mock.controller.terminalRead).toHaveBeenLastCalledWith(
    'terminal-2',
    1,
    expect.anything(),
  );
  expect(rows()[0]).toBe('exit');
});

it('closes from its header', async () => {
  const onClose = vi.fn();
  await view(onClose);
  const close = screen.getByRole('button', { name: 'Close terminal' });
  expect(close).toHaveAttribute('aria-keyshortcuts', 'Control+`');
  fireEvent.click(close);
  expect(onClose).toHaveBeenCalledTimes(1);
});

const desktop = (capabilities: string[]) => ({
  status: 'ok' as const,
  value: {
    kind: 'pywebview' as const,
    platform: 'windows' as const,
    capabilities,
    instanceId: 'instance',
    windowId: 'window',
    epoch: 1,
  },
});

it('opens the person’s own terminal at this conversation', async () => {
  mock.platform = createFakePlatform({
    openTerminal: opened,
    discover: desktop(['terminal_open', 'terminal_external']),
    openExternalTerminal: { status: 'ok', value: null },
  });
  const openExternal = vi.spyOn(mock.platform, 'openExternalTerminal');
  await view();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Open in your terminal' }),
    ),
  );
  expect(openExternal).toHaveBeenCalledWith('conversation-a');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('says so when the terminal app could not be opened', async () => {
  mock.platform = createFakePlatform({
    openTerminal: opened,
    discover: desktop(['terminal_open', 'terminal_external']),
    openExternalTerminal: { status: 'unavailable', reason: 'unsupported' },
  });
  await view();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Open in your terminal' }),
    ),
  );
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Row-Bot couldn’t open your terminal app.',
  );
});

it.each([
  ['a desktop window without it', desktop(['terminal_open'])],
  [
    'a browser',
    {
      status: 'ok' as const,
      value: {
        kind: 'browser' as const,
        platform: 'browser' as const,
        capabilities: ['terminal_external'],
      },
    },
  ],
])('hides Open in your terminal in %s', async (_where, discovered) => {
  mock.platform = createFakePlatform({
    openTerminal: opened,
    discover: discovered,
  });
  await view();
  expect(
    screen.queryByRole('button', { name: 'Open in your terminal' }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Stop the running command' }),
  ).toBeEnabled();
});
