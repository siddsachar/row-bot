import type { ClientController } from '../../api/controller';
import type { ResourceView, WorkspaceImportReview } from '../../api/types';
import {
  WorkspaceImportsSession,
  type WorkspaceImportsProps,
} from './WorkspaceImports';

/** Bounded imports follow the authenticated binding through panel remounts. */
export function createWorkspaceImportSessions(controller: ClientController) {
  type Entry = ReturnType<typeof make>;
  const entries = new Map<string, Entry>();
  const auth = () => {
    const h = controller.getSnapshot().handshake;
    return h
      ? JSON.stringify([h.instance_id, h.server_epoch, h.client_session_id])
      : '';
  };
  let identity = auth(),
    disposed = false;
  function make(conversation: string, resource: ResourceView) {
    const binding = resource.binding;
    const scope = JSON.stringify([
      conversation,
      binding.resource_id,
      binding.binding_id,
      binding.revision,
    ]);
    const session = new WorkspaceImportsSession(scope);
    const owned = identity;
    const guard = () => {
      sync();
      if (
        disposed ||
        !owned ||
        owned !== auth() ||
        !session.getSnapshot().active
      )
        throw new Error('authentication_required');
      const state = controller.getSnapshot();
      const current = state.workspace?.resources.find(
        (item) => item.binding.binding_id === binding.binding_id,
      );
      if (
        state.loadingConversation ||
        state.selectedConversationId !== conversation ||
        state.workspace?.conversation_id !== conversation ||
        !current?.available ||
        current.binding.kind !== 'workspace' ||
        current.binding.resource_id !== binding.resource_id ||
        current.binding.revision !== binding.revision
      )
        throw new Error('resource_binding_revoked');
    };
    const query = async <T>(call: () => Promise<T>) => {
      guard();
      const value = await call();
      guard();
      return value;
    };
    const checkReview = (review: WorkspaceImportReview) => {
      if (
        review.conversation_id !== conversation ||
        review.resource_id !== binding.resource_id ||
        review.binding_id !== binding.binding_id ||
        review.binding_revision !== binding.revision
      )
        throw new Error('resource_binding_revoked');
      return review;
    };
    const api: Omit<WorkspaceImportsProps, 'session'> = {
      scope,
      load: (cursor, signal) =>
        query(() =>
          controller.workspaceImports(
            conversation,
            binding.binding_id,
            cursor,
            signal,
          ),
        ),
      patch: (row, offset, signal) =>
        query(() =>
          controller.workspaceImportPatch(
            conversation,
            binding.binding_id,
            row.pending_change_id,
            row.revision,
            offset ?? 0,
            signal,
          ),
        ),
      review: async (row, signal) =>
        checkReview(
          await query(() =>
            controller.reviewWorkspaceImport(
              conversation,
              binding.binding_id,
              row.pending_change_id,
              signal,
            ),
          ),
        ),
      apply: async (review, command) =>
        query(() =>
          controller.executeWorkspaceImport(
            conversation,
            binding.binding_id,
            checkReview(review),
            command,
          ),
        ),
      receipt: (command, signal) =>
        query(() =>
          controller.workspaceImportReceipt(
            conversation,
            binding.binding_id,
            command,
            signal,
          ),
        ),
      recover: async (review, command) => {
        const renewed = checkReview(
          await query(() =>
            controller.reviewWorkspaceImportRecovery(
              conversation,
              binding.binding_id,
              command,
            ),
          ),
        );
        // A fresh nonce authorizes only the retained original reviewed action.
        if (
          renewed.action_digest !== review.action_digest ||
          renewed.pending_revision !== review.pending_revision ||
          renewed.host_revision !== review.host_revision
        )
          throw new Error('workspace_import_review_changed');
        return query(() =>
          controller.executeWorkspaceImport(
            conversation,
            binding.binding_id,
            renewed,
            command,
          ),
        );
      },
      onImported: () => {},
    };
    return { conversation, binding, scope, session, api };
  }
  function sync() {
    if (disposed) return;
    if (auth() !== identity) {
      entries.forEach((entry) => entry.session.dispose());
      entries.clear();
      identity = auth();
    }
    const state = controller.getSnapshot();
    for (const entry of entries.values()) {
      if (
        state.loadingConversation ||
        state.workspace?.conversation_id !== entry.conversation
      )
        continue;
      const current = state.workspace.resources.find(
        (item) => item.binding.binding_id === entry.binding.binding_id,
      );
      if (
        !current?.available ||
        current.binding.kind !== 'workspace' ||
        current.binding.resource_id !== entry.binding.resource_id ||
        current.binding.revision !== entry.binding.revision
      )
        entry.session.dispose();
    }
  }
  const unsubscribe = controller.subscribe(sync);
  return {
    forResource(conversation: string, resource: ResourceView) {
      sync();
      if (
        disposed ||
        !identity ||
        resource.binding.kind !== 'workspace' ||
        !resource.available
      )
        return null;
      const key = JSON.stringify([
        conversation,
        resource.binding.resource_id,
        resource.binding.binding_id,
        resource.binding.revision,
      ]);
      const prior = entries.get(key);
      if (prior) return prior;
      if (entries.size >= 8) {
        const settled = [...entries].find(
          ([, entry]) => !entry.session.hasRetained(),
        );
        if (!settled) return null;
        settled[1].session.dispose();
        entries.delete(settled[0]);
      }
      const entry = make(conversation, resource);
      entries.set(key, entry);
      return entry;
    },
    hasRetained: () =>
      [...entries.values()].some((entry) => entry.session.hasRetained()),
    dispose() {
      disposed = true;
      unsubscribe();
      entries.forEach((entry) => entry.session.dispose());
      entries.clear();
    },
  };
}
