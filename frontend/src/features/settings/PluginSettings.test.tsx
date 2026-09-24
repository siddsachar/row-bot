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
      plugin_id: 'cached-plugin',
      name: 'Cached Plugin',
      version: '2.0.0',
      description: 'A saved marketplace entry.',
      source: 'marketplace',
      installed: false,
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
    load: vi.fn().mockImplementation(async ({ source }) => {
      const items =
        source === 'installed'
          ? page.items.filter((item) => item.installed)
          : source === 'marketplace'
            ? page.items.filter((item) => !item.installed)
            : page.items;
      return { ...page, items, total: items.length };
    }),
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

async function manage() {
  await screen.findByText('1 matching plugins.');
  fireEvent.click(screen.getByRole('button', { name: 'Manage Sample Plugin' }));
  await screen.findByRole('heading', { name: 'Sample Plugin', level: 3 });
}

it('starts with installed local plugins and keeps the marketplace explicitly passive', async () => {
  const props = options();
  render(<PluginSettings {...props} />);
  await screen.findByText('1 matching plugins.');
  expect(screen.getByLabelText('Plugin source')).toHaveValue('installed');
  expect(screen.getByLabelText('Plugin source')).not.toBeVisible();
  expect(screen.queryByText('Cached Plugin')).not.toBeInTheDocument();
  expect(screen.getByText('1 tools')).toBeVisible();
  expect(screen.getByText('1 skills')).toBeVisible();
  expect(screen.getByText('1 loaded / 0 failed')).toBeVisible();
  expect(props.load).toHaveBeenCalledWith(
    { query: '', source: 'installed' },
    expect.any(AbortSignal),
  );

  fireEvent.click(screen.getByText('Search and filter plugins'));
  fireEvent.change(screen.getByLabelText('Plugin source'), {
    target: { value: 'marketplace' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  expect(await screen.findByText('Cached Plugin')).toBeVisible();
  expect(screen.getByText(/Install unavailable/)).toBeVisible();
  expect(props.load).toHaveBeenLastCalledWith(
    { query: '', source: 'marketplace', cursor: undefined },
    expect.any(AbortSignal),
  );
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
});

it('runs the saved plugin self-test in one click before enablement', async () => {
  const props = options();
  render(<PluginSettings {...props} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Test Sample Plugin' }),
  );
  await waitFor(() => expect(props.execute).toHaveBeenCalledTimes(1));
  expect(props.review.mock.calls[0][0]).toBe('plugin.test');
  expect(props.execute.mock.calls[0][0].type).toBe('plugin.test');
});

it('prioritizes owner-style marketplace and reload actions above closed filters', async () => {
  const props = options();
  render(<PluginSettings {...props} />);
  await screen.findByText('1 matching plugins.');
  expect(
    screen.getByRole('button', { name: 'Browse saved marketplace' }),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Reload plugins' })).toBeVisible();
  expect(screen.getByLabelText('Search plugins')).not.toBeVisible();
  fireEvent.click(
    screen.getByRole('button', { name: 'Browse saved marketplace' }),
  );
  expect(await screen.findByText('Cached Plugin')).toBeVisible();
  expect(props.load).toHaveBeenLastCalledWith(
    { query: '', source: 'marketplace', cursor: undefined },
    expect.any(AbortSignal),
  );
});

it('keeps path-like settings and saved secrets write-only', async () => {
  const props = options();
  render(<PluginSettings {...props} />);
  await manage();
  expect(screen.getByLabelText(/Region/)).toHaveValue('');
  expect(screen.getByLabelText(/Workspace/)).toHaveValue('');
  expect(screen.getByLabelText(/Token/)).toHaveValue('');
  expect(screen.getByText(/saved value is never displayed/)).toBeVisible();
});

it('saves exact configuration with a write-only secret in one click', async () => {
  const props = options();
  render(<PluginSettings {...props} />);
  await manage();
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
  render(<PluginSettings {...props} />);
  await manage();
  fireEvent.click(screen.getByRole('switch', { name: 'Plugin enabled' }));
  await screen.findByText(/Plugin change completed/);
  expect(props.review.mock.calls[0][0]).toBe('plugin.enable');
  expect(props.execute).toHaveBeenCalledOnce();
});

it('exposes one-click enablement from the resting plugin row', async () => {
  const props = options();
  render(<PluginSettings {...props} />);
  await screen.findByText('1 matching plugins.');
  fireEvent.click(
    screen.getByRole('switch', { name: 'Sample Plugin enabled' }),
  );
  await screen.findByText(/Plugin change completed/);
  expect(props.open).toHaveBeenCalledWith(
    'sample-plugin',
    expect.any(AbortSignal),
  );
  expect(props.review.mock.calls[0][0]).toBe('plugin.enable');
  expect(props.execute).toHaveBeenCalledOnce();
});

it('refreshes the enabled switch after the saved command completes', async () => {
  const props = options();
  let enabled = false;
  props.load.mockImplementation(async () => ({
    ...page,
    items: [
      {
        ...page.items[0],
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
      },
    ],
    total: 1,
  }));
  props.execute.mockImplementation(async (command) => {
    enabled = true;
    return {
      command_id: command.command_id,
      status: 'completed',
      plugin: { plugin_id: 'sample-plugin', action: command.type, enabled },
    };
  });
  render(<PluginSettings {...props} />);
  const toggle = await screen.findByRole('switch', {
    name: 'Sample Plugin enabled',
  });
  expect(toggle).not.toBeChecked();
  fireEvent.click(toggle);
  await waitFor(() =>
    expect(
      screen.getByRole('switch', { name: 'Sample Plugin enabled' }),
    ).toBeChecked(),
  );
  expect(props.load).toHaveBeenCalledTimes(2);
  expect(screen.getByText('Plugin change completed.')).toBeVisible();
});

it('keeps a newly installed marketplace plugin visible for its next action', async () => {
  sessionStorage.clear();
  const props = options();
  let installed = false;
  props.load.mockImplementation(async ({ source }) => {
    const item = {
      ...page.items[1],
      installed,
      source: installed ? ('installed' as const) : ('marketplace' as const),
    };
    return {
      ...page,
      items: source === 'all' || source === item.source ? [item] : [],
      total: source === 'all' || source === item.source ? 1 : 0,
    };
  });
  const lifecycle = {
    review: vi.fn().mockResolvedValue({ revision: 'd'.repeat(64) }),
    execute: vi.fn().mockImplementation(async (command) => {
      installed = true;
      return {
        command_id: command.command_id,
        status: 'completed',
        action: 'install',
        plugin_id: 'cached-plugin',
        message: 'Installed cached-plugin and kept it disabled.',
      };
    }),
    receipt: vi.fn(),
  };
  render(<PluginSettings {...props} lifecycle={lifecycle} />);
  await screen.findByText('0 matching plugins.');
  fireEvent.click(
    screen.getByRole('button', { name: 'Browse saved marketplace' }),
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Install' }));
  await waitFor(() =>
    expect(props.load).toHaveBeenLastCalledWith(
      { query: '', source: 'all', cursor: undefined },
      expect.any(AbortSignal),
    ),
  );
  expect(
    await screen.findByRole('button', { name: 'Uninstall' }),
  ).toBeVisible();
});

it('retains one uncertain original across remount and never creates a second command', async () => {
  const props = options();
  props.execute.mockRejectedValueOnce(Error('response lost'));
  const rendered = render(<PluginSettings {...props} />);
  await manage();
  fireEvent.click(screen.getByRole('switch', { name: 'Plugin enabled' }));
  await screen.findByText(/original plugin change is unconfirmed/);
  const original = props.execute.mock.calls[0];
  rendered.unmount();
  render(<PluginSettings {...props} />);
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
  render(<PluginSettings {...props} />);
  await manage();
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
  render(<PluginSettings {...props} />);
  await screen.findByText(/Saved plugin information is unavailable/);
  expect(screen.queryByText('2 matching plugins.')).not.toBeInTheDocument();
  await waitFor(() => expect(props.open).not.toHaveBeenCalled());
});
