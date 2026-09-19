import { useEffect, useSyncExternalStore } from 'react';
import type { ClientController } from '../../api/controller';
import {
  createDocumentUploadsSession,
  DocumentUploads,
  type DocumentUploadCommand,
  type DocumentUploadFile,
  type DocumentUploadReceipt,
  type DocumentUploadReview,
} from './DocumentUploads';

export type DocumentUploadController = Pick<
  ClientController,
  'getSnapshot' | 'subscribe'
> & {
  reviewDocumentUpload(
    files: DocumentUploadFile[],
  ): Promise<DocumentUploadReview>;
  uploadDocuments(
    command: DocumentUploadCommand,
    files: readonly File[],
  ): Promise<DocumentUploadReceipt>;
  documentUploadReceipt(commandId: string): Promise<DocumentUploadReceipt>;
};

/** Retain the existing upload session for one authenticated controller lifetime. */
export function createDocumentUploadSession(
  controller: DocumentUploadController,
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
  let notifiedCommand = '';
  const guard = () => {
    if (disposed || !authentication || identity() !== authentication) {
      purge();
      throw new Error('authentication_required');
    }
  };
  const session = createDocumentUploadsSession(
    {
      review: (files) => controller.reviewDocumentUpload(files),
      upload: (command, files) => controller.uploadDocuments(command, files),
      receipt: (commandId) => controller.documentUploadReceipt(commandId),
    },
    guard,
  );
  function purge() {
    if (disposed) return;
    disposed = true;
    notifiedCommand = '';
    session.purge();
  }
  const unsubscribe = controller.subscribe(() => {
    if (!authentication || identity() !== authentication) purge();
  });
  if (!authentication) purge();
  return {
    session,
    hasRetained() {
      const state = session.getSnapshot();
      return (
        !disposed &&
        !state.revoked &&
        (state.busy ||
          state.pending ||
          !!state.review ||
          state.files.length > 0)
      );
    },
    notifyStaged(callback?: (batchId: string) => void) {
      if (!callback || disposed) return;
      if (!authentication || identity() !== authentication) {
        purge();
        return;
      }
      const receipt = session.getSnapshot().receipt;
      // Only the canonical session's fully validated completed receipt is exposed here.
      if (
        receipt?.status !== 'completed' ||
        !receipt.batch_id ||
        receipt.command_id === notifiedCommand
      )
        return;
      guard();
      notifiedCommand = receipt.command_id;
      callback(receipt.batch_id);
    },
    dispose() {
      unsubscribe();
      purge();
    },
  };
}

export type DocumentUploadOwner = ReturnType<
  typeof createDocumentUploadSession
>;

export function DocumentUploadPanel({
  owner,
  onStaged,
}: {
  owner: DocumentUploadOwner;
  onStaged?: (batchId: string) => void;
}) {
  const state = useSyncExternalStore(
    owner.session.subscribe,
    owner.session.getSnapshot,
  );
  useEffect(() => {
    owner.notifyStaged(onStaged);
  }, [owner, onStaged, state.receipt]);
  return <DocumentUploads session={owner.session} />;
}
