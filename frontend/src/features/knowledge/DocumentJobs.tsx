import { useSyncExternalStore } from 'react';
import { Button, ErrorState } from '../../ui/primitives';

export type DocumentQueueItem = {
  id: string;
  batch_id: string | null;
  name: string;
  status: string;
  stage: string | null;
  pause_requested: boolean;
  cancel_requested: boolean;
  attempt: number | null;
  index_current: number | null;
  index_total: number | null;
  extraction_current: number | null;
  extraction_total: number | null;
  error_code: string | null;
  revision: string;
};
export type DocumentQueuePage = {
  revision: string;
  items: DocumentQueueItem[];
  total: number | null;
  next_cursor: string | null;
  availability: 'available' | 'missing' | 'unavailable';
};
export type DocumentJobAction =
  | 'document.batch.pause'
  | 'document.batch.resume'
  | 'document.batch.cancel'
  | 'document.job.cancel'
  | 'document.job.retry'
  | 'document.jobs.clear_finished';
export type DocumentControlReview = {
  review_id: string;
  action: DocumentJobAction;
  target_id: string | null;
  batch_ids: string[];
  revision: string;
  intent_digest: string;
  provider_work: boolean;
  retains_work: boolean;
};
export type DocumentControlCommand = {
  command_id: string;
  type: DocumentJobAction;
  payload: Record<string, unknown>;
};
export type DocumentControlReceipt = {
  command_id: string;
  status: 'completed' | 'rejected' | 'partial';
  code?: string;
  outcome?:
    'paused' | 'resumed' | 'cancellation_requested' | 'retried' | 'cleared';
  target_id?: string | null;
  batch_ids?: string[];
  saved_status?: string | null;
  count?: number;
  retained_work?: boolean;
};
export type DocumentJobsTransport = {
  batches(cursor?: string): Promise<DocumentQueuePage>;
  jobs(batchId: string, cursor?: string): Promise<DocumentQueuePage>;
  review(
    action: DocumentJobAction,
    payload: Record<string, unknown>,
  ): Promise<DocumentControlReview>;
  execute(command: DocumentControlCommand): Promise<DocumentControlReceipt>;
  receipt(commandId: string): Promise<DocumentControlReceipt>;
};
type State = {
  batches: DocumentQueuePage | null;
  jobs: DocumentQueuePage | null;
  batchId: string | null;
  selected: string[];
  review: DocumentControlReview | null;
  receipt: DocumentControlReceipt | null;
  busy: boolean;
  pending: boolean;
  revoked: boolean;
  error: string;
};
const terminal = new Set(['completed', 'completed_with_errors', 'cancelled']);
const outcomes: Record<DocumentJobAction, DocumentControlReceipt['outcome']> = {
  'document.batch.pause': 'paused',
  'document.batch.resume': 'resumed',
  'document.batch.cancel': 'cancellation_requested',
  'document.job.cancel': 'cancellation_requested',
  'document.job.retry': 'retried',
  'document.jobs.clear_finished': 'cleared',
};

