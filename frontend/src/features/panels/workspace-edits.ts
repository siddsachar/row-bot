import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type {
  Command,
  CommandReceipt,
  WorkspaceEditableFile,
  WorkspaceEditResult,
} from '../../api/types';

export type WorkspaceEditAttempt = {
  signature: string;
  command: Command;
  result?: Promise<WorkspaceEditResult>;
};
export type WorkspaceEditAttemptOwner = {
  pending: WorkspaceEditAttempt | null;
};

/** The injected holder belongs to the controller lifetime, not a panel mount. */
export function workspaceEdits(
  controller: ClientController,
  conversation: string,
  binding: string,
  owner: WorkspaceEditAttemptOwner = { pending: null },
) {
  const authentication = controller.getSnapshot().handshake;
  const session = authentication?.client_session_id;
  const server = authentication?.server_epoch;
  const instance = authentication?.instance_id;
  return async (
    source: WorkspaceEditableFile,
    content: string,
  ): Promise<WorkspaceEditResult> => {
    const snapshot = structuredClone(source);
    const authorize = (dispatch = false) => {
      const state = controller.getSnapshot();
      const resource = state.workspace?.resources.find(
        (item) => item.binding.binding_id === binding,
      );
      if (
        !state.handshake ||
        state.handshake.client_session_id !== session ||
        state.handshake.server_epoch !== server ||
        state.handshake.instance_id !== instance ||
        state.selectedConversationId !== conversation ||
        state.workspace?.conversation_id !== conversation ||
        !resource?.available ||
        resource.binding.kind !== 'workspace' ||
        snapshot.conversation_id !== conversation ||
        snapshot.resource_id !== resource.binding.resource_id ||
        snapshot.binding_id !== binding ||
        (resource.binding.revision !== undefined &&
          resource.binding.revision !== snapshot.binding_revision) ||
        (dispatch &&
          resource.resource_revision !== undefined &&
          resource.resource_revision !== snapshot.resource_revision)
      )
        throw clientError({ code: 'resource_binding_revoked' });
      return state;
    };
    const state = authorize();
    const signature = JSON.stringify([
      snapshot.resource_id,
      snapshot.conversation_id,
      snapshot.target,
      snapshot.relative_path,
      snapshot.resource_revision,
      snapshot.binding_revision,
      snapshot.digest,
      snapshot.review_token,
      content,
    ]);
    if (owner.pending && owner.pending.signature !== signature)
      throw clientError({ code: 'operation_uncertain' });
    if (owner.pending?.result) return owner.pending.result;
    const retry = !!owner.pending;
    if (!retry) authorize(true);
    const attempt = owner.pending ?? {
      signature,
      command: {
        type: 'workspace.edit',
        command_id: crypto.randomUUID(),
        client_session_id: state.handshake!.client_session_id,
        expected_revision: state.workspace!.revision,
        payload: {
          target: {
            kind: 'workspace',
            binding_id: binding,
            binding_revision: snapshot.binding_revision,
            resource_id: snapshot.resource_id,
            resource_revision: snapshot.resource_revision,
          },
          relative_path: snapshot.relative_path,
          content,
          file_digest: snapshot.digest,
          review_token: snapshot.review_token,
        },
      } as Command,
    };
    owner.pending = attempt;
    const perform = async () => {
      let receipt: CommandReceipt | null = null;
      if (retry) {
        try {
          receipt = await controller.receipt(attempt.command.command_id);
        } catch (error) {
          if (clientError(error).code !== 'not_found') throw error;
        }
      }
      if (receipt?.status !== 'completed' && receipt?.status !== 'rejected') {
        authorize(true);
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
      }
      if (
        receipt.command_id !== undefined &&
        receipt.command_id !== attempt.command.command_id
      )
        throw clientError({ code: 'operation_uncertain' });
      if (receipt.status === 'rejected') {
        owner.pending = null;
        return {
          ...snapshot,
          status: 'denied',
          file_saved: false,
          ledger_saved: false,
          code: clientError({ code: receipt.code ?? 'action_denied' }).code,
        } as WorkspaceEditResult;
      }
      const result = receipt.workspace_edit;
      if (
        !result ||
        result.resource_id !== snapshot.resource_id ||
        result.conversation_id !== conversation ||
        result.relative_path !== snapshot.relative_path ||
        result.binding_id !== binding ||
        result.binding_revision !== snapshot.binding_revision ||
        result.target !== snapshot.target
      )
        throw clientError({ code: 'operation_uncertain' });
      if (result.status !== 'partial') owner.pending = null;
      return result;
    };
    attempt.result = perform().finally(() => {
      attempt.result = undefined;
    });
    return attempt.result;
  };
}
