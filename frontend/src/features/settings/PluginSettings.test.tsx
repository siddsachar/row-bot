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

async function manage() {
  await screen.findByText('2 matching plugins.');
  fireEvent.click(screen.getByRole('button', { name: 'Manage Sample Plugin' }));
  await screen.findByRole('heading', { name: 'Sample Plugin', level: 3 });
}

it('reads installed and cached marketplace metadata without starting an action', async () => {
  const props = options();
  render(<PluginSettings {...props} />);
  await screen.findByText('2 matching plugins.');
  expect(screen.getByText(/Install unavailable/)).toBeVisible();
  expect(
    screen.getByText(/Tools 1; MCP servers 0; channels 0; skills 1/),
  ).toBeVisible();
  expect(props.load).toHaveBeenCalledWith(
    { query: '', source: 'all' },
    expect.any(AbortSignal),
  );
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
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

it('reviews then applies exact configuration with a write-only secret', async () => {
  const props = options();
  render(<PluginSettings {...props} />);
  await manage();
  fireEvent.change(screen.getByLabelText(/Region/), {
    target: { value: 'us' },
  });
  fireEvent.change(screen.getByLabelText(/Token/), {
    target: { value: 'private-token' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Review configuration' }));
  await screen.findByText('Synthetic reviewed plugin effect.');
  expect(props.execute).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Apply plugin change' }));
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

it('requires a separate reviewed action before enabling', async () => {
  const props = options();
  render(<PluginSettings {...props} />);
  await manage();
  fireEvent.click(screen.getByRole('button', { name: 'Enable plugin' }));
  await screen.findByText('Synthetic reviewed plugin effect.');
  expect(props.review.mock.calls[0][0]).toBe('plugin.enable');
  expect(props.execute).not.toHaveBeenCalled();
});

it('retains one uncertain original across remount and never creates a second command', async () => {
  const props = options();
  props.execute.mockRejectedValueOnce(Error('response lost'));
  const rendered = render(<PluginSettings {...props} />);
  await manage();
  fireEvent.click(screen.getByRole('button', { name: 'Enable plugin' }));
  await screen.findByText('Synthetic reviewed plugin effect.');
  fireEvent.click(screen.getByRole('button', { name: 'Apply plugin change' }));
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
  fireEvent.click(screen.getByRole('button', { name: 'Review configuration' }));
  await screen.findByText('Synthetic reviewed plugin effect.');
  fireEvent.click(screen.getByRole('button', { name: 'Apply plugin change' }));
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
