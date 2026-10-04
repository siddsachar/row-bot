import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import McpPolicyControls, {
  McpGlobalSwitch,
  createMcpPolicySession,
  type McpPolicyPage,
} from './McpPolicyControls';
import type { McpConfigurationReceipt } from './CapabilitySettings';
import { mcpRevision } from './mcp-revision';

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
function options(scope: string | null = serverId) {
  return {
    session: createMcpPolicySession(scope),
    load: vi
      .fn()
      .mockResolvedValue(scope ? page : { ...page, server_id: null }),
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
/** Each permission is a switch; flipping it reviews and saves in one step. */
async function flip(name = 'Server access') {
  const control = await screen.findByRole('switch', { name });
  await waitFor(() => expect(control).toBeEnabled());
  fireEvent.click(control);
}

it('reads permissions again when another MCP panel saves, and after a conflict retries once', async () => {
  const props = options();
  render(<McpPolicyControls {...props} />);
  await screen.findByText('1 of 2 on');
  const fresh = { ...page, revision: '9'.repeat(64) };
  props.load.mockResolvedValue(fresh);
  act(() => mcpRevision.saved('another panel'));
  await waitFor(() => expect(props.load).toHaveBeenCalledTimes(2));
  props.review.mockRejectedValueOnce({
    code: 'revision_conflict',
    status: 409,
  });
  props.load.mockResolvedValue({ ...page, revision: '8'.repeat(64) });
  await flip();
  await screen.findByText('Permission saved.');
  expect(
    props.review.mock.calls.map((call) => call[0].configuration_revision),
  ).toEqual(['9'.repeat(64), '8'.repeat(64)]);
  expect(props.execute).toHaveBeenCalledOnce();
});

it('only reads saved policy on mount and preserves mandatory approval', async () => {
  const props = options();
  render(<McpPolicyControls {...props} />);
  await screen.findByText('1 of 2 on');
  expect(screen.getByRole('switch', { name: 'Use read' })).toBeChecked();
  const locked = screen.getByRole('switch', {
    name: 'Ask before delete_record runs',
  });
  expect(locked).toBeChecked();
  expect(locked).toBeDisabled();
  expect(screen.getByText('Always asks first')).toBeVisible();
  expect(screen.getByText('Changes things')).toBeVisible();
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
});

it.each([
  [
    'Server access',
    { operation: 'server_enabled', server_id: serverId, enabled: false },
  ],
  [
    'Use read',
    {
      operation: 'tool_enabled',
      server_id: serverId,
      tool_id: 'c'.repeat(64),
      enabled: false,
    },
  ],
  [
    'Ask before read runs',
    {
      operation: 'tool_approval',
      server_id: serverId,
      tool_id: 'c'.repeat(64),
      enabled: true,
    },
  ],
  [
    'Resource access',
    {
      operation: 'utility_enabled',
      server_id: serverId,
      utility: 'resources',
      enabled: false,
    },
  ],
  [
    'Prompt access',
    {
      operation: 'utility_enabled',
      server_id: serverId,
      utility: 'prompts',
      enabled: true,
    },
  ],
] as const)(
  'saves %s with an exact intent in one click',
  async (name, intent) => {
    const props = options();
    render(<McpPolicyControls {...props} />);
    await flip(name);
    await screen.findByText(/Permission saved/);
    expect(props.execute.mock.calls[0][0].payload).toEqual({
      configuration_revision: page.revision,
      intent,
    });
    // The saved permissions are read again with the new revision.
    await waitFor(() => expect(props.load).toHaveBeenCalledTimes(2));
    expect(screen.getByText('Permission saved.')).toBeVisible();
    expect(props.session.hasRetained()).toBe(false);
  },
);

it('"Use MCP servers" turns MCP off with the same reviewed change', async () => {
  const props = options(null);
  render(<McpGlobalSwitch {...props} />);
  const control = await screen.findByRole('switch', {
    name: 'Use MCP servers',
  });
  await waitFor(() => expect(control).toBeChecked());
  await flip('Use MCP servers');
  await screen.findByText('Permission saved.');
  expect(props.execute.mock.calls[0][0].payload).toEqual({
    configuration_revision: page.revision,
    intent: { operation: 'global_enabled', enabled: false },
  });
});

it('shows the change being saved on its switch, then the saved value', async () => {
  const props = options();
  const response = deferred<McpConfigurationReceipt>();
  props.execute.mockReturnValue(response.promise);
  render(<McpPolicyControls {...props} />);
  await flip('Prompt access');
  await waitFor(() => expect(props.execute).toHaveBeenCalledOnce());
  const control = screen.getByRole('switch', { name: 'Prompt access' });
  expect(control).toBeChecked();
  expect(control).toBeDisabled();
  props.load.mockResolvedValue({ ...page, prompts_enabled: true });
  await act(async () =>
    response.resolve({
      command_id: props.execute.mock.calls[0][0].command_id,
      status: 'completed',
      mcp_configuration: { status: 'saved', revision: 'f'.repeat(64) },
    }),
  );
  await waitFor(() => expect(control).toBeEnabled());
  expect(control).toBeChecked();
});

it('retains exact uncertain intent and review across remount, without blind re-save', async () => {
  const props = options();
  props.execute.mockRejectedValueOnce(Error('synthetic response lost'));
  const first = render(<McpPolicyControls {...props} />);
  await flip();
  await screen.findByText(/Save outcome is uncertain/);
  const original = props.execute.mock.calls[0];
  first.unmount();
  render(<McpPolicyControls {...props} />);
  expect(props.execute).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('switch', { name: 'Server access' })).toBeDisabled();
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
  await flip();
  await waitFor(() => expect(props.execute).toHaveBeenCalledOnce());
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
  await flip();
  await waitFor(() => expect(props.execute).toHaveBeenCalledOnce());
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
  await screen.findByRole('switch', { name: 'Ask before delete_record runs' });
  expect(
    screen.queryByRole('switch', { name: 'Use read' }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'First permission page' }),
  );
  await screen.findByRole('switch', { name: 'Use read' });
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
  await flip();
  await screen.findByText(/permission could not be validated/);
  expect(props.execute).not.toHaveBeenCalled();
  // The switch shows the saved value again, not the change that failed.
  expect(screen.getByRole('switch', { name: 'Server access' })).toBeChecked();
  await flip();
  await screen.findByText(/Permission change rejected/);
  expect(props.session.getSnapshot().pending).toBeNull();
  expect(
    screen.queryByRole('button', { name: 'Check original permission change' }),
  ).not.toBeInTheDocument();
});

