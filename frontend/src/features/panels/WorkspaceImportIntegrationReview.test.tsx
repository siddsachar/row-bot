import { act, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ResourceView, WorkspaceImportReview } from '../../api/types';
import WorkspaceImports, {
  type WorkspaceImportResult,
} from './WorkspaceImports';
import { createWorkspaceImportSessions } from './workspace-import-sessions';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
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
  const state = {
    handshake: {
      instance_id: 'instance',
      server_epoch: 'epoch',
      client_session_id: 'session',
    },
    selectedConversationId: 'chat',
    loadingConversation: false,
    workspace: { conversation_id: 'chat', resources: [resource] },
  };
  const review = {
    resource_id: 'workspace',
    conversation_id: 'chat',
    resource_revision: 'resource',
    binding_id: 'binding',
    binding_revision: '1',
    pending_change_id: 'pending',
    pending_revision: 'a'.repeat(64),
    patch_digest: 'b'.repeat(64),
    host_revision: 'c'.repeat(64),
    git_policy_revision: 'd'.repeat(64),
    policy_revision: 'e'.repeat(64),
    policy_decision: 'ask',
    approval_required: true,
    files: ['file.txt'],
    directories: [],
    action_digest: 'f'.repeat(64),
    nonce: 'nonce',
  } as WorkspaceImportReview;
  const row = {
    pending_change_id: 'pending',
    revision: review.pending_revision,
    file_count: 1,
    imported: false,
    created_at: '',
  };
  const listeners = new Set<() => void>();
  const result = (command: string): WorkspaceImportResult => ({
    command_id: command,
    resource_id: 'workspace',
    conversation_id: 'chat',
    pending_change_id: 'pending',
    status: 'imported',
    files_applied: ['file.txt'],
    change_set_id: 'ledger',
    ledger_saved: true,
    imported: true,
  });
  const controller = {
    getSnapshot: () => state,
    subscribe: (callback: () => void) => {
      listeners.add(callback);
      return () => listeners.delete(callback);
    },
    workspaceImports: vi.fn(async () => ({
      items: [row],
      snapshot_revision: 'page',
      next_cursor: null,
      total: 1,
    })),
    workspaceImportPatch: vi.fn(async () => ({
      pending_change_id: 'pending',
      revision: row.revision,
      text: 'patch',
      next_offset: null,
    })),
    reviewWorkspaceImport: vi.fn(async () => review),
    reviewWorkspaceImportRecovery: vi.fn(async () => ({
      ...review,
      nonce: 'renewed',
    })),
    workspaceImportReceipt: vi.fn(async (_conversation, _binding, command) =>
      result(command),
    ),
    executeWorkspaceImport: vi.fn(
      async (_conversation, _binding, _review, command) => result(command),
    ),
  };
  const owner = createWorkspaceImportSessions(
    controller as unknown as ClientController,
  );
  const entry = owner.forResource('chat', resource)!;
  const ready = async () => {
    await entry.session.load(entry.api);
    await entry.session.select(entry.api, row);
    await entry.session.review(entry.api);
  };
  return {
    state,
    owner,
    entry,
    controller,
    ready,
    result,
    review,
    resource,
    notify: () => listeners.forEach((fn) => fn()),
  };
}

it('preserves exact original through navigation during import and receipt-only recovery', async () => {
  const f = fixture(),
    pending = deferred<WorkspaceImportResult>();
  f.controller.executeWorkspaceImport.mockImplementationOnce(
    () => pending.promise,
  );
  await f.ready();
  const work = f.entry.session.execute(f.entry.api);
  const command = f.entry.session.getSnapshot().pending!.commandId;
  f.state.selectedConversationId = 'other';
  f.notify();
  pending.resolve(f.result(command));
  await work;
  expect(f.entry.session.getSnapshot().pending?.commandId).toBe(command);
  f.state.selectedConversationId = 'chat';
  f.notify();
  await f.entry.session.check(f.entry.api);
  expect(f.entry.session.getSnapshot().result?.imported).toBe(true);
  expect(f.controller.executeWorkspaceImport).toHaveBeenCalledTimes(1);
  expect(f.controller.workspaceImportReceipt).toHaveBeenCalledWith(
    'chat',
    'binding',
    command,
    undefined,
  );
  f.owner.dispose();
});

it('purges a late import settlement on authentication change without resurrecting retained state', async () => {
  const f = fixture(),
    pending = deferred<WorkspaceImportResult>();
  f.controller.executeWorkspaceImport.mockImplementationOnce(
    () => pending.promise,
  );
  await f.ready();
  const work = f.entry.session.execute(f.entry.api);
  const command = f.entry.session.getSnapshot().pending!.commandId;
  f.state.handshake.client_session_id = 'replacement';
  f.notify();
  pending.resolve(f.result(command));
  await work;
  expect(f.entry.session.getSnapshot().active).toBe(false);
  expect(f.entry.session.getSnapshot().pending).toBeNull();
  expect(f.owner.hasRetained()).toBe(false);
  f.owner.dispose();
});

