import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type {
  OnboardingSnapshot,
  SubscriptionAccountsSnapshot,
  SubscriptionFlowSnapshot,
} from '../../api/types';
import { RuntimeContext } from '../../runtime';
import { SubscriptionAccountsSession } from '../settings/SubscriptionAccounts';
import FirstRun, { CODE_COPIED, CODE_NOT_COPIED } from './FirstRun';
import { WorkspaceActionsContext } from './workspace-actions';

// U3: on a server whose host Row-Bot knows, the first run is two big buttons
// and "Other ways", a sign-in goes straight on to a chat, and nothing looks
// for a local model runtime.

const revision = 'a'.repeat(64);
const accounts: SubscriptionAccountsSnapshot = {
  schema_version: 1,
  revision,
  accounts: ['codex', 'xai_oauth'].map((provider_id) => ({
    provider_id,
    revision,
    saved_state: 'disconnected',
    credential_storage: 'unknown',
    has_recovery: false,
    expires_at: null,
    runtime_state: 'unknown',
  })) as SubscriptionAccountsSnapshot['accounts'],
};
const waiting: SubscriptionFlowSnapshot = {
  flow_id: '10000000-0000-4000-8000-000000000001',
  server_epoch: '10000000-0000-4000-8000-000000000002',
  provider_id: 'codex',
  provider_revision: revision,
  state: 'waiting',
  method: 'device_code',
  authorization_url: 'https://auth.openai.com/codex/device',
  device_code: 'ABCD-EFGH',
  expires_at: null,
  quiescent: true,
};

const mock = vi.hoisted(() => ({
  session: null as unknown,
  controller: {
    localRuntime: vi.fn(),
    liveProviderStatus: vi.fn(),
    testChosenModel: vi.fn(),
    refreshChoices: vi.fn(),
    modelCatalogPage: vi.fn(),
    refreshLiveProvider: vi.fn(),
    liveProviderRefresh: vi.fn(),
    updates: vi.fn(),
    subscriptionAccounts: vi.fn(),
    reviewSubscriptionAction: vi.fn(),
    applySubscriptionAction: vi.fn(),
    subscriptionFlow: vi.fn(),
    cancelSubscriptionFlow: vi.fn(),
    cancelSubscriptionStart: vi.fn(),
    subscriptionReceipt: vi.fn(),
    claudeCodeInstall: vi.fn(),
    installClaudeCode: vi.fn(),
  },
}));

vi.mock('../../runtime', async () => {
  const { createContext } = await import('react');
  return {
    RuntimeContext: createContext(null),
    useRuntime: () => ({
      controller: mock.controller,
      subscriptionAccountsOwner: { get: () => mock.session },
    }),
  };
});

const snapshot: OnboardingSnapshot = {
  schema_version: 1,
  revision,
  setup_complete: false,
  needs_model: true,
  default_model: null,
  live_done: [],
  import_sources: [],
  starter_workflows_missing: 0,
  profile: [],
  completed_steps: [],
  skipped_steps: [],
  dismissed_home_card: false,
  dismissed_phone_card: false,
  host_name: 'PikaPods',
  steps: [],
  intents: [],
};

function Location() {
  return <output aria-label="Current location">{useLocation().search}</output>;
}

function show(extra: Partial<OnboardingSnapshot> = {}) {
  const actions = {
    choose: vi.fn(async () => ({ ...snapshot, needs_model: false })),
    chooseSuggested: vi.fn(async () => ({ ...snapshot, needs_model: false })),
    finish: vi.fn(async () => ({ ...snapshot, setup_complete: true })),
  };
  const newChat = vi.fn();
  const runtime = { controller: mock.controller, platform: {} } as never;
  render(
    <RuntimeContext.Provider value={runtime}>
      <MemoryRouter initialEntries={['/setup']}>
        <WorkspaceActionsContext.Provider
          value={{ resetLayout: vi.fn(), newChat }}
        >
          <FirstRun snapshot={{ ...snapshot, ...extra }} actions={actions} />
          <Location />
        </WorkspaceActionsContext.Provider>
      </MemoryRouter>
    </RuntimeContext.Provider>,
  );
  return { actions, newChat };
}

const card = (provider_id: string, configured: boolean, extra = {}) => ({
  provider_id,
  configured,
  group: 'subscription',
  billing: 'subscription',
  ...extra,
});

