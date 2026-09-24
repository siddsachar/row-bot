import { useEffect, useSyncExternalStore } from 'react';
import type { ClientController } from '../../api/controller';
import { Button, ErrorState } from '../../ui/primitives';

export type DocumentProcessingReview = {
  schema_version: 1;
  action: 'document.batch.process';
  conversation_id: string;
  batch_id: string;
  revision: string;
  policy_digest: string;
  provider_work: true;
  knowledge_projection_scope: 'saved_knowledge';
  chat: { provider_id: string; model_ref: string; execution_location: string };
  embedding: { provider: string; execution_location: string };
  review_id: string;
};
export type DocumentProcessingCommand = {
  command_id: string;
  type: 'document.batch.process';
  payload: {
    conversation_id: string;
    batch_id: string;
    revision: string;
    review_id: string;
  };
};
export type DocumentProcessingReceipt = {
  command_id: string;
  status: 'completed' | 'partial';
  batch_id?: string;
  processing?: 'admitted';
  code?: string;
};
export type DocumentProcessingController = Pick<
  ClientController,
  'getSnapshot' | 'subscribe'
> & {
  reviewDocumentProcessing(
    conversationId: string,
    batchId: string,
    revision: string,
  ): Promise<DocumentProcessingReview>;
  executeDocumentProcessing(
    conversationId: string,
    original: DocumentProcessingCommand,
  ): Promise<DocumentProcessingReceipt>;
  documentProcessingReceipt(
    conversationId: string,
    commandId: string,
  ): Promise<DocumentProcessingReceipt>;
};
type Selection = {
  conversationId: string;
  batchId: string;
  batchRevision: string;
};
type State = {
  selection: Selection | null;
  review: DocumentProcessingReview | null;
  original: DocumentProcessingCommand | null;
  receipt: DocumentProcessingReceipt | null;
  busy: boolean;
  pending: boolean;
  revoked: boolean;
  error: string;
};
const empty = (): State => ({
  selection: null,
  review: null,
  original: null,
  receipt: null,
  busy: false,
  pending: false,
  revoked: false,
  error: '',
});

/** One exact current attempt, retained by the authenticated editor owner. */
export function createDocumentProcessingSession(
  controller: DocumentProcessingController,
  newId: () => string = () => crypto.randomUUID(),
) {
  let state = empty();
  let disposed = false;
  let notifiedCommand: string | null = null;
  const listeners = new Set<() => void>();
  const identity = () => {
    const value = controller.getSnapshot().handshake;
    return value
      ? JSON.stringify([
          value.instance_id,
          value.server_epoch,
          value.client_session_id,
        ])
      : '';
  };
  const authentication = identity();
  const emit = (patch: Partial<State>) => {
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  };
  const purge = () => {
    if (disposed) return;
    disposed = true;
    notifiedCommand = null;
    state = { ...empty(), revoked: true };
    listeners.forEach((listener) => listener());
  };
  const guard = () => {
    if (!authentication || disposed || identity() !== authentication) {
      purge();
      throw new Error('authentication_required');
    }
  };
  const unsubscribe = controller.subscribe(() => {
    if (identity() !== authentication) purge();
  });
  async function run(operation: () => Promise<void>, recovery = false) {
    guard();
    if (state.busy || (state.pending && !recovery))
      throw new Error('document_processing_pending');
    emit({ busy: true, error: '' });
    try {
      await operation();
    } catch (error) {
      if (!disposed)
        emit({
          error:
            'Processing could not be confirmed. Check the original receipt if an admission was sent.',
        });
      throw error;
    } finally {
      if (!disposed) emit({ busy: false });
    }
  }
  function accept(receipt: DocumentProcessingReceipt) {
    guard();
    if (
      !state.original ||
      receipt.command_id !== state.original.command_id ||
      !['completed', 'partial'].includes(receipt.status) ||
      (receipt.status === 'completed' &&
        (receipt.batch_id !== state.selection?.batchId ||
          receipt.processing !== 'admitted'))
    )
      throw new Error('document_processing_receipt_mismatch');
    emit({
      receipt: structuredClone(receipt),
      pending: receipt.status !== 'completed',
    });
  }
  const owner = {
    getSnapshot: () => state,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    hasRetained: () =>
      !disposed && (!!state.selection || state.busy || state.pending),
    select(conversationId: string, batchId: string, batchRevision: string) {
      guard();
      if (state.busy || state.pending)
        throw new Error('document_processing_pending');
      if (!conversationId || !batchId.startsWith('client_') || !batchRevision)
        throw new Error('invalid_document_processing');
      if (
        state.selection?.conversationId === conversationId &&
        state.selection.batchId === batchId &&
        state.selection.batchRevision === batchRevision
      )
        return;
      notifiedCommand = null;
      state = {
        ...empty(),
        selection: { conversationId, batchId, batchRevision },
      };
      listeners.forEach((listener) => listener());
    },
    review: () =>
      run(async () => {
        const selected = state.selection;
        if (!selected) throw new Error('invalid_document_processing');
        if (state.original)
          throw new Error('document_processing_original_retained');
        const value = await controller.reviewDocumentProcessing(
          selected.conversationId,
          selected.batchId,
          selected.batchRevision,
        );
        guard();
        if (
          value.schema_version !== 1 ||
          value.action !== 'document.batch.process' ||
          value.conversation_id !== selected.conversationId ||
          value.batch_id !== selected.batchId ||
          !value.revision ||
          !value.review_id ||
          !value.policy_digest ||
          value.provider_work !== true ||
          value.knowledge_projection_scope !== 'saved_knowledge'
        )
          throw new Error('document_processing_review_mismatch');
        emit({ review: structuredClone(value), receipt: null });
      }),
    confirm: () =>
      run(async () => {
        if (!state.review || !state.selection || state.original)
          throw new Error('document_processing_review_required');
        const original: DocumentProcessingCommand = {
          command_id: newId(),
          type: 'document.batch.process',
          payload: {
            conversation_id: state.review.conversation_id,
            batch_id: state.review.batch_id,
            revision: state.review.revision,
            review_id: state.review.review_id,
          },
        };
        const conversationId = state.selection.conversationId;
        emit({ original, pending: true });
        guard();
        const receipt = await controller.executeDocumentProcessing(
          conversationId,
          structuredClone(original),
        );
        guard();
        accept(receipt);
      }),
    async start() {
      await this.review();
      await this.confirm();
    },
    refresh: () =>
      run(async () => {
        if (!state.original || !state.selection)
          throw new Error('document_processing_original_required');
        const receipt = await controller.documentProcessingReceipt(
          state.selection.conversationId,
          state.original.command_id,
        );
        guard();
        accept(receipt);
      }, true),
    notifyAdmitted(callback: () => void) {
      guard();
      if (
        state.receipt?.status === 'completed' &&
        state.receipt.command_id !== notifiedCommand
      ) {
        notifiedCommand = state.receipt.command_id;
        callback();
      }
    },
    dispose() {
      unsubscribe();
      purge();
    },
  };
  return owner;
}
export type DocumentProcessingOwner = ReturnType<
  typeof createDocumentProcessingSession
