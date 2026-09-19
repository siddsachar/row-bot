import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import {
  createMcpRuntimeInstallationSession,
  McpRuntimeInstallation,
  type RuntimeInstallationCallbacks,
  type RuntimeInstallationCommand,
  type RuntimeInstallationReceipt,
  type RuntimeInstallationReview,
  type RuntimeInstallationSnapshot,
} from './McpRuntimeInstallation';

const snapshot: RuntimeInstallationSnapshot = {
  schema_version: 1,
  runtime_id: 'node',
  resource_revision: 'revision',
  availability: 'missing',
  installed: false,
  active_command_id: null,
  quiesced: true,
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
function receipt(
  command: RuntimeInstallationCommand,
): RuntimeInstallationReceipt {
  return {
    command_id: command.command_id,
    status: 'completed',
    installation: {
      runtime_id: 'node',
      operation: command.type === 'mcp.runtime.resolve' ? 'resolve' : 'install',
      stage:
        command.type === 'mcp.runtime.resolve'
          ? 'resolved'
          : command.type === 'mcp.runtime.install.cancel'
            ? 'cancellation_requested'
            : 'installed',
      cancel_requested: command.type === 'mcp.runtime.install.cancel',
      quiesced: command.type !== 'mcp.runtime.install.cancel',
      installed: command.type === 'mcp.runtime.install',
      plan: null,
    },
  };
}
function options() {
  const saved = new Map<string, RuntimeInstallationReceipt>();
  const callbacks = {
    load: vi.fn().mockResolvedValue(snapshot),
    review: vi
      .fn()
      .mockImplementation(
        async (
          operation,
          sourceCommandId,
          revision,
        ): Promise<RuntimeInstallationReview> => ({
          schema_version: 1,
          runtime_id: 'node',
          operation,
          source_command_id: sourceCommandId,
          resource_revision: revision,
          action_digest: 'digest',
          network_required: true,
          executes_runtime: false,
          plan:
            operation === 'install'
              ? {
                  version: '1.2.3',
                  url: 'https://example.invalid/node.zip',
                  sha256: 'a'.repeat(64),
                  size_bytes: 123,
                  system: 'windows',
                  arch: 'x64',
                  asset_name: 'node.zip',
                }
              : null,
          disclosures: [
            'Contacts the runtime publisher. No server is connected.',
          ],
          nonce: 'nonce',
        }),
      ),
    execute: vi
      .fn()
      .mockImplementation(async (command: RuntimeInstallationCommand) => {
        const result = receipt(command);
        saved.set(command.command_id, result);
        return result;
      }),
    receipt: vi.fn().mockImplementation(async (id: string) => {
      const value = saved.get(id);
      if (!value) throw Error('unknown');
      return value;
    }),
  } satisfies RuntimeInstallationCallbacks;
  return {
    session: createMcpRuntimeInstallationSession('node'),
    callbacks,
    saved,
  };
}
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it('keeps passive reads separate from explicit metadata and pinned install approval', async () => {
  const { session, callbacks } = options();
  render(<McpRuntimeInstallation session={session} callbacks={callbacks} />);
  await waitFor(() => expect(callbacks.load).toHaveBeenCalledOnce());
  expect(callbacks.execute).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Review metadata resolution' }),
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Approve and resolve metadata' }),
  );
  await waitFor(() =>
    expect(session.getSnapshot().sourceCommandId).toBeTruthy(),
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Review pinned installation' }),
  );
  expect(await screen.findByText('1.2.3')).toBeInTheDocument();
  expect(screen.getByText('a'.repeat(64))).toBeInTheDocument();
  expect(callbacks.execute).toHaveBeenCalledTimes(1);
  fireEvent.click(
    screen.getByRole('button', { name: 'Approve and install pinned runtime' }),
  );
  await waitFor(() => expect(callbacks.execute).toHaveBeenCalledTimes(2));
  expect(callbacks.execute.mock.calls[1][0].payload.source_command_id).toBe(
    callbacks.execute.mock.calls[0][0].command_id,
  );
});

it('retains an uncertain original across full remount and recovers only its receipt', async () => {
  const { session, callbacks, saved } = options();
  callbacks.execute.mockImplementationOnce(async (command) => {
    saved.set(command.command_id, receipt(command));
    throw Error('lost response');
  });
  await session.read(callbacks);
  await session.review('resolve', callbacks);
  await session.run(callbacks);
  const original = session.getSnapshot().original;
  expect(session.hasRetained()).toBe(true);
  const view = render(
    <McpRuntimeInstallation session={session} callbacks={callbacks} />,
  );
  await waitFor(() =>
    expect(session.getSnapshot().result?.status).toBe('completed'),
  );
  view.unmount();
  render(<McpRuntimeInstallation session={session} callbacks={callbacks} />);
  await waitFor(() =>
    expect(callbacks.receipt).toHaveBeenCalledWith(
      original?.command_id,
      expect.any(AbortSignal),
    ),
  );
  expect(callbacks.execute).toHaveBeenCalledTimes(1);
});

