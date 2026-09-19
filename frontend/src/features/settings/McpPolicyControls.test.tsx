import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import McpPolicyControls, {
  createMcpPolicySession,
  type McpPolicyPage,
} from './McpPolicyControls';
import type { McpConfigurationReceipt } from './CapabilitySettings';

const serverId = 'a'.repeat(64);
const page: McpPolicyPage = {
  schema_version: 1,
  revision: 'b'.repeat(64),
  server_id: serverId,
  availability: 'available',
  global_enabled: true,
  server_enabled: true,
  resources_enabled: true,
  prompts_enabled: false,
  total: 2,
  next_cursor: null,
  items: [
    {
      tool_id: 'c'.repeat(64),
      name: 'read',
      enabled: true,
      requires_approval: false,
      approval_locked: false,
      destructive: false,
    },
    {
      tool_id: 'd'.repeat(64),
      name: 'delete_record',
      enabled: false,
      requires_approval: true,
      approval_locked: true,
      destructive: true,
    },
  ],
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}
function options() {
  return {
    session: createMcpPolicySession(serverId),
    load: vi.fn().mockResolvedValue(page),
    review: vi.fn().mockImplementation(async (payload) => ({
      configuration_revision: payload.configuration_revision,
      action_digest: 'e'.repeat(64),
      nonce: 'synthetic',
    })),
    execute: vi.fn().mockImplementation(async (command) => ({
      command_id: command.command_id,
      status: 'completed',
      mcp_configuration: {
        status: 'saved',
        revision: 'f'.repeat(64),
        saved_disabled: null,
        runtime_cleanup: 'not_requested',
      },
    })),
  };
}
async function selectAndReview(button = 'Disable Server access') {
  await screen.findByRole('button', { name: button });
  fireEvent.click(screen.getByRole('button', { name: button }));
  fireEvent.click(screen.getByRole('button', { name: 'Review permission' }));
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Save permission' }),
    ).toBeEnabled(),
  );
}

it('only reads saved policy on mount and preserves mandatory approval', async () => {
  const props = options();
  render(<McpPolicyControls {...props} />);
  await screen.findByText('2 saved tool permissions.');
  expect(
    screen.getByRole('button', { name: 'Disable delete_record approval' }),
  ).toBeDisabled();
  expect(screen.getByText(/do not connect, test or disconnect/)).toBeVisible();
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
});

it.each([
  ['Disable MCP access', { operation: 'global_enabled', enabled: false }],
  [
    'Disable Server access',
    { operation: 'server_enabled', server_id: serverId, enabled: false },
  ],
  [
    'Disable read access',
    {
      operation: 'tool_enabled',
      server_id: serverId,
      tool_id: 'c'.repeat(64),
      enabled: false,
    },
  ],
  [
    'Enable read approval',
    {
      operation: 'tool_approval',
      server_id: serverId,
      tool_id: 'c'.repeat(64),
      enabled: true,
    },
  ],
  [
    'Disable Resource access',
    {
      operation: 'utility_enabled',
      server_id: serverId,
      utility: 'resources',
      enabled: false,
    },
  ],
  [
    'Enable Prompt access',
    {
      operation: 'utility_enabled',
      server_id: serverId,
      utility: 'prompts',
      enabled: true,
    },
  ],
] as const)(
  'reviews then explicitly saves %s with an exact intent',
  async (button, intent) => {
    const props = options();
    render(<McpPolicyControls {...props} />);
    await selectAndReview(button);
    expect(props.execute).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Save permission' }));
    await screen.findByText(/Permission saved/);
    expect(props.execute.mock.calls[0][0].payload).toEqual({
      configuration_revision: page.revision,
      intent,
    });
    expect(
      screen.getByText(/Connection cleanup was not requested/),
    ).toBeVisible();
    expect(props.session.hasRetained()).toBe(false);
  },
);

it('retains exact uncertain intent and review across remount, without blind re-save', async () => {
  const props = options();
  props.execute.mockRejectedValueOnce(Error('synthetic response lost'));
  const first = render(<McpPolicyControls {...props} />);
  await selectAndReview();
  fireEvent.click(screen.getByRole('button', { name: 'Save permission' }));
  await screen.findByText(/Save outcome is uncertain/);
  const original = props.execute.mock.calls[0];
  first.unmount();
  render(<McpPolicyControls {...props} />);
  expect(props.execute).toHaveBeenCalledTimes(1);
  expect(
    screen.getByRole('button', { name: 'Disable MCP access' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Discard selected change' }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original permission change' }),
  );
  await screen.findByText(/Permission saved/);
  expect(props.execute.mock.calls[1]).toEqual(original);
  expect(props.review).toHaveBeenCalledTimes(1);
});

