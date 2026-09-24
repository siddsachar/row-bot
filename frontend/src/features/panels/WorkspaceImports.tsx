import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { clientError } from '../../api/errors';
import { Button, ErrorState, Field, Skeleton } from '../../ui/primitives';

// These closed structural types match the canonical import domain. The shared
// adapter adds its session-bound review nonce; no host path is renderer authority.
export type WorkspaceImportSummary = {
  pending_change_id: string;
  revision: string;
  file_count: number;
  imported: boolean;
  created_at: string;
};
export type WorkspaceImportPage = {
  items: WorkspaceImportSummary[];
  snapshot_revision: string;
  next_cursor: string | null;
  total: number;
};
export type WorkspaceImportPatch = {
  pending_change_id: string;
  revision: string;
  text: string;
  next_offset: number | null;
};
export type WorkspaceImportReview = {
  resource_id: string;
  conversation_id: string;
  resource_revision: string;
  binding_id: string;
  binding_revision: string;
  pending_change_id: string;
  pending_revision: string;
  patch_digest: string;
  host_revision: string;
  git_policy_revision: string;
  policy_revision: string;
  policy_decision: 'allow' | 'ask' | 'block';
  approval_required: boolean;
  files: string[];
  directories?: string[];
  action_digest: string;
  nonce: string;
};
export type WorkspaceImportResult = {
  command_id: string;
  resource_id: string;
  conversation_id: string;
  pending_change_id: string;
  status: 'imported' | 'partial' | 'conflict' | 'denied';
  files_applied: string[];
  change_set_id: string | null;
  ledger_saved: boolean;
  imported: boolean;
  code?: string;
};
type Pending = {
  review: WorkspaceImportReview;
  commandId: string;
  approvalRejected?: boolean;
};
type State = {
  active: boolean;
  page: WorkspaceImportPage | null;
  shifted: boolean;
  selected: WorkspaceImportSummary | null;
  patch: WorkspaceImportPatch | null;
  patchOffset: number;
  reviewed: WorkspaceImportReview | null;
  pending: Pending | null;
  busy: boolean;
  reading: boolean;
  stale: boolean;
  result: WorkspaceImportResult | null;
  error: string;
  notice: string;
};
const initial = (): State => ({
  active: true,
  page: null,
  shifted: false,
  selected: null,
  patch: null,
  patchOffset: 0,
  reviewed: null,
  pending: null,
  busy: false,
  reading: false,
  stale: false,
  result: null,
  error: '',
  notice: '',
});

