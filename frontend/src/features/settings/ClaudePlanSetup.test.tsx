import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  expect,
  it,
  vi,
} from 'vitest';
import type {
  ClaudeCodeInstallState,
  ClaudePlanStatus,
  ClaudeSignInOutput,
} from '../../api/types';
import type { ClientController } from '../../api/controller';
import { createFakePlatform } from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import ClaudePlanSetup, {
  CLAUDE_PLAN_SUMMARY,
  claudePlanSummary,
} from './ClaudePlanSetup';
import { SIGN_IN_GUIDE, SIGN_IN_GUIDE_HERE } from './ClaudeSignInPanel';

// xterm measures characters with layout jsdom lacks: an 8×16 px cell, a sized
// box for the fit addon, and the observers a browser has (as NativeTerminal's test).
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
    observe() {}
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

function plan(overrides: Partial<ClaudePlanStatus> = {}): ClaudePlanStatus {
  return {
    state: 'signed_out',
    version: '2.1.282',
    minimum_version: '2.1.263',
    plan: '',
    detail: '',
    set_up_again: false,
    signed_in_before: false,
    ...overrides,
  };
}

function progress(
  overrides: Partial<ClaudeCodeInstallState> = {},
): ClaudeCodeInstallState {
  return {
    state: 'running',
    step: 'download',
    received: 0,
    total: 0,
    version: '',
    reason: '',
    ...overrides,
  };
}

const CLAUDE_SCREEN =
  'Browser didn’t open? Use the url below to sign in:\r\nhttps://claude.ai/oauth/authorize?code=true\r\nPaste code here if prompted > ';

function output(
  cursor: number,
  data: string,
  ended: Partial<ClaudeSignInOutput> = {},
): ClaudeSignInOutput {
  return {
    cursor: data ? cursor + 1 : cursor,
    latest: data ? cursor + 1 : cursor,
    truncated: false,
    frames: data ? [{ sequence: cursor + 1, data }] : [],
    status: 'running',
    ended: null,
    plan: null,
    ...ended,
  };
}

function fakeController() {
  let screenText = CLAUDE_SCREEN;
  return {
    claudeCodeInstall: vi.fn(async () => progress({ state: 'idle' })),
    installClaudeCode: vi.fn(async () => progress()),
    openClaudeSignIn: vi.fn(async () => ({
      sign_in_id: 'sign-in-1',
      browser_opens: false,
    })),
    readClaudeSignIn: vi.fn(async (_id: string, cursor: number) => {
      const data = screenText;
      screenText = '';
      return output(cursor, data);
    }),
    writeClaudeSignIn: vi.fn(async (_id: string, _data: string) => ({
      ok: true as const,
    })),
    resizeClaudeSignIn: vi.fn(async () => ({ ok: true as const })),
    closeClaudeSignIn: vi.fn(async () => ({ disconnected: true as const })),
  };
}

let controller: ReturnType<typeof fakeController>;
const openExternal = vi.fn(async () => ({
  status: 'ok' as const,
  value: null,
}));

