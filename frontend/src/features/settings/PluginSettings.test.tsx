import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import PluginSettings, {
  createPluginSettingsSession,
  type PluginCatalogPage,
  type PluginDetail,
  type PluginReceipt,
} from './PluginSettings';

const capabilities = {
  test: { available: true, code: null },
  install: { available: false, code: 'plugin_lifecycle_worker_unavailable' },
  update: { available: false, code: 'plugin_lifecycle_worker_unavailable' },
  remove: { available: false, code: 'plugin_lifecycle_worker_unavailable' },
  configure: { available: true, code: null },
  enable: { available: true, code: null },
  disable: { available: false, code: 'plugin_already_disabled' },
};
const page: PluginCatalogPage = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  availability: 'available',
  total: 2,
  next_cursor: null,
  items: [
    {
      plugin_id: 'sample-plugin',
      name: 'Sample Plugin',
      version: '1.0.0',
      description: 'An installed plugin.',
      source: 'installed',
      installed: true,
      enabled: false,
      setup_complete: true,
      health: 'passed',
      update_version: '1.1.0',
      permissions: ['network'],
      provides: { native_tools: 1, mcp_servers: 0, channels: 0, skills: 1 },
      manifest_revision: 'b'.repeat(64),
      capabilities,
    },
    {
      plugin_id: 'other-plugin',
      name: 'Other Plugin',
      version: '2.0.0',
      description: 'Another installed plugin.',
      source: 'installed',
      installed: true,
      enabled: false,
      setup_complete: false,
      health: 'unknown',
      update_version: null,
      permissions: [],
      provides: {},
      manifest_revision: null,
      capabilities,
    },
  ],
};
const detail: PluginDetail = {
  schema_version: 1,
  plugin_id: 'sample-plugin',
  revision: 'c'.repeat(64),
  name: 'Sample Plugin',
  version: '1.0.0',
  description: 'An installed plugin.',
  enabled: false,
  settings: [
    {
      name: 'region',
      label: 'Region',
      type: 'select',
      required: true,
      options: ['eu', 'us'],
      configured: true,
      value: null,
    },
    {
      name: 'workspace',
      label: 'Workspace',
      type: 'local_path',
      required: false,
      options: [],
      configured: true,
      value: null,
    },
  ],
  secrets: [
    {
      name: 'token',
      label: 'Token',
      type: 'secret',
      required: true,
      options: [],
      configured: true,
      value: null,
    },
  ],
  health: { status: 'passed', checks: [{ label: 'Setup', status: 'ok' }] },
  permissions: ['network'],
  capabilities,
};

function options() {
  return {
    session: createPluginSettingsSession(),
    load: vi.fn().mockResolvedValue(page),
    open: vi.fn().mockResolvedValue(detail),
    review: vi.fn().mockImplementation(async (action, payload) => ({
      schema_version: 1,
      plugin_id: payload.plugin_id,
      action,
      revision: payload.revision,
      action_digest: 'd'.repeat(64),
      changes: {},
      disclosures: ['Synthetic reviewed plugin effect.'],
    })),
    execute: vi.fn().mockImplementation(async (command) => ({
      command_id: command.command_id,
      status: 'completed',
      plugin: { plugin_id: command.payload.plugin_id, action: command.type },
    })),
  };
}

/** Apps › a plugin › Advanced settings: the editor scoped to that one plugin. */
function renderScoped(props: ReturnType<typeof options>) {
  return render(<PluginSettings {...props} integrationId="sample-plugin" />);
}

async function manage(props: ReturnType<typeof options>) {
  const rendered = renderScoped(props);
  await screen.findByRole('heading', { name: 'Sample Plugin', level: 3 });
  return rendered;
}

it('opens only the scoped plugin and changes nothing until asked', async () => {
  const props = options();
  await manage(props);
  expect(props.load).toHaveBeenCalledWith(
    { query: '', source: 'installed' },
    expect.any(AbortSignal),
  );
  expect(props.open).toHaveBeenCalledOnce();
  expect(props.open).toHaveBeenCalledWith(
    'sample-plugin',
    expect.any(AbortSignal),
  );
  expect(screen.queryByText('Other Plugin')).not.toBeInTheDocument();
  expect(
    screen.getByText(/Health: passed\. Permissions: Network/),
  ).toBeVisible();
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
});

it('runs the saved plugin self-test in one click before enablement', async () => {
  const props = options();
  await manage(props);
  fireEvent.click(screen.getByRole('button', { name: 'Run local test' }));
  await waitFor(() => expect(props.execute).toHaveBeenCalledTimes(1));
  expect(props.review.mock.calls[0][0]).toBe('plugin.test');
  expect(props.execute.mock.calls[0][0].type).toBe('plugin.test');
});

it('keeps path-like settings and saved secrets write-only', async () => {
  const props = options();
  await manage(props);
  expect(screen.getByLabelText(/Region/)).toHaveValue('');
  expect(screen.getByLabelText(/Workspace/)).toHaveValue('');
  expect(screen.getByLabelText(/Token/)).toHaveValue('');
  expect(screen.getByText(/saved value is never displayed/)).toBeVisible();
});

