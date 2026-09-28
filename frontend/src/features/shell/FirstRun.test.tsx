import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { OnboardingSnapshot } from '../../api/types';
import FirstRun, { SETUP_LATER_KEY } from './FirstRun';

const mock = vi.hoisted(() => ({
  controller: {
    localRuntime: vi.fn(),
    liveProviderStatus: vi.fn(),
    testChosenModel: vi.fn(),
    refreshChoices: vi.fn(),
    modelCatalogPage: vi.fn(),
    refreshLiveProvider: vi.fn(),
    liveProviderRefresh: vi.fn(),
  },
}));

vi.mock('../../runtime', async () => {
  const { createContext } = await import('react');
  return {
    RuntimeContext: createContext(null),
    useRuntime: () => ({ controller: mock.controller }),
  };
});

const snapshot: OnboardingSnapshot = {
  schema_version: 1,
  revision: 'a'.repeat(64),
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
  steps: [],
  intents: [],
};

const runtime = (
  state: 'running' | 'installed' | 'not_installed',
  models: string[] = [],
) => ({
  schema_version: 1 as const,
  state,
  platform: 'windows' as const,
  download_url: 'https://ollama.com/download',
  models: models.map((name) => ({
    model_ref: `model:ollama:${name}`,
    name,
    agent_ready: name !== 'tiny-chat',
  })),
});

function Location() {
  const location = useLocation();
  return <output aria-label="Current location">{location.pathname}</output>;
}

function show(extra: Partial<OnboardingSnapshot> = {}) {
  const actions = {
    choose: vi.fn(async () => ({ ...snapshot, needs_model: false })),
    finish: vi.fn(async () => ({
      ...snapshot,
      needs_model: false,
      setup_complete: true,
    })),
  };
  render(
    <MemoryRouter initialEntries={['/setup']}>
      <FirstRun snapshot={{ ...snapshot, ...extra }} actions={actions} />
      <Location />
    </MemoryRouter>,
  );
  return actions;
}

const location = () =>
  screen.getByLabelText('Current location').textContent ?? '';

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  for (const fn of Object.values(mock.controller)) fn.mockReset();
  mock.controller.localRuntime.mockResolvedValue(runtime('not_installed'));
  mock.controller.liveProviderStatus.mockResolvedValue({
    schema_version: 1,
    providers: [],
  });
  mock.controller.testChosenModel.mockResolvedValue({
    schema_version: 1,
    ok: true,
    detail: 'qwen3.8:27b answered.',
    elapsed_ms: 900,
  });
  mock.controller.refreshChoices.mockResolvedValue(undefined);
  sessionStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
});

it('asks one question and detects Ollama without a refresh button', async () => {
  show();
  expect(
    screen.getByRole('heading', { name: 'How should Row-Bot think?' }),
  ).toBeVisible();
  const choices = screen.getByRole('group', {
    name: 'How should Row-Bot think?',
  });
  expect(
    within(choices)
      .getAllByRole('button')
      .map((button) => button.querySelector('strong')?.textContent),
  ).toEqual(['On this computer', 'With my subscription', 'With an API key']);
  expect(
    await within(choices).findByText("Ollama isn't installed yet"),
  ).toBeVisible();
  fireEvent.click(
    within(choices).getByRole('button', { name: /On this computer/ }),
  );
  expect(screen.getByRole('heading', { name: 'Install Ollama' })).toBeVisible();
  expect(screen.getByRole('link', { name: 'Download Ollama' })).toHaveAttribute(
    'href',
    'https://ollama.com/download',
  );
  expect(screen.queryByRole('button', { name: /refresh/i })).toBeNull();

  // Ollama appears: the next automatic read lists its models.
  mock.controller.localRuntime.mockResolvedValue(
    runtime('running', ['qwen3.8:27b', 'tiny-chat']),
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3100);
  });
  const list = await screen.findByRole('list', {
    name: 'Models on this computer',
  });
  expect(within(list).getAllByRole('button')).toHaveLength(2);
  expect(within(list).getByText('Chat only')).toBeVisible();
  expect(
    within(choices).getByText('Ollama is running · 2 models'),
  ).toBeVisible();
});

it('saves the pick as the default, tests it, then opens Home', async () => {
  mock.controller.localRuntime.mockResolvedValue(
    runtime('running', ['qwen3.8:27b']),
  );
  const actions = show();
  fireEvent.click(screen.getByRole('button', { name: /On this computer/ }));
  fireEvent.click(await screen.findByRole('button', { name: /qwen3\.8:27b/ }));
  await vi.waitFor(() => expect(location()).toBe('/'));
  expect(actions.choose).toHaveBeenCalledWith('model:ollama:qwen3.8:27b');
  expect(mock.controller.testChosenModel).toHaveBeenCalledTimes(1);
  expect(actions.finish).toHaveBeenCalledTimes(1);
  expect(mock.controller.refreshChoices).toHaveBeenCalled();
});