beforeEach(() => {
  vi.useFakeTimers();
  controller = fakeController();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

async function settle(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function show(status: ClaudePlanStatus | null, checking = false) {
  const onCheck = vi.fn();
  const onDone = vi.fn();
  const platform = createFakePlatform();
  platform.openExternal = openExternal;
  const view = (value: ClaudePlanStatus | null) => (
    <RuntimeContext.Provider
      value={{
        controller: controller as unknown as ClientController,
        platform,
      }}
    >
      <ClaudePlanSetup
        plan={value}
        checking={checking}
        onCheck={onCheck}
        onDone={onDone}
      />
    </RuntimeContext.Provider>
  );
  let result!: ReturnType<typeof render>;
  await act(async () => {
    result = render(view(status));
  });
  await settle(20);
  return {
    onCheck,
    onDone,
    rerender: async (value: ClaudePlanStatus) => {
      await act(async () => result.rerender(view(value)));
      await settle(20);
    },
  };
}

function rows() {
  return [...document.querySelectorAll('.xterm-rows > div')]
    .map((row) => (row.textContent ?? '').trimEnd())
    .join('\n');
}

it('says what Row-Bot does with the Claude plan in the plain words of U11', () => {
  expect(CLAUDE_PLAN_SUMMARY).toBe(
    'Row-Bot runs Claude Code, Anthropic’s program, with your own Claude plan. Its limits apply.',
  );
});

it('says Row-Bot never sees the sign-in in every state, and never offers a token field', async () => {
  for (const state of [
    'not_installed',
    'signed_out',
    'too_old',
    'ready',
    'error',
  ] as const) {
    await show(plan({ state }));
    expect(
      screen.getByText(
        'Row-Bot never sees your Claude sign-in. It starts Claude Code with its non-essential traffic off: no telemetry, error reports or automatic updates from these runs.',
      ),
    ).toBeVisible();
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.queryByRole('button', { name: /^Claude Code$/ })).toBeNull();
    cleanup();
  }
});

it('not installed: one button installs Claude Code from Anthropic, then opens Claude’s own sign-in', async () => {
  controller.claudeCodeInstall
    .mockResolvedValueOnce(progress({ state: 'idle' }))
    .mockResolvedValueOnce(progress({ received: 50, total: 100 }))
    .mockResolvedValueOnce(
      progress({ state: 'done', step: '', version: '2.1.300' }),
    );
  const { onCheck } = await show(plan({ state: 'not_installed', version: '' }));

  fireEvent.click(screen.getByRole('button', { name: 'Use my Claude plan' }));
  await settle();
  expect(controller.installClaudeCode).toHaveBeenCalledWith(false);
  expect(
    screen.getByText('Installing Claude Code from Anthropic…'),
  ).toBeVisible();
  expect(screen.getByRole('progressbar')).toHaveAttribute('value', '45');

  await settle(600);
  expect(screen.getByText('Installed (version 2.1.300)')).toBeVisible();
  expect(onCheck).toHaveBeenCalled();
  expect(controller.openClaudeSignIn).toHaveBeenCalledOnce();
  expect(screen.getByText(SIGN_IN_GUIDE)).toBeVisible();
  await settle(50);
  expect(rows()).toContain('Paste code here if prompted >');
});

it.each([
  ['no_internet', 'Claude Code couldn’t be installed: no internet connection.'],
  [
    'checksum_mismatch',
    'Claude Code couldn’t be installed: the download didn’t match Anthropic’s checksum, so it was deleted.',
  ],
  [
    'disk_full',
    'Claude Code couldn’t be installed: there isn’t enough disk space.',
  ],
  [
    'unsupported_platform',
    'Claude Code couldn’t be installed: it doesn’t run on this system.',
  ],
  [
    'download_failed',
    'Claude Code couldn’t be installed: the download from Anthropic didn’t work.',
  ],
  [
    'install_failed',
    'Claude Code couldn’t be installed: its installer stopped with an error.',
  ],
] as const)(
  'install failed (%s): a plain reason and Try again',
  async (reason, words) => {
    controller.claudeCodeInstall
      .mockResolvedValueOnce(progress({ state: 'idle' }))
      .mockResolvedValueOnce(progress({ state: 'failed', step: '', reason }));
    await show(plan({ state: 'not_installed', version: '' }));

    fireEvent.click(screen.getByRole('button', { name: 'Use my Claude plan' }));
    await settle();
    expect(screen.getByRole('alert')).toHaveTextContent(words);
    expect(controller.openClaudeSignIn).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await settle();
    expect(controller.installClaudeCode).toHaveBeenCalledTimes(2);
  },
);

it('signed out: Use my Claude plan opens Claude’s sign-in; what is typed goes to Claude', async () => {
  await show(plan());
  expect(controller.installClaudeCode).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'Use my Claude plan' }));
  await settle(50);
  expect(controller.openClaudeSignIn).toHaveBeenCalledOnce();
  expect(screen.getByText(SIGN_IN_GUIDE)).toBeVisible();
  expect(rows()).toContain('https://claude.ai/oauth/authorize?code=true');

  // Pasted into Claude's prompt as a terminal takes it.
  fireEvent(
    screen.getByRole('textbox', { name: 'Terminal input' }),
    new InputEvent('input', { data: 'code#state', inputType: 'insertText' }),
  );
  await settle(50);
  expect(
    controller.writeClaudeSignIn.mock.calls.map(([, data]) => data).join(''),
  ).toContain('code#state');
});

it('on this computer: Claude opens the browser itself, so the line says to finish there', async () => {
  controller.openClaudeSignIn.mockResolvedValue({
    sign_in_id: 'sign-in-1',
    browser_opens: true,
  });
  await show(plan());

  fireEvent.click(screen.getByRole('button', { name: 'Use my Claude plan' }));
  await settle(50);
  expect(screen.getByText(SIGN_IN_GUIDE_HERE)).toBeVisible();
  expect(screen.queryByText(SIGN_IN_GUIDE)).toBeNull();
});

it('connected: Claude Code ended signed in, so the panel closes by itself and the plan is connected', async () => {
  const { onCheck, rerender } = await show(plan());
  fireEvent.click(screen.getByRole('button', { name: 'Use my Claude plan' }));
  await settle(50);
  controller.readClaudeSignIn.mockImplementation(async (_id, cursor) =>
    output(cursor, '', {
      status: 'ended',
      ended: 'exited',
      plan: plan({ state: 'ready', plan: 'Claude Max' }),
    }),
  );
  await settle(300);

  expect(screen.queryByRole('region', { name: 'Claude’s sign-in' })).toBeNull();
  expect(screen.getByText('Claude plan connected')).toBeVisible();
  expect(onCheck).toHaveBeenCalled();
  await rerender(
    plan({ state: 'ready', plan: 'Claude Max', version: '2.1.300' }),
  );
  expect(screen.getByText('Claude Max · Claude Code 2.1.300')).toBeVisible();
});

