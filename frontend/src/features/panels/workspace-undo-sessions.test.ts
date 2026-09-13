import { createElement } from 'react';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ResourceView } from '../../api/types';
import WorkspaceUndo, {
  type WorkspaceUndoResult,
  type WorkspaceUndoReview,
} from './WorkspaceUndo';
import { createWorkspaceUndoSessions } from './workspace-undo-sessions';

afterEach(cleanup);

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function fixture() {
  const resource = {
    available: true,
    resource_revision: 'resource',
    binding: {
      kind: 'workspace',
      resource_id: 'workspace',
      binding_id: 'binding',
      revision: '1',
    },
  } as ResourceView;
  const state: {
    handshake: {
      instance_id: string;
      server_epoch: string;
      client_session_id: string;
    } | null;
    selectedConversationId: string;
    loadingConversation: boolean;
    workspace: { conversation_id: string; resources: ResourceView[] } | null;
  } = {
    handshake: {
      instance_id: 'instance',
      server_epoch: 'epoch',
      client_session_id: 'session',
    },
    selectedConversationId: 'chat',
    loadingConversation: false,
    workspace: { conversation_id: 'chat', resources: [resource] },
  };
  const listeners = new Set<() => void>();
  const review: WorkspaceUndoReview = {
    resource_id: 'workspace',
    conversation_id: 'chat',
    resource_revision: 'resource',
    binding_id: 'binding',
    binding_revision: '1',
    change_set_id: 'change',
    change_set_revision: 'a'.repeat(64),
    host_revision: 'b'.repeat(64),
    policy_revision: 'c'.repeat(64),
    policy_decision: 'ask',
    approval_required: true,
    files: ['file.txt'],
    directories_retained: ['retained-folder'],
    action_digest: 'd'.repeat(64),
    nonce: 'original-nonce',
  };
  const result = (
    command: string,
    change = 'change',
    partial = false,
  ): WorkspaceUndoResult => ({
    command_id: command,
    resource_id: 'workspace',
    conversation_id: 'chat',
    change_set_id: change,
    status: partial ? 'partial' : 'undone',
    files_restored: partial ? [] : ['file.txt'],
    ledger_saved: !partial,
    reverted: !partial,
    code: partial ? 'workspace_undo_unconfirmed' : '',
  });
  const controller = {
    getSnapshot: () => state,
    subscribe: (fn: () => void) => {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
    reviewWorkspaceUndo: vi.fn(
      async (
        _conversation: string,
        _binding: string,
        change: string,
        _signal?: AbortSignal,
      ) => ({
        ...review,
        files: [...review.files],
        directories_retained: [...review.directories_retained],
        change_set_id: change,
      }),
    ),
    reviewWorkspaceUndoRecovery: vi.fn(
      async (_conversation: string, _binding: string, _command: string) => ({
        ...review,
        nonce: 'renewed-nonce',
      }),
    ),
    executeWorkspaceUndo: vi.fn(
      async (
        _conversation: string,
        _binding: string,
        captured: WorkspaceUndoReview,
        command: string,
      ) => result(command, captured.change_set_id),
    ),
    workspaceUndoReceipt: vi.fn(
      async (
        _conversation: string,
        _binding: string,
        command: string,
        _signal?: AbortSignal,
      ): Promise<WorkspaceUndoResult | null> => result(command),
    ),
  };
  const owner = createWorkspaceUndoSessions(
    controller as unknown as ClientController,
  );
  return {
    owner,
    controller,
    state,
    resource,
    review,
    result,
    listeners,
    notify: () => listeners.forEach((fn) => fn()),
    entry: (id = 'change') => owner.forChangeSet('chat', resource, id)!,
  };
}

it('keeps one session per exact change set and denies IO while another conversation is selected', async () => {
  const f = fixture(),
    entry = f.entry();
  await entry.session.review(entry.api);
  const other = f.entry('second');
  expect(other).not.toBe(entry);
  expect(other.scope).not.toBe(entry.scope);
  expect(other.session.getSnapshot().review).toBeNull();
  f.state.selectedConversationId = 'elsewhere';
  f.state.workspace = { conversation_id: 'elsewhere', resources: [] };
  f.notify();
  await expect(entry.api.apply(f.review, 'original')).rejects.toThrow(
    'resource_binding_revoked',
  );
  expect(f.controller.executeWorkspaceUndo).not.toHaveBeenCalled();
  expect(entry.session.getSnapshot().review).toEqual(f.review);
  f.state.selectedConversationId = 'chat';
  f.state.workspace = { conversation_id: 'chat', resources: [f.resource] };
  f.notify();
  expect(f.entry()).toBe(entry);
  f.owner.dispose();
});

it('retains actual component state on remount and retries only the original command with a renewed nonce', async () => {
  const f = fixture(),
    entry = f.entry();
  f.controller.executeWorkspaceUndo.mockImplementation(
    async (_conversation, _binding, review, command) =>
      f.result(command, review.change_set_id, true),
  );
  const first = render(
    createElement(WorkspaceUndo, { ...entry.api, session: entry.session }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Review Undo' }));
  fireEvent.click(
    await screen.findByRole('button', { name: 'Undo these changes' }),
  );
  await screen.findByText(
    'Undo is incomplete. Keep this original operation for recovery.',
  );
  const original = f.controller.executeWorkspaceUndo.mock.calls[0][3];
  expect(f.owner.hasRetained()).toBe(true);
  first.unmount();
  expect(f.entry()).toBe(entry);
  f.controller.executeWorkspaceUndo.mockImplementation(
    async (_conversation, _binding, review, command) =>
      f.result(command, review.change_set_id),
  );
  render(
    createElement(WorkspaceUndo, { ...entry.api, session: entry.session }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Retry original Undo' }));
  await screen.findByText(
    'Original files restored. Created directories remain.',
  );
  expect(f.controller.reviewWorkspaceUndo).toHaveBeenCalledTimes(1);
  expect(f.controller.reviewWorkspaceUndoRecovery).toHaveBeenCalledWith(
    'chat',
    'binding',
    original,
  );
  expect(f.controller.executeWorkspaceUndo).toHaveBeenLastCalledWith(
    'chat',
    'binding',
    { ...f.review, nonce: 'renewed-nonce' },
    original,
  );
  expect(f.owner.hasRetained()).toBe(false);
  f.owner.dispose();
});

it('settles a single in-flight success after navigation and component unmount', async () => {
  const f = fixture(),
    entry = f.entry(),
    response = deferred<WorkspaceUndoResult>();
  f.controller.executeWorkspaceUndo.mockReturnValue(response.promise);
  const view = render(
    createElement(WorkspaceUndo, { ...entry.api, session: entry.session }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Review Undo' }));
  const confirm = await screen.findByRole('button', {
    name: 'Undo these changes',
  });
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  expect(f.controller.executeWorkspaceUndo).toHaveBeenCalledTimes(1);
  const command = f.controller.executeWorkspaceUndo.mock.calls[0][3];
  view.unmount();
  f.state.selectedConversationId = 'elsewhere';
  f.state.workspace = null;
  f.notify();
  await act(async () => {
    response.resolve(f.result(command));
    await response.promise;
  });
  expect(entry.session.getSnapshot().result?.status).toBe('undone');
  expect(entry.session.getSnapshot().pending).toBeNull();
  expect(f.owner.hasRetained()).toBe(false);
  f.owner.dispose();
});

it.each(['instance_id', 'server_epoch', 'client_session_id'] as const)(
  'purges private review and late settlement on %s change',
  async (field) => {
    const f = fixture(),
      entry = f.entry(),
      response = deferred<WorkspaceUndoResult>();
    await entry.session.review(entry.api);
    f.controller.executeWorkspaceUndo.mockReturnValue(response.promise);
    const pending = entry.session.execute(entry.api);
    const command = f.controller.executeWorkspaceUndo.mock.calls[0][3];
    f.state.handshake![field] = 'replacement';
    f.notify();
    expect(entry.session.getSnapshot().active).toBe(false);
    expect(entry.session.getSnapshot().review).toBeNull();
    response.resolve(f.result(command));
    await pending;
    expect(entry.session.getSnapshot().result).toBeNull();
    expect(f.owner.hasRetained()).toBe(false);
    await expect(entry.api.receipt(command)).rejects.toThrow(
      'authentication_required',
    );
    expect(f.controller.workspaceUndoReceipt).not.toHaveBeenCalled();
    expect(f.entry().session).not.toBe(entry.session);
    f.owner.dispose();
  },
);

it.each(['removed', 'revision', 'resource', 'unavailable'] as const)(
  'retires the captured binding after %s without resurrecting its original session',
  async (change) => {
    const f = fixture(),
      entry = f.entry();
    await entry.session.review(entry.api);
    const old = { ...f.resource, binding: { ...f.resource.binding } };
    if (change === 'removed') f.state.workspace!.resources = [];
    else if (change === 'revision') f.resource.binding.revision = '2';
    else if (change === 'resource')
      f.resource.binding.resource_id = 'replacement';
    else f.resource.available = false;
    f.notify();
    expect(entry.session.getSnapshot().active).toBe(false);
    await expect(entry.api.apply(f.review, 'original')).rejects.toThrow(
      'authentication_required',
    );
    expect(f.controller.executeWorkspaceUndo).not.toHaveBeenCalled();
    f.state.workspace!.resources = [old];
    f.notify();
    expect(f.owner.forChangeSet('chat', old, 'change')).toBeNull();
    f.owner.dispose();
  },
);

it.each([
  { resource_revision: 'different' },
  { change_set_revision: 'different' },
  { host_revision: 'different' },
  { policy_revision: 'different' },
  { policy_decision: 'allow' as const },
  { approval_required: false },
  { files: ['different.txt'] },
  { directories_retained: [] },
  { action_digest: 'different' },
  { change_set_id: 'other' },
  { resource_id: 'other' },
  { conversation_id: 'other' },
  { binding_id: 'other' },
  { binding_revision: '2' },
])(
  'refuses recovery when any retained review field changes: %j',
  async (change) => {
    const f = fixture(),
      entry = f.entry();
    f.controller.reviewWorkspaceUndoRecovery.mockResolvedValue({
      ...f.review,
      ...change,
      nonce: 'fresh',
    });
    await expect(
      entry.api.recover(f.review, 'original-command'),
    ).rejects.toThrow();
    expect(f.controller.executeWorkspaceUndo).not.toHaveBeenCalled();
    f.owner.dispose();
  },
);

it('accepts only a nonce renewal even if object key order differs and captures intent before awaiting', async () => {
  const f = fixture(),
    entry = f.entry(),
    renewed = deferred<WorkspaceUndoReview>();
  const original = { ...f.review, files: [...f.review.files] };
  f.controller.reviewWorkspaceUndoRecovery.mockReturnValue(renewed.promise);
  const pending = entry.api.recover(original, 'original-command');
  original.files[0] = 'mutable caller change';
  const reordered = Object.fromEntries(
    Object.entries({ ...f.review, nonce: 'fresh' }).reverse(),
  ) as WorkspaceUndoReview;
  renewed.resolve(reordered);
  await pending;
  expect(f.controller.executeWorkspaceUndo).toHaveBeenCalledWith(
    'chat',
    'binding',
    reordered,
    'original-command',
  );
  f.owner.dispose();
});

it('never evicts retained or pending reviews at capacity and reclaims only a settled slot', async () => {
  const f = fixture();
  const entries = [];
  for (let index = 0; index < 8; index++) {
    const entry = f.entry(`change-${index}`);
    await entry.session.review(entry.api);
    entries.push(entry);
  }
  expect(f.owner.forChangeSet('chat', f.resource, 'ninth')).toBeNull();
  expect(entries.every((entry) => entry.session.getSnapshot().active)).toBe(
    true,
  );
  entries[2].session.cancelReview();
  const ninth = f.owner.forChangeSet('chat', f.resource, 'ninth');
  expect(ninth).not.toBeNull();
  expect(entries[2].session.getSnapshot().active).toBe(false);
  expect(
    entries
      .filter((_, index) => index !== 2)
      .every((entry) => entry.session.hasRetained()),
  ).toBe(true);
  f.owner.dispose();
});

it('counts an in-flight review as retained for capacity and rejects a different change-set request', async () => {
  const f = fixture(),
    response = deferred<WorkspaceUndoReview>();
  f.controller.reviewWorkspaceUndo.mockReturnValue(response.promise);
  const pending = [];
  for (let index = 0; index < 8; index++) {
    const entry = f.entry(`change-${index}`);
    pending.push(entry.session.review(entry.api));
  }
  expect(f.owner.hasRetained()).toBe(true);
  expect(f.owner.forChangeSet('chat', f.resource, 'ninth')).toBeNull();
  const entry = f.entry('change-0');
  await expect(entry.api.review('wrong')).rejects.toThrow(
    'resource_binding_revoked',
  );
  expect(f.controller.reviewWorkspaceUndo).toHaveBeenCalledTimes(8);
  f.owner.dispose();
  response.resolve(f.review);
  await Promise.all(pending);
  expect(f.owner.hasRetained()).toBe(false);
});

it('keeps a missing or wrong-target receipt inert and forwards abort signals', async () => {
  const f = fixture(),
    entry = f.entry();
  f.controller.executeWorkspaceUndo.mockImplementation(
    async (_conversation, _binding, review, command) =>
      f.result(command, review.change_set_id, true),
  );
  await entry.session.review(entry.api);
  await entry.session.execute(entry.api);
  const original = entry.session.getSnapshot().pending!;
  f.controller.workspaceUndoReceipt.mockResolvedValue(null);
  await entry.session.check(entry.api);
  expect(entry.session.getSnapshot().pending).toEqual(original);
  f.controller.workspaceUndoReceipt.mockResolvedValue(
    f.result('wrong-command'),
  );
  await entry.session.check(entry.api);
  expect(entry.session.getSnapshot().pending).toEqual(original);
  expect(entry.session.getSnapshot().result?.status).toBe('partial');
  expect(f.controller.workspaceUndoReceipt.mock.calls[0][3]).toBeInstanceOf(
    AbortSignal,
  );
  expect(f.controller.executeWorkspaceUndo).toHaveBeenCalledTimes(1);
  f.owner.dispose();
});

it('purges on sign-out, unsubscribes on dispose, and cannot create stale entries', async () => {
  const f = fixture(),
    entry = f.entry();
  await entry.session.review(entry.api);
  f.state.handshake = null;
  f.notify();
  expect(entry.session.getSnapshot().active).toBe(false);
  expect(f.owner.forChangeSet('chat', f.resource, 'change')).toBeNull();
  expect(f.owner.hasRetained()).toBe(false);
  f.owner.dispose();
  expect(f.listeners.size).toBe(0);
  expect(f.owner.forChangeSet('chat', f.resource, 'new')).toBeNull();
});

it('passively remounts the most recently selected retained change set after panel selection is lost', async () => {
  const f = fixture();
  expect(f.owner.retainedForResource('chat', f.resource)).toBeNull();
  const first = f.entry('first');
  await first.session.review(first.api);
  const second = f.entry('second');
  await second.session.review(second.api);
  expect(f.owner.retainedForResource('chat', f.resource)).toBe(second);
  expect(f.entry('first')).toBe(first);
  const calls = f.controller.reviewWorkspaceUndo.mock.calls.length;
  const restored = f.owner.retainedForResource('chat', f.resource)!;
  expect(restored).toBe(first);
  const view = render(
    createElement(WorkspaceUndo, {
      ...restored.api,
      session: restored.session,
    }),
  );
  expect(screen.getByText('Change set: first')).toBeInTheDocument();
  view.unmount();
  expect(f.owner.retainedForResource('chat', f.resource)).toBe(first);
  expect(f.controller.reviewWorkspaceUndo).toHaveBeenCalledTimes(calls);
  expect(f.controller.executeWorkspaceUndo).not.toHaveBeenCalled();
  first.session.cancelReview();
  expect(f.owner.retainedForResource('chat', f.resource)).toBe(second);
  f.state.selectedConversationId = 'elsewhere';
  f.notify();
  expect(f.owner.retainedForResource('chat', f.resource)).toBeNull();
  f.state.selectedConversationId = 'chat';
  f.state.loadingConversation = true;
  expect(f.owner.retainedForResource('chat', f.resource)).toBeNull();
  f.state.loadingConversation = false;
  f.state.handshake = null;
  f.notify();
  expect(f.owner.retainedForResource('chat', f.resource)).toBeNull();
  f.owner.dispose();
});
