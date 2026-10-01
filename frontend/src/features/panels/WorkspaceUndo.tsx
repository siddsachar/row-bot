import { useEffect, useMemo, useSyncExternalStore } from 'react';
import { clientError } from '../../api/errors';
import { Button, ErrorState } from '../../ui/primitives';

export type WorkspaceUndoReview = {
  resource_id: string;
  conversation_id: string;
  resource_revision: string;
  binding_id: string;
  binding_revision: string;
  change_set_id: string;
  change_set_revision: string;
  host_revision: string;
  policy_revision: string;
  policy_decision: 'allow' | 'ask' | 'block';
  approval_required: boolean;
  files: string[];
  directories_retained: string[];
  action_digest: string;
  nonce: string;
};
export type WorkspaceUndoResult = {
  command_id: string;
  resource_id: string;
  conversation_id: string;
  change_set_id: string;
  status: 'undone' | 'partial' | 'conflict' | 'denied';
  files_restored: string[];
  ledger_saved: boolean;
  reverted: boolean;
  code?: string;
};
export type WorkspaceUndoProps = {
  scope: string;
  changeSetId: string;
  session?: WorkspaceUndoSession;
  review: (
    changeSetId: string,
    signal?: AbortSignal,
  ) => Promise<WorkspaceUndoReview>;
  apply: (
    review: WorkspaceUndoReview,
    commandId: string,
  ) => Promise<WorkspaceUndoResult>;
  recover: (
    review: WorkspaceUndoReview,
    commandId: string,
  ) => Promise<WorkspaceUndoResult>;
  receipt: (
    commandId: string,
    signal?: AbortSignal,
  ) => Promise<WorkspaceUndoResult | null>;
  onUndone?: () => void;
  /** The change's own description, for the confirmation. */
  summary?: string;
  /** Close the confirmation without changing anything. */
  onCancel?: () => void;
};
type Pending = { review: WorkspaceUndoReview; commandId: string };
type State = {
  active: boolean;
  reading: boolean;
  busy: boolean;
  review: WorkspaceUndoReview | null;
  pending: Pending | null;
  result: WorkspaceUndoResult | null;
  error: string;
  notice: string;
};
const initial = (): State => ({
  active: true,
  reading: false,
  busy: false,
  review: null,
  pending: null,
  result: null,
  error: '',
  notice: '',
});
const copyReview = (review: WorkspaceUndoReview): WorkspaceUndoReview => ({
  ...review,
  files: [...review.files],
  directories_retained: [...review.directories_retained],
});

/** One immutable Undo intent in the authenticated controller lifetime. */
export class WorkspaceUndoSession {
  private state = initial();
  private listeners = new Set<() => void>();
  private read: AbortController | null = null;
  constructor(readonly scope: string) {}
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private publish(value: Partial<State>) {
    if (!this.state.active) return;
    this.state = { ...this.state, ...value };
    this.listeners.forEach((listener) => listener());
  }
  hasRetained() {
    return !!(this.state.review || this.state.pending || this.state.busy);
  }
  dispose() {
    this.read?.abort();
    this.read = null;
    this.state = { ...initial(), active: false };
    this.listeners.forEach((listener) => listener());
  }
  cancelReview() {
    if (this.state.busy || this.state.pending) return;
    this.read?.abort();
    this.read = null;
    this.publish({
      review: null,
      reading: false,
      error: '',
      notice: 'Undo review cancelled. Files are unchanged.',
    });
  }
  async review(io: WorkspaceUndoProps) {
    if (
      !this.state.active ||
      this.state.busy ||
      this.state.reading ||
      this.state.pending ||
      io.scope !== this.scope
    )
      return;
    this.read?.abort();
    const abort = new AbortController();
    this.read = abort;
    this.publish({
      reading: true,
      review: null,
      result: null,
      error: '',
      notice: '',
    });
    try {
      const review = await io.review(io.changeSetId, abort.signal);
      if (this.read !== abort || abort.signal.aborted) return;
      if (review.change_set_id !== io.changeSetId)
        throw new Error('Undo review target did not match.');
      this.publish({ review: copyReview(review) });
    } catch (error) {
      if (this.read === abort && !abort.signal.aborted)
        this.publish({ error: clientError(error).message });
    } finally {
      if (this.read === abort) {
        this.read = null;
        this.publish({ reading: false });
      }
    }
  }
  async start(io: WorkspaceUndoProps) {
    if (this.state.busy || this.state.reading || this.state.pending) return;
    await this.review(io);
    if (this.state.review?.policy_decision !== 'block') await this.execute(io);
  }
  async execute(io: WorkspaceUndoProps, recover = false) {
    if (
      !this.state.active ||
      this.state.busy ||
      this.state.reading ||
      io.scope !== this.scope
    )
      return;
    let pending = this.state.pending;
    if (!recover) {
      if (
        pending ||
        !this.state.review ||
        this.state.review.policy_decision === 'block'
      )
        return;
      pending = {
        review: copyReview(this.state.review),
        commandId: crypto.randomUUID(),
      };
    }
    if (!pending) return;
    this.publish({
      pending,
      review: pending.review,
      busy: true,
      error: '',
      notice: '',
    });
    try {
      const result = await (recover ? io.recover : io.apply)(
        copyReview(pending.review),
        pending.commandId,
      );
      this.accept(result, pending, io);
    } catch (error) {
      this.publish({
        error: clientError(error).message,
        notice:
          "Row-Bot couldn't confirm the undo. Check again, or retry the same undo.",
      });
    } finally {
      this.publish({ busy: false });
    }
  }
  private accept(
    result: WorkspaceUndoResult,
    pending: Pending,
    io: WorkspaceUndoProps,
  ) {
    if (!this.state.active) return;
    if (
      result.command_id !== pending.commandId ||
      result.change_set_id !== pending.review.change_set_id ||
      result.resource_id !== pending.review.resource_id ||
      result.conversation_id !== pending.review.conversation_id
    ) {
      throw new Error('Undo receipt target did not match.');
    }
    if (result.status === 'undone' && result.reverted && result.ledger_saved) {
      this.publish({
        pending: null,
        review: null,
        result,
        error: '',
        notice: 'Original files restored. Created directories remain.',
      });
      io.onUndone?.();
    } else if (
      (result.status === 'conflict' || result.status === 'denied') &&
      !result.files_restored.length &&
      !result.reverted
    ) {
      this.publish({
        pending: null,
        review: null,
        result,
        notice:
          'Undo did not complete. Review the current files and policy before another attempt.',
      });
    } else {
      this.publish({
        result,
        notice:
          'Undo is incomplete. Keep this original operation for recovery.',
      });
    }
  }
  async check(io: WorkspaceUndoProps) {
    const pending = this.state.pending;
    if (
      !pending ||
      !this.state.active ||
      this.state.busy ||
      this.state.reading ||
      io.scope !== this.scope
    )
      return;
    const abort = new AbortController();
    this.read = abort;
    this.publish({ reading: true, error: '' });
    try {
      const result = await io.receipt(pending.commandId, abort.signal);
      if (this.read !== abort || abort.signal.aborted) return;
      if (result) this.accept(result, pending, io);
      else
        this.publish({
          notice:
            "Row-Bot can't confirm what happened. The undo is kept so it can be checked.",
        });
    } catch (error) {
      if (this.read === abort && !abort.signal.aborted)
        this.publish({ error: clientError(error).message });
    } finally {
      if (this.read === abort) {
        this.read = null;
        this.publish({ reading: false });
      }
    }
  }
}