/** One bounded import intent, retained only in the current authenticated runtime. */
export class WorkspaceImportsSession {
  private state = initial();
  private listeners = new Set<() => void>();
  private read: AbortController | null = null;
  private operation: Promise<void> | null = null;
  constructor(readonly scope: string) {}
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  getSnapshot = () => this.state;
  private publish(update: Partial<State>) {
    if (!this.state.active) return;
    this.state = { ...this.state, ...update };
    this.listeners.forEach((listener) => listener());
  }
  hasRetained() {
    return !!(this.state.pending || this.state.reviewed || this.operation);
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
      reviewed: null,
      reading: false,
      error: '',
      notice: 'Import review cancelled. Workspace files are unchanged.',
    });
  }
  private async query(
    effect: (signal: AbortSignal) => Promise<Partial<State>>,
  ) {
    if (!this.state.active || this.state.busy || this.state.pending) return;
    this.read?.abort();
    const abort = new AbortController();
    this.read = abort;
    this.publish({ reading: true, error: '' });
    try {
      const update = await effect(abort.signal);
      if (this.read === abort && !abort.signal.aborted) this.publish(update);
    } catch (error) {
      if (this.read === abort && !abort.signal.aborted)
        this.publish({
          error: clientError(error).message,
          stale: true,
          reviewed: null,
        });
    } finally {
      if (this.read === abort) {
        this.read = null;
        this.publish({ reading: false });
      }
    }
  }
  load(io: WorkspaceImportsProps, more = false) {
    if (more && (this.state.stale || !this.state.page?.next_cursor))
      return Promise.resolve();
    return this.query(async (signal) => {
      const previous = more ? this.state.page : null;
      const page = await io.load(previous?.next_cursor ?? undefined, signal);
      if (previous && previous.snapshot_revision !== page.snapshot_revision)
        throw { code: 'snapshot_revision_conflict' };
      const combined = previous
        ? [...previous.items, ...page.items]
        : page.items;
      return {
        page: { ...page, items: combined.slice(-200) },
        shifted: more ? this.state.shifted || combined.length > 200 : false,
        selected: null,
        patch: null,
        reviewed: null,
        stale: false,
        error: '',
        notice: '',
      };
    });
  }
  select(io: WorkspaceImportsProps, row: WorkspaceImportSummary, offset = 0) {
    return this.query(async (signal) => {
      const patch = await io.patch(row, offset, signal);
      if (
        patch.pending_change_id !== row.pending_change_id ||
        patch.revision !== row.revision
      )
        throw { code: 'snapshot_revision_conflict' };
      return {
        selected: row,
        patch,
        patchOffset: offset,
        reviewed: null,
        result: null,
        notice: '',
      };
    });
  }
  review(io: WorkspaceImportsProps) {
    const row = this.state.selected;
    if (!row || row.imported || this.state.stale) return Promise.resolve();
    return this.query(async (signal) => {
      const reviewed = await io.review(row, signal);
      if (
        reviewed.pending_change_id !== row.pending_change_id ||
        reviewed.pending_revision !== row.revision ||
        !reviewed.nonce
      )
        throw { code: 'snapshot_revision_conflict' };
      return { reviewed: structuredClone(reviewed), notice: '' };
    });
  }
  async start(io: WorkspaceImportsProps) {
    const selected = this.state.selected;
    if (!selected || selected.imported || this.state.stale) return;
    await this.review(io);
    if (this.state.reviewed?.policy_decision === 'block') {
      this.publish({
        notice: 'The current approval policy blocks this import.',
      });
      return;
    }
    if (
      this.state.active &&
      this.state.reviewed?.pending_change_id === selected.pending_change_id &&
      this.state.reviewed.pending_revision === selected.revision
    )
      await this.execute(io);
  }
  execute(io: WorkspaceImportsProps, recover = false) {
    if (!this.state.active || this.operation || this.state.reading)
      return this.operation ?? Promise.resolve();
    const previous = this.state.pending;
    if (
      recover
        ? !previous
        : !!previous ||
          !this.state.reviewed ||
          this.state.reviewed.policy_decision === 'block'
    )
      return Promise.resolve();
    const pending = previous ?? {
      commandId: crypto.randomUUID(),
      review: structuredClone(this.state.reviewed!),
    };
    this.publish({
      pending,
      reviewed: null,
      busy: true,
      error: '',
      notice: '',
    });
    this.operation = (async () => {
      try {
        const result = await (recover ? io.recover : io.apply)(
          structuredClone(pending.review),
          pending.commandId,
        );
        this.accept(io, pending, result);
      } catch (error) {
        const failure = clientError(error);
        this.publish({
          pending:
            failure.code === 'approval_expired'
              ? { ...pending, approvalRejected: true }
              : pending,
          error: failure.message,
          notice:
            'The import outcome is unconfirmed. Check its original receipt before recovering.',
        });
      } finally {
        this.operation = null;
        this.publish({ busy: false });
      }
    })();
    return this.operation;
  }
  private accept(
    io: WorkspaceImportsProps,
    pending: Pending,
    result: WorkspaceImportResult,
  ) {
    if (!this.state.active) return;
    if (
      result.command_id !== pending.commandId ||
      result.resource_id !== pending.review.resource_id ||
      result.conversation_id !== pending.review.conversation_id ||
      result.pending_change_id !== pending.review.pending_change_id
    )
      throw { code: 'operation_uncertain' };
    const complete =
      result.status === 'imported' && result.imported && result.ledger_saved;
    const rejected =
      (result.status === 'denied' || result.status === 'conflict') &&
      !result.files_applied.length &&
      !result.imported;
    this.publish({
      result,
      pending: complete || rejected ? null : pending,
      selected:
        complete && this.state.selected
          ? { ...this.state.selected, imported: true }
          : this.state.selected,
      notice: complete
        ? 'Sandbox changes imported. Original files and change history are retained.'
        : rejected
          ? 'Import was not applied. Reload and review the current workspace before trying again.'
          : 'Some import stages remain unconfirmed. Check the receipt or recover this original command.',
      error: result.code ? clientError({ code: result.code }).message : '',
    });
    if (complete) io.onImported(result);
  }
  check(io: WorkspaceImportsProps) {
    const pending = this.state.pending;
    if (!this.state.active || !pending || this.operation)
      return this.operation ?? Promise.resolve();
    this.publish({ busy: true, error: '' });
    this.operation = (async () => {
      try {
        const result = await io.receipt(pending.commandId);
        if (result) this.accept(io, pending, result);
        else
          this.publish({
            notice:
              'The original receipt is still unconfirmed. No new import has been submitted.',
          });
      } catch (error) {
        const failure = clientError(error);
        if (
          failure.code === 'workspace_import_not_admitted' &&
          pending.approvalRejected === true &&
          this.state.pending?.commandId === pending.commandId
        ) {
          this.publish({
            pending: null,
            reviewed: null,
            stale: true,
            error: '',
            notice:
              'This original command was not admitted. Reload and explicitly review the current changes before importing.',
          });
        } else this.publish({ error: failure.message });
      } finally {
        this.operation = null;
        this.publish({ busy: false });
      }
    })();
    return this.operation;
  }
}

