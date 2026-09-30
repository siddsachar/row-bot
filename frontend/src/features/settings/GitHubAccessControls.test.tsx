import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  GitHubAccessCommand,
  GitHubAccessSnapshot,
  SettingsSnapshot,
} from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import {
  AccountsSnapshotPanel,
  SettingsDraftOwner,
  type SettingsMutationIO,
} from './SettingsSnapshotPanels';

type Accounts = SettingsSnapshot['accounts'];
const item = (patch: Partial<Accounts['github']>): Accounts['github'] => ({
  account_id: 'github',
  enabled: null,
  configured: false,
  authentication_state: 'not_configured',
  credential: null,
  operations: [],
  read_operations: [],
  post_operations: [],
  engage_operations: [],
  ...patch,
});
function accounts(github: Partial<Accounts['github']>): Accounts {
  return {
    availability: 'available',
    github: item(github),
    gmail: item({ account_id: 'gmail' }),
    calendar: item({ account_id: 'calendar' }),
    x: item({ account_id: 'x' }),
  };
}
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

function page(
  github: Partial<Accounts['github']>,
  access: Partial<GitHubAccessSnapshot> = {},
  prepare: (controller: ReturnType<typeof fake>) => void = () => undefined,
) {
  const controller = fake({ ...snapshot, ...access });
  prepare(controller);
  const mutation: SettingsMutationIO = {
    revision: 'settings-a',
    page: 'accounts',
    review: vi.fn(),
    execute: vi.fn(),
    receipt: vi.fn(),
    drafts: new SettingsDraftOwner(),
    onSnapshot: vi.fn(),
    refreshSnapshot: vi.fn(() => new Promise<SettingsSnapshot>(() => {})),
  };
  render(
    <RuntimeContext.Provider
      value={{
        controller: controller as unknown as ClientController,
        platform: {} as ClientPlatform,
      }}
    >
      <MemoryRouter>
        <OverlayProvider>
          <AccountsSnapshotPanel
            snapshot={accounts(github)}
            mutation={mutation}
            showActions
          />
        </OverlayProvider>
      </MemoryRouter>
    </RuntimeContext.Provider>,
  );
  return { controller, mutation };
}
function fake(current: GitHubAccessSnapshot) {
  return {
    githubAccess: vi.fn(async () => current),
    githubAccessCommand: vi.fn(async (command: GitHubAccessCommand) => ({
      schema_version: 1 as const,
      command_id: command.command_id,
      action: command.action,
      phase:
        command.action === 'cli_login' || command.action === 'cli_refresh'
          ? ('started' as const)
          : ('completed' as const),
      snapshot:
        command.action === 'check'
          ? { ...current, connected: true, state: 'connected' as const }
          : current,
    })),
    githubAccessReceipt: vi.fn(async (commandId: string) => ({
      schema_version: 1 as const,
      command_id: commandId,
      action: 'cli_login' as const,
      phase: 'started' as const,
      snapshot: current,
    })),
    // Google and X read their saved state only.
    accountAuth: vi.fn(() => new Promise(() => {})),
  };
}
function githubRow() {
  return screen
    .getByText('GitHub', { selector: '.settings-row-label' })
    .closest('.settings-row') as HTMLElement;
}

beforeEach(() => sessionStorage.clear());

it('checks a saved token on one click and reloads the page', async () => {
  const { controller, mutation } = page({
    configured: true,
    authentication_state: 'configured_unchecked',
    credential: { configured: true, source: 'keyring', fingerprint: '' },
  });
  expect(within(githubRow()).getByText('Not checked yet')).toBeVisible();
  expect(
    within(githubRow()).getByText('With a token in your keychain'),
  ).toBeVisible();
  expect(controller.githubAccessCommand).not.toHaveBeenCalled();
  fireEvent.click(
    await within(githubRow()).findByRole('button', { name: 'Check GitHub' }),
  );
  expect(await screen.findByText('GitHub access checked.')).toBeVisible();
  expect(controller.githubAccessCommand).toHaveBeenCalledWith(
    expect.objectContaining({
      action: 'check',
      expected_revision: snapshot.revision,
    }),
  );
  expect(mutation.refreshSnapshot).toHaveBeenCalled();
});

it('starts the GitHub CLI sign-in from the guide only on click', async () => {
  const { controller } = page({});
  expect(within(githubRow()).getByText('Not connected')).toBeVisible();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Sign in with the GitHub CLI' }),
  );
  expect(await screen.findByText(/Finish signing in to GitHub/)).toBeVisible();
  expect(controller.githubAccessCommand).toHaveBeenCalledWith(
    expect.objectContaining({ action: 'cli_login' }),
  );
  expect(within(githubRow()).getByText('Not connected')).toBeVisible();
});

it('keeps the token field in the folded guide when connected through the CLI', async () => {
  page(
    { configured: true, authentication_state: 'connected' },
    { credential_source: 'github_cli', connected: true, state: 'connected' },
  );
  expect(
    await within(githubRow()).findByText(
      'Through the GitHub CLI on this computer',
    ),
  ).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Add GitHub token' }),
  ).not.toBeVisible();
  fireEvent.click(screen.getByText('How to set up GitHub'));
  expect(
    screen.getByRole('button', { name: 'Add GitHub token' }),
  ).toBeVisible();
});

it('reconnects a GitHub CLI sign-in that stopped working', async () => {
  const { controller } = page(
    { configured: true, authentication_state: 'invalid' },
    { credential_source: 'github_cli', state: 'invalid_token' },
  );
  expect(within(githubRow()).getByText('Needs reconnecting')).toBeVisible();
  fireEvent.click(
    await within(githubRow()).findByRole('button', {
      name: 'Reconnect GitHub',
    }),
  );
  expect(controller.githubAccessCommand).toHaveBeenCalledWith(
    expect.objectContaining({ action: 'cli_refresh' }),
  );
});

it('recovers a pending action without launching it again', async () => {
  sessionStorage.setItem(
    'row-bot:github-access:pending:v1',
    JSON.stringify({
      command_id: '33333333-3333-4333-8333-333333333333',
      action: 'cli_login',
      expected_revision: snapshot.revision,
    }),
  );
  const { controller } = page({});
  expect(await screen.findByText(/Finish signing in to GitHub/)).toBeVisible();
  expect(controller.githubAccessReceipt).toHaveBeenCalledTimes(1);
  expect(controller.githubAccessCommand).not.toHaveBeenCalled();
});

it('shows the local owner boundary without account actions', async () => {
  page({ configured: true, authentication_state: 'connected' }, {}, (fake) =>
    fake.githubAccess.mockRejectedValueOnce({ code: 'action_denied' }),
  );
  expect(
    await screen.findByText(/available on the local owner device/),
  ).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Check GitHub' })).toBeNull();
});