it('closed early: Claude Code ended without a sign-in, or the panel was closed', async () => {
  await show(plan());
  fireEvent.click(screen.getByRole('button', { name: 'Use my Claude plan' }));
  await settle(50);
  controller.readClaudeSignIn.mockImplementation(async (_id, cursor) =>
    output(cursor, '', { status: 'ended', ended: 'exited', plan: plan() }),
  );
  await settle(300);
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Claude’s sign-in was closed before it finished.',
  );

  controller.readClaudeSignIn.mockImplementation(async (_id, cursor) =>
    output(cursor, CLAUDE_SCREEN),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Sign in again' }));
  await settle(50);
  fireEvent.click(
    screen.getByRole('button', { name: 'Close Claude’s sign-in' }),
  );
  await settle();
  expect(controller.closeClaudeSignIn).toHaveBeenCalledWith('sign-in-1');
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Claude’s sign-in was closed before it finished.',
  );
});

it('signed out by Claude after it worked here: says so, and Sign in again', async () => {
  await show(plan({ signed_in_before: true }));
  expect(
    screen.getByText(
      'Claude signed you out. Sign in again to keep using your plan.',
    ),
  ).toBeVisible();
  expect(claudePlanSummary(plan({ signed_in_before: true })).detail).toBe(
    'Claude signed you out',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Sign in again' }));
  await settle(50);
  expect(controller.openClaudeSignIn).toHaveBeenCalledOnce();
});

it('signed in without a Claude plan: says why, and Sign in again', async () => {
  await show(
    plan({
      detail:
        'Claude Code is signed in without a Claude plan, so it would bill another way.',
    }),
  );
  expect(
    screen.getByText(
      'Claude Code is signed in without a Claude plan, so it would bill another way.',
    ),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Sign in again' })).toBeVisible();
});

it('too old: names the version Row-Bot needs, and Update Claude Code reinstalls the newest', async () => {
  controller.claudeCodeInstall
    .mockResolvedValueOnce(progress({ state: 'running' }))
    .mockResolvedValueOnce(
      progress({ state: 'done', step: '', version: '2.1.300' }),
    );
  const { onCheck } = await show(
    plan({ state: 'too_old', version: '2.1.100' }),
  );
  expect(
    screen.getByText('Row-Bot needs Claude Code 2.1.263 or newer.'),
  ).toBeVisible();

  fireEvent.click(screen.getByRole('button', { name: 'Update Claude Code' }));
  await settle();
  expect(controller.installClaudeCode).toHaveBeenCalledWith(true);
  expect(
    screen.getByText('Updating Claude Code from Anthropic…'),
  ).toBeVisible();
  await settle(600);
  expect(onCheck).toHaveBeenCalled();
  expect(controller.openClaudeSignIn).not.toHaveBeenCalled();
});

it('ready: Claude plan connected, with Done and Update Claude Code', async () => {
  const { onDone } = await show(
    plan({ state: 'ready', plan: 'Claude Max', version: '2.1.282' }),
  );

  expect(screen.getByRole('status')).toHaveTextContent(
    /^Claude plan connected\s*Claude Max · Claude Code 2\.1\.282$/,
  );
  expect(
    screen.getByText('Claude models are in the model picker.'),
  ).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Update Claude Code' }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Done' }));
  expect(onDone).toHaveBeenCalledOnce();
});

it('could not check: Check again, or update Claude Code; never a terminal', async () => {
  const { onCheck } = await show(
    plan({
      state: 'error',
      detail: 'Claude Code didn’t say whether it is signed in.',
    }),
  );
  expect(
    screen.getByText(
      'Claude Code didn’t say whether it is signed in. Check again, or update Claude Code.',
    ),
  ).toBeVisible();
  expect(screen.queryByText(/terminal/i)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
  expect(onCheck).toHaveBeenCalledOnce();
});

it('an install already running when the dialog opens shows its progress', async () => {
  controller.claudeCodeInstall
    .mockResolvedValueOnce(progress({ received: 10, total: 100 }))
    .mockResolvedValueOnce(progress({ received: 10, total: 100 }));
  await show(plan({ state: 'not_installed', version: '' }));
  expect(
    screen.getByText('Installing Claude Code from Anthropic…'),
  ).toBeVisible();
  expect(controller.installClaudeCode).not.toHaveBeenCalled();
});

it('a profile the old Claude sign-in had set up is told once to set it up again', async () => {
  await show(plan({ set_up_again: true }));
  expect(
    screen.getByText('Claude now works through Claude Code. Set it up again.'),
  ).toBeVisible();
});

it('before Claude Code answers: a quiet loading state', async () => {
  await show(null);
  expect(screen.getByText('Checking your Claude plan')).toBeInTheDocument();
});