export type WorkspaceImportsProps = {
  scope: string;
  session?: WorkspaceImportsSession;
  load: (cursor?: string, signal?: AbortSignal) => Promise<WorkspaceImportPage>;
  patch: (
    row: WorkspaceImportSummary,
    offset?: number,
    signal?: AbortSignal,
  ) => Promise<WorkspaceImportPatch>;
  review: (
    row: WorkspaceImportSummary,
    signal?: AbortSignal,
  ) => Promise<WorkspaceImportReview>;
  apply: (
    review: WorkspaceImportReview,
    commandId: string,
  ) => Promise<WorkspaceImportResult>;
  receipt: (
    commandId: string,
    signal?: AbortSignal,
  ) => Promise<WorkspaceImportResult | null>;
  recover: (
    review: WorkspaceImportReview,
    commandId: string,
  ) => Promise<WorkspaceImportResult>;
  onImported: (result: WorkspaceImportResult) => void;
};

export default function WorkspaceImports(props: WorkspaceImportsProps) {
  const local = useMemo(
    () => new WorkspaceImportsSession(props.scope),
    [props.scope],
  );
  const session = props.session ?? local;
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const callbacks = useRef(props);
  callbacks.current = props;
  useEffect(() => () => local.dispose(), [local]);
  useEffect(() => {
    if (
      session.scope === props.scope &&
      !session.getSnapshot().page &&
      !session.hasRetained()
    )
      void session.load(callbacks.current);
  }, [session, props.scope]);
  if (!state.active || session.scope !== props.scope)
    return (
      <ErrorState title="Workspace access changed">
        This retained import is unavailable in the current binding.
      </ErrorState>
    );
  const locked = state.busy || state.reading || !!state.pending;
  return (
    <section
      className="stack studio-section"
      aria-label="Sandbox imports"
      aria-busy={state.busy || state.reading}
    >
      <header className="capability-header">
        <div>
          <h3>Sandbox changes</h3>
          <p>
            Inspect saved sandbox changes before importing them into this
            workspace.
          </p>
        </div>
      </header>
      {state.error && (
        <ErrorState title="Import requires attention">{state.error}</ErrorState>
      )}
      {state.notice && <p role="status">{state.notice}</p>}
      {state.reading && <Skeleton label="Reading saved sandbox changes" />}
      {state.shifted && (
        <p role="status">
          Showing the latest 200 loaded changes. Reload returns to the start.
        </p>
      )}
      {state.stale && (
        <p role="status">
          The saved revision changed. Reload before reviewing another import.
        </p>
      )}
      <div className="actions">
        <Button disabled={locked} onClick={() => void session.load(props)}>
          Reload sandbox changes
        </Button>
      </div>
      {state.page?.total === 0 && <p>No saved sandbox changes.</p>}
      {state.page && (
        <p>
          {state.page.total} saved change{state.page.total === 1 ? '' : 's'}
        </p>
      )}
      <ul className="stack" aria-label="Saved sandbox changes">
        {state.page?.items.map((row) => (
          <li key={row.pending_change_id}>
            <Button
              disabled={locked || state.stale}
              aria-pressed={
                state.selected?.pending_change_id === row.pending_change_id
              }
              onClick={() => void session.select(props, row)}
            >
              {row.imported ? 'Imported' : 'Pending'} · {row.file_count} file
              {row.file_count === 1 ? '' : 's'} ·{' '}
              {row.created_at || row.pending_change_id}
            </Button>
          </li>
        ))}
      </ul>
      {state.page?.next_cursor && (
        <Button
          disabled={locked || state.stale}
          onClick={() => void session.load(props, true)}
        >
          Load more changes
        </Button>
      )}
      {state.patch && state.selected && (
        <>
          <Field label="Saved patch">
            <textarea
              className="input code-sample"
              rows={12}
              readOnly
              value={state.patch.text}
            />
          </Field>
          {(state.patchOffset > 0 || state.patch.next_offset !== null) && (
            <p>
              Patch excerpt starting at character {state.patchOffset + 1}.
              Review other pages to read the complete patch.
            </p>
          )}
          <div className="actions">
            {state.patchOffset > 0 && (
              <Button
                disabled={locked}
                onClick={() => void session.select(props, state.selected!)}
              >
                First patch page
              </Button>
            )}
            {state.patch.next_offset !== null && (
              <Button
                disabled={locked}
                onClick={() =>
                  void session.select(
                    props,
                    state.selected!,
                    state.patch!.next_offset!,
                  )
                }
              >
                Next patch page
              </Button>
            )}
            <Button
              disabled={locked || state.selected.imported || state.stale}
              onClick={() => void session.start(props)}
            >
              Import selected changes
            </Button>
          </div>
        </>
      )}
      {state.pending && (
        <div className="actions">
          <Button
            disabled={state.busy}
            onClick={() => void session.check(props)}
          >
            Check import receipt
          </Button>
          <Button
            disabled={state.busy}
            onClick={() => void session.execute(props, true)}
          >
            Recover original import
          </Button>
        </div>
      )}
      {state.result && (
        <p>
          {state.result.files_applied.length} file publication
          {state.result.files_applied.length === 1 ? '' : 's'} confirmed. Change
          history: {state.result.ledger_saved ? 'saved' : 'unconfirmed'}. Import
          marker: {state.result.imported ? 'saved' : 'unconfirmed'}.
        </p>
      )}
    </section>
  );
}