it('can cancel the exact original while its start response remains blocked', async () => {
  const { session, callbacks } = options();
  const blocked = deferred<RuntimeInstallationReceipt>();
  callbacks.execute.mockImplementationOnce(() => blocked.promise);
  await session.read(callbacks);
  await session.review('resolve', callbacks);
  const running = session.run(callbacks);
  const original = session.getSnapshot().original!;
  await session.cancel(callbacks);
  expect(callbacks.execute.mock.calls[1][0].payload.source_command_id).toBe(
    original.command_id,
  );
  expect(callbacks.execute.mock.calls[1][0].type).toBe(
    'mcp.runtime.install.cancel',
  );
  expect(session.getSnapshot().busy).toBe(true);
  blocked.resolve(receipt(original));
  await running;
  expect(session.getSnapshot().cancelResult?.installation.quiesced).toBe(false);
});

it.each(['resolve', 'reject'] as const)(
  'auth disposal prevents late %s settlement from resurrecting retained intent',
  async (mode) => {
    const { session, callbacks } = options();
    const blocked = deferred<RuntimeInstallationReceipt>();
    callbacks.execute.mockImplementationOnce(() => blocked.promise);
    await session.read(callbacks);
    await session.review('resolve', callbacks);
    const running = session.run(callbacks);
    const original = session.getSnapshot().original!;
    session.dispose();
    if (mode === 'resolve') blocked.resolve(receipt(original));
    else blocked.reject(Error('late'));
    await running;
    expect(session.getSnapshot().original).toBeNull();
    expect(session.getSnapshot().result).toBeNull();
    expect(session.hasRetained()).toBe(false);
    await session.run(callbacks);
    await session.cancel(callbacks);
    expect(callbacks.execute).toHaveBeenCalledTimes(1);
  },
);

it('retires completed historical attempts so 40 explicit resolutions remain usable', async () => {
  const { session, callbacks } = options();
  await session.read(callbacks);
  for (let index = 0; index < 40; index += 1) {
    await session.review('resolve', callbacks);
    await session.run(callbacks);
  }
  expect(callbacks.execute).toHaveBeenCalledTimes(40);
  expect(session.hasRetained()).toBe(false);
});

it('recovers a durable original discovered after the app session was lost', async () => {
  const { session, callbacks, saved } = options();
  const original: RuntimeInstallationCommand = {
    command_id: crypto.randomUUID(),
    type: 'mcp.runtime.resolve',
    payload: { runtime_id: 'node', source_command_id: null },
  };
  saved.set(original.command_id, receipt(original));
  callbacks.load.mockResolvedValue({
    ...snapshot,
    availability: 'recovery_required',
    active_command_id: original.command_id,
  });
  await session.read(callbacks);
  expect(session.getSnapshot().sourceCommandId).toBe(original.command_id);
  expect(callbacks.execute).not.toHaveBeenCalled();
});

it('refreshes saved status after receipt reconciliation releases a durable original', async () => {
  const { session, callbacks, saved } = options();
  const original: RuntimeInstallationCommand = {
    command_id: crypto.randomUUID(),
    type: 'mcp.runtime.resolve',
    payload: { runtime_id: 'node', source_command_id: null },
  };
  saved.set(original.command_id, receipt(original));
  callbacks.load.mockResolvedValueOnce({
    ...snapshot,
    availability: 'recovery_required',
    active_command_id: original.command_id,
  });
  await session.read(callbacks);
  expect(callbacks.load).toHaveBeenCalledTimes(2);
  await session.review('install', callbacks);
  expect(session.getSnapshot().review?.source_command_id).toBe(
    original.command_id,
  );
});

it('pauses observation while hidden and never overlaps blocked status reads', async () => {
  vi.useFakeTimers();
  const { session, callbacks } = options();
  const visibility = vi
    .spyOn(document, 'visibilityState', 'get')
    .mockReturnValue('hidden');
  const view = render(
    <McpRuntimeInstallation session={session} callbacks={callbacks} />,
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(callbacks.load).not.toHaveBeenCalled();
  const blocked = deferred<RuntimeInstallationSnapshot>();
  callbacks.load.mockImplementationOnce(() => blocked.promise);
  visibility.mockReturnValue('visible');
  await act(async () => {
    fireEvent(document, new Event('visibilitychange'));
  });
  await act(async () => {
    fireEvent(document, new Event('visibilitychange'));
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(callbacks.load).toHaveBeenCalledTimes(1);
  view.unmount();
  blocked.resolve(snapshot);
  await act(async () => {
    await Promise.resolve();
  });
  expect(session.getSnapshot().snapshot).toBeNull();
});
