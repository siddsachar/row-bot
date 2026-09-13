import { useSyncExternalStore } from 'react';
import { Button, ErrorState } from '../../ui/primitives';

export type DocumentRemovalOutcome = {
  removal_id: string;
  document_id: string | null;
  status: 'complete' | 'pending' | 'partial';
  removed: boolean;
  derived_entities_removed: number;
  retained_copy_count: number;
  retained_kinds: string[];
  stages: { stage: string; status: string }[];
  stage_counts: { complete: number; pending: number; partial: number };
  failure_codes: string[];
};
export type DocumentRemovalReview = {
  review_id: string;
  document_id: string | null;
  source_revision: string;
  source_count: number;
  retains_copies: true;
  source_command_id?: string;
  removal_id?: string;
};
export type DocumentRemovalCommand = {
  command_id: string;
  type: 'document.remove' | 'document.removal.retry';
  payload: Record<string, unknown>;
};
export type DocumentRemovalReceipt = {
  command_id: string;
  status: 'completed' | 'partial' | 'rejected';
  code?: string;
  removal?: DocumentRemovalOutcome;
};
export type DocumentRemovalTransport = {
  review(documentId: string | null): Promise<DocumentRemovalReview>;
  reviewRetry(sourceCommandId: string): Promise<DocumentRemovalReview>;
  execute(command: DocumentRemovalCommand): Promise<DocumentRemovalReceipt>;
  receipt(commandId: string): Promise<DocumentRemovalReceipt>;
};
type Attempt = {
  command: DocumentRemovalCommand;
  removalId: string;
  receipt: DocumentRemovalReceipt | null;
};
type State = {
  review: DocumentRemovalReview | null;
  receipt: DocumentRemovalReceipt | null;
  busy: boolean;
  pending: boolean;
  revoked: boolean;
  error: string;
};

/** Root retains this per-target session for the authenticated controller lifetime.
 * Connect pending to the existing beforeunload guard; purge on auth loss.
 * These bounded original command buffers are never browser-persisted.
 */
export function createDocumentRemovalSession(
  documentId: string | null,
  transport: DocumentRemovalTransport,
  guard: () => void,
  newId: () => string = () => crypto.randomUUID(),
) {
  let state: State = {
    review: null,
    receipt: null,
    busy: false,
    pending: false,
    revoked: false,
    error: '',
  };
  let epoch = 0;
  let operation: Promise<unknown> | null = null;
  let attempt: Attempt | null = null;
  const retained: Attempt[] = [];
  const listeners = new Set<() => void>();
  const emit = (patch: Partial<State>) => {
    state = { ...state, ...patch };
    listeners.forEach((notify) => notify());
  };
  const authority = (captured = epoch) => {
    if (state.revoked || captured !== epoch)
      throw new Error('authentication_required');
    guard();
  };
  async function read<T>(call: () => Promise<T>) {
    const captured = epoch;
    authority(captured);
    const value = await call();
    authority(captured);
    return value;
  }
  function accept(current: Attempt, value: DocumentRemovalReceipt) {
    if (
      value.command_id !== current.command.command_id ||
      (value.removal &&
        (value.removal.removal_id !== current.removalId ||
          value.removal.document_id !== documentId))
    )
      throw new Error('document_receipt_changed');
    if (value.status === 'completed' && value.removal?.status !== 'complete')
      throw new Error('document_receipt_changed');
    current.receipt = value;
    emit({
      receipt: value,
      pending: value.status !== 'rejected' && !value.removal,
      error: '',
    });
  }
  async function run<T>(call: () => Promise<T>) {
    authority();
    if (operation) throw new Error('document_operation_busy');
    const captured = epoch;
    emit({ busy: true, error: '' });
    const pending = Promise.resolve().then(call);
    operation = pending;
    try {
      return await pending;
    } catch (error) {
      if (captured === epoch)
        emit({
          error:
            'Document removal could not be confirmed. Refresh the original command before another action.',
        });
      throw error;
    } finally {
      if (operation === pending) operation = null;
      if (captured === epoch) emit({ busy: false });
    }
  }
  return {
    documentId,
    getSnapshot: () => state,
    subscribe(notify: () => void) {
      listeners.add(notify);
      return () => {
        listeners.delete(notify);
      };
    },
    review: () =>
      run(async () => {
        if (state.pending) throw new Error('document_outcome_uncertain');
        if (
          state.receipt?.removal &&
          state.receipt.removal.status !== 'complete'
        )
          throw new Error('document_retry_required');
        const value = await read(() => transport.review(documentId));
        if (
          value.document_id !== documentId ||
          value.source_command_id ||
          !value.review_id ||
          !value.source_revision
        )
          throw new Error('document_review_changed');
        emit({ review: value });
      }),
    reviewRetry: () =>
      run(async () => {
        const prior = attempt;
        if (
          !prior ||
          !prior.receipt?.removal ||
          prior.receipt.removal.status === 'complete' ||
          state.pending
        )
          throw new Error('document_operation_unavailable');
        const value = await read(() =>
          transport.reviewRetry(prior.command.command_id),
        );
        if (
          value.document_id !== documentId ||
          value.removal_id !== prior.removalId ||
          value.source_command_id !== prior.command.command_id ||
          !value.review_id
        )
          throw new Error('document_review_changed');
        emit({ review: value });
      }),
    dismissReview() {
      authority();
      if (!operation) emit({ review: null });
    },
    confirm: () =>
      run(async () => {
        const review = state.review;
        if (!review || state.pending || retained.length >= 32)
          throw new Error('document_review_unavailable');
        const command: DocumentRemovalCommand = {
          command_id: newId(),
          type: review.source_command_id
            ? 'document.removal.retry'
            : 'document.remove',
          payload: review.source_command_id
            ? {
                source_command_id: review.source_command_id,
                review_id: review.review_id,
              }
            : {
                document_id: documentId,
                source_revision: review.source_revision,
                review_id: review.review_id,
              },
        };
        const current: Attempt = {
          command,
          removalId:
            review.removal_id ?? command.command_id.replaceAll('-', ''),
          receipt: null,
        };
        retained.push(current);
        attempt = current;
        emit({ review: null, pending: true });
        // Exactly one dispatch per retained command, including lost responses.
        const receipt = await read(() =>
          transport.execute(structuredClone(command)),
        );
        accept(current, receipt);
        return receipt;
      }),
    refresh: () =>
      run(async () => {
        const current = attempt;
        if (!current) throw new Error('document_operation_unavailable');
        const receipt = await read(() =>
          transport.receipt(current.command.command_id),
        );
        accept(current, receipt);
        return receipt;
      }),
    purge() {
      epoch += 1;
      attempt = null;
      retained.length = 0;
      emit({
        review: null,
        receipt: null,
        busy: false,
        pending: false,
        revoked: true,
        error: '',
      });
    },
  };
}
export type DocumentRemovalSession = ReturnType<
  typeof createDocumentRemovalSession
