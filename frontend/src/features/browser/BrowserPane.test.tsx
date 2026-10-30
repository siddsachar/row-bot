import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  BrowserFrame,
  BrowserInputEvent,
  BrowserLiveSize,
  BrowserPaneState,
} from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import BrowserPane, { actingWords } from './BrowserPane';
import { BrowserStep } from './BrowserThumbnail';

const PAGE: BrowserPaneState = {
  schema_version: 1,
  conversation_id: 'conversation-a',
  status: 'running',
  acting: '',
  waiting: '',
  url: 'https://www.homeserve.co.uk/claim/1',
  title: 'Make a claim',
  site: 'homeserve.co.uk',
  loading: false,
  sign_in: '',
  recording: false,
  tabs: [
    {
      id: 't1',
      title: 'Make a claim',
      url: 'https://www.homeserve.co.uk/claim/1',
      active: true,
    },
  ],
  revision: 3,
};
const FRAME: BrowserFrame = {
  seq: 1,
  css_width: 1280,
  css_height: 800,
  image_base64: '/9j/4AAQSkZJRg==',
};

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function setup(
  state: Partial<BrowserPaneState> | null,
  {
    frame = FRAME,
    running = false,
    asked = false,
  }: { frame?: BrowserFrame | null; running?: boolean; asked?: boolean } = {},
) {
  const sent: BrowserInputEvent[][] = [];
  const snapshot = {
    status: 'ready',
    conversation: { id: 'conversation-a', revision: '7', title: 'Plumber' },
    projection: asked
      ? {
          generation: {
            quiesced: true,
            can_stop: true,
            status: 'waiting_approval',
            approval_id: 'approval-1',
          },
        }
      : running
        ? { generation: { quiesced: false, can_stop: true, status: 'running' } }
        : undefined,
    activity: asked
      ? [
          {
            event: {
              type: 'approval.required',
              payload: {
                status: 'waiting_approval',
                approval_id: 'approval-1',
                questions: [
                  {
                    header: 'Sign in',
                    question:
                      'Sign in to ChatGPT in the browser, then press Done.',
                    options: [
                      { label: 'Done', recommended: true },
                      { label: "I can't" },
                    ],
                  },
                ],
              },
            },
          },
        ]
      : [],
  };
  const controller = {
    subscribe: () => () => {},
    getSnapshot: () => snapshot,
    browserLive: vi.fn(async function* (
      _conversation: string,
      _view: string,
      _size: BrowserLiveSize,
      signal: AbortSignal,
    ) {
      if (state) yield { state: { ...PAGE, ...state } };
      if (frame) yield { frame };
      await new Promise((resolve) => signal.addEventListener('abort', resolve));
    }),
    browserAction: vi.fn(async () => ({ ...PAGE, ...state })),
    browserInput: vi.fn(async (_c: string, events: BrowserInputEvent[]) => {
      sent.push(events);
      return {
        status: 'ok',
        copied: events.some((event) => event.type === 'copy')
          ? 'ABCD-1234'
          : '',
      };
    }),
    intent: vi.fn(async () => ({})),
    approval: vi.fn(async () => ({
      nonce: 'n'.repeat(40),
      revision: '0',
      questions: [
        {
          header: 'Sign in',
          question: 'Sign in to ChatGPT in the browser, then press Done.',
          options: [{ label: 'Done', recommended: true }, { label: "I can't" }],
        },
      ],
    })),
    pendingApprovals: vi.fn(async () => ({ items: [] })),
    browserLocalAddresses: vi.fn(),
    removeBrowserLocalAddress: vi.fn(),
  } as unknown as ClientController & {
    browserAction: ReturnType<typeof vi.fn>;
    browserInput: ReturnType<typeof vi.fn>;
    intent: ReturnType<typeof vi.fn>;
    browserLive: ReturnType<typeof vi.fn>;
    approval: ReturnType<typeof vi.fn>;
  };
  const platform = {
    managedWindow: vi.fn(async () => ({ status: 'ok' })),
    readClipboard: vi.fn(async () => ({ status: 'ok', value: 'WXYZ-9876' })),
  } as unknown as ClientPlatform & {
    managedWindow: ReturnType<typeof vi.fn>;
    readClipboard: ReturnType<typeof vi.fn>;
  };
  render(
    <RuntimeContext.Provider value={{ controller, platform }}>
      <OverlayProvider>
        <BrowserPane conversationId="conversation-a" />
      </OverlayProvider>
    </RuntimeContext.Provider>,
  );
  return { controller, platform, sent };
}

