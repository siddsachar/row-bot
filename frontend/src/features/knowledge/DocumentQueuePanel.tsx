import { useEffect } from 'react';
import type { ClientController } from '../../api/controller';
import {
  createDocumentJobsSession,
  DocumentJobs,
  type DocumentControlCommand,
  type DocumentControlReceipt,
  type DocumentControlReview,
  type DocumentJobAction,
  type DocumentQueuePage,
  type DocumentQueueItem,
} from './DocumentJobs';

export type DocumentQueueController = Pick<
  ClientController,
  'getSnapshot' | 'subscribe'
> & {
  documentQueue(
    query: { kind: 'batches' | 'jobs'; batch_id?: string; cursor?: string },
    signal?: AbortSignal,
  ): Promise<DocumentQueuePage>;
  reviewDocumentControl(
    action: DocumentJobAction,
    payload: Record<string, unknown>,
  ): Promise<DocumentControlReview>;
  executeDocumentControl(
    command: DocumentControlCommand,
  ): Promise<DocumentControlReceipt>;
  documentControlReceipt(commandId: string): Promise<DocumentControlReceipt>;
};

/** One existing queue session belongs to one authenticated editor lifetime. */
export function createDocumentQueueSession(
  controller: DocumentQueueController,
) {
  const identity = () => {
    const handshake = controller.getSnapshot().handshake;
    return handshake
      ? JSON.stringify([
          handshake.instance_id,
          handshake.server_epoch,
          handshake.client_session_id,
        ])
      : '';
  };
  const authentication = identity();
  let disposed = false;
  let initialRequested = false;
  const readers = new Set<AbortController>();
  const guard = () => {
    if (disposed || !authentication || identity() !== authentication)
      throw new Error('authentication_required');
  };
  const query = async (
    value: Parameters<DocumentQueueController['documentQueue']>[0],
  ) => {
    guard();
    const abort = new AbortController();
    readers.add(abort);
    try {
      const result = await controller.documentQueue(value, abort.signal);
      guard();
      return result;
    } finally {
      readers.delete(abort);
    }
  };
  const session = createDocumentJobsSession(
    {
      batches: (cursor) =>
        query({ kind: 'batches', ...(cursor ? { cursor } : {}) }),
      jobs: (batch_id, cursor) =>
        query({ kind: 'jobs', batch_id, ...(cursor ? { cursor } : {}) }),
      review: (action, payload) =>
        controller.reviewDocumentControl(action, payload),
      execute: (command) => controller.executeDocumentControl(command),
      receipt: (command) => controller.documentControlReceipt(command),
    },
    guard,
  );
  const purge = () => {
    if (disposed) return;
    disposed = true;
    readers.forEach((reader) => reader.abort());
    readers.clear();
    session.purge();
  };
  const unsubscribe = controller.subscribe(() => {
    if (identity() !== authentication) purge();
  });
  const hasRetained = () => {
    const state = session.getSnapshot();
    return (
      !disposed &&
      !state.revoked &&
      (state.pending ||
        state.busy ||
        !!state.review ||
        state.selected.length > 0)
    );
  };
  return {
    session,
    hasRetained,
    loadInitial() {
      const state = session.getSnapshot();
      if (
        disposed ||
        initialRequested ||
        state.batches ||
        state.revoked ||
        hasRetained()
      )
        return;
      initialRequested = true;
      // The session records read failures. Remount never implicitly retries an effect.
      void session.load().catch(() => undefined);
    },
    dispose() {
      unsubscribe();
      purge();
    },
  };
}

export type DocumentQueueOwner = ReturnType<typeof createDocumentQueueSession>;

export function DocumentQueuePanel({
  owner,
  onProcess,
}: {
  owner: DocumentQueueOwner;
  onProcess?: (batch: DocumentQueueItem) => void;
}) {
  useEffect(() => {
    owner.loadInitial();
  }, [owner]);
  return <DocumentJobs session={owner.session} onProcess={onProcess} />;
}
