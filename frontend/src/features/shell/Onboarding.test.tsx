import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import type { OnboardingSnapshot } from '../../api/types';
import { OnboardingCenter } from './Onboarding';

const snapshot: OnboardingSnapshot = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  setup_complete: false,
  starter_workflows_missing: 0,
  profile: [],
  completed_steps: [],
  skipped_steps: [],
  dismissed_home_card: false,
  steps: [
    { id: 'models', title: 'Models', description: 'Connect a model.' },
    { id: 'voice', title: 'Voice', description: 'Configure voice.' },
  ],
  intents: [{ id: 'chat', label: 'Chat assistant' }],
};

function show(
  load = vi.fn(async () => snapshot),
  send = vi.fn(async (command) => ({
    schema_version: 1 as const,
    command_id: command.command_id,
    status: 'completed' as const,
    snapshot: {
      ...snapshot,
      setup_complete: true,
      completed_steps: ['models'],
    },
  })),
) {
  render(
    <MemoryRouter>
      <OnboardingCenter owner={{ load, send }} />
    </MemoryRouter>,
  );
  return { load, send };
}

beforeEach(() => sessionStorage.clear());

it('loads progress passively and finishes selected model from one click', async () => {
  const { send } = show();
  expect(await screen.findByText('Connect your first model')).toBeVisible();
  expect(send).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Use selected model and continue' }),
  );
  expect(
    await screen.findByRole('region', { name: 'Setup checklist' }),
  ).toBeVisible();
  expect(send).toHaveBeenCalledWith(
    expect.objectContaining({
      action: 'finish_models',
      expected_revision: snapshot.revision,
    }),
  );
});

it('saves an intent switch directly and uses its receipt revision', async () => {
  const { send } = show();
  fireEvent.click(
    await screen.findByRole('switch', { name: 'Chat assistant' }),
  );
  expect(send).toHaveBeenCalledWith(
    expect.objectContaining({ action: 'save_profile', profile: ['chat'] }),
  );
});

it('adds starter workflows only from an explicit click in resumed setup', async () => {
  const resumed: OnboardingSnapshot = {
    ...snapshot,
    setup_complete: true,
    starter_workflows_missing: 5,
    completed_steps: ['models'],
    steps: [
      ...snapshot.steps,
      {
        id: 'workflows',
        title: 'Workflows',
        description: 'Starter workflows.',
      },
    ],
  };
  const send = vi.fn(async (command) => ({
    schema_version: 1 as const,
    command_id: command.command_id,
    status: 'completed' as const,
    snapshot: {
      ...resumed,
      starter_workflows_missing: 0,
      completed_steps: ['models', 'workflows'],
    },
  }));
  show(
    vi.fn(async () => resumed),
    send,
  );
  expect(await screen.findByText('Continue setup')).toBeVisible();
  expect(send).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Add missing starter workflows' }),
  );
  expect(send).toHaveBeenCalledWith(
    expect.objectContaining({
      action: 'add_starters',
      expected_revision: resumed.revision,
    }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole('button', { name: 'Add missing starter workflows' }),
    ).toBeNull(),
  );
  expect(
    screen.getByRole('link', { name: 'Import from Hermes or OpenClaw' }),
  ).toHaveAttribute('href', '/settings/system');
});

it('retains the original command for recovery after an uncertain response', async () => {
  const send = vi
    .fn()
    .mockRejectedValueOnce(new Error('unconfirmed'))
    .mockImplementationOnce(async (command) => ({
      schema_version: 1,
      command_id: command.command_id,
      status: 'completed',
      snapshot,
    }));
  show(
    vi.fn(async () => snapshot),
    send,
  );
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Use selected model and continue',
    }),
  );
  expect(
    await screen.findByRole('button', { name: 'Check setup action' }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Check setup action' }));
  expect(await screen.findByText('Setup choice saved.')).toBeVisible();
  expect(send.mock.calls[0][0].command_id).toBe(
    send.mock.calls[1][0].command_id,
  );
});

it('offers interrupted command recovery after remount without auto-executing', async () => {
  const command = {
    command_id: crypto.randomUUID(),
    expected_revision: snapshot.revision,
    action: 'save_profile',
    profile: ['chat'],
    step: '',
  };
  sessionStorage.setItem(
    'row-bot:onboarding:pending:v1',
    JSON.stringify(command),
  );
  const send = vi.fn(async () => ({
    schema_version: 1 as const,
    command_id: command.command_id,
    status: 'completed' as const,
    snapshot: { ...snapshot, profile: ['chat'] },
  }));
  show(
    vi.fn(async () => snapshot),
    send,
  );
  expect(
    await screen.findByRole('button', { name: 'Check setup action' }),
  ).toBeVisible();
  expect(send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Check setup action' }));
  expect(await screen.findByText('Setup choice saved.')).toBeVisible();
  expect(send).toHaveBeenCalledWith(command);
});

it('lets the user correct a rejected unready model choice', async () => {
  const send = vi
    .fn()
    .mockRejectedValue({ code: 'onboarding_model_required', status: 409 });
  show(
    vi.fn(async () => snapshot),
    send,
  );
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Use selected model and continue',
    }),
  );
  expect(
    await screen.findByText(
      'Choose an available model in Settings, then try this step again.',
    ),
  ).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Check setup action' }),
  ).toBeNull();
  expect(
    screen.getByRole('button', { name: 'Use selected model and continue' }),
  ).toBeEnabled();
});
