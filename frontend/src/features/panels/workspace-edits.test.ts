import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { WorkspaceEditableFile } from '../../api/types';
import {
  workspaceEdits,
  type WorkspaceEditAttemptOwner,
} from './workspace-edits';

const snapshot: WorkspaceEditableFile = {
  review_token: 'a'.repeat(64),
  resource_id: 'workspace',
  conversation_id: 'chat',
  relative_path: 'hello.txt',
  resource_revision: 'version',
  binding_id: 'binding',
  binding_revision: '2',
  target: 'workspace',
  status: 'text',
  content: 'old',
  digest: 'a'.repeat(64),
};
function fixture() {
  const state = {
    selectedConversationId: 'chat',
    handshake: { client_session_id: 'session' },
    workspace: {
      conversation_id: 'chat',
      revision: '3',
      resources: [
        {
          available: true,
          binding: {
            binding_id: 'binding',
            kind: 'workspace',
            resource_id: 'workspace',
          },
        },
      ],
    },
  };
  const controller = {
    getSnapshot: () => state,
    command: vi.fn(),
    receipt: vi.fn(),
    retryCommand: vi.fn(),
  };
  return {
    state,
    controller,
    save: workspaceEdits(
      controller as unknown as ClientController,
      'chat',
      'binding',
    ),
  };
}

it('sends exact revision and recovers completed receipt without replaying bytes', async () => {
  const { controller, save } = fixture();
  controller.command.mockRejectedValue(new TypeError('lost response'));
  await expect(save(snapshot, 'new')).rejects.toThrow();
  controller.receipt.mockResolvedValue({
    status: 'completed',
    workspace_edit: { ...snapshot, status: 'saved' },
  });
  expect(await save(snapshot, 'new')).toMatchObject({ status: 'saved' });
  expect(controller.command).toHaveBeenCalledOnce();
  expect(controller.command.mock.calls[0][1].payload).toEqual({
    target: {
      kind: 'workspace',
      resource_id: 'workspace',
      resource_revision: 'version',
      binding_id: 'binding',
      binding_revision: '2',
    },
    relative_path: 'hello.txt',
    content: 'new',
    file_digest: snapshot.digest,
    review_token: snapshot.review_token,
  });
  expect(controller.retryCommand).not.toHaveBeenCalled();
});

it('reconciles partial save with its unchanged command identity', async () => {
  const { controller, save } = fixture();
  controller.command.mockResolvedValue({
    status: 'partial',
    workspace_edit: { ...snapshot, status: 'partial' },
  });
  expect(await save(snapshot, 'new')).toMatchObject({ status: 'partial' });
  await expect(save(snapshot, 'different')).rejects.toMatchObject({
    code: 'operation_uncertain',
  });
  controller.receipt.mockResolvedValue({
    status: 'partial',
    workspace_edit: { ...snapshot, status: 'partial' },
  });
  controller.retryCommand.mockResolvedValue({
    status: 'completed',
    workspace_edit: { ...snapshot, status: 'saved' },
  });
  await save(snapshot, 'new');
  expect(controller.retryCommand).toHaveBeenCalledWith(
    ...controller.command.mock.calls[0],
  );
});

it('rejects a switched conversation before applying an existing file snapshot', async () => {
  const { state, controller, save } = fixture();
  state.selectedConversationId = 'other';
  await expect(save(snapshot, 'new')).rejects.toMatchObject({
    code: 'resource_binding_revoked',
  });
  expect(controller.command).not.toHaveBeenCalled();
});

it('a recreated adapter retains the original command holder and receipt identity', async () => {
  const { controller } = fixture();
  const holder: WorkspaceEditAttemptOwner = { pending: null };
  const first = workspaceEdits(
    controller as unknown as ClientController,
    'chat',
    'binding',
    holder,
  );
  controller.command.mockRejectedValue(new TypeError('lost response'));
  await expect(first(snapshot, 'retained')).rejects.toThrow();
  const original = structuredClone(holder.pending!.command);
  const second = workspaceEdits(
    controller as unknown as ClientController,
    'chat',
    'binding',
    holder,
  );
  controller.receipt.mockRejectedValue({ code: 'not_found' });
  controller.retryCommand.mockResolvedValue({
    status: 'completed',
    workspace_edit: { ...snapshot, status: 'saved' },
  });
  await second(snapshot, 'retained');
  expect(controller.retryCommand).toHaveBeenCalledWith(
    'chat',
    original,
    original.command_id,
  );
});

it('revalidates current binding after receipt lookup before any retry dispatch', async () => {
  const { controller, state } = fixture();
  const holder: WorkspaceEditAttemptOwner = { pending: null };
  const save = workspaceEdits(
    controller as unknown as ClientController,
    'chat',
    'binding',
    holder,
  );
  controller.command.mockRejectedValue(new TypeError('lost response'));
  await expect(save(snapshot, 'retained')).rejects.toThrow();
  const original = holder.pending!.command;
  controller.receipt.mockImplementation(async () => {
    state.workspace.resources = [];
    return {
      status: 'partial',
      workspace_edit: { ...snapshot, status: 'partial' },
    };
  });
  await expect(save(snapshot, 'retained')).rejects.toMatchObject({
    code: 'resource_binding_revoked',
  });
  expect(controller.retryCommand).not.toHaveBeenCalled();
  expect(holder.pending!.command).toBe(original);
});

it('rejects renewed authentication and cannot recreate the prior command under a new session', async () => {
  const { controller, state, save } = fixture();
  controller.command.mockRejectedValue(new TypeError('lost response'));
  await expect(save(snapshot, 'retained')).rejects.toThrow();
  state.handshake.client_session_id = 'new-session';
  await expect(save(snapshot, 'retained')).rejects.toMatchObject({
    code: 'resource_binding_revoked',
  });
  expect(controller.receipt).not.toHaveBeenCalled();
  expect(controller.command).toHaveBeenCalledOnce();
});

it('a definitive original rejected receipt releases the intent without resending', async () => {
  const { controller } = fixture();
  const holder: WorkspaceEditAttemptOwner = { pending: null };
  const save = workspaceEdits(
    controller as unknown as ClientController,
    'chat',
    'binding',
    holder,
  );
  controller.command.mockRejectedValue(new TypeError('lost response'));
  await expect(save(snapshot, 'retained')).rejects.toThrow();
  controller.receipt.mockResolvedValue({
    command_id: holder.pending!.command.command_id,
    status: 'rejected',
    code: 'action_denied',
  });
  expect(await save(snapshot, 'retained')).toMatchObject({
    status: 'denied',
    file_saved: false,
  });
  expect(holder.pending).toBeNull();
  expect(controller.retryCommand).not.toHaveBeenCalled();
});

it('a receipt for another command or file is never accepted as completion', async () => {
  const { controller, save } = fixture();
  controller.command.mockResolvedValue({
    command_id: 'other',
    status: 'completed',
    workspace_edit: { ...snapshot, status: 'saved' },
  });
  await expect(save(snapshot, 'retained')).rejects.toMatchObject({
    code: 'operation_uncertain',
  });
  controller.receipt.mockResolvedValue({
    status: 'completed',
    workspace_edit: { ...snapshot, binding_revision: 'wrong', status: 'saved' },
  });
  await expect(save(snapshot, 'retained')).rejects.toMatchObject({
    code: 'operation_uncertain',
  });
  expect(controller.retryCommand).not.toHaveBeenCalled();
});
