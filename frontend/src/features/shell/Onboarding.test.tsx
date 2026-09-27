import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import type { OnboardingSnapshot } from '../../api/types';
import { OnboardingCenter } from './Onboarding';
import { WorkspaceActionsContext } from './workspace-actions';

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

it('saves an intent tile directly and uses its receipt revision', async () => {
  const { send } = show();
  fireEvent.click(
    await screen.findByRole('checkbox', { name: 'Chat assistant' }),
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

it('orders recommended areas first with a status chip and one primary action', async () => {
  const resumed: OnboardingSnapshot = {
    ...snapshot,
    setup_complete: true,
    profile: ['designer'],
    completed_steps: ['models'],
    skipped_steps: ['voice'],
    steps: [
      ...snapshot.steps,
      { id: 'knowledge', title: 'Knowledge', description: 'Memory.' },
      { id: 'designer', title: 'Designer', description: 'Designs.' },
    ],
    intents: [
      ...snapshot.intents,
      { id: 'designer', label: 'Designer Studio' },
    ],
  };
  const send = vi.fn(async (command) => ({
    schema_version: 1 as const,
    command_id: command.command_id,
    status: 'completed' as const,
    snapshot: { ...resumed, completed_steps: ['models', 'knowledge'] },
  }));
  const newChat = vi.fn();
  render(
    <MemoryRouter>
      <WorkspaceActionsContext.Provider
        value={{ resetLayout: vi.fn(), newChat }}
      >
        <OnboardingCenter owner={{ load: vi.fn(async () => resumed), send }} />
      </WorkspaceActionsContext.Provider>
    </MemoryRouter>,
  );
  const list = await screen.findByRole('list');
  const titles = within(list)
    .getAllByRole('heading', { level: 3 })
    .map((heading) => heading.textContent);
  expect(titles).toEqual(['Designer', 'Knowledge', 'Models', 'Voice']);
  expect(
    screen.getByRole('progressbar', { name: '2 of 4 areas handled' }),
  ).toBeInTheDocument();
  const items = within(list).getAllByRole('listitem');
  expect(items.map((item) => item.getAttribute('data-status'))).toEqual([
    'recommended',
    'recommended',
    'done',
    'skipped',
  ]);
  expect(within(items[2]).getByText('Done')).toBeVisible();
  expect(
    within(items[1]).getByRole('link', { name: 'Open Knowledge' }),
  ).toHaveAttribute('href', '/settings/knowledge');
  fireEvent.click(
    within(items[0]).getByRole('button', { name: 'Start a design' }),
  );
  expect(newChat).toHaveBeenCalledWith('Create a design: ');
  expect(send).not.toHaveBeenCalled();
  const more = within(items[1]).getByRole('button', {
    name: 'More actions for Knowledge',
  });
  await act(async () => fireEvent.keyDown(more, { key: 'Enter' }));
  await act(async () =>
    fireEvent.click(
      screen.getByRole('menuitem', { name: 'Mark Knowledge done' }),
    ),
  );
  expect(send).toHaveBeenCalledWith(
    expect.objectContaining({ action: 'mark_done', step: 'knowledge' }),
  );
});