it('keeps unknown states explicit and blocks new changes during configuration recovery', async () => {
  const props = options(null);
  props.load.mockResolvedValue({
    ...page,
    server_id: null,
    availability: 'recovery_required',
    global_enabled: null,
    total: null,
  });
  render(<McpGlobalSwitch {...props} />);
  await screen.findByText(/interrupted configuration save needs recovery/);
  expect(screen.getByText('status unknown')).toBeVisible();
  expect(
    screen.getByRole('switch', { name: 'Use MCP servers' }),
  ).toBeDisabled();
  expect(props.execute).not.toHaveBeenCalled();
});

it('does not present an unclassified tool as read-only when it is not marked destructive', async () => {
  const props = options();
  props.load.mockResolvedValue({
    ...page,
    total: 1,
    items: [
      {
        ...page.items[0],
        name: 'unrecognized',
        enabled: false,
        requires_approval: true,
        approval_locked: true,
      },
    ],
  });
  render(<McpPolicyControls {...props} />);
  expect(
    await screen.findByText('Review tool effects before use'),
  ).toBeVisible();
  expect(screen.queryByText('Reads only')).toBeNull();
  expect(
    screen.getByRole('switch', { name: 'Ask before unrecognized runs' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('switch', { name: 'Use unrecognized' }),
  ).not.toBeChecked();
  expect(props.execute).not.toHaveBeenCalled();
});
