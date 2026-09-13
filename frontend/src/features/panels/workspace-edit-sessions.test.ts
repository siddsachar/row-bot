import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  WorkspaceEditableFile,
  WorkspaceEditResult,
} from '../../api/types';
import { createWorkspaceEditSessions } from './workspace-edit-sessions';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const file: WorkspaceEditableFile = {
  review_token: 'a'.repeat(64),
  resource_id: 'workspace',
  conversation_id: 'chat',
  relative_path: 'hello.txt',
  resource_revision: '1',
  binding_id: 'binding',
  binding_revision: '2',
  target: 'workspace',
  status: 'text',
  content: 'Original',
  digest: 'a'.repeat(64),
};
const saved: WorkspaceEditResult = {
  ...file,
  status: 'saved',
  digest: 'b'.repeat(64),
  file_saved: true,
  ledger_saved: true,
};
function fixture(capacity = 8) {
  const state = {
    selectedConversationId: 'chat',
    loadingConversation: false,
    handshake: {
      client_session_id: 'session',
      server_epoch: 'epoch',
      instance_id: 'instance',
    } as {
      client_session_id: string;
      server_epoch: string;
      instance_id: string;
    } | null,
    workspace: {
      conversation_id: 'chat',
      revision: '3',
      resources: [
        {
          available: true,
          resource_revision: '1',
          binding: {
            binding_id: 'binding',
            revision: '2',
            kind: 'workspace',
            resource_id: 'workspace',
          },
        },
      ],
    },
  };
  const listeners = new Set<() => void>();
  const controller = {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    workspaceEditableFile: vi.fn(async (_conversation, _binding, path) => ({
      ...file,
      relative_path: path,
    })),
    command: vi
      .fn()
      .mockResolvedValue({ status: 'completed', workspace_edit: saved }),
    receipt: vi.fn(),
    retryCommand: vi.fn(),
  };
  const owner = createWorkspaceEditSessions(
    controller as unknown as ClientController,
    { capacity },
  );
  const scope = owner.forBinding('chat', 'binding');
  return {
    owner,
    scope,
    controller,
    state,
    emit: () => listeners.forEach((listener) => listener()),
    listeners,
  };
}

it('restores draft and exact snapshot through a newly constructed binding scope', async () => {
  const { owner, scope, controller } = fixture();
  scope.open('hello.txt');
  const session = scope.getSnapshot().session!;
  await session.load();
  session.setDraft('Retained draft');
  scope.close();
  const remount = owner.forBinding('chat', 'binding');
  expect(remount.getSnapshot().session).toBe(session);
  expect(remount.getSnapshot().open).toBe(false);
  remount.open('hello.txt');
  await session.load();
  expect(session.getSnapshot()).toMatchObject({
    draft: 'Retained draft',
    snapshot: file,
  });
  expect(controller.workspaceEditableFile).toHaveBeenCalledOnce();
  owner.dispose();
});

it('retains one exact in-flight promise and settles successful save with no mounted view', async () => {
  const { owner, scope, controller } = fixture();
  const response = deferred<{
    status: string;
    workspace_edit: WorkspaceEditResult;
  }>();
  controller.command.mockReturnValue(response.promise);
  scope.open('hello.txt');
  const session = scope.getSnapshot().session!;
  await session.load();
  session.setDraft('Saved after unmount');
  const operation = session.commit();
  expect(session.commit()).toBe(operation);
  scope.close();
  await Promise.resolve();
  response.resolve({ status: 'completed', workspace_edit: saved });
  await operation;
  const restored = owner.forBinding('chat', 'binding').getSnapshot().session!;
  expect(restored.getSnapshot()).toMatchObject({
    draft: null,
    busy: false,
    uncertain: false,
    snapshot: { content: 'Saved after unmount', digest: saved.digest },
  });
  expect(controller.command).toHaveBeenCalledOnce();
  owner.dispose();
});