export default function WorkspaceUndo(props: WorkspaceUndoProps) {
  const owned = useMemo(
    () => props.session ?? new WorkspaceUndoSession(props.scope),
    [props.session, props.scope],
  );
  useEffect(
    () => () => {
      if (!props.session) owned.dispose();
    },
    [owned, props.session],
  );
  const state = useSyncExternalStore(owned.subscribe, owned.getSnapshot);
  if (!state.active || owned.scope !== props.scope)
    return <p role="status">Undo is unavailable for this workspace session.</p>;
  const reviewed = state.review;
  const done = state.result && !state.pending;
  return (
    <section className="dev-undo" aria-label="Undo workspace changes">
      <strong>
        {done
          ? 'Change undone'
          : state.pending
            ? 'Undo not confirmed'
            : `Undo ${props.summary ? `“${props.summary}”` : 'this agent change'}?`}
      </strong>
      {/* A retained undo names its own change, whatever is selected now. */}
      {(state.pending?.review ?? reviewed) && (
        <p className="muted">
          Change set: {(state.pending?.review ?? reviewed)?.change_set_id}
        </p>
      )}
      {!done && !state.pending && (
        <p>
          The files this change touched go back to how they were before it.
          Edits made after it are kept.
        </p>
      )}
      {state.error && (
        <ErrorState title="Undo needs attention">{state.error}</ErrorState>
      )}
      {state.notice && <p role="status">{state.notice}</p>}
      {state.result && (
        <p>
          {state.result.files_restored.length}{' '}
          {state.result.files_restored.length === 1
            ? 'file restored'
            : 'files restored'}{' '}
          · {state.result.status}
        </p>
      )}
      {reviewed && (
        <>
          <ul aria-label="Files this undo restores">
            {reviewed.files.map((path) => (
              <li key={path}>{path}</li>
            ))}
          </ul>
          {reviewed.directories_retained.length > 0 && (
            <>
              <p>These created folders stay:</p>
              <ul>
                {reviewed.directories_retained.map((path) => (
                  <li key={path}>{path}</li>
                ))}
              </ul>
            </>
          )}
          {reviewed.policy_decision === 'block' && (
            <p role="alert">The current policy blocks Undo.</p>
          )}
        </>
      )}
      {!state.pending && (
        <div className="action-cluster">
          {!done && (
            <Button
              disabled={state.reading || state.busy}
              onClick={() => {
                owned.cancelReview();
                props.onCancel?.();
              }}
            >
              Keep changes
            </Button>
          )}
          {!done ? (
            <Button
              variant="danger"
              disabled={state.reading || state.busy || !props.changeSetId}
              onClick={() => void owned.start(props)}
            >
              Undo change
            </Button>
          ) : (
            <Button
              onClick={() => {
                owned.cancelReview();
                props.onCancel?.();
              }}
            >
              Dismiss details
            </Button>
          )}
        </div>
      )}
      {state.pending && (
        <div className="action-cluster">
          <Button
            disabled={state.busy || state.reading}
            onClick={() => void owned.check(props)}
          >
            Check Undo
          </Button>
          <Button
            disabled={state.busy || state.reading}
            onClick={() => void owned.execute(props, true)}
          >
            Retry original Undo
          </Button>
        </div>
      )}
    </section>
  );
}
