import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { Command, CommandReceipt, ResourceView } from '../../api/types';
import type {
  WorkspaceProcessAttempt,
  WorkspaceProcessSnapshot,
} from './WorkspaceProcesses';
import { createWorkspaceProcessSessions } from './workspace-process-sessions';

const resource = {
  available: true,
  resource_revision: 'resource-1',
  binding: {
    kind: 'workspace',
    resource_id: 'workspace',
    binding_id: 'binding',
    revision: 'binding-1',
  },
} as ResourceView;
const snapshot: WorkspaceProcessSnapshot = {
  resource_id: 'workspace',
  conversation_id: 'chat',
  binding_id: 'binding',
  binding_revision: 'binding-1',
  resource_revision: 'resource-1',
  processes: [],
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
function outcome(command: Command, overrides: Partial<CommandReceipt> = {}) {
  const payload = command.payload as { process_id?: string };
  const id =
    command.type === 'workspace.process.start'
      ? command.command_id
      : payload.process_id!;
  return {
    command_id: command.command_id,
    conversation_id: 'chat',
    resource_id: 'workspace',
    binding_id: 'binding',
    binding_revision: 'binding-1',
    status: 'completed',
    workspace_process: {
      process_id: id,
      command_id: id,
      run_id: 'run-' + id,
      command: '',
      state: command.type === 'workspace.process.start' ? 'running' : 'exited',
      exit_code: null,
      quiesced: command.type !== 'workspace.process.start',
    },
    ...overrides,
  } as CommandReceipt;
}
function fixture() {
  const state = {
    selectedConversationId: 'chat',
    loadingConversation: false,
    handshake: {
      instance_id: 'instance',
      server_epoch: 'epoch',
      client_session_id: 'session',
    } as {
      instance_id: string;
      server_epoch: string;
      client_session_id: string;
    } | null,
    workspace: {
      conversation_id: 'chat',
      revision: '1',
      resources: [structuredClone(resource)],
    },
  };
  const listeners = new Set<() => void>();
  const controller = {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    workspaceProcesses: vi.fn(async () => snapshot),
    workspaceProcessRecovery: vi.fn(async () => ({
      items: [],
      next_cursor: null,
    })),
    workspaceProcessOutput: vi.fn(),
    reviewWorkspaceProcess: vi.fn(
      async (
        _conversation: string,
        _binding: string,
        body: { command_id: string; command: string },
      ) => ({
        ...snapshot,
        command_id: body.command_id,
        command: body.command,
        conversation_revision: '1',
        policy_revision: 'p',
        action_digest: 'digest',
        nonce: 'nonce-' + body.command_id,
        policy_decision: 'ask',
        approval_required: true,
      }),
    ),
    command: vi.fn(async (_conversation: string, command: Command) =>
      outcome(command),
    ),
    receipt: vi.fn(),
    retryCommand: vi.fn(),
  };
  const owner = createWorkspaceProcessSessions(
    controller as unknown as ClientController,
  );
  const entry = owner.forResource('chat', state.workspace.resources[0])!;
  entry.session.update((value) => ({ ...value, snapshot }));
  const attempt = (id = 'start-one'): WorkspaceProcessAttempt => ({
    command_id: id,
    command: 'python safe.py',
    snapshot,
    review: null,
    started: false,
    uncertain: false,
  });
  async function reviewed(id = 'start-one') {
    const value = attempt(id);
    const evidence = await entry.api.review(value);
    return { value, evidence };
  }
  return {
    state,
    controller,
    owner,
    entry,
    attempt,
    reviewed,
    emit: () => listeners.forEach((listener) => listener()),
  };
}

it('retains the exact entry and completed Start response across panel reconstruction', async () => {
  const { entry, owner, controller, reviewed, state } = fixture();
  const { value, evidence } = await reviewed();
  entry.session.update((current) => ({
    ...current,
    draft: value.command,
    attempt: value,
  }));
  const first = await entry.api.start(value, evidence);
  expect(owner.forResource('chat', state.workspace.resources[0])).toBe(entry);
  expect(await entry.api.start(value, evidence)).toEqual(first);
  expect(controller.command).toHaveBeenCalledOnce();
  expect(controller.command.mock.calls[0][1]).toMatchObject({
    command_id: 'start-one',
    payload: {
      command: value.command,
      nonce: 'nonce-start-one',
      target: { binding_id: 'binding', binding_revision: 'binding-1' },
    },
  });
});

it('recovers the original lost response from its receipt without another Start', async () => {
  const { entry, controller, reviewed } = fixture();
  const { value, evidence } = await reviewed();
  controller.command.mockRejectedValueOnce(new Error('lost response'));
  await expect(entry.api.start(value, evidence)).rejects.toThrow();
  const original = controller.command.mock.calls[0][1];
  controller.receipt.mockResolvedValue(outcome(original));
  await entry.api.start(value, evidence);
  expect(controller.receipt).toHaveBeenCalledWith('start-one');
  expect(controller.command).toHaveBeenCalledOnce();
  expect(controller.retryCommand).not.toHaveBeenCalled();
});

it('retries an uncertain receipt with the unchanged command and nonce despite a fresh review', async () => {
  const { entry, controller, reviewed } = fixture();
  const { value, evidence } = await reviewed();
  controller.command.mockRejectedValueOnce(new Error('lost response'));
  await expect(entry.api.start(value, evidence)).rejects.toThrow();
  const original = structuredClone(controller.command.mock.calls[0][1]);
  controller.receipt.mockResolvedValue({
    status: 'partial',
    command_id: 'start-one',
  });
  controller.retryCommand.mockResolvedValue(outcome(original));
  controller.reviewWorkspaceProcess.mockImplementationOnce(async () => ({
    ...snapshot,
    command_id: 'start-one',
    command: value.command,
    conversation_revision: '2',
    policy_revision: 'new-policy',
    action_digest: 'new-digest',
    nonce: 'new-nonce',
    policy_decision: 'ask',
    approval_required: true,
  }));
  const fresh = await entry.api.review(value);
  await entry.api.start(value, fresh);
  expect(controller.retryCommand).toHaveBeenCalledWith(
    'chat',
    original,
    'start-one',
  );
});

it('a definitive rejected original receipt is never redispatched', async () => {
  const { entry, controller, reviewed } = fixture();
  const { value, evidence } = await reviewed();
  controller.command.mockRejectedValueOnce(new Error('lost response'));
  await expect(entry.api.start(value, evidence)).rejects.toThrow();
  controller.receipt.mockResolvedValue({
    command_id: 'start-one',
    status: 'rejected',
    code: 'action_denied',
  });
  await expect(entry.api.start(value, evidence)).rejects.toMatchObject({
    code: 'action_denied',
  });
  await expect(async () =>
    entry.api.start(value, evidence),
  ).rejects.toMatchObject({ code: 'action_denied' });
  expect(controller.retryCommand).not.toHaveBeenCalled();
  expect(controller.command).toHaveBeenCalledOnce();
});

it('Stop settles independently while Start is pending and late Start cannot undo quiescence', async () => {
  const { entry, controller, reviewed } = fixture();
  const { value, evidence } = await reviewed();
  const pending = deferred<CommandReceipt>();
  controller.command.mockReturnValueOnce(pending.promise);
  const start = entry.api.start(value, evidence);
  const command = controller.command.mock.calls[0][1];
  const stopped = await entry.api.stop(value.command_id);
  expect(stopped.quiesced).toBe(true);
  pending.resolve(outcome(command));
  expect((await start).quiesced).toBe(true);
  expect((await entry.api.start(value, evidence)).quiesced).toBe(true);
  expect(controller.command).toHaveBeenCalledTimes(2);
});

it('purges authentication state permanently and rejects late pending results', async () => {
  const { entry, controller, reviewed, state, emit, owner } = fixture();
  const { value, evidence } = await reviewed();
  entry.session.update((current) => ({
    ...current,
    draft: 'private draft',
    attempt: value,
  }));
  const pending = deferred<CommandReceipt>();
  controller.command.mockReturnValueOnce(pending.promise);
  const start = entry.api.start(value, evidence);
  const original = controller.command.mock.calls[0][1];
  state.handshake = null;
  emit();
  pending.resolve(outcome(original));
  await expect(start).rejects.toMatchObject({
    code: 'authentication_required',
  });
  entry.session.update((current) => ({
    ...current,
    draft: 'late private draft',
    attempt: value,
  }));
  expect(entry.session.getSnapshot()).toMatchObject({
    draft: '',
    attempt: null,
    processes: [],
    snapshot: null,
    revoked: true,
  });
  state.handshake = {
    instance_id: 'instance',
    server_epoch: 'epoch',
    client_session_id: 'replacement',
  };
  emit();
  const replacement = owner.forResource('chat', state.workspace.resources[0]);
  expect(replacement).not.toBe(entry);
  expect(replacement?.session.getSnapshot().draft).toBe('');
});

it('binding ABA cannot revive a revoked entry and guards read-to-retry races without a notification', async () => {
  const { entry, controller, reviewed, state, emit, owner } = fixture();
  const { value, evidence } = await reviewed();
  controller.command.mockRejectedValueOnce(new Error('lost response'));
  await expect(entry.api.start(value, evidence)).rejects.toThrow();
  const receipt = deferred<CommandReceipt>();
  controller.receipt.mockReturnValueOnce(receipt.promise);
  const retry = entry.api.start(value, evidence);
  state.workspace.resources = [];
  receipt.resolve({
    command_id: 'start-one',
    status: 'partial',
  } as CommandReceipt);
  await expect(retry).rejects.toThrow();
  expect(controller.retryCommand).not.toHaveBeenCalled();
  emit();
  state.workspace.resources = [structuredClone(resource)];
  emit();
  expect(owner.forResource('chat', state.workspace.resources[0])).toBeNull();
  expect(entry.session.getSnapshot().revoked).toBe(true);
});

it.each(['command_id', 'resource_id', 'binding_revision'])(
  'rejects a receipt with a foreign %s',
  async (field) => {
    const { entry, controller, reviewed } = fixture();
    const { value, evidence } = await reviewed();
    controller.command.mockImplementationOnce(async (_conversation, command) =>
      outcome(command, { [field]: 'foreign' }),
    );
    await expect(entry.api.start(value, evidence)).rejects.toThrow();
  },
);

it('enforces eight scope capacity without evicting retained drafts', () => {
  const { owner, entry, state } = fixture();
  entry.session.update((current) => ({ ...current, draft: 'retained' }));
  for (let index = 1; index <= 8; index++) {
    const next = {
      ...resource,
      binding: { ...resource.binding, binding_id: 'binding-' + index },
    } as ResourceView;
    state.workspace.resources.push(next);
    const retained = owner.forResource('chat', next);
    expect(!!retained).toBe(index < 8);
    retained?.session.update((current) => ({
      ...current,
      draft: 'retained ' + index,
    }));
  }
  expect(entry.session.getSnapshot().draft).toBe('retained');
  expect(owner.hasRetained()).toBe(true);
});

it('bounds uncertain Starts while preserving independent cleanup capacity', async () => {
  const { entry, controller, reviewed } = fixture();
  controller.command.mockRejectedValue(new Error('unknown effect'));
  for (let index = 0; index < 32; index++) {
    const { value, evidence } = await reviewed('start-' + index);
    await expect(entry.api.start(value, evidence)).rejects.toThrow();
  }
  const excess = await reviewed('excess');
  await expect(async () =>
    entry.api.start(excess.value, excess.evidence),
  ).rejects.toMatchObject({ code: 'process_limit' });
  controller.command.mockImplementation(async (_conversation, command) =>
    outcome(command),
  );
  expect((await entry.api.stop('start-0')).quiesced).toBe(true);
  expect(controller.command).toHaveBeenCalledTimes(33);
});

it('reuses capacity only from clean scopes and counts detached pending adapter effects as retained', async () => {
  const { owner, entry, state, controller, reviewed } = fixture();
  const { value, evidence } = await reviewed();
  controller.command.mockRejectedValueOnce(new Error('unknown admission'));
  await expect(entry.api.start(value, evidence)).rejects.toThrow();
  expect(owner.hasRetained()).toBe(true);
  for (let index = 1; index < 12; index++) {
    const next = {
      ...resource,
      binding: { ...resource.binding, binding_id: 'clean-' + index },
    } as ResourceView;
    state.workspace.resources.push(next);
    expect(owner.forResource('chat', next)).not.toBeNull();
  }
  expect(owner.forResource('chat', state.workspace.resources[0])).toBe(entry);
});

it('cleanup retries retain their original command after a workspace revision change', async () => {
  const { entry, controller } = fixture();
  controller.command.mockRejectedValueOnce(new Error('lost cleanup response'));
  await expect(entry.api.stop('owned')).rejects.toThrow();
  const original = structuredClone(controller.command.mock.calls[0][1]);
  entry.session.update((current) => ({
    ...current,
    snapshot: { ...snapshot, resource_revision: 'later' },
  }));
  controller.receipt.mockResolvedValue({
    command_id: original.command_id,
    status: 'partial',
  });
  controller.retryCommand.mockResolvedValue(outcome(original));
  await entry.api.stop('owned');
  expect(controller.retryCommand).toHaveBeenCalledWith(
    'chat',
    original,
    original.command_id,
  );
});

it('rejects stale caller resources, switched conversations and wrong review evidence before Start', async () => {
  const { entry, owner, state, reviewed, controller } = fixture();
  const { value, evidence } = await reviewed();
  await expect(
    entry.api.start(value, { ...evidence, binding_id: 'foreign' }),
  ).rejects.toThrow();
  expect(
    owner.forResource('chat', {
      ...resource,
      binding: { ...resource.binding, resource_id: 'foreign' },
    } as ResourceView),
  ).toBeNull();
  state.selectedConversationId = 'other';
  await expect(async () =>
    entry.api.start(value, evidence),
  ).rejects.toMatchObject({ code: 'resource_binding_revoked' });
  expect(controller.command).not.toHaveBeenCalled();
});
