import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { OnboardingSnapshot } from '../../api/types';
import { OnboardingCenter } from './Onboarding';

const mock = vi.hoisted(() => ({
  test: { resolve: (() => undefined) as (value: unknown) => void },
  controller: {
    localRuntime: vi.fn(async () => ({
      schema_version: 1,
      state: 'running',
      platform: 'windows',
      download_url: 'https://ollama.com/download',
      models: [{ model_ref: 'model:ollama:qwen3.8:27b', name: 'qwen3.8:27b' }],
    })),
    liveProviderStatus: vi.fn(async () => ({
      schema_version: 1,
      providers: [],
    })),
    testChosenModel: vi.fn(),
    refreshChoices: vi.fn(async () => undefined),
  },
}));

vi.mock('../../runtime', async () => {
  const { createContext } = await import('react');
  return {
    RuntimeContext: createContext(null),
    useRuntime: () => ({ controller: mock.controller }),
  };
});

const fresh: OnboardingSnapshot = {
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
  steps: [{ id: 'models', title: 'Models', description: 'Models.' }],
  intents: [],
};

function Location() {
  return (
    <output aria-label="Current location">{useLocation().pathname}</output>
  );
}

it('keeps the first run on screen while the chosen model is tested, then opens Home', async () => {
  mock.controller.testChosenModel.mockImplementation(
    () =>
      new Promise((resolve) => {
        mock.test.resolve = resolve;
      }),
  );
  const send = vi.fn(
    async (command: { command_id: string; action: string }) => ({
      schema_version: 1 as const,
      command_id: command.command_id,
      status: 'completed' as const,
      // Choosing saves the default, so the snapshot no longer needs a model.
      snapshot: {
        ...fresh,
        // Every setup command moves the revision (onboarding version 4).
        revision:
          command.action === 'finish_models' ? 'c'.repeat(64) : 'b'.repeat(64),
        needs_model: false,
        default_model: 'model:ollama:qwen3.8:27b',
        setup_complete:
          command.action === 'finish_models' || fresh.setup_complete,
      },
    }),
  );
  render(
    <MemoryRouter initialEntries={['/setup']}>
      <OnboardingCenter owner={{ load: vi.fn(async () => fresh), send }} />
      <Location />
    </MemoryRouter>,
  );
  fireEvent.click(
    await screen.findByRole('button', { name: /On this computer/ }),
  );
  fireEvent.click(await screen.findByRole('button', { name: /qwen3\.8:27b/ }));
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith(
      expect.objectContaining({
        action: 'choose_model',
        model_ref: 'model:ollama:qwen3.8:27b',
      }),
    ),
  );
  await waitFor(() =>
    expect(mock.controller.testChosenModel).toHaveBeenCalled(),
  );
  // Still the first run (not Setup Center) while the test runs.
  expect(
    screen.getByRole('heading', { name: 'Checking the model' }),
  ).toBeVisible();
  expect(screen.queryByRole('region', { name: 'Setup Center' })).toBeNull();
  mock.test.resolve({
    schema_version: 1,
    ok: true,
    detail: 'qwen3.8:27b answered.',
    elapsed_ms: 900,
  });
  await waitFor(() =>
    expect(screen.getByLabelText('Current location')).toHaveTextContent(/^\/$/),
  );
  // Finishing uses the revision the choice returned, not the first one.
  expect(send).toHaveBeenLastCalledWith(
    expect.objectContaining({
      action: 'finish_models',
      expected_revision: 'b'.repeat(64),
    }),
  );
});