it('shows the live page with the address, and plain words for what Row-Bot is doing', async () => {
  const { controller } = setup(
    { acting: 'Clicking button "Next"' },
    { running: true },
  );
  expect(
    await screen.findByRole('img', { name: 'Live page: Make a claim' }),
  ).toBeVisible();
  expect(screen.getByRole('textbox', { name: 'Address' })).toHaveValue(
    'https://www.homeserve.co.uk/claim/1',
  );
  expect(await screen.findByText('Row-Bot: Clicking "Next"')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Stop' }));
  expect(controller.intent).toHaveBeenCalledWith(
    'conversation-a',
    'conversation.stop',
    {},
    '7',
  );
  // One shared page: nothing to take or give back.
  expect(screen.queryByRole('button', { name: 'Take control' })).toBeNull();
});

it('gives the page the pane’s size, and the new size once a resize settles', async () => {
  let resized: ResizeObserverCallback = () => {};
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(callback: ResizeObserverCallback) {
        resized = callback;
      }
      observe() {}
      disconnect() {}
    },
  );
  let area = { width: 420, height: 700 };
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
    function (this: HTMLElement) {
      return (
        this.classList.contains('browser-viewport')
          ? { ...area, top: 0, left: 0, right: area.width, bottom: area.height }
          : { width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0 }
      ) as DOMRect;
    },
  );
  vi.stubGlobal('devicePixelRatio', 2);
  const { controller } = setup({});
  await screen.findByRole('img', { name: 'Live page: Make a claim' });
  // Measured before it first connects: one connection, at the pane's size.
  expect(controller.browserLive).toHaveBeenCalledTimes(1);
  expect(controller.browserLive.mock.calls[0][2]).toEqual({
    width: 420,
    height: 700,
    scale: 2,
  });
  // Docked → phone (or a drag): the page follows once the size settles.
  area = { width: 390, height: 780 };
  act(() => resized([], {} as ResizeObserver));
  await waitFor(() =>
    expect(controller.browserLive).toHaveBeenLastCalledWith(
      'conversation-a',
      'pane',
      { width: 390, height: 780, scale: 2 },
      expect.any(AbortSignal),
    ),
  );
  expect(controller.browserLive).toHaveBeenCalledTimes(2);
});

it('says plainly that the person signs in on the page, with nothing to take over', async () => {
  setup({ sign_in: 'password' });
  expect(await screen.findByText('Sign in here.')).toBeVisible();
  expect(screen.getByText(/click the page and sign in/)).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Take control' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Give back' })).toBeNull();
});

it('when Row-Bot asks for something on the page, Done above it answers', async () => {
  const { controller } = setup({ sign_in: 'password' }, { asked: true });
  expect(await screen.findByText('Row-Bot is waiting for you.')).toBeVisible();
  expect(
    screen.getByText('Sign in to ChatGPT in the browser, then press Done.'),
  ).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Done' }));
  await waitFor(() =>
    expect(controller.intent).toHaveBeenCalledWith(
      'approval-1',
      'approval.resolve',
      {
        decision: 'approve',
        nonce: 'n'.repeat(40),
        answers: [{ choices: [0] }],
      },
      '0',
    ),
  );
});

it('Ctrl+C copies the page’s selection to the clipboard', async () => {
  const writeText = vi.fn(async () => {});
  vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
  const { sent } = setup({});
  await screen.findByRole('img', { name: 'Live page: Make a claim' });
  const keys = screen.getByRole('textbox', { name: /Type into the page/ });
  fireEvent.keyDown(keys, { key: 'c', ctrlKey: true });
  fireEvent.keyUp(keys, { key: 'c', ctrlKey: true });
  await waitFor(() => expect(writeText).toHaveBeenCalledWith('ABCD-1234'));
  expect(await screen.findByText('Copied')).toBeVisible();
  // The copy is read from the page, never sent to it as keys.
  expect(sent.flat()).toEqual([{ type: 'copy' }]);
});