>;

export function DocumentProcessingPanel({
  owner,
  onAdmitted,
}: {
  owner: DocumentProcessingOwner;
  onAdmitted?: () => void;
}) {
  const state = useSyncExternalStore(owner.subscribe, owner.getSnapshot);
  useEffect(() => {
    if (onAdmitted && !state.revoked && state.receipt?.status === 'completed')
      owner.notifyAdmitted(onAdmitted);
  }, [owner, onAdmitted, state.receipt, state.revoked]);
  const invoke = (operation: () => Promise<unknown>) => {
    void operation().catch(() => undefined);
  };
  if (state.revoked)
    return <p role="status">Authenticate again to process documents.</p>;
  if (!state.selection) return <p>Select a paused batch to process.</p>;
  return (
    <section aria-label="Document processing">
      <h3>Process saved documents</h3>
      <p>Conversation: {state.selection.conversationId}</p>
      <p>Batch: {state.selection.batchId}</p>
      <p>
        This selection remains attached to its original conversation when you
        navigate to another chat.
      </p>
      {state.error && (
        <ErrorState title="Processing needs attention">
          {state.error}
        </ErrorState>
      )}
      {!state.original && (
        <Button
          disabled={state.busy}
          onClick={() => invoke(() => owner.start())}
        >
          Start processing
        </Button>
      )}
      {state.review && (
        <div>
          <p>
            Chat provider: {state.review.chat.provider_id} ·{' '}
            {state.review.chat.model_ref} ·{' '}
            {state.review.chat.execution_location}
          </p>
          <p>
            Embedding provider: {state.review.embedding.provider} ·{' '}
            {state.review.embedding.execution_location}
          </p>
          <p>
            Processing reads the saved document sources and may send their
            content to the reviewed providers. Results update saved knowledge
            and its projections.
          </p>
        </div>
      )}
      {state.original && (
        <>
          <p>Original command: {state.original.command_id}</p>
          <Button disabled={state.busy} onClick={() => invoke(owner.refresh)}>
            Check original processing receipt
          </Button>
        </>
      )}
      {(state.pending || state.receipt) && (
        <p role="status">
          {state.receipt?.status === 'completed'
            ? 'Processing admitted. Documents may still be indexing, extracting, or finalizing. Check the queue for progress.'
            : 'Admission is unconfirmed. Keep this original command and check its receipt before another action.'}
        </p>
      )}
    </section>
  );
}
