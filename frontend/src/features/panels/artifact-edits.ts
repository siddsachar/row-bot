import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type {
  ArtifactEditPayload,
  Command,
  CommandReceipt,
} from '../../api/types';

/** One mounted panel retains one uncertain identity; retry never invents a write. */
export function artifactEdits(
  controller: ClientController,
  conversation: string,
  bindingId: string,
) {
  let pending: {
    signature: string;
    command: Command;
    result?: Promise<CommandReceipt>;
  } | null = null;
  return async (
    payload: Omit<ArtifactEditPayload, 'target'>,
    resourceRevision: string,
  ): Promise<CommandReceipt> => {
    const state = controller.getSnapshot();
    const workspace = state.workspace;
    const resource = workspace?.resources.find(
      (item) => item.binding.binding_id === bindingId,
    );
    if (
      state.selectedConversationId !== conversation ||
      workspace?.conversation_id !== conversation ||
      !resource?.available ||
      resource.binding.kind !== 'artifact' ||
      !state.handshake
    )
      throw clientError({ code: 'resource_binding_revoked' });
    const signature = JSON.stringify([
      resource.binding.resource_id,
      resourceRevision,
      payload,
    ]);
    if (pending && pending.signature !== signature)
      throw clientError({ code: 'operation_uncertain' });
    if (pending?.result) return pending.result;
    const retry = !!pending;
    const attempt = pending ?? {
      signature,
      command: {
        command_id: crypto.randomUUID(),
        client_session_id: state.handshake.client_session_id,
        type: 'artifact.edit',
        expected_revision: workspace.revision,
        payload: {
          ...payload,
          target: {
            kind: 'artifact',
            resource_id: resource.binding.resource_id,
            resource_revision: resourceRevision,
            binding_id: bindingId,
            binding_revision: resource.binding.revision,
          },
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
      if (receipt?.status !== 'completed')
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
      if (receipt.status !== 'completed')
        throw clientError({ code: 'operation_uncertain' });
      pending = null;
      // The receipt is authoritative even if a subsequent read loses connection.
      await controller.refreshWorkspace().catch(() => undefined);
      return receipt;
    };
    attempt.result = perform()
      .catch((error: unknown) => {
        if (
          [
            'revision_conflict',
            'resource_revision_conflict',
            'resource_binding_revoked',
            'invalid_edit',
            'element_unavailable',
            'history_unavailable',
            'invalid_command',
            'action_denied',
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
}