it('retains a pending command through unmount and late success', async () => {
  const props = options();
  const pending = deferred<McpConfigurationReceipt>();
  props.execute.mockReturnValue(pending.promise);
  const first = render(<McpPolicyControls {...props} />);
  await selectAndReview();
  fireEvent.click(screen.getByRole('button', { name: 'Save permission' }));
  const original = props.execute.mock.calls[0][0];
  first.unmount();
  render(<McpPolicyControls {...props} />);
  expect(
    screen.getByRole('button', { name: 'Check original permission change' }),
  ).toBeDisabled();
  await act(async () =>
    pending.resolve({
      command_id: original.command_id,
      status: 'completed',
      mcp_configuration: { status: 'saved', revision: 'f'.repeat(64) },
    }),
  );
  expect(props.session.hasRetained()).toBe(false);
  expect(props.execute).toHaveBeenCalledTimes(1);
});

it('does not resurrect a saved draft or receipt after authentication purge', async () => {
  const props = options();
  const pending = deferred<McpConfigurationReceipt>();
  props.execute.mockReturnValue(pending.promise);
  render(<McpPolicyControls {...props} />);
  await selectAndReview();
  fireEvent.click(screen.getByRole('button', { name: 'Save permission' }));
  act(() => props.session.dispose());
  await act(async () =>
    pending.resolve({
      command_id: props.execute.mock.calls[0][0].command_id,
      status: 'completed',
      mcp_configuration: { status: 'saved', revision: 'f'.repeat(64) },
    }),
  );
  expect(props.session.getSnapshot().page).toBeNull();
  expect(props.session.getSnapshot().draft).toBeNull();
  expect(props.session.getSnapshot().pending).toBeNull();
  expect(props.session.hasRetained()).toBe(false);
});

it('replaces pages, provides First and refuses oversized or wrong-scope data', async () => {
  const props = options();
  props.load
    .mockResolvedValueOnce({
      ...page,
      next_cursor: 'next',
      items: [page.items[0]],
    })
    .mockResolvedValueOnce({ ...page, items: [page.items[1]] })
    .mockResolvedValueOnce(page)
    .mockResolvedValue({
      ...page,
      items: Array.from({ length: 51 }, () => page.items[0]),
    });
  render(<McpPolicyControls {...props} />);
  await screen.findByRole('button', { name: 'Next permission page' });
  fireEvent.click(screen.getByRole('button', { name: 'Next permission page' }));
  await screen.findByRole('button', { name: 'Disable delete_record approval' });
  expect(
    screen.queryByRole('button', { name: 'Disable read access' }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'First permission page' }),
  );
  await screen.findByRole('button', { name: 'Disable read access' });
  expect(props.load.mock.calls[2][0].cursor).toBeUndefined();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh permissions' }));
  await screen.findByText(/Saved permissions are unavailable or changed/);
  expect(props.session.getSnapshot().page?.items).toHaveLength(2);
});

it('does not enable a stale review or replay rejected commands', async () => {
  const props = options();
  props.review.mockResolvedValueOnce({
    configuration_revision: 'different',
    action_digest: 'e'.repeat(64),
  });
  props.execute.mockImplementation(async (command) => ({
    command_id: command.command_id,
    status: 'rejected',
  }));
  render(<McpPolicyControls {...props} />);
  await screen.findByRole('button', { name: 'Disable Server access' });
  fireEvent.click(
    screen.getByRole('button', { name: 'Disable Server access' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Review permission' }));
  await screen.findByText(/permission could not be reviewed/);
  expect(
    screen.getByRole('button', { name: 'Save permission' }),
  ).toBeDisabled();
  await selectAndReview();
  fireEvent.click(screen.getByRole('button', { name: 'Save permission' }));
  await screen.findByText(/Permission change rejected/);
  expect(props.session.getSnapshot().pending).toBeNull();
  expect(
    screen.queryByRole('button', { name: 'Check original permission change' }),
  ).not.toBeInTheDocument();
});

it('keeps unknown states explicit and blocks new changes during configuration recovery', async () => {
  const props = options();
  props.load.mockResolvedValue({
    ...page,
    availability: 'recovery_required',
    global_enabled: null,
    total: null,
  });
  render(<McpPolicyControls {...props} />);
  await screen.findByText(/interrupted configuration save needs recovery/);
  expect(screen.getByText('MCP access: Unknown.')).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Enable MCP access' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Disable MCP access' }),
  ).toBeDisabled();
  expect(props.execute).not.toHaveBeenCalled();
});