let open: ReturnType<typeof vi.spyOn>;
let copied: string[];

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  for (const fn of Object.values(mock.controller)) fn.mockReset();
  mock.session = new SubscriptionAccountsSession();
  mock.controller.liveProviderStatus.mockResolvedValue({
    schema_version: 1,
    providers: [card('codex', false)],
  });
  mock.controller.updates.mockResolvedValue({ mode: 'external' });
  mock.controller.testChosenModel.mockResolvedValue({
    schema_version: 1,
    ok: true,
    detail: 'gpt-5.6-sol answered.',
    elapsed_ms: 900,
  });
  mock.controller.refreshChoices.mockResolvedValue(undefined);
  mock.controller.refreshLiveProvider.mockResolvedValue({ running: true });
  mock.controller.liveProviderRefresh.mockResolvedValue({ running: false });
  mock.controller.subscriptionAccounts.mockResolvedValue(accounts);
  mock.controller.reviewSubscriptionAction.mockImplementation(
    async (intent) => ({
      provider_id: intent.provider_id,
      provider_revision: intent.provider_revision,
      operation: intent.operation,
      flow_id: intent.flow_id ?? null,
      server_epoch: intent.server_epoch ?? null,
      action_digest: 'b'.repeat(64),
      nonce: 'nonce',
    }),
  );
  mock.controller.applySubscriptionAction.mockImplementation(
    async (intent, _review, commandId) => ({
      command_id: commandId,
      status: 'completed',
      accounts,
      flow: { ...waiting, provider_id: intent.provider_id },
    }),
  );
  mock.controller.subscriptionFlow.mockResolvedValue(waiting);
  open = vi
    .spyOn(window, 'open')
    .mockImplementation(() => ({ opener: window }) as unknown as Window);
  copied = [];
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: {
      writeText: vi.fn(async (text: string) => {
        copied.push(text);
      }),
    },
  });
  document.execCommand = vi.fn(() => false);
});

afterEach(() => {
  open.mockRestore();
  vi.useRealTimers();
});

it('asks with two big buttons and other ways, and never looks for a local runtime', async () => {
  show();
  const choices = screen.getByRole('group', {
    name: 'How should Row-Bot think?',
  });
  expect(
    within(choices)
      .getAllByRole('button')
      .map((button) => button.textContent),
  ).toEqual(['Sign in with ChatGPT', 'Use my Claude plan']);
  expect(screen.queryByText('On this computer')).toBeNull();
  fireEvent.click(screen.getByText('Other ways'));
  const other = screen.getByRole('list', { name: 'Other ways' });
  expect(within(other).getByText('Use an API key')).toBeVisible();
  expect(within(other).getByText('Sign in with Grok')).toBeVisible();
  // Your own model server opens the endpoint form with Ollama picked, and its guide.
  expect(
    within(other).getByRole('link', { name: /Use my own model server/ }),
  ).toHaveAttribute(
    'href',
    '/settings/providers?add=custom-endpoint&type=ollama&from=setup',
  );
  expect(
    within(other).getByRole('link', { name: 'How to reach it safely' }),
  ).toHaveAttribute('href', 'https://row-bot.ai/docs/guides/own-model-server');
  const gpu = screen.getByText(/Running models on this server needs a GPU/);
  expect(gpu).toHaveTextContent(
    'Running models on this server needs a GPU. Use your own model server, or get the desktop app →',
  );
  expect(
    within(gpu).getByRole('link', { name: 'get the desktop app →' }),
  ).toHaveAttribute('href', 'https://row-bot.ai/#install');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(7000);
  });
  expect(mock.controller.localRuntime).not.toHaveBeenCalled();
  expect(
    screen.queryByRole('link', { name: 'Other (custom endpoint)' }),
  ).toBeNull();
});

it('one click on ChatGPT starts the sign-in, copies the code and opens OpenAI’s page', async () => {
  show();
  fireEvent.click(screen.getByRole('button', { name: 'Sign in with ChatGPT' }));
  expect(await screen.findByText(CODE_COPIED)).toBeVisible();
  expect(copied).toEqual(['ABCD-EFGH']);
  expect(open).toHaveBeenCalledWith(
    'https://auth.openai.com/codex/device',
    '_blank',
  );
  expect(mock.controller.applySubscriptionAction).toHaveBeenCalledOnce();
  // The code stays on screen too, with its own copy button.
  expect(screen.getByText('ABCD-EFGH')).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Copy device code' }),
  ).toBeVisible();
});

it('shows the code to copy by hand when the browser refuses the copy', async () => {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: vi.fn(async () => Promise.reject(new Error('no'))) },
  });
  show();
  fireEvent.click(screen.getByRole('button', { name: 'Sign in with ChatGPT' }));
  expect(await screen.findByText(CODE_NOT_COPIED)).toBeVisible();
  expect(screen.getByText('ABCD-EFGH')).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Copy device code' }),
  ).toBeVisible();
});

