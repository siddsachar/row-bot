import { useEffect, useSyncExternalStore } from 'react';
import { CircleX, Eye, Files, RefreshCw } from 'lucide-react';
import { Button, IconButton } from '../../ui/primitives';
import { batchTitle, documentFailure, jobStatus } from './document-words';

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
  /** A batch's document count; its name is its first document's. */
  document_count?: number | null;
};
/** How often the queue reads itself again while documents are being added. */
export const QUEUE_POLL_MS = 3000;
const BATCH_STATUS: Record<string, string> = {
  staging: 'Upload not finished',
  queued: 'Queued',
  running: 'Processing',
  paused: 'Paused',
  completed: 'Completed',
  completed_with_errors: 'Completed with errors',
  cancelled: 'Cancelled',
};
function batchStatus(item: DocumentQueueItem) {
  const status = BATCH_STATUS[item.status] ?? item.status;
  return item.cancel_requested && !terminal.has(item.status)
    ? `${status} · cancelling`
    : status;
}

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

/** A confirmed action's outcome, in words. */
function outcomeWords(receipt: DocumentControlReceipt) {
  switch (receipt.outcome) {
    case 'paused':
      return 'Paused.';
    case 'resumed':
      return 'Resumed.';
    case 'cancellation_requested':
      return receipt.saved_status === 'cancelled'
        ? 'Cancelled.'
        : 'Cancelling. Row-Bot stops after the step it is on.';
    case 'retried':
      return 'Trying again.';
    case 'cleared':
      return receipt.count === 1
        ? 'Cleared 1 finished upload from the list.'
        : `Cleared ${receipt.count ?? 0} finished uploads from the list.`;
    default:
      return 'Done.';
  }
}

const ACTION_QUESTIONS: Record<DocumentJobAction, string> = {
  'document.batch.pause': 'Pause this batch?',
  'document.batch.resume': 'Resume this batch?',
  'document.batch.cancel': 'Cancel the rest of this batch?',
  'document.job.cancel': 'Cancel this document?',
  'document.job.retry': 'Retry this document?',
  'document.jobs.clear_finished': 'Clear the selected finished batches?',
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
  // A completed action changed the queue: read it again so the rows show
  // the new state (cancelled, paused, cleared) without a manual refresh.
  // Receipt recovery stays receipt-only.
  async function showCompleted() {
    if (state.receipt?.status !== 'completed') return;
    const batches = page(await read(() => transport.batches()));
    const batchId =
      state.batchId && batches.items.some((item) => item.id === state.batchId)
        ? state.batchId
        : null;
    const jobs = batchId
      ? page(await read(() => transport.jobs(batchId)))
      : null;
    emit({ batches, batchId, jobs, selected: [] });
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
    /**
     * Read the queue again while documents are being added, so its states
     * move on by themselves. Never while you are choosing or confirming.
     */
    async poll() {
      const idle = () =>
        !operation &&
        !state.pending &&
        !state.review &&
        !state.revoked &&
        state.selected.length === 0;
      if (!state.batches || !idle()) return;
      const ticket = epoch;
      const batches = page(await read(() => transport.batches()));
      const batchId =
        state.batchId && batches.items.some((item) => item.id === state.batchId)
          ? state.batchId
          : null;
      const jobs = batchId
        ? page(await read(() => transport.jobs(batchId)))
        : null;
      if (ticket !== epoch || !idle()) return;
      emit({ batches, batchId, jobs });
    },
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
        await showCompleted();
      }),
    async start(action: DocumentJobAction, id?: string) {
      await this.review(action, id);
      await this.confirm();
    },
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

/** A batch's dot: working, waiting for you, or finished. */
function batchTone(item: DocumentQueueItem) {
  if (item.status === 'running') return 'info' as const;
  if (item.status === 'paused' || item.pause_requested)
    return 'warning' as const;
  if (item.status === 'completed') return 'success' as const;
  if (item.status === 'completed_with_errors') return 'danger' as const;
  return undefined;
}