it('keeps original body and ID through a lost response and fresh scope retry', async () => {
  const { owner, scope, controller } = fixture();
  controller.command.mockRejectedValue(new TypeError('lost response'));
  scope.open('hello.txt');
  const session = scope.getSnapshot().session!;
  await session.load();
  session.setDraft('Original submitted body');
  await session.commit();
  expect(session.getSnapshot().uncertain).toBe(true);
  const original = structuredClone(controller.command.mock.calls[0]);
  const restored = owner.forBinding('chat', 'binding');
  restored.open('hello.txt');
  expect(restored.discard()).toBe(false);
  session.setDraft('Different body');
  expect(session.getSnapshot().draft).toBe('Original submitted body');
  controller.receipt.mockRejectedValue({ code: 'not_found' });
  controller.retryCommand.mockResolvedValue({
    status: 'completed',
    workspace_edit: saved,
  });
  await restored.getSnapshot().session!.commit();
  expect(controller.retryCommand).toHaveBeenCalledWith(...original);
  expect(session.getSnapshot().uncertain).toBe(false);
  owner.dispose();
});

it('redacts a switched conversation but restores its draft under the same binding', async () => {
  const { owner, scope, controller, state, emit } = fixture();
  scope.open('hello.txt');
  const session = scope.getSnapshot().session!;
  await session.load();
  session.setDraft('Private draft');
  state.selectedConversationId = 'other';
  emit();
  expect(scope.getSnapshot().session).toBeNull();
  expect(session.getSnapshot()).toMatchObject({
    accessible: false,
    snapshot: null,
    draft: null,
    path: '',
  });
  await session.commit();
  expect(controller.command).not.toHaveBeenCalled();
  state.selectedConversationId = 'chat';
  emit();
  expect(scope.getSnapshot().session).toBe(session);
  expect(session.getSnapshot().draft).toBe('Private draft');
  owner.dispose();
});

it('detach and same-ID rebind never revive the old private edit', async () => {
  const { owner, scope, controller, state, emit } = fixture();
  scope.open('hello.txt');
  const session = scope.getSnapshot().session!;
  await session.load();
  session.setDraft('Old binding draft');
  state.workspace.resources = [];
  emit();
  state.workspace.resources = [
    {
      available: true,
      resource_revision: '1',
      binding: {
        binding_id: 'binding',
        revision: '2',
        kind: 'workspace',
        resource_id: 'workspace',
      },
    },
  ];
  emit();
  expect(scope.getSnapshot().session).toBeNull();
  expect(scope.open('hello.txt')).toBe(false);
  await session.commit();
  expect(controller.command).not.toHaveBeenCalled();
  state.workspace.resources[0].binding.revision = '3';
  emit();
  expect(scope.open('hello.txt')).toBe(true);
  expect(scope.getSnapshot().session).not.toBe(session);
  expect(session.getSnapshot().draft).toBeNull();
  owner.dispose();
});

it('auth loss clears contents and late completion cannot resurrect them under renewed auth', async () => {
  const { owner, scope, controller, state, emit } = fixture();
  const response = deferred<{
    status: string;
    workspace_edit: WorkspaceEditResult;
  }>();
  controller.command.mockReturnValue(response.promise);
  scope.open('hello.txt');
  const session = scope.getSnapshot().session!;
  await session.load();
  session.setDraft('Private body');
  const operation = session.commit();
  await Promise.resolve();
  state.handshake = null;
  emit();
  expect(session.getSnapshot().snapshot).toBeNull();
  expect(session.path).toBe('');
  state.handshake = {
    client_session_id: 'new-session',
    server_epoch: 'epoch',
    instance_id: 'instance',
  };
  emit();
  response.resolve({ status: 'completed', workspace_edit: saved });
  await operation;
  expect(session.getSnapshot().snapshot).toBeNull();
  expect(scope.getSnapshot().session).toBeNull();
  scope.open('hello.txt');
  await scope.getSnapshot().session!.load();
  expect(scope.getSnapshot().session!.getSnapshot().draft).toBeNull();
  expect(scope.getSnapshot().session).not.toBe(session);
  owner.dispose();
});

