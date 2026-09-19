import type { ClientController } from '../../api/controller';
import type { ArtifactExport, Command, CommandReceipt } from '../../api/types';
import { clientError } from '../../api/errors';
import { saveBrowserDownload } from '../../platform/download';
import type { ArtifactExportOptions } from './ArtifactExports';

export function artifactExports(
  controller: ClientController,
  conversation: string,
  bindingId: string,
) {
  let pending: {
    signature: string;
    command: Command;
    result?: Promise<ArtifactExport>;
  } | null = null;
  const exports = new Map<string, ArtifactExport>();
  const create = async (
    options: ArtifactExportOptions,
    revision: string,
  ): Promise<ArtifactExport> => {
    const state = controller.getSnapshot();
    const resource = state.workspace?.resources.find(
      (item) => item.binding.binding_id === bindingId,
    );
    if (
      state.selectedConversationId !== conversation ||
      state.workspace?.conversation_id !== conversation ||
      !resource?.available ||
      resource.binding.kind !== 'artifact' ||
      !state.handshake
    )
      throw clientError({ code: 'resource_binding_revoked' });
    const signature = JSON.stringify([
      resource.binding.resource_id,
      revision,
      options,
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
        type: 'artifact.export',
        expected_revision: state.workspace.revision,
        payload: {
          ...options,
          target: {
            kind: 'artifact',
            resource_id: resource.binding.resource_id,
            resource_revision: revision,
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
      if (!receipt || receipt.status === 'admitting')
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
      if (receipt.status === 'partial') {
        pending = null; // Explicit next Export creates a new attempt; retained partial bytes remain.
        throw { code: receipt.code ?? 'export_incomplete' };
      }
      if (receipt.status !== 'completed' || !receipt.export_id)
        throw clientError({ code: 'operation_uncertain' });
      const value = await controller.artifactExport(
        conversation,
        bindingId,
        receipt.export_id,
      );
      if (
        value.resource_id !== resource.binding.resource_id ||
        value.resource_revision !== revision
      )
        throw clientError({ code: 'protocol_incompatible' });
      exports.set(value.export_id, value);
      if (exports.size > 32) exports.delete(exports.keys().next().value!);
      pending = null;
      return value;
    };
    attempt.result = perform()
      .catch((error: unknown) => {
        if (
          [
            'revision_conflict',
            'resource_revision_conflict',
            'resource_binding_revoked',
            'invalid_command',
            'action_denied',
            'export_busy',
            'invalid_export',
            'invalid_page_range',
            'export_expired',
            'export_capacity_reached',
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
  const download = async (exportId: string) => {
    const value = exports.get(exportId);
    if (!value) throw { code: 'export_unavailable' };
    const result = await saveBrowserDownload(
      () => controller.artifactDownload(conversation, bindingId, value),
      value.filename,
    );
    if (result.status !== 'ok') throw { code: 'export_unavailable' };
  };
  return { create, download };
}
