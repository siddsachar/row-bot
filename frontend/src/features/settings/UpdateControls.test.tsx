import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type {
  UpdateCommand,
  UpdateInstallCommand,
  UpdateInstallStatus,
  UpdateReceipt,
  UpdateSnapshot,
} from '../../api/types';
import { UpdateControls } from './UpdateControls';

const snapshot: UpdateSnapshot = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  channel: 'stable',
  current_version: '1.0.0',
  last_check: null,
  last_success: null,
  skipped_versions: [],
  available: null,
  dev_install: false,
};

function show(
  load: (signal?: AbortSignal) => Promise<UpdateSnapshot> = vi.fn(
    async () => snapshot,
  ),
  send: (command: UpdateCommand) => Promise<UpdateReceipt> = vi.fn(
    async (command) => ({
      schema_version: 1 as const,
      command_id: command.command_id,
      status: 'completed' as const,
      snapshot: { ...snapshot, last_success: '2026-09-23T12:00:00Z' },
    }),
  ),
) {
  render(<UpdateControls owner={{ load, send }} />);
  return { load, send };
}

beforeEach(() => sessionStorage.clear());

it('loads cached status without checking and checks from one explicit click', async () => {
  const { load, send } = show();
  expect(await screen.findByText(/Current version: 1.0.0/)).toBeVisible();
  expect(load).toHaveBeenCalledTimes(1);
  expect(send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Check for updates' }));
  expect(
    await screen.findByText('No update is available on this channel.'),
  ).toBeVisible();
  expect(send).toHaveBeenCalledWith(
    expect.objectContaining({
      action: 'check',
      expected_revision: snapshot.revision,
    }),
  );
});

it('shows a verified release and skips it from one click', async () => {
  const release: UpdateSnapshot = {
    ...snapshot,
    available: {
      version: '2.0.0',
      channel: 'stable',
      published_at: '2026-09-23T12:00:00Z',
      notes: 'Synthetic changes',
      html_url: 'https://github.com/example/release',
      asset_size: 12,
      verified_manifest: true,
    },
  };
  const send = vi.fn(async (command) => ({
    schema_version: 1 as const,
    command_id: command.command_id,
    status: 'completed' as const,
    snapshot: { ...release, available: null, skipped_versions: ['2.0.0'] },
  }));
  show(
    vi.fn(async () => release),
    send,
  );
  expect(await screen.findByText('Version 2.0.0 is available')).toBeVisible();
  expect(screen.getByText('Synthetic changes')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Skip version 2.0.0' }));
  expect(await screen.findByText('Skipped: 2.0.0')).toBeVisible();
  expect(send).toHaveBeenCalledWith(
    expect.objectContaining({ action: 'skip', version: '2.0.0' }),
  );
});

it('reports a failed check without claiming the installed version is current', async () => {
  show(
    vi.fn(async () => snapshot),
    vi.fn(async (command) => ({
      schema_version: 1 as const,
      command_id: command.command_id,
      status: 'failed' as const,
      snapshot,
    })),
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Check for updates' }),
  );
  expect(
    await screen.findByText(/could not reach a verified release source/),
  ).toBeVisible();
  expect(
    screen.queryByText('No update is available on this channel.'),
  ).toBeNull();
});

it('keeps an uncertain action for explicit recovery after remount', async () => {
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
    await screen.findByRole('button', { name: 'Check for updates' }),
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Check update action' }),
  );
  expect(
    await screen.findByText('No update is available on this channel.'),
  ).toBeVisible();
  expect(send.mock.calls[0][0].command_id).toBe(
    send.mock.calls[1][0].command_id,
  );
});

it('shows development installs as unavailable without making a check', async () => {
  const { send } = show(
    vi.fn(async () => ({ ...snapshot, dev_install: true })),
  );
  expect(await screen.findByText(/Development checkout/)).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Check for updates' }),
  ).toBeNull();
  expect(send).not.toHaveBeenCalled();
});

