import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type {
  ArtifactShareReview,
  Command,
  CommandReceipt,
} from '../../api/types';
import type {
  ArtifactShareOptions,
  ArtifactShareOutcome,
} from './ArtifactSharing';

export function artifactSharing(
  controller: ClientController,
  conversation: string,
  binding: string,
) {
  let reviewed: { options: string; value: ArtifactShareReview } | null = null;
  let pending: {
    signature: string;
    command: Command;
    result?: Promise<ArtifactShareOutcome>;
  } | null = null;
  const authority = () => {
    const state = controller.getSnapshot();
    const resource = state.workspace?.resources.find(
      (item) => item.binding.binding_id === binding,
    );
    if (
      !state.handshake ||
      state.selectedConversationId !== conversation ||
      state.workspace?.conversation_id !== conversation ||
      !resource?.available ||
      resource.binding.kind !== 'artifact'
    )
      throw clientError({ code: 'resource_binding_revoked' });
    return { state, resource };
  };
  const prepare = async (options: ArtifactShareOptions) => {
    authority();
    const value = await controller.prepareArtifactShare(conversation, binding, {
      ...options,
      channel_name: options.channel_name ?? null,
      target: options.target ?? null,
    });
    const { resource } = authority();
    if (
      value.resource_id !== resource.binding.resource_id ||
      value.resource_revision !== resource.resource_revision
    )
      throw clientError({ code: 'resource_revision_conflict' });
    reviewed = { options: JSON.stringify(options), value };
    return value;
  };
  const execute = (
    options: ArtifactShareOptions,
    reviewId: string,
    revision: string,
  ): Promise<ArtifactShareOutcome> => {
    const { state, resource } = authority();
    const signature = JSON.stringify([
      resource.binding.resource_id,
      options,
      reviewId,
      revision,
    ]);
    if (pending && pending.signature !== signature)
      return Promise.reject(clientError({ code: 'operation_uncertain' }));
    if (pending?.result) return pending.result;
    if (
      !pending &&
      (!reviewed ||
        reviewed.options !== JSON.stringify(options) ||
        reviewed.value.review_id !== reviewId ||
        reviewed.value.resource_revision !== revision)
    )
      return Promise.reject(clientError({ code: 'approval_expired' }));
    const retry = !!pending;
    const attempt = pending ?? {
      signature,
      command: {
        type: 'artifact.share',
        command_id: crypto.randomUUID(),
        client_session_id: state.handshake!.client_session_id,
        expected_revision: state.workspace!.revision,
        payload: {
          target: {
            kind: 'artifact',
            binding_id: binding,
            binding_revision: resource.binding.revision,
            resource_id: resource.binding.resource_id,
            resource_revision: revision,
          },
          options: {
            ...structuredClone(options),
            channel_name: options.channel_name ?? null,
            target: options.target ?? null,
          },
          review_id: reviewId,
          nonce: reviewed!.value.nonce,
        },
      } as Command,
    };
    pending = attempt;
    const perform = async () => {
      let receipt: CommandReceipt | null = null;
      if (retry) {
        try {
          receipt = await controller.receipt(attempt.command.command_id);
        } catch (error) {
          if (clientError(error).code !== 'not_found') throw error;
        }
      }
      if (!receipt)
        receipt = retry
          ? await controller.retryCommand(
              conversation,
              attempt.command,
              attempt.command.command_id,
            )
          : await controller.command(
              conversation,
              attempt.command,
              attempt.command.command_id,
            );
      const outcome = receipt.share_outcome;
      if (!outcome || outcome.resource_id !== resource.binding.resource_id)
        throw clientError({ code: 'operation_uncertain' });
      if (outcome.status !== 'uncertain' && outcome.status !== 'partial')
        pending = null;
      return outcome;
    };
    attempt.result = perform()
      .catch((error: unknown) => {
        if (
          [
            'approval_expired',
            'share_review_changed',
            'resource_revision_conflict',
            'action_denied',
            'resource_binding_revoked',
            'invalid_share',
          ].includes(clientError(error).code)
        )
          pending = null;
        throw error;
      })
      .finally(() => {
        attempt.result = undefined;
      });
    return attempt.result;
  };
  return { prepare, execute };
}
