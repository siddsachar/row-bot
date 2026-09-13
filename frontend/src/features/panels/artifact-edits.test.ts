import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import { artifactEdits } from './artifact-edits';

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
            resource_id: 'design',
            kind: 'artifact',
            revision: '1',
          },
        },
      ],
    },
  };
  const controller = {
    getSnapshot: () => state,
    command: vi.fn(),
    retryCommand: vi.fn(),
    receipt: vi.fn(),
    refreshWorkspace: vi.fn().mockResolvedValue(undefined),
  };
  const edit = artifactEdits(
    controller as unknown as ClientController,
    'chat',
    'binding',
  );
  return { state, controller, edit };
}
const payload = {
  operation: 'project_properties',
  name: 'Edited design',
} as const;

it('captures the bound resource and revision without selecting another conversation', async () => {
  const { controller, edit } = fixture();
  controller.command.mockResolvedValue({ status: 'completed' });
  await edit(payload, 'resource-version');
  expect(controller.command).toHaveBeenCalledWith(
    'chat',
    expect.objectContaining({
      type: 'artifact.edit',
      expected_revision: '3',
      payload: expect.objectContaining({
        target: {
          kind: 'artifact',
          binding_id: 'binding',
          binding_revision: '1',
          resource_id: 'design',
          resource_revision: 'resource-version',
        },
      }),
    }),
    expect.any(String),
  );
  expect(controller.refreshWorkspace).toHaveBeenCalledOnce();
});

it('reconciles a lost response from its original receipt without resending', async () => {
  const { controller, edit } = fixture();
  controller.command.mockRejectedValue(new TypeError('lost response'));
  await expect(edit(payload, 'resource-version')).rejects.toThrow(
    'lost response',
  );
  const identity = controller.command.mock.calls[0][2];
  controller.receipt.mockResolvedValue({
    status: 'completed',
    command_id: identity,
  });
  await expect(edit(payload, 'resource-version')).resolves.toMatchObject({
    command_id: identity,
  });
  expect(controller.receipt).toHaveBeenCalledWith(identity);
  expect(controller.command).toHaveBeenCalledOnce();
  expect(controller.retryCommand).not.toHaveBeenCalled();
});

it('does not replace an uncertain action or edit through a retired binding', async () => {
  const { controller, edit, state } = fixture();
  controller.command.mockRejectedValue(new TypeError('lost response'));
  await expect(edit(payload, 'resource-version')).rejects.toThrow();
  await expect(
    edit({ ...payload, name: 'Different' }, 'resource-version'),
  ).rejects.toMatchObject({ code: 'operation_uncertain' });
  state.selectedConversationId = 'another';
  await expect(edit(payload, 'resource-version')).rejects.toMatchObject({
    code: 'resource_binding_revoked',
  });
  expect(controller.command).toHaveBeenCalledOnce();
});
