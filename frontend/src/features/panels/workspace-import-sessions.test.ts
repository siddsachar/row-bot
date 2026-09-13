import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ResourceView, WorkspaceImportReview } from '../../api/types';
import { createWorkspaceImportSessions } from './workspace-import-sessions';

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
  const listeners = new Set<() => void>();
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
    nonce: 'original',
  } as WorkspaceImportReview;
  const controller = {
    getSnapshot: () => state,
    subscribe: (fn: () => void) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    reviewWorkspaceImport: vi.fn(async () => review),
    reviewWorkspaceImportRecovery: vi.fn(async () => ({
      ...review,
      nonce: 'fresh',
    })),
    workspaceImportReceipt: vi.fn(),
    executeWorkspaceImport: vi.fn(async () => ({ status: 'partial' })),
  };
  const owner = createWorkspaceImportSessions(
    controller as unknown as ClientController,
  );
  return {
    owner,
    controller,
    state,
    resource,
    review,
    notify: () => listeners.forEach((fn) => fn()),
  };
}

it('retains the binding session through navigation and renews only the original reviewed recovery', async () => {
  const f = fixture(),
    entry = f.owner.forResource('chat', f.resource)!;
  f.state.selectedConversationId = 'other';
  f.notify();
  await expect(entry.api.recover(f.review, 'original-command')).rejects.toThrow(
    'resource_binding_revoked',
  );
  expect(f.controller.executeWorkspaceImport).not.toHaveBeenCalled();
  f.state.selectedConversationId = 'chat';
  f.notify();
  expect(f.owner.forResource('chat', f.resource)).toBe(entry);
  await entry.api.recover(f.review, 'original-command');
  expect(f.controller.executeWorkspaceImport).toHaveBeenCalledWith(
    'chat',
    'binding',
    { ...f.review, nonce: 'fresh' },
    'original-command',
  );
  f.controller.executeWorkspaceImport.mockClear();
  f.controller.reviewWorkspaceImportRecovery.mockResolvedValue({
    ...f.review,
    action_digest: 'changed',
  });
  await expect(entry.api.recover(f.review, 'original-command')).rejects.toThrow(
    'workspace_import_review_changed',
  );
  expect(f.controller.executeWorkspaceImport).not.toHaveBeenCalled();
  f.owner.dispose();
});

it('revoked bindings and authentication purge retained import state and block later IO', async () => {
  const f = fixture(),
    entry = f.owner.forResource('chat', f.resource)!;
  f.state.workspace.resources = [];
  f.notify();
  expect(entry.session.getSnapshot().active).toBe(false);
  await expect(entry.api.recover(f.review, 'original-command')).rejects.toThrow(
    'authentication_required',
  );
  expect(f.controller.reviewWorkspaceImportRecovery).not.toHaveBeenCalled();
  f.state.handshake.client_session_id = 'replacement';
  f.notify();
  expect(f.owner.hasRetained()).toBe(false);
  f.owner.dispose();
});