it('saves exact configuration with a write-only secret in one click', async () => {
  const props = options();
  await manage(props);
  fireEvent.change(screen.getByLabelText(/Region/), {
    target: { value: 'us' },
  });
  fireEvent.change(screen.getByLabelText(/Token/), {
    target: { value: 'private-token' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save configuration' }));
  await screen.findByText(/Plugin change completed/);
  expect(props.review).toHaveBeenCalledWith(
    'plugin.configure',
    {
      plugin_id: 'sample-plugin',
      revision: detail.revision,
      settings: { region: 'us' },
      secrets: { token: 'private-token' },
    },
    expect.any(AbortSignal),
  );
  expect(props.execute.mock.calls[0][0].payload.secrets).toEqual({
    token: 'private-token',
  });
});

it('enables a plugin with one click', async () => {
  const props = options();
  await manage(props);
  fireEvent.click(screen.getByRole('switch', { name: 'Plugin enabled' }));
  await screen.findByText(/Plugin change completed/);
  expect(props.review.mock.calls[0][0]).toBe('plugin.enable');
  expect(props.execute).toHaveBeenCalledOnce();
});

it('refreshes the enabled switch after the saved command completes', async () => {
  const props = options();
  let enabled = false;
  props.open.mockImplementation(async () => ({
    ...detail,
    enabled,
    capabilities: {
      ...capabilities,
      enable: {
        available: !enabled,
        code: enabled ? 'plugin_already_enabled' : null,
      },
      disable: {
        available: enabled,
        code: enabled ? null : 'plugin_already_disabled',
      },
    },
  }));
  props.execute.mockImplementation(async (command) => {
    enabled = true;
    return {
      command_id: command.command_id,
      status: 'completed',
      plugin: { plugin_id: 'sample-plugin', action: command.type, enabled },
    };
  });
  await manage(props);
  const toggle = screen.getByRole('switch', { name: 'Plugin enabled' });
  expect(toggle).not.toBeChecked();
  fireEvent.click(toggle);
  await waitFor(() =>
    expect(
      screen.getByRole('switch', { name: 'Plugin enabled' }),
    ).toBeChecked(),
  );
  expect(props.load).toHaveBeenCalledTimes(2);
  expect(props.open).toHaveBeenCalledTimes(2);
  expect(screen.getByText('Plugin change completed.')).toBeVisible();
});

it('retains one uncertain original across remount and never creates a second command', async () => {
  const props = options();
  props.execute.mockRejectedValueOnce(Error('response lost'));
  const rendered = await manage(props);
  fireEvent.click(screen.getByRole('switch', { name: 'Plugin enabled' }));
  await screen.findByText(/original plugin change is unconfirmed/);
  const original = props.execute.mock.calls[0];
  rendered.unmount();
  renderScoped(props);
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original plugin change' }),
  );
  await screen.findByText(/Plugin change completed/);
  expect(props.execute.mock.calls[1]).toEqual(original);
  expect(props.review).toHaveBeenCalledTimes(1);
});

it('tombstones pending secrets and commands when authentication is lost', async () => {
  const props = options();
  let resolve!: (value: PluginReceipt) => void;
  props.execute.mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  await manage(props);
  fireEvent.change(screen.getByLabelText(/Token/), {
    target: { value: 'private-token' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save configuration' }));
  await waitFor(() => expect(props.execute).toHaveBeenCalledOnce());
  act(() => props.session.dispose());
  await act(async () =>
    resolve({
      command_id: props.execute.mock.calls[0][0].command_id,
      status: 'partial',
    }),
  );
  expect(props.session.hasRetained()).toBe(false);
  expect(props.session.getSnapshot().selected).toBeNull();
  expect(props.session.getSnapshot().secrets).toEqual({});
  expect(screen.queryByText('private-token')).not.toBeInTheDocument();
});

it('rejects oversized pages and detail records from the client boundary', async () => {
  const props = options();
  props.load.mockResolvedValue({
    ...page,
    items: Array.from({ length: 51 }, () => page.items[0]),
  });
  const rendered = renderScoped(props);
  await screen.findByText(/Saved plugin information is unavailable/);
  await waitFor(() => expect(props.open).not.toHaveBeenCalled());
  rendered.unmount();

  const oversized = options();
  oversized.open.mockResolvedValue({
    ...detail,
    settings: Array.from({ length: 129 }, (_, index) => ({
      ...detail.settings[0],
      name: `field_${index}`,
    })),
  });
  renderScoped(oversized);
  await screen.findByText('Plugin details are unavailable.');
  expect(
    screen.queryByRole('heading', { name: 'Sample Plugin' }),
  ).not.toBeInTheDocument();
});

it('says an enabled plugin failed to load in its health line', async () => {
  const props = options();
  props.open.mockResolvedValue({
    ...detail,
    enabled: true,
    health: { status: 'load_failed', checks: [] },
  });
  await manage(props);
  expect(screen.getByText(/Health: load failed\./)).toBeVisible();
  expect(screen.queryByText(/Health: passed/)).not.toBeInTheDocument();
});