it('a paste reaches the page as its own paste, from the keyboard or the menu', async () => {
  const { sent, platform } = setup({});
  await screen.findByRole('img', { name: 'Live page: Make a claim' });
  const keys = screen.getByRole('textbox', { name: /Type into the page/ });
  fireEvent.paste(keys, {
    clipboardData: { getData: () => 'ABCD-1234' },
  });
  await waitFor(() =>
    expect(sent.flat()).toContainEqual({ type: 'paste', text: 'ABCD-1234' }),
  );

  await userEvent.click(
    screen.getByRole('button', { name: 'More browser actions' }),
  );
  await userEvent.click(
    await screen.findByRole('menuitem', { name: 'Paste into the page' }),
  );
  await waitFor(() =>
    expect(sent.flat()).toContainEqual({ type: 'paste', text: 'WXYZ-9876' }),
  );

  platform.readClipboard.mockResolvedValueOnce({
    status: 'unavailable',
    message: '',
  });
  await userEvent.click(
    screen.getByRole('button', { name: 'More browser actions' }),
  );
  await userEvent.click(
    await screen.findByRole('menuitem', { name: 'Paste into the page' }),
  );
  expect(await screen.findByText(/couldn't read the clipboard/)).toBeVisible();
});

it('a crash says so and restarts on one click', async () => {
  const { controller } = setup(
    { status: 'crashed', tabs: [] },
    { frame: null },
  );
  expect(await screen.findByRole('alert')).toHaveTextContent(
    "Row-Bot's browser stopped. Restart it?",
  );
  await userEvent.click(screen.getByRole('button', { name: 'Restart' }));
  expect(controller.browserAction).toHaveBeenCalledWith('conversation-a', {
    action: 'restart',
  });
});

it('with nothing open it says what will show here and the address bar opens a page', async () => {
  const { controller } = setup(
    { status: 'stopped', tabs: [], url: '', title: '', site: '' },
    { frame: null },
  );
  expect(await screen.findByText('Nothing open yet')).toBeVisible();
  const address = screen.getByRole('textbox', { name: 'Address' });
  await userEvent.type(address, 'homeserve.co.uk{Enter}');
  expect(controller.browserAction).toHaveBeenCalledWith('conversation-a', {
    action: 'navigate',
    url: 'homeserve.co.uk',
  });
});

it('the person clicks and types into the page in its own CSS pixels', async () => {
  const { sent } = setup({});
  const picture = await screen.findByRole('img', {
    name: 'Live page: Make a claim',
  });
  vi.spyOn(picture, 'getBoundingClientRect').mockReturnValue({
    left: 100,
    top: 50,
    width: 640,
    height: 400,
    right: 740,
    bottom: 450,
    x: 100,
    y: 50,
    toJSON: () => ({}),
  });
  const viewport = picture.parentElement!;
  fireEvent.pointerDown(viewport, {
    clientX: 420,
    clientY: 250,
    button: 0,
    pointerType: 'mouse',
    detail: 1,
  });
  fireEvent.pointerUp(viewport, {
    clientX: 420,
    clientY: 250,
    button: 0,
    pointerType: 'mouse',
    detail: 1,
  });
  const keys = screen.getByRole('textbox', { name: /Type into the page/ });
  fireEvent.keyDown(keys, { key: 'a' });
  fireEvent.keyUp(keys, { key: 'a' });
  await waitFor(() =>
    expect(sent.flat()).toEqual([
      {
        type: 'mouse',
        action: 'down',
        x: 640,
        y: 400,
        button: 'left',
        clicks: 1,
      },
      {
        type: 'mouse',
        action: 'up',
        x: 640,
        y: 400,
        button: 'left',
        clicks: 1,
      },
      { type: 'key', action: 'down', key: 'a' },
      { type: 'key', action: 'up', key: 'a' },
    ]),
  );
});

it('pops out to its own window in the desktop app', async () => {
  const { platform } = setup({});
  await userEvent.click(await screen.findByRole('button', { name: 'Pop out' }));
  expect(platform.managedWindow).toHaveBeenCalledWith(
    '/app-v2/browser/conversation-a',
  );
});

it('shows the conversation’s tabs when there are several', async () => {
  const { controller } = setup({
    tabs: [
      {
        id: 't1',
        title: 'Make a claim',
        url: 'https://a.example/',
        active: false,
      },
      {
        id: 't2',
        title: 'Your engineer',
        url: 'https://a.example/e',
        active: true,
      },
    ],
  });
  const tab = await screen.findByRole('tab', { name: 'Make a claim' });
  expect(screen.getByRole('tab', { name: 'Your engineer' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await userEvent.click(tab);
  expect(controller.browserAction).toHaveBeenCalledWith('conversation-a', {
    action: 'switch_tab',
    tab: 't1',
  });
});

it('the chat’s live step names the site and opens the pane', async () => {
  const onOpen = vi.fn();
  const controller = {
    subscribe: () => () => {},
    getSnapshot: () => ({ status: 'ready' }),
    browserLive: async function* (
      _c: string,
      _v: string,
      _s: BrowserLiveSize,
      signal: AbortSignal,
    ) {
      yield { state: { ...PAGE, acting: 'Typing into textbox "Details"' } };
      yield { frame: FRAME };
      await new Promise((resolve) => signal.addEventListener('abort', resolve));
    },
  } as unknown as ClientController;
  await act(async () => {
    render(
      <RuntimeContext.Provider
        value={{ controller, platform: {} as ClientPlatform }}
      >
        <BrowserStep conversationId="conversation-a" onOpen={onOpen} />
      </RuntimeContext.Provider>,
    );
  });
  expect(await screen.findByText(/Using the browser/)).toHaveTextContent(
    'Using the browser · homeserve.co.uk',
  );
  expect(screen.getByText('Typing into "Details"')).toBeVisible();
  await userEvent.click(
    screen.getByRole('button', { name: 'Open the browser: homeserve.co.uk' }),
  );
  expect(onOpen).toHaveBeenCalled();
});

it('reads Row-Bot’s action words without control roles', () => {
  expect(actingWords('Clicking button "Confirm and pay £60"')).toBe(
    'Clicking "Confirm and pay £60"',
  );
  expect(actingWords('Opening https://example.com')).toBe(
    'Opening https://example.com',
  );
});
