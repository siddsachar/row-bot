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
          'The outcome is unconfirmed. Check the original receipt or retry this same operation.',
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
            'No confirmed receipt is available. The original operation remains retained.',
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
  return (
    <section aria-label="Undo workspace changes">
      <h3>Undo imported changes</h3>
      <p>
        Review the exact retained originals before restoring files. Later edits
        will be preserved.
      </p>
      {state.error && (
        <ErrorState title="Undo needs attention">{state.error}</ErrorState>
      )}
      {state.notice && <p role="status">{state.notice}</p>}
      {state.result && (
        <p>
          {state.result.files_restored.length} files restored ·{' '}
          {state.result.status}
        </p>
      )}
      {!state.pending && (
        <Button
          disabled={state.reading || state.busy || !props.changeSetId}
          onClick={() => void owned.review(props)}
        >
          Review Undo
        </Button>
      )}
      {reviewed && (
        <div>
          <p>Change set: {reviewed.change_set_id}</p>
          <ul>
            {reviewed.files.map((path) => (
              <li key={path}>{path}</li>
            ))}
          </ul>
          {reviewed.directories_retained.length > 0 && (
            <>
              <p>These created folders will remain:</p>
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
          {!state.pending && (
            <div className="actions">
              <Button
                disabled={state.busy || state.reading}
                onClick={() => owned.cancelReview()}
              >
                Cancel review
              </Button>
              <Button
                disabled={
                  state.busy ||
                  state.reading ||
                  reviewed.policy_decision === 'block'
                }
                onClick={() => void owned.execute(props)}
              >
                Undo these changes
              </Button>
            </div>
          )}
        </div>
      )}
      {state.pending && (
        <div className="actions">
          <Button
            disabled={state.busy || state.reading}
            onClick={() => void owned.check(props)}
          >
            Check Undo receipt
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
