import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  AccountAuthCommand,
  AccountAuthReceipt,
  AccountAuthSnapshot,
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
type Item = Accounts['github'];
const item = (patch: Partial<Item>): Item => ({
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
/** GitHub connected, Google's sign-in expired, X not signed in yet. */
const accounts: Accounts = {
  availability: 'available',
  github: item({
    configured: true,
    authentication_state: 'connected',
    credential: { configured: false, source: '', fingerprint: '' },
  }),
  gmail: item({
    account_id: 'gmail',
    enabled: true,
    configured: true,
    authentication_state: 'expired',
    operations: ['search_gmail'],
  }),
  calendar: item({
    account_id: 'calendar',
    enabled: false,
    configured: true,
    authentication_state: 'connected',
    operations: ['search_events'],
  }),
  x: item({
    account_id: 'x',
    enabled: true,
    configured: true,
    authentication_state: 'not_authenticated',
    read_operations: ['x_search'],
    post_operations: [],
    engage_operations: [],
    callback_url: 'http://127.0.0.1:9/callback',
  }),
};
const github: GitHubAccessSnapshot = {
  schema_version: 1,
  revision: 'g'.repeat(64),
  state: 'connected',
  credential_source: 'github_cli',
  connected: true,
  anonymous_ok: true,
  cli_installed: true,
  cli_authenticated: true,
  remaining: 4000,
  retry_after_seconds: null,
};
const auth = (
  account: 'google' | 'x',
  patch: Partial<AccountAuthSnapshot> = {},
): AccountAuthSnapshot => ({
  schema_version: 1,
  account,
  revision: 'a'.repeat(64),
  configured: true,
  state: account === 'google' ? 'expired' : 'not_authenticated',
  token_files: account === 'google' ? 2 : 0,
  ...patch,
});

function page(
  snapshot: Accounts = accounts,
  prepare: (controller: ReturnType<typeof fakeController>) => void = () =>
    undefined,
) {
  const controller = fakeController();
  prepare(controller);
  return mount(snapshot, controller);
}
function fakeController() {
  return {
    accountAuth: vi.fn(async (account: 'google' | 'x') => auth(account)),
    accountAuthCommand: vi.fn(
      async (
        account: 'google' | 'x',
        command: AccountAuthCommand,
      ): Promise<AccountAuthReceipt> => ({
        schema_version: 1,
        command_id: command.command_id,
        account,
        action: command.action,
        phase: command.action === 'start' ? 'running' : 'completed',
        message: 'Synthetic account result',
        snapshot: auth(account),
      }),
    ),
    accountAuthReceipt: vi.fn(
      async (
        account: 'google' | 'x',
        commandId: string,
      ): Promise<AccountAuthReceipt> => ({
        schema_version: 1,
        command_id: commandId,
        account,
        action: 'start',
        phase: 'completed',
        message: 'Synthetic sign-in complete',
        snapshot: auth(account, { state: 'saved_unchecked', token_files: 1 }),
      }),
    ),
    cancelAccountAuth: vi.fn(),
    githubAccess: vi.fn(async () => github),
    githubAccessCommand: vi.fn(),
    githubAccessReceipt: vi.fn(),
  };
}
function mount(
  snapshot: Accounts,
  controller: ReturnType<typeof fakeController>,
) {
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
  const view = render(
    <RuntimeContext.Provider
      value={{
        controller: controller as unknown as ClientController,
        platform: {} as ClientPlatform,
      }}
    >
      <MemoryRouter>
        <OverlayProvider>
          <AccountsSnapshotPanel
            snapshot={snapshot}
            mutation={mutation}
            showActions
          />
        </OverlayProvider>
      </MemoryRouter>
    </RuntimeContext.Provider>,
  );
  return { controller, mutation, view };
}
/** An account's row: its name, one status and its actions. */
function row(name: string) {
  return screen
    .getByText(name, { selector: '.settings-row-label' })
    .closest('.settings-row') as HTMLElement;
}

beforeEach(() => sessionStorage.clear());

it('shows each account as one row with one status and labelled actions', async () => {
  const { controller } = page();
  // Google counts once, and needs reconnecting because Gmail's sign-in expired.
  expect(screen.getByText('1 connected')).toBeVisible();
  expect(screen.getByText('1 needs reconnecting')).toBeVisible();
  expect(screen.getByText('1 not connected')).toBeVisible();
  expect(within(row('GitHub')).getByText('Connected')).toBeVisible();
  expect(within(row('Google')).getByText('Needs reconnecting')).toBeVisible();
  expect(within(row('X')).getByText('Not connected')).toBeVisible();
  expect(
    await within(row('GitHub')).findByRole('button', { name: 'Check GitHub' }),
  ).toBeVisible();
  expect(
    await within(row('Google')).findByRole('button', {
      name: 'Reconnect Google',
    }),
  ).toBeVisible();
  expect(
    within(row('Google')).getByRole('button', { name: 'Disconnect Google' }),
  ).toBeVisible();
  // Not connected: no actions in the row; its guide shows the steps.
  expect(within(row('X')).queryByRole('button')).toBeNull();
  expect(screen.getByRole('region', { name: 'Connect X' })).toBeVisible();
  expect(
    await screen.findByRole('button', { name: 'Connect X' }),
  ).toBeEnabled();
  // Signed in: the guide is folded away.
  expect(
    screen.getByRole('region', { name: 'Connect Google' }),
  ).not.toBeVisible();
  expect(screen.getByText('How to set up Google')).toBeVisible();
  // Opening the page contacts nothing and refreshes no token.
  expect(controller.accountAuthCommand).not.toHaveBeenCalled();
  expect(controller.githubAccessCommand).not.toHaveBeenCalled();
});

it('labels Gmail and Calendar access and keeps their actions behind Choose', async () => {
  page();
  expect(screen.getByRole('switch', { name: 'Gmail access' })).toBeChecked();
  expect(
    screen.getByRole('switch', { name: 'Calendar access' }),
  ).not.toBeChecked();
  expect(screen.getByText('1 of 5 actions')).toBeVisible();
  expect(screen.getByText('1 of 7 actions')).toBeVisible();
  expect(screen.queryByRole('checkbox', { name: 'Search email' })).toBeNull();
  fireEvent.click(
    screen.getByRole('button', { name: 'Choose gmail access actions' }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Gmail access: actions' });
  expect(
    within(dialog).getByRole('checkbox', { name: 'Search email' }),
  ).toBeChecked();
  expect(
    within(dialog).getByRole('checkbox', { name: 'Send email' }),
  ).not.toBeChecked();
});

it('chooses Google’s sign-in file with a file picker and never shows its contents', async () => {
  const { controller, view } = page();
  const choose = await screen.findByRole('button', {
    name: 'Choose Google sign-in file',
  });
  const input =
    view.container.querySelector<HTMLInputElement>('input[type="file"]')!;
  const opened = vi.spyOn(input, 'click');
  fireEvent.click(choose);
  expect(opened).toHaveBeenCalledOnce();
  const file = new File(
    ['{"installed":{"client_secret":"private-fixture"}}'],
    'client.json',
    { type: 'application/json' },
  );
  fireEvent.change(input, { target: { files: [file] } });
  await waitFor(() =>
    expect(controller.accountAuthCommand).toHaveBeenCalledWith(
      'google',
      expect.objectContaining({
        action: 'import_credentials',
        credentials_json: expect.stringContaining('private-fixture'),
      }),
    ),
  );
  expect(screen.queryByText(/private-fixture/)).toBeNull();
});

it('signs in to X from its guide and recovers the original receipt, then reloads the page', async () => {
  const { controller, mutation } = page();
  fireEvent.click(await screen.findByRole('button', { name: 'Connect X' }));
  expect(controller.accountAuthCommand).toHaveBeenCalledWith(
    'x',
    expect.objectContaining({ action: 'start' }),
  );
  expect(await screen.findByText('Synthetic sign-in complete')).toBeVisible();
  expect(controller.accountAuthReceipt).toHaveBeenCalledTimes(1);
  expect(
    sessionStorage.getItem('row-bot:account-auth:x:pending:v1'),
  ).toBeNull();
  expect(mutation.refreshSnapshot).toHaveBeenCalled();
});

it('confirms a disconnect before removing the local sign-in', async () => {
  const { controller } = page();
  fireEvent.click(
    await within(row('Google')).findByRole('button', {
      name: 'Disconnect Google',
    }),
  );
  expect(
    screen.getByRole('alertdialog', {
      name: 'Confirm local account disconnect',
    }),
  ).toBeVisible();
  expect(controller.accountAuthCommand).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Remove local tokens' }));
  await waitFor(() =>
    expect(controller.accountAuthCommand).toHaveBeenCalledWith(
      'google',
      expect.objectContaining({ action: 'disconnect', confirmed: true }),
    ),
  );
});

it('reconnects an expired Google sign-in in the browser', async () => {
  const { controller } = page();
  fireEvent.click(
    await within(row('Google')).findByRole('button', {
      name: 'Reconnect Google',
    }),
  );
  expect(controller.accountAuthCommand).toHaveBeenCalledWith(
    'google',
    expect.objectContaining({ action: 'start' }),
  );
});

it('shows the local-owner boundary instead of sign-in actions', async () => {
  page(accounts, (controller) =>
    controller.accountAuth.mockRejectedValue({ code: 'action_denied' }),
  );
  expect(
    await screen.findAllByText(
      /sign-in is available on the local owner device/,
    ),
  ).toHaveLength(2);
  expect(screen.queryByRole('button', { name: 'Connect X' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Reconnect Google' })).toBeNull();
});

it('recovers a retained sign-in without starting another one', async () => {
  sessionStorage.setItem(
    'row-bot:account-auth:google:pending:v1',
    '33333333-3333-4333-8333-333333333333',
  );
  const { controller } = page();
  expect(await screen.findByText('Synthetic sign-in complete')).toBeVisible();
  expect(controller.accountAuthReceipt).toHaveBeenCalledWith(
    'google',
    '33333333-3333-4333-8333-333333333333',
  );
  expect(controller.accountAuthCommand).not.toHaveBeenCalled();
});