it('never evicts dirty or uncertain records at capacity; explicit safe discard releases a slot', async () => {
  const { owner, scope, controller } = fixture(2);
  scope.open('hello.txt');
  const first = scope.getSnapshot().session!;
  await first.load();
  first.setDraft('Dirty');
  scope.open('second.txt');
  const second = scope.getSnapshot().session!;
  await second.load();
  second.setDraft('Pending');
  controller.command.mockRejectedValue(new TypeError('lost'));
  await second.commit();
  expect(scope.open('third.txt')).toBe(false);
  expect(scope.getSnapshot().capacity).toBe(true);
  expect(first.getSnapshot().draft).toBe('Dirty');
  expect(second.getSnapshot().uncertain).toBe(true);
  expect(scope.discard()).toBe(false);
  scope.open('hello.txt');
  expect(scope.discard()).toBe(true);
  expect(scope.open('third.txt')).toBe(true);
  expect(second.getSnapshot().uncertain).toBe(true);
  owner.dispose();
});

it('a same-resource revision change keeps the draft inert until explicit refresh', async () => {
  const { owner, scope, controller, state, emit } = fixture();
  scope.open('hello.txt');
  const session = scope.getSnapshot().session!;
  await session.load();
  session.setDraft('Retained');
  state.workspace.resources[0].resource_revision = 'new';
  emit();
  expect(session.getSnapshot().stale).toBe(true);
  await session.commit();
  expect(controller.command).not.toHaveBeenCalled();
  controller.workspaceEditableFile.mockResolvedValue({
    ...file,
    resource_revision: 'new',
    content: 'External',
    digest: 'c'.repeat(64),
  });
  await session.load(true);
  expect(session.getSnapshot()).toMatchObject({
    stale: false,
    draft: 'Retained',
    snapshot: { content: 'External' },
  });
  owner.dispose();
});

it.each([
  { resource_id: 'other-workspace' },
  { conversation_id: 'other-chat' },
  { binding_id: 'other-binding' },
  { binding_revision: '999' },
  { relative_path: 'other.txt' },
])(
  'rejects a loaded snapshot with mismatched identity %j before exposing content',
  async (identity) => {
    const { owner, scope, controller } = fixture();
    controller.workspaceEditableFile.mockResolvedValue({
      ...file,
      ...identity,
      content: 'Must not appear',
    });
    scope.open('hello.txt');
    const session = scope.getSnapshot().session!;
    await session.load();
    expect(session.getSnapshot().snapshot).toBeNull();
    expect(session.getSnapshot().error).not.toBe('');
    await session.commit();
    expect(controller.command).not.toHaveBeenCalled();
    owner.dispose();
  },
);

it('clears all public references on disposal', async () => {
  const { owner, scope, controller, listeners } = fixture();
  scope.open('hello.txt');
  const session = scope.getSnapshot().session!;
  await session.load();
  session.setDraft('Retained');
  owner.dispose();
  expect(listeners.size).toBe(0);
  expect(scope.getSnapshot().session).toBeNull();
  expect(session.path).toBe('');
  expect(session.getSnapshot().draft).toBeNull();
  await session.commit();
  expect(controller.command).not.toHaveBeenCalled();
});

it('hasRetained tracks dirty, busy and uncertain intent until known resolution or discard', async () => {
  const { owner, scope, controller } = fixture();
  scope.open('hello.txt');
  const session = scope.getSnapshot().session!;
  await session.load();
  expect(owner.hasRetained()).toBe(false);
  session.setDraft('Unsaved');
  expect(owner.hasRetained()).toBe(true);
  const response = deferred<{
    status: string;
    workspace_edit: WorkspaceEditResult;
  }>();
  controller.command.mockReturnValue(response.promise);
  const operation = session.commit();
  await Promise.resolve();
  expect(owner.hasRetained()).toBe(true);
  response.resolve({
    status: 'partial',
    workspace_edit: { ...saved, status: 'partial' },
  });
  await operation;
  expect(owner.hasRetained()).toBe(true);
  expect(scope.discard()).toBe(false);
  controller.receipt.mockResolvedValue({
    status: 'completed',
    workspace_edit: saved,
  });
  await session.commit();
  expect(owner.hasRetained()).toBe(false);
  session.setDraft('Another draft');
  expect(owner.hasRetained()).toBe(true);
  expect(scope.discard()).toBe(true);
  expect(owner.hasRetained()).toBe(false);
  owner.dispose();
});
