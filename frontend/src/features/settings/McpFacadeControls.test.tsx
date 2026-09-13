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
async function reviewChoice(enabled = true) {
  const choose = await screen.findByRole('button', {
    name: enabled ? 'Enable in chat' : 'Disable in chat',
  });
  await waitFor(() => expect(choose).toBeEnabled());
  fireEvent.click(choose);
  fireEvent.click(screen.getByRole('button', { name: 'Review chat access' }));
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Save chat access' }),
    ).toBeEnabled(),
  );
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
  'reviews then saves the exact %s toggle',
  async (enabled) => {
    const props = options();
    render(<McpFacadeControls {...props} />);
    await reviewChoice(enabled);
    expect(props.execute).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Save chat access' }));
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
  await reviewChoice();
  fireEvent.click(screen.getByRole('button', { name: 'Save chat access' }));
  await screen.findByText(/original save is unconfirmed/);
  const original = props.execute.mock.calls[0];
  rendered.unmount();
  rendered = render(<McpFacadeControls {...props} />);
  expect(
    screen.getByRole('button', { name: 'Disable in chat' }),
  ).toBeDisabled();
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
  await reviewChoice();
  fireEvent.click(screen.getByRole('button', { name: 'Save chat access' }));
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
  await reviewChoice();
  fireEvent.click(screen.getByRole('button', { name: 'Save chat access' }));
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
      screen.getByRole('button', { name: 'Enable in chat' }),
    ).toBeDisabled();
    expect(props.review).not.toHaveBeenCalled();
  },
);

it('rejects a mismatched review without enabling Save', async () => {
  const props = options();
  props.review.mockImplementation(async (payload) => ({
    ...payload,
    enabled: !payload.enabled,
    nonce: 'synthetic',
    action_digest: 'b'.repeat(64),
  }));
  render(<McpFacadeControls {...props} />);
  const choose = await screen.findByRole('button', { name: 'Enable in chat' });
  await waitFor(() => expect(choose).toBeEnabled());
  fireEvent.click(choose);
  fireEvent.click(screen.getByRole('button', { name: 'Review chat access' }));
  await screen.findByText(/could not be reviewed/);
  expect(
    screen.getByRole('button', { name: 'Save chat access' }),
  ).toBeDisabled();
});

it('rejects another command receipt and retains its own uncertain command', async () => {
  const props = options();
  props.execute.mockResolvedValue({
    command_id: 'unrelated',
    status: 'completed',
  });
  render(<McpFacadeControls {...props} />);
  await reviewChoice();
  fireEvent.click(screen.getByRole('button', { name: 'Save chat access' }));
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
  await reviewChoice();
  fireEvent.click(screen.getByRole('button', { name: 'Save chat access' }));
  await screen.findByText(/change was rejected/);
  expect(
    screen.queryByRole('button', { name: 'Check original save' }),
  ).not.toBeInTheDocument();
});
