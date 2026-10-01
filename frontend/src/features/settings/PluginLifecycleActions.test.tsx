import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import PluginLifecycleActions, {
  type PluginLifecycleApi,
} from './PluginLifecycleActions';
import type { PluginCatalogItem } from './PluginSettings';

const plugin: PluginCatalogItem = {
  plugin_id: 'synthetic-plugin',
  name: 'Synthetic plugin',
  version: '1.0.0',
  description: 'A fake package',
  source: 'marketplace',
  installed: false,
  enabled: false,
  setup_complete: false,
  health: 'unknown',
  update_version: null,
  permissions: ['filesystem_read'],
  provides: {},
  manifest_revision: null,
  capabilities: {},
};

beforeEach(() => sessionStorage.clear());

it('shows what the server says about a plugin before installing it (B144)', async () => {
  const review = vi.fn().mockResolvedValue({
    action: 'install',
    plugin_id: 'synthetic-plugin',
    name: 'Synthetic plugin',
    version: '1.0.0',
    source: 'Local directory: synthetic-plugin',
    checksum: '',
    permissions: ['filesystem_read'],
    disclosures: [
      'The marketplace index lists no checksum for this local folder; it installs only if unchanged since this review.',
    ],
    revision: 'a'.repeat(64),
  });
  const execute = vi.fn().mockImplementation(async (command) => ({
    command_id: command.command_id,
    status: 'completed',
    message: 'Installed synthetic-plugin and kept it disabled.',
  }));
  const receipt = vi.fn();
  const onChanged = vi.fn();
  const api = { review, execute, receipt } as unknown as PluginLifecycleApi;
  render(
    <PluginLifecycleActions plugin={plugin} api={api} onChanged={onChanged} />,
  );
  expect(
    screen.getByText(/Third-party plugin code may contact external services/i),
  ).toBeTruthy();
  expect(execute).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Install' }));
  // The review is shown first; nothing is downloaded until it is confirmed.
  const dialog = await screen.findByRole('dialog', {
    name: /Install Synthetic plugin/,
  });
  expect(dialog).toHaveTextContent('lists no checksum for this local folder');
  expect(dialog).toHaveTextContent('Local directory: synthetic-plugin');
  expect(dialog).toHaveTextContent('Not pinned');
  expect(execute).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Install plugin' }));
  await waitFor(() => expect(execute).toHaveBeenCalledTimes(1));
  expect(execute.mock.calls[0][0]).toMatchObject({
    action: 'install',
    plugin_id: 'synthetic-plugin',
    revision: 'a'.repeat(64),
  });
  expect(await screen.findByText(/kept it disabled/)).toBeTruthy();
  expect(onChanged).toHaveBeenCalledOnce();
});

it('requires a confirmation for irreversible uninstall', async () => {
  const review = vi.fn(async () => ({
    action: 'remove',
    plugin_id: 'synthetic-plugin',
    name: 'Synthetic plugin',
    version: '1.0.0',
    source: 'installed local plugin',
    checksum: '',
    permissions: [],
    disclosures: [
      'Removal deletes plugin files, settings, and secret metadata. This cannot be undone.',
    ],
    revision: 'a'.repeat(64),
  }));
  const execute = vi.fn(async (command: { action: string }) => ({
    command_id: 'removed',
    status: 'completed',
    action: command.action,
    message: 'Plugin removed.',
  }));
  const api = {
    review,
    execute,
    receipt: vi.fn(),
  } as unknown as PluginLifecycleApi;
  render(
    <PluginLifecycleActions
      plugin={{ ...plugin, installed: true }}
      api={api}
      onChanged={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Uninstall' }));
  const dialog = await screen.findByRole('dialog', { name: /Uninstall/ });
  expect(dialog).toHaveTextContent('deletes its files');
  expect(dialog).toHaveTextContent('cannot be undone');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(execute).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Uninstall' }));
  fireEvent.click(
    await screen.findByRole('button', { name: 'Uninstall plugin' }),
  );
  await waitFor(() => expect(execute).toHaveBeenCalledOnce());
  expect(execute.mock.calls[0][0]).toMatchObject({ action: 'remove' });
});
