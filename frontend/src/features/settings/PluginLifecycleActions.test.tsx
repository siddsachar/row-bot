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

it('installs on one click after visible disclosure and keeps the package disabled', async () => {
  const review = vi.fn().mockResolvedValue({ revision: 'a'.repeat(64) });
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
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  const review = vi.fn();
  const execute = vi.fn();
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
  expect(confirm).toHaveBeenCalledOnce();
  expect(review).not.toHaveBeenCalled();
  expect(execute).not.toHaveBeenCalled();
  confirm.mockRestore();
});