it('starts a verified installed-app update from one click and shows the original progress', async () => {
  const available: UpdateSnapshot = {
    ...snapshot,
    available: {
      version: '2.0.0',
      channel: 'stable',
      published_at: '',
      notes: 'Fixture release',
      html_url: 'https://github.com/example/release',
      asset_size: 12,
      verified_manifest: true,
    },
  };
  const startInstall = vi.fn(
    async (command: UpdateInstallCommand): Promise<UpdateInstallStatus> => ({
      schema_version: 1,
      command_id: command.command_id,
      version: command.version,
      phase: 'downloading',
      downloaded: 3,
      total: 12,
      message: 'Downloading and verifying.',
    }),
  );
  const installStatus = vi.fn(
    async (commandId: string): Promise<UpdateInstallStatus> => ({
      schema_version: 1,
      command_id: commandId,
      version: '2.0.0',
      phase: 'handoff',
      downloaded: 12,
      total: 12,
      message: 'Installer started.',
    }),
  );
  render(
    <UpdateControls
      owner={{
        load: async () => available,
        send: vi.fn(),
        startInstall,
        installStatus,
        cancelInstall: vi.fn(),
      }}
    />,
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Install version 2.0.0' }),
  );
  expect(await screen.findByText('Installer started.')).toBeVisible();
  expect(startInstall).toHaveBeenCalledWith(
    expect.objectContaining({
      expected_revision: available.revision,
      version: '2.0.0',
    }),
  );
  expect(installStatus).toHaveBeenCalledWith(
    startInstall.mock.calls[0][0].command_id,
  );
  expect(screen.getByRole('progressbar')).toHaveAttribute('value', '12');
});

it('allows a running download to request cancellation without another install', async () => {
  const available: UpdateSnapshot = {
    ...snapshot,
    available: {
      version: '2.0.0',
      channel: 'stable',
      published_at: '',
      notes: '',
      html_url: '',
      asset_size: 12,
      verified_manifest: true,
    },
  };
  const status = (
    commandId: string,
    phase: UpdateInstallStatus['phase'],
  ): UpdateInstallStatus => ({
    schema_version: 1,
    command_id: commandId,
    version: '2.0.0',
    phase,
    downloaded: 1,
    total: 12,
    message: phase,
  });
  const startInstall = vi.fn(async (command: UpdateInstallCommand) =>
    status(command.command_id, 'downloading'),
  );
  const cancelInstall = vi.fn(async (commandId: string) =>
    status(commandId, 'cancel_requested'),
  );
  render(
    <UpdateControls
      owner={{
        load: async () => available,
        send: vi.fn(),
        startInstall,
        installStatus: async (commandId) => status(commandId, 'downloading'),
        cancelInstall,
      }}
    />,
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Install version 2.0.0' }),
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Cancel download' }),
  );
  expect(cancelInstall).toHaveBeenCalledWith(
    startInstall.mock.calls[0][0].command_id,
  );
  expect(startInstall).toHaveBeenCalledTimes(1);
});

it('recovers an interrupted installation by reading its original status', async () => {
  const command: UpdateInstallCommand = {
    command_id: crypto.randomUUID(),
    expected_revision: snapshot.revision,
    version: '2.0.0',
  };
  sessionStorage.setItem('row-bot:updates:install:v1', JSON.stringify(command));
  const startInstall = vi.fn();
  const installStatus = vi.fn(
    async (commandId: string): Promise<UpdateInstallStatus> => ({
      schema_version: 1,
      command_id: commandId,
      version: '2.0.0',
      phase: 'handoff',
      downloaded: 12,
      total: 12,
      message: 'Installer started.',
    }),
  );
  render(
    <UpdateControls
      owner={{
        load: async () => snapshot,
        send: vi.fn(),
        startInstall,
        installStatus,
      }}
    />,
  );
  expect(await screen.findByText('Installer started.')).toBeVisible();
  expect(installStatus).toHaveBeenCalledWith(command.command_id);
  expect(startInstall).not.toHaveBeenCalled();
});

it('clears a known stale install rejection so the release can be refreshed', async () => {
  const available: UpdateSnapshot = {
    ...snapshot,
    available: {
      version: '2.0.0',
      channel: 'stable',
      published_at: '',
      notes: '',
      html_url: '',
      asset_size: 12,
      verified_manifest: true,
    },
  };
  render(
    <UpdateControls
      owner={{
        load: async () => available,
        send: vi.fn(),
        startInstall: vi
          .fn()
          .mockRejectedValue({ code: 'update_changed', status: 409 }),
        installStatus: vi.fn(),
      }}
    />,
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Install version 2.0.0' }),
  );
  expect(
    await screen.findByText(
      'Update state changed. Refresh it before choosing an action.',
    ),
  ).toBeVisible();
  expect(sessionStorage.getItem('row-bot:updates:install:v1')).toBeNull();
  expect(
    screen.getByRole('button', { name: 'Install version 2.0.0' }),
  ).toBeEnabled();
});