it('goes from a finished sign-in straight into a new chat on ChatGPT’s own pick', async () => {
  const { actions, newChat } = show();
  fireEvent.click(screen.getByRole('button', { name: 'Sign in with ChatGPT' }));
  await screen.findByText(CODE_COPIED);
  // The person approves on OpenAI's page; Row-Bot notices by itself.
  mock.controller.subscriptionFlow.mockResolvedValue({
    ...waiting,
    state: 'connected',
  });
  mock.controller.liveProviderStatus.mockResolvedValue({
    schema_version: 1,
    providers: [card('codex', true)],
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5200);
  });
  await vi.waitFor(() => expect(newChat).toHaveBeenCalledOnce());
  expect(actions.chooseSuggested).toHaveBeenCalledWith('codex');
  expect(mock.controller.refreshLiveProvider).toHaveBeenCalledWith('codex');
  expect(mock.controller.testChosenModel).toHaveBeenCalledOnce();
  expect(actions.finish).toHaveBeenCalledOnce();
  expect(actions.choose).not.toHaveBeenCalled();
});

it('lists the provider’s models when it has none ready to start on', async () => {
  mock.controller.liveProviderStatus.mockResolvedValue({
    schema_version: 1,
    providers: [card('codex', true)],
  });
  mock.controller.modelCatalogPage.mockResolvedValue({ items: [] });
  const { actions, newChat } = show();
  actions.chooseSuggested.mockRejectedValueOnce({
    code: 'model_configuration_unavailable',
  });
  // Already connected before (a reload mid-sign-in): it carries straight on.
  fireEvent.click(screen.getByRole('button', { name: 'Sign in with ChatGPT' }));
  expect(
    await screen.findByText('No chat models are available from ChatGPT yet.'),
  ).toBeVisible();
  expect(newChat).not.toHaveBeenCalled();
  expect(mock.controller.applySubscriptionAction).not.toHaveBeenCalled();
});

it('says Connected while the first model is checked', async () => {
  mock.controller.liveProviderStatus.mockResolvedValue({
    schema_version: 1,
    providers: [card('codex', true)],
  });
  let answer: (value: unknown) => void = () => undefined;
  mock.controller.testChosenModel.mockReturnValue(
    new Promise((resolve) => (answer = resolve)),
  );
  show();
  fireEvent.click(screen.getByRole('button', { name: 'Sign in with ChatGPT' }));
  expect(
    await screen.findByRole('heading', { name: 'Checking the model' }),
  ).toBeVisible();
  expect(screen.getByText('Connected to ChatGPT')).toBeVisible();
  expect(screen.queryByText(/A local model can take a minute/)).toBeNull();
  answer({ schema_version: 1, ok: true, detail: 'ok', elapsed_ms: 1 });
});

it('Use my Claude plan installs Claude Code at once, with no second button', async () => {
  mock.controller.liveProviderStatus.mockResolvedValue({
    schema_version: 1,
    providers: [
      card('claude_subscription', false, {
        claude_plan: {
          state: 'not_installed',
          version: '',
          minimum_version: '2.1.263',
          plan: '',
          detail: '',
          set_up_again: false,
        },
      }),
    ],
  });
  mock.controller.claudeCodeInstall.mockResolvedValue({
    state: 'idle',
    step: '',
    received: 0,
    total: 0,
    version: '',
    reason: '',
  });
  mock.controller.installClaudeCode.mockReturnValue(new Promise(() => {}));
  show();
  fireEvent.click(screen.getByRole('button', { name: 'Use my Claude plan' }));
  expect(
    await screen.findByText('Installing Claude Code from Anthropic…'),
  ).toBeVisible();
  expect(mock.controller.installClaudeCode).toHaveBeenCalledWith(false);
  expect(
    screen.getAllByRole('button', { name: 'Use my Claude plan' }),
  ).toHaveLength(1);
});

it('Sign in with Grok tells a browser elsewhere to paste the address it ends on', async () => {
  mock.controller.applySubscriptionAction.mockImplementation(
    async (intent, _review, commandId) => ({
      command_id: commandId,
      status: 'completed',
      accounts,
      flow: {
        ...waiting,
        provider_id: intent.provider_id,
        method: 'loopback',
        device_code: null,
        authorization_url: 'https://auth.x.ai/oauth2/authorize?state=s',
      },
    }),
  );
  show();
  fireEvent.click(screen.getByText('Other ways'));
  fireEvent.click(screen.getByRole('button', { name: /Sign in with Grok/ }));
  expect(
    await screen.findByText(/sends you to a page that doesn’t load/),
  ).toBeVisible();
  expect(
    screen.getByLabelText('Address of the page that didn’t load'),
  ).toBeVisible();
  expect(open).toHaveBeenCalledWith(
    'https://auth.x.ai/oauth2/authorize?state=s',
    '_blank',
  );
});