it('keeps the person in Setup with the reason when the quick test fails', async () => {
  mock.controller.localRuntime.mockResolvedValue(
    runtime('running', ['qwen3.8:27b']),
  );
  mock.controller.testChosenModel.mockResolvedValueOnce({
    schema_version: 1,
    ok: false,
    detail: "qwen3.8:27b via Ollama Local didn't answer: model not found",
    elapsed_ms: 20,
  });
  const actions = show();
  fireEvent.click(screen.getByRole('button', { name: /On this computer/ }));
  fireEvent.click(await screen.findByRole('button', { name: /qwen3\.8:27b/ }));
  expect(
    await screen.findByRole('heading', { name: 'That model didn’t answer' }),
  ).toBeVisible();
  expect(screen.getByRole('alert')).toHaveTextContent('model not found');
  expect(actions.finish).not.toHaveBeenCalled();
  expect(location()).toBe('/setup');
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  await vi.waitFor(() => expect(location()).toBe('/'));
  expect(actions.finish).toHaveBeenCalledTimes(1);
});

it('offers an import only when another assistant was found', async () => {
  mock.controller.localRuntime.mockResolvedValue(
    runtime('running', ['qwen3.8:27b']),
  );
  show({ import_sources: [{ id: 'hermes', label: 'Hermes Agent' }] });
  fireEvent.click(screen.getByRole('button', { name: /On this computer/ }));
  fireEvent.click(await screen.findByRole('button', { name: /qwen3\.8:27b/ }));
  expect(
    await screen.findByRole('heading', {
      name: 'Bring your Hermes Agent data along?',
    }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Not now' }));
  expect(location()).toBe('/');
});

it('explains a stopped Ollama and keeps watching', async () => {
  mock.controller.localRuntime.mockResolvedValue(runtime('installed'));
  show();
  fireEvent.click(screen.getByRole('button', { name: /On this computer/ }));
  expect(
    await screen.findByRole('heading', { name: 'Start Ollama' }),
  ).toBeVisible();
  expect(screen.getByText(/Open Ollama from the Start menu/)).toBeVisible();
  expect(screen.getByText(/notices by itself/)).toBeVisible();
});

it('lists recommended key providers first, the rest under More providers', async () => {
  show();
  fireEvent.click(screen.getByRole('button', { name: /With an API key/ }));
  const providers = screen.getByRole('list', { name: 'Providers' });
  expect(
    within(providers)
      .getAllByRole('button')
      .map((button) => button.querySelector('span')?.textContent),
  ).toEqual(['OpenAI', 'Anthropic', 'Google Gemini', 'OpenRouter']);
  expect(within(providers).getAllByText('Pay per use')).toHaveLength(4);
  expect(screen.getByText('More providers')).toBeVisible();
});

it('never traps the person: Set up later and the custom endpoint link', () => {
  show();
  expect(
    screen.getByRole('link', { name: 'Other (custom endpoint)' }),
  ).toHaveAttribute('href', '/settings/providers?add=custom-endpoint');
  fireEvent.click(screen.getByRole('button', { name: 'Set up later' }));
  expect(sessionStorage.getItem(SETUP_LATER_KEY)).toBe('1');
  expect(location()).toBe('/');
});

it("lists a connected provider's usable chat models from the assessed catalog", async () => {
  mock.controller.liveProviderStatus.mockResolvedValue({
    schema_version: 1,
    providers: [
      {
        provider_id: 'anthropic',
        configured: true,
        group: 'api',
        billing: 'pay_per_use',
      },
    ],
  });
  mock.controller.refreshLiveProvider.mockResolvedValue({ running: true });
  mock.controller.liveProviderRefresh.mockResolvedValue({ running: false });
  const row = (model_id: string, extra = {}) => ({
    provider_id: 'anthropic',
    model_id,
    selection_ref: `model:anthropic:${model_id}`,
    display_name: model_id,
    categories: ['chat'],
    configured: true,
    runtime_ready: true,
    installed: true,
    runtime_mode: 'agent',
    ...extra,
  });
  mock.controller.modelCatalogPage.mockResolvedValue({
    items: [
      row('claude-usable'),
      row('claude-not-ready', { runtime_ready: false }),
    ],
  });
  const actions = show();
  fireEvent.click(screen.getByRole('button', { name: /With an API key/ }));
  fireEvent.click(await screen.findByRole('button', { name: /^Anthropic/ }));
  const list = await screen.findByRole('list', { name: 'Anthropic models' });
  expect(within(list).getAllByRole('button')).toHaveLength(1);
  expect(mock.controller.modelCatalogPage).toHaveBeenCalledWith(
    'chat',
    'anthropic',
    '',
    undefined,
  );
  fireEvent.click(within(list).getByRole('button', { name: /claude-usable/ }));
  await vi.waitFor(() =>
    expect(actions.choose).toHaveBeenCalledWith(
      'model:anthropic:claude-usable',
    ),
  );
});
