import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import McpRuntimeControls, {
  createMcpRuntimeSession,
  type McpRuntimeCommand,
  type McpRuntimeReceipt,
  type McpRuntimeState,
} from './McpRuntimeControls';

const serverId = 'a'.repeat(64);
const runtimeId = '12345678-1234-4234-8234-123456789012';
const saved: McpRuntimeState = {
  schema_version: 1,
  server_id: serverId,
  configuration_revision: 'b'.repeat(64),
  cleanup_revision: null,
  availability: 'available',
  runtime_id: null,
  state: 'missing',
  session_quiesced: null,
};
const connected: McpRuntimeState = {
  ...saved,
  runtime_id: runtimeId,
  cleanup_revision: 'c'.repeat(64),
  state: 'connected',
  session_quiesced: false,
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function receipt(command: McpRuntimeCommand): McpRuntimeReceipt {
  const operation = command.payload.operation;
  return {
    command_id: command.command_id,
    status: 'completed',
    mcp_runtime: {
      schema_version: 1,
      server_id: serverId,
      operation,
      runtime_id: runtimeId,
      state:
        operation === 'connect'
          ? 'connected'
          : operation === 'test'
            ? 'tested'
            : 'stopped',
      session_quiesced: operation !== 'connect',
      code: null,
    },
  };
}
function options() {
  return {
    session: createMcpRuntimeSession(serverId),
    load: vi.fn().mockResolvedValue(saved),
    review: vi.fn().mockImplementation(async (payload) => ({
      resource_revision: payload.resource_revision,
      server_id: payload.server_id,
      operation: payload.operation,
      runtime_id: payload.expected_runtime_id,
      action_digest: 'd'.repeat(64),
      nonce: 'synthetic-nonce',
    })),
    execute: vi.fn().mockImplementation(async (command) => receipt(command)),
  };
}
async function reviewed(name = 'Connect') {
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: `Review ${name}` }),
    ).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: `Review ${name}` }));
  await screen.findByRole('button', { name: `${name} now` });
}
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it('reads only on mount and requires distinct review and explicit Test', async () => {
  const props = options();
  render(<McpRuntimeControls {...props} />);
  await reviewed('Test');
  expect(props.execute).not.toHaveBeenCalled();
  expect(
    screen.getByText(/temporary connection and then closes/),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Test now' }));
  await screen.findByText(/Test completed and the temporary connection closed/);
  expect(props.execute).toHaveBeenCalledTimes(1);
  expect(props.session.hasRetained()).toBe(false);
});

it('retains the reviewed command across unmount and never executes on reopen', async () => {
  const props = options();
  const first = render(<McpRuntimeControls {...props} />);
  await reviewed();
  const original = props.session.getSnapshot().launch.reviewed;
  first.unmount();
  render(<McpRuntimeControls {...props} />);
  expect(props.execute).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Connect now' }));
  await screen.findByText(/Connect command completed/);
  expect(props.execute).toHaveBeenCalledWith(
    original?.command,
    original?.review,
  );
});

it('reconciles only the original uncertain intent after remount and revision changes', async () => {
  const props = options();
  props.execute.mockRejectedValueOnce(Error('synthetic loss'));
  const first = render(<McpRuntimeControls {...props} />);
  await reviewed();
  fireEvent.click(screen.getByRole('button', { name: 'Connect now' }));
  await screen.findByText(/outcome is uncertain/);
  const original = props.execute.mock.calls[0];
  first.unmount();
  props.load.mockResolvedValue({
    ...saved,
    configuration_revision: 'e'.repeat(64),
  });
  render(<McpRuntimeControls {...props} />);
  expect(screen.getByRole('button', { name: 'Review Connect' })).toBeDisabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original launch' }),
  );
  await screen.findByText(/Connect command completed/);
  expect(props.execute.mock.calls[1]).toEqual(original);
  expect(props.review).toHaveBeenCalledTimes(1);
});

