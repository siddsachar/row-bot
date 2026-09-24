import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type {
  GitHubAccessCommand,
  GitHubAccessSnapshot,
} from '../../api/types';
import { GitHubAccessControls } from './GitHubAccessControls';

const snapshot: GitHubAccessSnapshot = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  state: 'configured_unchecked',
  credential_source: 'keyring',
  connected: false,
  anonymous_ok: false,
  cli_installed: true,
  cli_authenticated: false,
  remaining: null,
  retry_after_seconds: null,
};

function owner() {
  const load = vi.fn(async () => snapshot);
  const send = vi.fn(async (command: GitHubAccessCommand) => ({
    schema_version: 1 as const,
    command_id: command.command_id,
    action: command.action,
    phase:
      command.action === 'cli_login'
        ? ('started' as const)
        : ('completed' as const),
    snapshot:
      command.action === 'check'
        ? { ...snapshot, connected: true, state: 'connected' as const }
        : snapshot,
  }));
  const receipt = vi.fn(async (commandId: string) => ({
    schema_version: 1 as const,
    command_id: commandId,
    action: 'cli_login' as const,
    phase: 'started' as const,
    snapshot,
  }));
  return { load, send, receipt };
}

beforeEach(() => sessionStorage.clear());

it('reads passive status and checks access on one click', async () => {
  const actions = owner();
  render(<GitHubAccessControls owner={actions} />);
  expect(await screen.findByText(/configured unchecked/)).toBeVisible();
  expect(actions.send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Check GitHub access' }));
  expect(await screen.findByText(/Connected · keyring/)).toBeVisible();
  expect(actions.send).toHaveBeenCalledWith(
    expect.objectContaining({
      action: 'check',
      expected_revision: snapshot.revision,
    }),
  );
});

it('starts CLI login only on click and does not claim connection', async () => {
  const actions = owner();
  render(<GitHubAccessControls owner={actions} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Connect GitHub CLI' }),
  );
  expect(
    await screen.findByText(/Complete GitHub CLI authentication/),
  ).toBeVisible();
  expect(actions.send).toHaveBeenCalledWith(
    expect.objectContaining({ action: 'cli_login' }),
  );
  expect(screen.queryByText(/Connected · keyring/)).toBeNull();
});

it('recovers a pending action without launching it again', async () => {
  const actions = owner();
  sessionStorage.setItem(
    'row-bot:github-access:pending:v1',
    JSON.stringify({
      command_id: '33333333-3333-4333-8333-333333333333',
      action: 'cli_login',
      expected_revision: snapshot.revision,
    }),
  );
  render(<GitHubAccessControls owner={actions} />);
  expect(
    await screen.findByText(/Complete GitHub CLI authentication/),
  ).toBeVisible();
  expect(actions.receipt).toHaveBeenCalledTimes(1);
  expect(actions.send).not.toHaveBeenCalled();
});

it('shows the local owner boundary without account actions', async () => {
  const actions = owner();
  actions.load.mockRejectedValueOnce({ code: 'action_denied' });
  render(<GitHubAccessControls owner={actions} />);
  expect(
    await screen.findByText(/available on the local owner device/),
  ).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Check GitHub access' }),
  ).toBeNull();
});
