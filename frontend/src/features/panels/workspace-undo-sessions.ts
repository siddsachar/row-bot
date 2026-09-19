import type { ClientController } from '../../api/controller';
import type { ResourceView } from '../../api/types';
import {
  WorkspaceUndoSession,
  type WorkspaceUndoProps,
  type WorkspaceUndoReview,
} from './WorkspaceUndo';

/** Retain exact Undo intent in one authenticated controller, never on disk. */
export function createWorkspaceUndoSessions(controller: ClientController) {
  type Entry = ReturnType<typeof make>;
  const entries = new Map<string, Entry>();
  const auth = () => {
    const handshake = controller.getSnapshot().handshake;
    return handshake
      ? JSON.stringify([
          handshake.instance_id,
          handshake.server_epoch,
          handshake.client_session_id,
        ])
      : '';
  };
  let identity = auth();
  let disposed = false;
  const retained = (entry: Entry) =>
    entry.session.hasRetained() || entry.session.getSnapshot().reading;

  function make(
    conversation: string,
    resource: ResourceView,
    changeSetId: string,
  ) {
    // A later mutable controller snapshot cannot rewrite this entry's binding.
    const binding = { ...resource.binding };
    const owned = identity;
    const scope = JSON.stringify([
      owned,
      conversation,
      binding.resource_id,
      binding.binding_id,
      binding.revision,
      changeSetId,
    ]);
    const session = new WorkspaceUndoSession(scope);
    const guard = (selected = true) => {
      sync();
      if (
        disposed ||
        !owned ||
        owned !== auth() ||
        !session.getSnapshot().active
      )
        throw new Error('authentication_required');
      if (!selected) return;
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
      // Navigation hides a session; it does not erase a known late outcome.
      // Actual binding revocation/authentication changes still purge it in sync.
      guard(false);
      return value;
    };
    const checkReview = (review: WorkspaceUndoReview) => {
      if (
        review.conversation_id !== conversation ||
        review.resource_id !== binding.resource_id ||
        review.binding_id !== binding.binding_id ||
        review.binding_revision !== binding.revision ||
        review.change_set_id !== changeSetId
      )
        throw new Error('resource_binding_revoked');
      return review;
    };
    const reviewIdentity = (review: WorkspaceUndoReview) =>
      JSON.stringify(
        Object.entries(checkReview(review))
          .filter(([key]) => key !== 'nonce')
          .sort(([a], [b]) => a.localeCompare(b)),
      );
    const api: Omit<WorkspaceUndoProps, 'session'> = {
      scope,
      changeSetId,
      review: async (target, signal) => {
        if (target !== changeSetId) throw new Error('resource_binding_revoked');
        return checkReview(
          await query(() =>
            controller.reviewWorkspaceUndo(
              conversation,
              binding.binding_id,
              changeSetId,
              signal,
            ),
          ),
        );
      },
      apply: (review, command) =>
        query(() =>
          controller.executeWorkspaceUndo(
            conversation,
            binding.binding_id,
            checkReview(review),
            command,
          ),
        ),
      receipt: (command, signal) =>
        query(() =>
          controller.workspaceUndoReceipt(
            conversation,
            binding.binding_id,
            command,
            signal,
          ),
        ),
      recover: async (review, command) => {
        // Capture before awaiting so a mutable caller cannot change the intent
        // while its renewed approval is in flight. Only nonce may differ.
        const original = reviewIdentity(review);
        const renewed = await query(() =>
          controller.reviewWorkspaceUndoRecovery(
            conversation,
            binding.binding_id,
            command,
          ),
        );
        if (reviewIdentity(renewed) !== original)
          throw new Error('workspace_undo_review_changed');
        return query(() =>
          controller.executeWorkspaceUndo(
            conversation,
            binding.binding_id,
            renewed,
            command,
          ),
        );
      },
    };
    return { conversation, binding, changeSetId, scope, session, api };
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
  function currentResource(conversation: string, resource: ResourceView) {
    const state = controller.getSnapshot();
    const current = state.workspace?.resources.find(
      (item) => item.binding.binding_id === resource.binding.binding_id,
    );
    return (
      !disposed &&
      !!identity &&
      !state.loadingConversation &&
      state.selectedConversationId === conversation &&
      state.workspace?.conversation_id === conversation &&
      resource.available &&
      resource.binding.kind === 'workspace' &&
      !!current?.available &&
      current.binding.kind === 'workspace' &&
      current.binding.resource_id === resource.binding.resource_id &&
      current.binding.revision === resource.binding.revision
    );
  }
  return {
    forChangeSet(
      conversation: string,
      resource: ResourceView,
      changeSetId: string,
    ) {
      sync();
      if (
        !changeSetId ||
        changeSetId.length > 128 ||
        !currentResource(conversation, resource)
      )
        return null;
      const key = JSON.stringify([
        conversation,
        resource.binding.resource_id,
        resource.binding.binding_id,
        resource.binding.revision,
        changeSetId,
      ]);
      const prior = entries.get(key);
      if (prior) {
        if (!prior.session.getSnapshot().active) return null;
        entries.delete(key);
        entries.set(key, prior);
        return prior;
      }
      if (entries.size >= 8) {
        const settled = [...entries].find(([, entry]) => !retained(entry));
        if (!settled) return null;
        settled[1].session.dispose();
        entries.delete(settled[0]);
      }
      const entry = make(conversation, resource, changeSetId);
      entries.set(key, entry);
      return entry;
    },
    retainedForResource(conversation: string, resource: ResourceView) {
      sync();
      if (!currentResource(conversation, resource)) return null;
      return (
        [...entries.values()]
          .reverse()
          .find(
            (entry) =>
              entry.conversation === conversation &&
              entry.binding.resource_id === resource.binding.resource_id &&
              entry.binding.binding_id === resource.binding.binding_id &&
              entry.binding.revision === resource.binding.revision &&
              entry.session.getSnapshot().active &&
              retained(entry),
          ) ?? null
      );
    },
    hasRetained() {
      sync();
      return [...entries.values()].some(retained);
    },
    dispose() {
      disposed = true;
      unsubscribe();
      entries.forEach((entry) => entry.session.dispose());
      entries.clear();
    },
  };
}