/** Root retains this session for the authenticated lifetime, never browser storage. */
export function createDocumentJobsSession(
  transport: DocumentJobsTransport,
  guard: () => void,
  newId: () => string = () => crypto.randomUUID(),
) {
  let state: State = {
    batches: null,
    jobs: null,
    batchId: null,
    selected: [],
    review: null,
    receipt: null,
    busy: false,
    pending: false,
    revoked: false,
    error: '',
  };
  let epoch = 0;
  let operation: Promise<unknown> | null = null;
  let payload: Record<string, unknown> | null = null;
  type Attempt = {
    command: DocumentControlCommand;
    review: DocumentControlReview;
  };
  // Retain the current exact original; settled history lives in server receipts.
  // Never keep unreachable historical payloads or File references here.
  let attempt: Attempt | null = null;
  const listeners = new Set<() => void>();
  const emit = (patch: Partial<State>) => {
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  };
  function authority(ticket = epoch) {
    if (ticket !== epoch || state.revoked)
      throw new Error('authentication_required');
    guard();
  }
  async function read<T>(call: () => Promise<T>) {
    const ticket = epoch;
    authority(ticket);
    const value = await call();
    authority(ticket);
    return value;
  }
  async function run<T>(call: () => Promise<T>, recovery = false) {
    authority();
    if (operation || (state.pending && !recovery))
      throw new Error('document_control_pending');
    const ticket = epoch;
    emit({ busy: true, error: '' });
    const promise = Promise.resolve().then(call);
    operation = promise;
    try {
      return await promise;
    } catch (error) {
      if (ticket === epoch)
        emit({
          error:
            'The queue action could not be confirmed. Refresh the original command if its outcome is unknown.',
        });
      throw error;
    } finally {
      if (operation === promise) operation = null;
      if (ticket === epoch) emit({ busy: false });
    }
  }
  function page(value: DocumentQueuePage) {
    if (value.availability === 'unavailable')
      throw new Error('document_queue_unavailable');
    return value;
  }
  function accept(value: DocumentControlReceipt) {
    if (!attempt || value.command_id !== attempt.command.command_id)
      throw new Error('document_receipt_mismatch');
    if (
      value.status === 'completed' &&
      (value.outcome !== outcomes[attempt.command.type] ||
        value.target_id !== attempt.review.target_id ||
        JSON.stringify(value.batch_ids) !==
          JSON.stringify(attempt.review.batch_ids))
    )
      throw new Error('document_receipt_mismatch');
    if (!['completed', 'rejected', 'partial'].includes(value.status))
      throw new Error('document_receipt_mismatch');
    emit({
      receipt: value,
      pending: value.status === 'partial',
      review: value.status === 'partial' ? state.review : null,
    });
  }
  const session = {
    getSnapshot: () => state,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    isPending: () => state.pending || state.busy,
    load: () =>
      run(async () => {
        const batches = page(await read(() => transport.batches()));
        payload = null;
        emit({
          batches,
          jobs: null,
          batchId: null,
          selected: [],
          review: null,
        });
      }),
    nextBatches: () =>
      run(async () => {
        const cursor = state.batches?.next_cursor;
        if (!cursor) return;
        const previous = state.batches?.revision;
        const batches = page(await read(() => transport.batches(cursor)));
        if (batches.revision !== previous)
          throw new Error('document_queue_changed');
        payload = null;
        emit({
          batches,
          jobs: null,
          batchId: null,
          selected: [],
          review: null,
        });
      }),
    openBatch: (id: string) =>
      run(async () => {
        if (!state.batches?.items.some((item) => item.id === id))
          throw new Error('invalid_document_target');
        const jobs = page(await read(() => transport.jobs(id)));
        payload = null;
        emit({ jobs, batchId: id, review: null });
      }),
    nextJobs: () =>
      run(async () => {
        const id = state.batchId,
          cursor = state.jobs?.next_cursor;
        if (!id || !cursor) return;
        const previous = state.jobs?.revision;
        const jobs = page(await read(() => transport.jobs(id, cursor)));
        if (jobs.revision !== previous)
          throw new Error('document_queue_changed');
        payload = null;
        emit({ jobs, review: null });
      }),
    selectFinished(id: string, selected: boolean) {
      authority();
      if (operation || state.pending)
        throw new Error('document_control_pending');
      const item = state.batches?.items.find((item) => item.id === id);
      if (!item || !terminal.has(item.status))
        throw new Error('invalid_document_target');
      payload = null;
      emit({
        selected: selected
          ? [...new Set([...state.selected, id])]
          : state.selected.filter((value) => value !== id),
        review: null,
      });
    },
    review: (action: DocumentJobAction, id?: string) =>
      run(async () => {
        let draft: Record<string, unknown>;
        let target: string | null = null;
        let batches: string[];
        if (action === 'document.jobs.clear_finished') {
          const items =
            state.batches?.items.filter((item) =>
              state.selected.includes(item.id),
            ) ?? [];
          if (!items.length || items.some((item) => !terminal.has(item.status)))
            throw new Error('invalid_document_target');
          draft = {
            targets: items.map((item) => ({
              id: item.id,
              revision: item.revision,
            })),
          };
          batches = items.map((item) => item.id);
        } else {
          const job = action.startsWith('document.job.');
          const item = (job ? state.jobs : state.batches)?.items.find(
            (item) => item.id === id,
          );
          if (!item || (job && item.batch_id !== state.batchId))
            throw new Error('invalid_document_target');
          target = item.id;
          batches = [item.batch_id ?? item.id];
          draft = { target_id: item.id, revision: item.revision };
        }
        const review = await read(() =>
          transport.review(action, structuredClone(draft)),
        );
        if (
          review.action !== action ||
          !review.review_id ||
          review.target_id !== target ||
          JSON.stringify(review.batch_ids) !== JSON.stringify(batches)
        )
          throw new Error('document_review_mismatch');
        payload = draft;
        emit({ review: structuredClone(review), receipt: null });
      }),
    confirm: () =>
      run(async () => {
        if (!state.review || !payload)
          throw new Error('document_review_required');
        const command: DocumentControlCommand = {
          command_id: newId(),
          type: state.review.action,
          payload: {
            ...structuredClone(payload),
            review_id: state.review.review_id,
          },
        };
        attempt = { command, review: structuredClone(state.review) };
        emit({ pending: true });
        accept(await read(() => transport.execute(structuredClone(command))));
      }),
    refresh: () =>
      run(async () => {
        if (!attempt) throw new Error('document_command_required');
        accept(
          await read(() => transport.receipt(attempt!.command.command_id)),
        );
      }, true),
    purge() {
      epoch += 1;
      attempt = null;
      payload = null;
      emit({
        batches: null,
        jobs: null,
        batchId: null,
        selected: [],
        review: null,
        receipt: null,
        pending: false,
        busy: false,
        revoked: true,
        error: '',
      });
    },
  };
  return session;
}
export type DocumentJobsSession = ReturnType<typeof createDocumentJobsSession>;

