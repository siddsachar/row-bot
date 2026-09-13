import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import { artifactSharing } from './artifact-sharing';

const options = {
  action: 'publish',
  delivery: 'link',
  pages: 'all',
  text: '',
  pptx_mode: 'screenshot',
  remote: false,
} as const;
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
          resource_revision: 'version',
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
  const reviewed = {
    review_id: 'a'.repeat(64),
    resource_id: 'design',
    resource_revision: 'version',
    nonce: 'proof',
  };
  const outcome = {
    resource_id: 'design',
    resource_revision: 'version',
    status: 'published',
  };
  const receipt = { status: 'completed', share_outcome: outcome };
  const controller = {
    getSnapshot: () => state,
    prepareArtifactShare: vi.fn().mockResolvedValue(reviewed),
    command: vi.fn().mockResolvedValue(receipt),
    receipt: vi.fn().mockResolvedValue(receipt),
    retryCommand: vi.fn(),
  };
  return {
    state,
    reviewed,
    outcome,
    controller,
    sharing: artifactSharing(
      controller as unknown as ClientController,
      'chat',
      'binding',
    ),
  };
}

it('requires explicit review and binds its session proof to the exact send', async () => {
  const { controller, sharing, reviewed, outcome } = fixture();
  await expect(
    sharing.execute(options, reviewed.review_id, 'version'),
  ).rejects.toMatchObject({ code: 'approval_expired' });
  await sharing.prepare(options);
  expect(controller.command).not.toHaveBeenCalled();
  expect(await sharing.execute(options, reviewed.review_id, 'version')).toEqual(
    outcome,
  );
  expect(controller.command.mock.calls[0][1]).toMatchObject({
    type: 'artifact.share',
    payload: {
      nonce: 'proof',
      review_id: reviewed.review_id,
      target: { binding_revision: '1', resource_revision: 'version' },
    },
  });
});

it('checks a lost response by receipt without repeating delivery', async () => {
  const { controller, sharing, reviewed, outcome } = fixture();
  await sharing.prepare(options);
  controller.command.mockRejectedValueOnce(new TypeError('Lost response'));
  await expect(
    sharing.execute(options, reviewed.review_id, 'version'),
  ).rejects.toThrow();
  expect(await sharing.execute(options, reviewed.review_id, 'version')).toEqual(
    outcome,
  );
  expect(controller.command).toHaveBeenCalledOnce();
  expect(controller.retryCommand).not.toHaveBeenCalled();
  expect(controller.receipt).toHaveBeenCalledWith(
    controller.command.mock.calls[0][2],
  );
});

it('keeps uncertain delivery inert and rejects changed intent', async () => {
  const { controller, sharing, reviewed } = fixture();
  await sharing.prepare(options);
  controller.command.mockResolvedValueOnce({
    status: 'partial',
    share_outcome: { resource_id: 'design', status: 'uncertain' },
  });
  await sharing.execute(options, reviewed.review_id, 'version');
  await expect(
    sharing.execute(
      { ...options, remote: true },
      reviewed.review_id,
      'version',
    ),
  ).rejects.toMatchObject({ code: 'operation_uncertain' });
  expect(controller.command).toHaveBeenCalledOnce();
});

it('rejects authority changes during passive review', async () => {
  const { state, controller, sharing, reviewed } = fixture();
  controller.prepareArtifactShare.mockImplementationOnce(async () => {
    state.workspace.resources[0].available = false;
    return reviewed;
  });
  await expect(sharing.prepare(options)).rejects.toMatchObject({
    code: 'resource_binding_revoked',
  });
  expect(controller.command).not.toHaveBeenCalled();
});

it('normalizes optional cleared UI values before the strict command decoder', async () => {
  const { controller, sharing, reviewed } = fixture();
  const cleared = { ...options, channel_name: undefined, target: undefined };
  await sharing.prepare(cleared);
  await sharing.execute(cleared, reviewed.review_id, 'version');
  expect(controller.command.mock.calls[0][1].payload.options).toEqual({
    ...options,
    channel_name: null,
    target: null,
  });
});
