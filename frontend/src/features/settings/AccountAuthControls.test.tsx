import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type {
  AccountAuthCommand,
  AccountAuthReceipt,
  AccountAuthSnapshot,
} from '../../api/types';
import { AccountAuthControls } from './AccountAuthControls';

const google: AccountAuthSnapshot = {
  schema_version: 1,
  account: 'google',
  revision: 'a'.repeat(64),
  configured: true,
  state: 'not_authenticated',
  token_files: 0,
};

function owner(snapshot: AccountAuthSnapshot = google) {
  const load = vi.fn(async () => snapshot);
  const send = vi.fn(
    async (
      _account: 'google' | 'x',
      command: AccountAuthCommand,
    ): Promise<AccountAuthReceipt> => ({
      schema_version: 1,
      command_id: command.command_id,
      account: command.account,
      action: command.action,
      phase: command.action === 'start' ? 'running' : 'completed',
      message: 'Synthetic account result',
      snapshot,
    }),
  );
  const receipt = vi.fn(
    async (
      _account: 'google' | 'x',
      commandId: string,
    ): Promise<AccountAuthReceipt> => ({
      schema_version: 1,
      command_id: commandId,
      account: snapshot.account,
      action: 'start',
      phase: 'completed',
      message: 'Synthetic sign-in complete',
      snapshot: {
        ...snapshot,
        state: 'saved_unchecked',
        token_files: snapshot.account === 'google' ? 2 : 1,
      },
    }),
  );
  const cancel = vi.fn(
    async (
      _account: 'google' | 'x',
      commandId: string,
    ): Promise<AccountAuthReceipt> => ({
      schema_version: 1,
      command_id: commandId,
      account: snapshot.account,
      action: 'start',
      phase: 'cancel_requested',
      message: 'Cancellation requested',
      snapshot,
    }),
  );
  return { load, send, receipt, cancel };
}

beforeEach(() => sessionStorage.clear());

it('reads state passively and imports a chosen Google client file without rendering secrets', async () => {
  const actions = owner();
  render(<AccountAuthControls account="google" owner={actions} />);
  expect(await screen.findByText(/not authenticated/)).toBeVisible();
  expect(actions.send).not.toHaveBeenCalled();
  const file = new File(
    ['{"installed":{"client_secret":"private-fixture"}}'],
    'client.json',
    { type: 'application/json' },
  );
  fireEvent.change(screen.getByLabelText('Google OAuth client JSON'), {
    target: { files: [file] },
  });
  await waitFor(() =>
    expect(actions.send).toHaveBeenCalledWith(
      'google',
      expect.objectContaining({
        action: 'import_credentials',
        credentials_json: expect.stringContaining('private-fixture'),
      }),
    ),
  );
  expect(screen.queryByText(/private-fixture/)).toBeNull();
});

it('starts browser authentication on click and recovers its original receipt', async () => {
  const actions = owner();
  render(<AccountAuthControls account="google" owner={actions} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Authenticate Google' }),
  );
  expect(actions.send).toHaveBeenCalledWith(
    'google',
    expect.objectContaining({ action: 'start' }),
  );
  expect(await screen.findByText('Synthetic sign-in complete')).toBeVisible();
  expect(actions.receipt).toHaveBeenCalledTimes(1);
  expect(
    sessionStorage.getItem('row-bot:account-auth:google:pending:v1'),
  ).toBeNull();
});

it('confirms local disconnect only for the destructive token removal', async () => {
  const actions = owner({
    ...google,
    state: 'saved_unchecked',
    token_files: 2,
  });
  render(<AccountAuthControls account="google" owner={actions} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Disconnect Google locally' }),
  );
  expect(
    screen.getByRole('alertdialog', {
      name: 'Confirm local account disconnect',
    }),
  ).toBeVisible();
  expect(actions.send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Remove local tokens' }));
  await waitFor(() =>
    expect(actions.send).toHaveBeenCalledWith(
      'google',
      expect.objectContaining({ action: 'disconnect', confirmed: true }),
    ),
  );
});

it('shows an X local-owner boundary without exposing sign-in actions remotely', async () => {
  const actions = owner({ ...google, account: 'x' });
  actions.load.mockRejectedValueOnce({ code: 'action_denied' });
  render(<AccountAuthControls account="x" owner={actions} />);
  expect(
    await screen.findByText(/available on the local owner device/),
  ).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Authenticate X' })).toBeNull();
});

it('recovers a retained OAuth job without starting another one', async () => {
  const actions = owner();
  sessionStorage.setItem(
    'row-bot:account-auth:google:pending:v1',
    '33333333-3333-4333-8333-333333333333',
  );
  render(<AccountAuthControls account="google" owner={actions} />);
  expect(await screen.findByText('Synthetic sign-in complete')).toBeVisible();
  expect(actions.receipt).toHaveBeenCalledWith(
    'google',
    '33333333-3333-4333-8333-333333333333',
  );
  expect(actions.send).not.toHaveBeenCalled();
});