export function DocumentJobs({
  session,
  onProcess,
}: {
  session: DocumentJobsSession;
  onProcess?: (batch: DocumentQueueItem) => void;
}) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const disabled = state.busy || state.pending || state.revoked;
  const invoke = (call: () => Promise<unknown>) => {
    void call().catch(() => undefined);
  };
  return (
    <section aria-label="Document ingestion queue">
      <h3>Ingestion queue</h3>
      <p>
        Documents can become searchable before knowledge extraction finishes.
      </p>
      <Button disabled={disabled} onClick={() => invoke(session.load)}>
        Refresh queue
      </Button>
      {state.error && (
        <ErrorState title="Queue action needs attention">
          {state.error}
        </ErrorState>
      )}
      {state.batches && <p>{state.batches.total ?? 'Unknown'} saved batches</p>}
      {state.batches?.items.map((item) => (
        <div key={item.id}>
          <p>Batch · {item.status}</p>
          {onProcess &&
            item.id.startsWith('client_') &&
            item.status === 'paused' &&
            !item.cancel_requested && (
              <Button disabled={disabled} onClick={() => onProcess(item)}>
                Review processing {item.id}
              </Button>
            )}
          <Button
            disabled={disabled}
            onClick={() => invoke(() => session.openBatch(item.id))}
          >
            Inspect batch {item.id}
          </Button>
          {terminal.has(item.status) ? (
            <label>
              <input
                type="checkbox"
                checked={state.selected.includes(item.id)}
                disabled={disabled}
                onChange={(event) =>
                  session.selectFinished(item.id, event.target.checked)
                }
              />
              Select finished batch {item.id}
            </label>
          ) : (
            <>
              <Button
                disabled={disabled}
                onClick={() =>
                  invoke(() =>
                    session.review(
                      item.pause_requested
                        ? 'document.batch.resume'
                        : 'document.batch.pause',
                      item.id,
                    ),
                  )
                }
              >
                {item.pause_requested ? 'Review resume' : 'Review pause'}
              </Button>
              <Button
                disabled={disabled}
                onClick={() =>
                  invoke(() => session.review('document.batch.cancel', item.id))
                }
              >
                Review cancel remaining
              </Button>
            </>
          )}
        </div>
      ))}
      {state.batches?.next_cursor && (
        <Button disabled={disabled} onClick={() => invoke(session.nextBatches)}>
          Next batches
        </Button>
      )}
      {state.selected.length > 0 && (
        <Button
          disabled={disabled}
          onClick={() =>
            invoke(() => session.review('document.jobs.clear_finished'))
          }
        >
          Review clear selected finished
        </Button>
      )}
      {state.jobs && (
        <p>{state.jobs.total ?? 'Unknown'} saved jobs in this batch</p>
      )}
      {state.jobs?.items.map((item) => (
        <div key={item.id}>
          <p>
            {item.name} · {item.status} · {item.stage}
          </p>
          {item.cancel_requested && (
            <p>
              Cancellation requested. Active work must acknowledge the request.
            </p>
          )}
          {item.error_code && <p>{item.error_code}</p>}
          {item.index_total !== null && item.index_total > 0 && (
            <p>
              Index {item.index_current ?? 0}/{item.index_total}
            </p>
          )}
          {item.extraction_total !== null && item.extraction_total > 0 && (
            <p>
              Knowledge {item.extraction_current ?? 0}/{item.extraction_total}
            </p>
          )}
          {item.status === 'failed' ? (
            <Button
              disabled={disabled}
              onClick={() =>
                invoke(() => session.review('document.job.retry', item.id))
              }
            >
              Review retry {item.name}
            </Button>
          ) : (
            !['completed', 'cancelled', 'skipped_duplicate'].includes(
              item.status,
            ) && (
              <Button
                disabled={disabled}
                onClick={() =>
                  invoke(() => session.review('document.job.cancel', item.id))
                }
              >
                Review cancel {item.name}
              </Button>
            )
          )}
        </div>
      ))}
      {state.jobs?.next_cursor && (
        <Button disabled={disabled} onClick={() => invoke(session.nextJobs)}>
          Next jobs
        </Button>
      )}
      {state.review && (
        <div aria-label="Document queue action review">
          <p>Reviewed action: {state.review.action}</p>
          {state.review.provider_work && (
            <p>
              This explicitly resumes queued parsing, embedding or knowledge
              work under the current provider and approval policy.
            </p>
          )}
          {state.review.retains_work && (
            <p>
              Existing source and work copies are retained for recovery.
              Clearing finished removes only selected queue records.
            </p>
          )}
          <Button disabled={disabled} onClick={() => invoke(session.confirm)}>
            Confirm queue action
          </Button>
        </div>
      )}
      {state.pending && (
        <Button
          disabled={state.busy || state.revoked}
          onClick={() => invoke(session.refresh)}
        >
          Refresh original queue command
        </Button>
      )}
      {state.receipt && (
        <p role="status">
          {state.receipt.status === 'completed'
            ? `Saved queue outcome: ${state.receipt.outcome}. ${state.receipt.saved_status ?? ''}`
            : state.receipt.status === 'partial'
              ? 'The original outcome is uncertain. Do not repeat the action.'
              : 'The queue action was rejected.'}
        </p>
      )}
    </section>
  );
}