it('keeps Disconnect independent while Connect response is pending', async () => {
  const props = options();
  const pending = deferred<McpRuntimeReceipt>();
  props.execute.mockImplementation((command) =>
    command.payload.operation === 'connect'
      ? pending.promise
      : Promise.resolve(receipt(command)),
  );
  render(<McpRuntimeControls {...props} />);
  await reviewed();
  props.load.mockResolvedValue(connected);
  fireEvent.click(screen.getByRole('button', { name: 'Connect now' }));
  await reviewed('Disconnect');
  fireEvent.click(screen.getByRole('button', { name: 'Disconnect now' }));
  await screen.findByText('Connection cleanup completed.');
  expect(props.execute.mock.calls[1][0].payload).toEqual({
    resource_revision: connected.cleanup_revision,
    server_id: serverId,
    operation: 'disconnect',
    expected_runtime_id: runtimeId,
  });
  expect(props.session.getSnapshot().launch.pending).not.toBeNull();
  await act(async () =>
    pending.resolve(receipt(props.execute.mock.calls[0][0])),
  );
  expect(props.session.getSnapshot().cleanup.pending).toBeNull();
});

it('uses cleanup revision to Disconnect with unavailable saved configuration', async () => {
  const props = options();
  props.load.mockResolvedValue({
    ...connected,
    availability: 'unavailable',
    configuration_revision: null,
  });
  render(<McpRuntimeControls {...props} />);
  await reviewed('Disconnect');
  expect(screen.getByRole('button', { name: 'Review Connect' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Disconnect now' }));
  await screen.findByText('Connection cleanup completed.');
  expect(props.review.mock.calls[0][0].resource_revision).toBe(
    connected.cleanup_revision,
  );
});

it('does not equate quiescent snapshot or a partial receipt with completed cleanup', async () => {
  const props = options();
  props.load.mockResolvedValue({
    ...connected,
    state: 'cleanup_incomplete',
    session_quiesced: true,
  });
  props.execute.mockImplementation(async (command) => ({
    ...receipt(command),
    status: 'partial',
  }));
  render(<McpRuntimeControls {...props} />);
  await reviewed('Disconnect');
  expect(screen.getByText(/saved receipt still needs recovery/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Disconnect now' }));
  await screen.findByText(/outcome is not confirmed/);
  expect(
    screen.getByRole('button', { name: 'Check original disconnect' }),
  ).toBeEnabled();
  expect(props.session.hasRetained()).toBe(true);
});

it('tombstones late effect settlement and aborts reads on authentication loss', async () => {
  const props = options();
  const pending = deferred<McpRuntimeReceipt>();
  props.execute.mockReturnValue(pending.promise);
  render(<McpRuntimeControls {...props} />);
  await reviewed();
  fireEvent.click(screen.getByRole('button', { name: 'Connect now' }));
  act(() => props.session.dispose());
  await act(async () =>
    pending.resolve(receipt(props.execute.mock.calls[0][0])),
  );
  expect(props.session.getSnapshot().snapshot).toBeNull();
  expect(props.session.getSnapshot().launch.pending).toBeNull();
  expect(props.session.hasRetained()).toBe(false);
  expect(props.load.mock.calls.every((call) => call[1].aborted)).toBe(true);
  expect(screen.getByRole('button', { name: 'Review Connect' })).toBeDisabled();
});

it('rejects mismatched review scope without enabling execution', async () => {
  const props = options();
  props.review.mockResolvedValue({
    resource_revision: saved.configuration_revision,
    server_id: 'f'.repeat(64),
    operation: 'connect',
    runtime_id: null,
    action_digest: 'd'.repeat(64),
  });
  render(<McpRuntimeControls {...props} />);
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Review Connect' }),
    ).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Review Connect' }));
  await screen.findByText(/action could not be reviewed/);
  expect(
    screen.queryByRole('button', { name: 'Connect now' }),
  ).not.toBeInTheDocument();
  expect(props.execute).not.toHaveBeenCalled();
});

it('retains the exact runtime ID when an original response changes identity', async () => {
  const props = options();
  props.execute.mockImplementationOnce(async (command) => ({
    ...receipt(command),
    status: 'partial',
  }));
  render(<McpRuntimeControls {...props} />);
  await reviewed();
  fireEvent.click(screen.getByRole('button', { name: 'Connect now' }));
  await screen.findByText(/outcome is not confirmed/);
  props.execute.mockImplementation(async (command) => {
    const value = receipt(command);
    return {
      ...value,
      mcp_runtime: {
        ...value.mcp_runtime!,
        runtime_id: '12345678-1234-4234-8234-123456789013',
      },
    };
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original launch' }),
  );
  await screen.findByText(/outcome is uncertain/);
  expect(props.session.getSnapshot().launch.pending?.runtimeId).toBe(runtimeId);
  expect(props.session.getSnapshot().launch.pending?.command).toBe(
    props.execute.mock.calls[0][0],
  );
});

it('does not retain or replay a rejected intent', async () => {
  const props = options();
  props.execute.mockImplementation(async (command) => ({
    command_id: command.command_id,
    status: 'rejected',
  }));
  render(<McpRuntimeControls {...props} />);
  await reviewed();
  fireEvent.click(screen.getByRole('button', { name: 'Connect now' }));
  await screen.findByText(/action was rejected/);
  expect(props.session.hasRetained()).toBe(false);
  expect(
    screen.queryByRole('button', { name: 'Check original launch' }),
  ).not.toBeInTheDocument();
});

it('bounds visible observation and stops timers when unmounted without sending effects', async () => {
  vi.useFakeTimers();
  const props = options();
  props.load.mockResolvedValue(connected);
  const view = render(<McpRuntimeControls {...props} />);
  await act(async () => {
    await Promise.resolve();
  });
  for (let count = 0; count < 31; count++)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
  expect(props.load).toHaveBeenCalledTimes(30);
  expect(screen.getByText(/Automatic observation paused/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh connection' }));
  await act(async () => {
    await Promise.resolve();
  });
  view.unmount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(props.load).toHaveBeenCalledTimes(31);
  expect(props.execute).not.toHaveBeenCalled();
});

it('does not overlap passive reads when one response remains pending', async () => {
  vi.useFakeTimers();
  const props = options();
  const blocked = deferred<McpRuntimeState>();
  props.load.mockReturnValue(blocked.promise);
  const view = render(<McpRuntimeControls {...props} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20000);
  });
  expect(props.load).toHaveBeenCalledTimes(1);
  view.unmount();
  await act(async () => blocked.resolve(connected));
  expect(props.session.getSnapshot().snapshot).toBeNull();
});

it('pauses observation while hidden and resumes within the same finite window', async () => {
  vi.useFakeTimers();
  let visibility: DocumentVisibilityState = 'hidden';
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(
    () => visibility,
  );
  const props = options();
  props.load.mockResolvedValue(connected);
  const view = render(<McpRuntimeControls {...props} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(props.load).not.toHaveBeenCalled();
  visibility = 'visible';
  await act(async () => {
    document.dispatchEvent(new Event('visibilitychange'));
  });
  expect(props.load).toHaveBeenCalledTimes(1);
  visibility = 'hidden';
  act(() => document.dispatchEvent(new Event('visibilitychange')));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(props.load).toHaveBeenCalledTimes(1);
  visibility = 'visible';
  await act(async () => {
    document.dispatchEvent(new Event('visibilitychange'));
  });
  for (let count = 0; count < 31; count++)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
  expect(props.load).toHaveBeenCalledTimes(30);
  expect(props.execute).not.toHaveBeenCalled();
  view.unmount();
});

it('retains one read slot across hidden, Refresh and remount until actual settlement', async () => {
  let visibility: DocumentVisibilityState = 'visible';
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(
    () => visibility,
  );
  const props = options();
  const pending = deferred<McpRuntimeState>();
  props.load.mockReturnValueOnce(pending.promise).mockResolvedValue(connected);
  const first = render(<McpRuntimeControls {...props} />);
  expect(props.load).toHaveBeenCalledTimes(1);
  visibility = 'hidden';
  act(() => document.dispatchEvent(new Event('visibilitychange')));
  expect(props.load.mock.calls[0][1].aborted).toBe(true);
  visibility = 'visible';
  act(() => document.dispatchEvent(new Event('visibilitychange')));
  fireEvent.click(screen.getByRole('button', { name: 'Refresh connection' }));
  first.unmount();
  render(<McpRuntimeControls {...props} />);
  expect(props.load).toHaveBeenCalledTimes(1);
  await act(async () => pending.resolve(saved));
  expect(props.load).toHaveBeenCalledTimes(2);
  expect(props.session.getSnapshot().snapshot).toEqual(connected);
  expect(props.execute).not.toHaveBeenCalled();
});
