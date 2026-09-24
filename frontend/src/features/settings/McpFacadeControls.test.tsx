import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import McpFacadeControls, {
  createMcpFacadeSession,
  type NativeMcpReceipt,
  type NativeMcpState,
} from './McpFacadeControls';

const snapshot: NativeMcpState = {
  schema_version: 1,
  resource_revision: 'a'.repeat(64),
  availability: 'available',
  saved_enabled: false,
  effective_enabled: false,
  registered: true,
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { resolve, promise };
}
function options() {
  return {
    session: createMcpFacadeSession(),
    load: vi.fn().mockResolvedValue(snapshot),
    review: vi.fn().mockImplementation(async (payload) => ({
      ...payload,
      action_digest: 'b'.repeat(64),
      nonce: 'synthetic',
    })),
    execute: vi.fn().mockImplementation(async (command) => ({
      command_id: command.command_id,
      status: 'completed',
      native_mcp: {
        schema_version: 1,
        status: 'saved',
        resource_revision: 'c'.repeat(64),
        saved_enabled: command.payload.enabled,
        effective_enabled: command.payload.enabled,
        code: null,
      },
    })),
  };
}
async function toggle() {
  const control = await screen.findByRole('switch', { name: 'Enable in chat' });
  await waitFor(() => expect(control).toBeEnabled());
  fireEvent.click(control);
}

it('mount reads only and distinguishes saved access from connection management', async () => {
  const props = options();
  render(<McpFacadeControls {...props} />);
  await screen.findByText(
    'Saved access: Disabled. Current chat access: Disabled.',
  );
  expect(
    screen.getByText(/Server connections and saved server permissions/),
  ).toBeVisible();
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
});

it.each([true, false])(
  'saves the exact %s toggle with one click',
  async (enabled) => {
    const props = options();
    if (!enabled)
      props.load.mockResolvedValue({ ...snapshot, saved_enabled: true });
    render(<McpFacadeControls {...props} />);
    await toggle();
    await screen.findByText(
      'Chat access saved. Server connections were not changed.',
    );
    expect(props.execute.mock.calls[0][0].payload).toEqual({
      resource_revision: snapshot.resource_revision,
      enabled,
    });
    expect(props.session.hasRetained()).toBe(false);
  },
);

it('preserves the original uncertain command across full remount and checks only it', async () => {
  const props = options();
  props.execute.mockRejectedValueOnce(Error('response lost'));
  let rendered = render(<McpFacadeControls {...props} />);
  await toggle();
  await screen.findByText(/original save is unconfirmed/);
  const original = props.execute.mock.calls[0];
  rendered.unmount();
  rendered = render(<McpFacadeControls {...props} />);
  expect(screen.getByRole('switch', { name: 'Enable in chat' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Check original save' }));
  await screen.findByText(
    'Chat access saved. Server connections were not changed.',
  );
  expect(props.execute.mock.calls[1]).toEqual(original);
  expect(props.load).toHaveBeenCalledTimes(1);
  rendered.unmount();
});

it('retains an in-flight command through remount without repeating it', async () => {
  const props = options();
  const response = deferred<NativeMcpReceipt>();
  props.execute.mockReturnValue(response.promise);
  let rendered = render(<McpFacadeControls {...props} />);
  await toggle();
  await waitFor(() => expect(props.execute).toHaveBeenCalledOnce());
  rendered.unmount();
  rendered = render(<McpFacadeControls {...props} />);
  expect(
    screen.getByRole('button', { name: 'Check original save' }),
  ).toBeDisabled();
  const command = props.execute.mock.calls[0][0];
  await act(async () =>
    response.resolve({ command_id: command.command_id, status: 'partial' }),
  );
  expect(
    screen.getByRole('button', { name: 'Check original save' }),
  ).toBeEnabled();
  expect(props.execute).toHaveBeenCalledTimes(1);
  rendered.unmount();
});

it('auth purge prevents late settlement from restoring saved state or original intent', async () => {
  const props = options();
  const response = deferred<NativeMcpReceipt>();
  props.execute.mockReturnValue(response.promise);
  render(<McpFacadeControls {...props} />);
  await toggle();
  await waitFor(() => expect(props.execute).toHaveBeenCalledOnce());
  act(() => props.session.dispose());
  await act(async () =>
    response.resolve({
      command_id: props.execute.mock.calls[0][0].command_id,
      status: 'partial',
    }),
  );
  expect(props.session.hasRetained()).toBe(false);
  expect(props.session.getSnapshot().snapshot).toBeNull();
  expect(
    screen.queryByRole('button', { name: 'Check original save' }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByText('Sign in again to manage chat tool access.'),
  ).toBeVisible();
});

it.each(['unavailable', 'registration_unavailable', 'recovery_required'])(
  'keeps %s read-only without claiming disabled',
  async (availability) => {
    const props = options();
    props.load.mockResolvedValue({
      ...snapshot,
      availability,
      saved_enabled: null,
      effective_enabled: null,
    });
    render(<McpFacadeControls {...props} />);
    await screen.findByText(
      'Saved access: Unknown. Current chat access: Unknown.',
    );
    expect(
      screen.getByRole('switch', { name: 'Enable in chat' }),
    ).toBeDisabled();
    expect(props.review).not.toHaveBeenCalled();
  },
);

it('rejects a mismatched validation without execution', async () => {
  const props = options();
  props.review.mockImplementation(async (payload) => ({
    ...payload,
    enabled: !payload.enabled,
    nonce: 'synthetic',
    action_digest: 'b'.repeat(64),
  }));
  render(<McpFacadeControls {...props} />);
  await toggle();
  await screen.findByText(/could not be validated/);
  expect(props.execute).not.toHaveBeenCalled();
});

it('rejects another command receipt and retains its own uncertain command', async () => {
  const props = options();
  props.execute.mockResolvedValue({
    command_id: 'unrelated',
    status: 'completed',
  });
  render(<McpFacadeControls {...props} />);
  await toggle();
  await screen.findByText(/original save is unconfirmed/);
  expect(props.session.hasRetained()).toBe(true);
});

it('a rejected command cannot be blindly retried', async () => {
  const props = options();
  props.execute.mockImplementation(async (command) => ({
    command_id: command.command_id,
    status: 'rejected',
  }));
  render(<McpFacadeControls {...props} />);
  await toggle();
  await screen.findByText(/change was rejected/);
  expect(
    screen.queryByRole('button', { name: 'Check original save' }),
  ).not.toBeInTheDocument();
});
