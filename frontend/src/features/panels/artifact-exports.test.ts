import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import { artifactExports } from './artifact-exports';

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
  const descriptor = {
    export_id: 'export',
    resource_id: 'design',
    resource_revision: 'version',
    filename: 'Design.html',
  };
  const controller = {
    getSnapshot: () => state,
    command: vi.fn(),
    retryCommand: vi.fn(),
    receipt: vi.fn(),
    artifactExport: vi.fn().mockResolvedValue(descriptor),
    artifactDownload: vi.fn(),
  };
  return {
    state,
    controller,
    descriptor,
    exports: artifactExports(
      controller as unknown as ClientController,
      'chat',
      'binding',
    ),
  };
}
const options = { format: 'html', pages: 'all' } as const;

it('exports exactly the captured binding without starting a download', async () => {
  const { controller, descriptor, exports } = fixture();
  controller.command.mockResolvedValue({
    status: 'completed',
    export_id: 'export',
  });
  await expect(exports.create(options, 'version')).resolves.toEqual(descriptor);
  expect(controller.command).toHaveBeenCalledWith(
    'chat',
    expect.objectContaining({
      type: 'artifact.export',
      expected_revision: '3',
      payload: {
        ...options,
        target: {
          kind: 'artifact',
          binding_id: 'binding',
          binding_revision: '1',
          resource_id: 'design',
          resource_revision: 'version',
        },
      },
    }),
    expect.any(String),
  );
  expect(controller.artifactExport).toHaveBeenCalledWith(
    'chat',
    'binding',
    'export',
  );
  expect(controller.artifactDownload).not.toHaveBeenCalled();
});

it('recovers a lost response by receipt without rendering a second export', async () => {
  const { controller, exports } = fixture();
  controller.command.mockRejectedValue(new TypeError('lost response'));
  await expect(exports.create(options, 'version')).rejects.toThrow(
    'lost response',
  );
  await expect(
    exports.create({ ...options, pages: '1' }, 'version'),
  ).rejects.toMatchObject({ code: 'operation_uncertain' });
  controller.receipt.mockResolvedValue({
    status: 'completed',
    export_id: 'export',
  });
  await exports.create(options, 'version');
  expect(controller.receipt).toHaveBeenCalledWith(
    controller.command.mock.calls[0][2],
  );
  expect(controller.command).toHaveBeenCalledOnce();
  expect(controller.retryCommand).not.toHaveBeenCalled();
});

it('reconciles an admitting receipt with its original identity', async () => {
  const { controller, exports } = fixture();
  controller.command.mockRejectedValue(new TypeError('lost response'));
  await expect(exports.create(options, 'version')).rejects.toThrow();
  controller.receipt.mockResolvedValue({ status: 'admitting' });
  controller.retryCommand.mockResolvedValue({
    status: 'completed',
    export_id: 'export',
  });
  await exports.create(options, 'version');
  expect(controller.retryCommand).toHaveBeenCalledWith(
    ...controller.command.mock.calls[0],
  );
});

it('retains a partial attempt and only creates a fresh identity on explicit export', async () => {
  const { controller, exports } = fixture();
  controller.command.mockResolvedValue({
    status: 'partial',
    code: 'export_incomplete',
  });
  await expect(exports.create(options, 'version')).rejects.toMatchObject({
    code: 'export_incomplete',
  });
  expect(controller.artifactExport).not.toHaveBeenCalled();
  controller.command.mockResolvedValue({
    status: 'completed',
    export_id: 'export',
  });
  await exports.create(options, 'version');
  expect(controller.command.mock.calls[0][2]).not.toBe(
    controller.command.mock.calls[1][2],
  );
});

it('rejects revoked bindings and inconsistent completed metadata', async () => {
  const { state, controller, exports } = fixture();
  state.workspace.resources[0].available = false;
  await expect(exports.create(options, 'version')).rejects.toMatchObject({
    code: 'resource_binding_revoked',
  });
  expect(controller.command).not.toHaveBeenCalled();
  state.workspace.resources[0].available = true;
  controller.command.mockResolvedValue({
    status: 'completed',
    export_id: 'export',
  });
  controller.artifactExport.mockResolvedValue({
    resource_id: 'different',
    resource_revision: 'version',
  });
  await expect(exports.create(options, 'version')).rejects.toMatchObject({
    code: 'protocol_incompatible',
  });
  await expect(exports.download('export')).rejects.toMatchObject({
    code: 'export_unavailable',
  });
  expect(controller.artifactDownload).not.toHaveBeenCalled();
});