>;

const stages: Record<string, string> = {
  worker: 'Worker stop',
  derived_snapshot: 'Derived knowledge capture',
  index: 'Search index',
  source: 'Saved source',
  raw_copy: 'Vault raw copy',
  derived_knowledge: 'Derived knowledge',
  markers: 'Processed markers',
  record: 'Document record',
  legacy_index: 'Legacy search index',
  bulk_removal: 'Captured documents',
};
export default function DocumentRemoval({
  session,
  label,
}: {
  session: DocumentRemovalSession;
  label: string;
}) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  if (state.revoked) return null;
  const result = state.receipt?.removal;
  return (
    <section
      aria-label="Document removal"
      aria-busy={state.busy}
      className="stack"
    >
      <p>
        Remove {label} from search and delete its extracted knowledge. Recovery
        copies and externally edited files are retained.
      </p>
      {!state.review &&
        !state.pending &&
        (!result || result.status === 'complete') && (
          <Button
            disabled={state.busy}
            onClick={() => void session.review().catch(() => {})}
          >
            {session.documentId === null
              ? 'Review clear all documents'
              : 'Review document removal'}
          </Button>
        )}
      {state.review && (
        <section aria-label="Review document removal" className="surface stack">
          <p>
            {state.review.source_command_id
              ? 'Continue only the unfinished stages of the original removal.'
              : `${state.review.source_count.toLocaleString()} saved source(s) are included in this request.`}
          </p>
          <p>
            Your retained copies remain available for recovery. This action does
            not delete unrelated sources added later.
          </p>
          <Button
            disabled={state.busy}
            onClick={() => void session.confirm().catch(() => {})}
          >
            {state.review.source_command_id
              ? 'Confirm remaining cleanup'
              : 'Confirm document removal'}
          </Button>
          <Button
            variant="ghost"
            disabled={state.busy}
            onClick={() => session.dismissReview()}
          >
            Cancel review
          </Button>
        </section>
      )}
      {result && (
        <section
          aria-label="Saved removal outcome"
          role="status"
          className="surface stack"
        >
          <p>
            {result.status === 'complete'
              ? 'Removal complete.'
              : result.status === 'pending'
                ? 'Waiting for the document worker to stop. Refresh status before continuing cleanup.'
                : 'Removal is incomplete. Completed stages are saved.'}
          </p>
          <p>
            Derived knowledge removed:{' '}
            {result.derived_entities_removed.toLocaleString()}
          </p>
          <p>
            Retained recovery references:{' '}
            {result.retained_copy_count.toLocaleString()}
          </p>
          <p>
            Complete stages: {result.stage_counts.complete.toLocaleString()} ·
            Pending: {result.stage_counts.pending.toLocaleString()} · Partial:{' '}
            {result.stage_counts.partial.toLocaleString()}
          </p>
          {result.stages.length > 0 && (
            <ul>
              {result.stages.map((stage) => (
                <li key={stage.stage}>
                  {stages[stage.stage] ?? 'Saved stage'}: {stage.status}
                </li>
              ))}
            </ul>
          )}
          {result.status !== 'complete' && !state.pending && !state.review && (
            <Button
              disabled={state.busy}
              onClick={() => void session.reviewRetry().catch(() => {})}
            >
              Review remaining cleanup
            </Button>
          )}
        </section>
      )}
      {state.receipt?.status === 'rejected' && (
        <p role="status">
          The original command was rejected before removal started. Review the
          current source again.
        </p>
      )}
      {(state.pending || state.receipt) && (
        <Button
          disabled={state.busy}
          onClick={() => void session.refresh().catch(() => {})}
        >
          Refresh original removal
        </Button>
      )}
      {state.error && (
        <ErrorState title="Document removal needs attention">
          {state.error}
        </ErrorState>
      )}
    </section>
  );
}
