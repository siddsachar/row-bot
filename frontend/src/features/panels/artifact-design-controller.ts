import type { ClientController } from '../../api/controller';
import type { CommandReceipt } from '../../api/types';
import {
  createArtifactDesignSessions,
  type DesignReceipt,
  type DesignScope,
  type DesignSessionOwner,
} from './artifact-design-sessions';

/** Adapt one runtime owner to typed API calls; private intent stays in its session. */
export function createControllerDesignSessions(controller: ClientController) {
  function target(scope: DesignScope, revision: string) {
    return {
      kind: 'artifact' as const,
      resource_id: scope.resource_id,
      resource_revision: revision,
      binding_id: scope.binding_id,
      binding_revision: scope.binding_revision,
    };
  }
  function receipt(value: CommandReceipt): DesignReceipt {
    // Wire nullable defaults are absent from the closed internal descriptor.
    return {
      command_id: value.command_id,
      status: value.status,
      ...(value.conversation_id
        ? { conversation_id: value.conversation_id }
        : {}),
      ...(value.binding_id ? { binding_id: value.binding_id } : {}),
      ...(value.binding_revision
        ? { binding_revision: value.binding_revision }
        : {}),
      ...(value.resource_id ? { resource_id: value.resource_id } : {}),
      ...(value.code ? { code: value.code } : {}),
      ...(value.artifact_design
        ? {
            artifact_design: {
              resource_id: value.artifact_design.resource_id,
              resource_revision: value.artifact_design.resource_revision,
              operation: value.artifact_design.operation,
              status: value.artifact_design.status,
              code: value.artifact_design.code,
              ...(value.artifact_design.asset_id
                ? { asset_id: value.artifact_design.asset_id }
                : {}),
              ...(value.artifact_design.preset_id
                ? { preset_id: value.artifact_design.preset_id }
                : {}),
            },
          }
        : {}),
    };
  }
  const owner: DesignSessionOwner = {
    getSnapshot: () => {
      const state = controller.getSnapshot(),
        auth = state.handshake;
      return {
        identity: auth
          ? JSON.stringify([
              auth.instance_id,
              auth.server_epoch,
              auth.client_session_id,
            ])
          : '',
        conversationId: state.selectedConversationId,
        conversationRevision: state.conversation?.revision ?? '0',
        loading: state.loadingConversation,
        resources: state.workspace?.resources ?? [],
      };
    },
    subscribe: controller.subscribe,
    load: async (scope, options, signal) => {
      const value = await controller.designControls(
        scope.conversation_id,
        scope.binding_id,
        options,
        signal,
      );
      const styles: Record<string, string> = {};
      for (const [key, item] of Object.entries(value.element?.styles ?? {})) {
        if (typeof item !== 'string') throw new Error('invalid_design_control');
        styles[key] = item;
      }
      return {
        ...value,
        element: value.element ? { ...value.element, styles } : null,
      };
    },
    review: (scope, options, signal) =>
      controller.designReview(
        scope.conversation_id,
        scope.binding_id,
        options,
        signal,
      ),
    draftFix: async (scope, options, signal) =>
      (
        await controller.artifactReviewDraft(
          scope.conversation_id,
          scope.binding_id,
          options,
          signal,
        )
      ).text,
    stageUpload: (scope, file, commandId, signal) =>
      controller.stageArtifactUpload(
        scope.conversation_id,
        file,
        commandId,
        signal,
      ),
    importPreview: (scope, body, signal) =>
      controller.artifactDocumentImportPreview(
        scope.conversation_id,
        scope.binding_id,
        body,
        signal,
      ),
    presetReview: (scope, options, signal) =>
      controller.reviewArtifactPreset(
        scope.conversation_id,
        scope.binding_id,
        {
          target: target(scope, options.expected_revision),
          command_id: options.command_id,
          action: options.action,
          name: options.name,
          ...(options.preset_id ? { preset_id: options.preset_id } : {}),
        },
        signal,
      ),
    execute: async (scope, commandId, type, payload, expectedRevision) =>
      receipt(
        await controller.executeArtifactDesign(
          scope.conversation_id,
          commandId,
          type,
          payload,
          expectedRevision,
        ),
      ),
    receipt: async (_scope, commandId) => {
      try {
        return receipt(await controller.receipt(commandId));
      } catch (error) {
        if ((error as { code?: string }).code === 'not_found') return null;
        throw error;
      }
    },
  };
  return createArtifactDesignSessions(owner);
}