/**
 * The documents being added, as rows at the top of "Your documents" (B258):
 * each batch shows its state and its next step (Process, Resume or Pause)
 * in the row, with Inspect and Cancel as icons; several paused batches get
 * "Resume all". Every action keeps its reviewed command.
 */
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
  const items = state.batches?.items ?? [];
  const paused = items.filter(
    (item) =>
      item.pause_requested &&
      !terminal.has(item.status) &&
      !item.cancel_requested,
  );
  const resumeAll = async () => {
    for (const item of paused)
      await session.start('document.batch.resume', item.id);
  };
  // Documents waiting or being read: check on them until they finish.
  const active =
    !state.revoked &&
    items.some(
      (item) =>
        item.status === 'queued' ||
        item.status === 'running' ||
        (item.cancel_requested && !terminal.has(item.status)),
    );
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(
      () => void session.poll().catch(() => undefined),
      QUEUE_POLL_MS,
    );
    return () => clearInterval(timer);
  }, [active, session]);
  return (
    <section aria-label="Document ingestion queue" className="document-queue">
      <div className="settings-divided document-queue-head">
        <span>
          {!state.batches
            ? 'Documents being added'
            : items.length
              ? `Being added · ${state.batches.total ?? items.length}`
              : 'Nothing being added'}
        </span>
        {paused.length > 1 && (
          <Button
            variant="ghost"
            className="settings-link"
            disabled={disabled}
            onClick={() => invoke(resumeAll)}
          >
            Resume all
          </Button>
        )}
        <IconButton
          size="sm"
          label="Refresh queue"
          disabled={disabled}
          onClick={() => invoke(session.load)}
        >
          <RefreshCw size={14} aria-hidden />
        </IconButton>
      </div>
      {state.error && (
        <p role="alert" className="settings-divided document-queue-note">
          {state.error}
        </p>
      )}
      {items.map((item) => (
        <div key={item.id} className="settings-divided document-batch-row">
          <span className="settings-row-icon" data-tone="5" aria-hidden>
            <Files size={16} aria-hidden />
          </span>
          <div className="document-batch-summary">
            <strong>{batchTitle(item)}</strong>
            <small className="status-indicator" data-tone={batchTone(item)}>
              <span
                className="status-indicator-dot"
                data-pulse={item.status === 'running' ? 'true' : undefined}
                aria-hidden
              />
              <span>{batchStatus(item)}</span>
            </small>
          </div>
          <div className="document-batch-actions">
            {onProcess &&
              item.id.startsWith('client_') &&
              item.status === 'paused' &&
              !item.cancel_requested && (
                <Button
                  variant="ghost"
                  className="settings-link"
                  disabled={disabled}
                  aria-label={`Process ${batchTitle(item)}`}
                  onClick={() => onProcess(item)}
                >
                  Process
                </Button>
              )}
            {!terminal.has(item.status) && (
              <Button
                variant="ghost"
                className="settings-link"
                disabled={disabled}
                onClick={() =>
                  invoke(() =>
                    session.start(
                      item.pause_requested
                        ? 'document.batch.resume'
                        : 'document.batch.pause',
                      item.id,
                    ),
                  )
                }
              >
                {item.pause_requested ? 'Resume' : 'Pause'}
              </Button>
            )}
            <IconButton
              size="sm"
              label={`Show files in ${batchTitle(item)}`}
              disabled={disabled}
              onClick={() => invoke(() => session.openBatch(item.id))}
            >
              <Eye size={15} aria-hidden />
            </IconButton>
            {terminal.has(item.status) ? (
              <label className="document-batch-select">
                <input
                  type="checkbox"
                  aria-label={`Select ${batchTitle(item)}`}
                  checked={state.selected.includes(item.id)}
                  disabled={disabled}
                  onChange={(event) =>
                    session.selectFinished(item.id, event.target.checked)
                  }
                />
                Select
              </label>
            ) : (
              <IconButton
                size="sm"
                label="Cancel remaining"
                disabled={disabled}
                onClick={() =>
                  invoke(() => session.review('document.batch.cancel', item.id))
                }
              >
                <CircleX size={15} aria-hidden />
              </IconButton>
            )}
          </div>
        </div>
      ))}
      {(state.batches?.next_cursor || state.selected.length > 0) && (
        <div className="settings-divided document-queue-foot">
          {state.batches?.next_cursor && (
            <Button
              variant="ghost"
              disabled={disabled}
              onClick={() => invoke(session.nextBatches)}
            >
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
              Clear selected finished
            </Button>
          )}
        </div>
      )}
      {state.jobs && (
        <div className="settings-divided document-jobs">
          <p className="document-queue-note">
            {state.jobs.total === 1
              ? '1 file in this upload'
              : `${state.jobs.total ?? 'Some'} files in this upload`}
          </p>
          {state.jobs.items.map((item) => (
            <div key={item.id} className="document-job-row">
              <div className="document-batch-summary">
                <strong>{item.name}</strong>
                <small>
                  {jobStatus(item.status)}
                  {item.index_total !== null && item.index_total > 0 && (
                    <>
                      {' · '}Read {item.index_current ?? 0} of{' '}
                      {item.index_total}
                    </>
                  )}
                  {item.extraction_total !== null &&
                    item.extraction_total > 0 && (
                      <>
                        {' · '}Knowledge {item.extraction_current ?? 0} of{' '}
                        {item.extraction_total}
                      </>
                    )}
                </small>
                {item.cancel_requested && !terminal.has(item.status) && (
                  <small>Cancelling after the step it is on.</small>
                )}
                {documentFailure(item.status, item.error_code) && (
                  <small>{documentFailure(item.status, item.error_code)}</small>
                )}
              </div>
              {item.status === 'failed' ? (
                <Button
                  variant="ghost"
                  disabled={disabled}
                  onClick={() =>
                    invoke(() => session.start('document.job.retry', item.id))
                  }
                >
                  Retry {item.name}
                </Button>
              ) : (
                !['completed', 'cancelled', 'skipped_duplicate'].includes(
                  item.status,
                ) && (
                  <Button
                    variant="ghost"
                    disabled={disabled}
                    onClick={() =>
                      invoke(() =>
                        session.review('document.job.cancel', item.id),
                      )
                    }
                  >
                    Cancel {item.name}
                  </Button>
                )
              )}
            </div>
          ))}
          {state.jobs.next_cursor && (
            <Button
              variant="ghost"
              disabled={disabled}
              onClick={() => invoke(session.nextJobs)}
            >
              Next jobs
            </Button>
          )}
        </div>
      )}
      {state.review && (
        <div
          role="group"
          aria-label="Confirm document queue action"
          className="settings-divided settings-confirm-change document-queue-confirm"
        >
          <p>
            <strong>{ACTION_QUESTIONS[state.review.action]}</strong>
          </p>
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
          <div className="action-cluster">
            <Button
              variant="primary"
              disabled={disabled}
              onClick={() => invoke(session.confirm)}
            >
              {state.review.action === 'document.jobs.clear_finished'
                ? 'Confirm clear selected'
                : 'Confirm cancellation'}
            </Button>
          </div>
        </div>
      )}
      {state.pending && (
        <div className="settings-divided document-queue-foot">
          <Button
            disabled={state.busy || state.revoked}
            onClick={() => invoke(session.refresh)}
          >
            Refresh original queue command
          </Button>
        </div>
      )}
      {state.receipt && (
        <p role="status" className="settings-divided document-queue-note">
          {state.receipt.status === 'completed'
            ? outcomeWords(state.receipt)
            : state.receipt.status === 'partial'
              ? 'The original outcome is uncertain. Do not repeat the action.'
              : 'The queue action was rejected.'}
        </p>
      )}
    </section>
  );
}