it('does not reuse an old import intent after detach and reattach to the same resource', async () => {
  const f = fixture();
  await f.ready();
  f.state.workspace.resources = [];
  f.notify();
  const replacement = {
    ...f.resource,
    binding: { ...f.resource.binding, binding_id: 'replacement' },
  };
  f.state.workspace.resources = [replacement];
  f.notify();
  expect(f.owner.forResource('chat', replacement)).not.toBe(f.entry);
  await f.entry.session.execute(f.entry.api);
  expect(f.controller.executeWorkspaceImport).not.toHaveBeenCalled();
  f.owner.dispose();
});

it('does not automatically renew approval or submit again when a response is lost', async () => {
  const f = fixture();
  await f.ready();
  f.controller.executeWorkspaceImport.mockRejectedValueOnce({
    code: 'transport_unavailable',
  });
  await f.entry.session.execute(f.entry.api);
  const command = f.entry.session.getSnapshot().pending!.commandId;
  const view = render(
    <WorkspaceImports {...f.entry.api} session={f.entry.session} />,
  );
  expect(f.controller.reviewWorkspaceImportRecovery).not.toHaveBeenCalled();
  expect(f.controller.executeWorkspaceImport).toHaveBeenCalledTimes(1);
  await act(async () => {
    await f.entry.session.execute(f.entry.api, true);
  });
  expect(f.controller.executeWorkspaceImport.mock.calls[1][2].nonce).toBe(
    'renewed',
  );
  expect(f.controller.executeWorkspaceImport.mock.calls[1][3]).toBe(command);
  view.unmount();
  f.owner.dispose();
});

it('allows a fresh explicit review after an expired unadmitted intent is authoritatively absent', async () => {
  const f = fixture();
  await f.ready();
  f.controller.executeWorkspaceImport.mockRejectedValueOnce({
    code: 'approval_expired',
    status: 409,
  });
  f.controller.workspaceImportReceipt.mockRejectedValue({
    code: 'workspace_import_not_admitted',
    status: 404,
  });
  f.controller.reviewWorkspaceImportRecovery.mockRejectedValue({
    code: 'workspace_import_unavailable',
    status: 404,
  });
  await f.entry.session.execute(f.entry.api);
  const view = render(
    <WorkspaceImports {...f.entry.api} session={f.entry.session} />,
  );
  await act(async () => {
    await f.entry.session.check(f.entry.api);
    await f.entry.session.execute(f.entry.api, true);
  });
  expect(
    screen.getByRole('button', { name: 'Reload sandbox changes' }),
  ).toBeEnabled();
  expect(f.entry.session.getSnapshot().pending).toBeNull();
  expect(f.entry.session.getSnapshot().reviewed).toBeNull();
  expect(f.controller.executeWorkspaceImport).toHaveBeenCalledTimes(1);
  view.unmount();
  f.owner.dispose();
});

it('keeps an unknown original when an absent receipt might precede delayed admission', async () => {
  const f = fixture();
  await f.ready();
  f.controller.executeWorkspaceImport.mockRejectedValueOnce({
    code: 'network_unavailable',
  });
  f.controller.workspaceImportReceipt.mockRejectedValue({
    code: 'workspace_import_not_admitted',
    status: 404,
  });
  await f.entry.session.execute(f.entry.api);
  const command = f.entry.session.getSnapshot().pending!.commandId;
  await f.entry.session.check(f.entry.api);
  expect(f.entry.session.getSnapshot().pending?.commandId).toBe(command);
  expect(f.entry.session.hasRetained()).toBe(true);
  f.owner.dispose();
});

it('keeps an expired original if its existing receipt is malformed or incomplete', async () => {
  const f = fixture();
  await f.ready();
  f.controller.executeWorkspaceImport.mockRejectedValueOnce({
    code: 'approval_expired',
  });
  f.controller.workspaceImportReceipt.mockRejectedValue({
    code: 'workspace_import_unavailable',
    status: 404,
  });
  await f.entry.session.execute(f.entry.api);
  const command = f.entry.session.getSnapshot().pending!.commandId;
  await f.entry.session.check(f.entry.api);
  expect(f.entry.session.getSnapshot().pending?.commandId).toBe(command);
  expect(f.entry.session.hasRetained()).toBe(true);
  f.owner.dispose();
});
