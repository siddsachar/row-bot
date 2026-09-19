import { useSyncExternalStore } from 'react';
import { Button, ErrorState, Field, Input, Select } from '../../ui/primitives';

export type KnowledgeFields = {
  entity_type: string;
  subject: string;
  description: string;
  aliases: string;
  tags: string;
};
export type KnowledgeEntity = {
  id: string;
  revision: string;
  fields: KnowledgeFields;
  status: 'active' | 'archived' | 'needs_review' | 'superseded';
  created_at: string;
  updated_at: string;
  saved_state: 'saved';
  projection_state: 'unknown';
};
export type KnowledgeEditorState = {
  schema_version: 1;
  entity: KnowledgeEntity | null;
  entity_types: string[];
};
export type KnowledgeAction =
  | 'knowledge.create'
  | 'knowledge.edit'
  | 'knowledge.archive'
  | 'knowledge.restore'
  | 'knowledge.resolve';
export type KnowledgeReview = {
  review_id: string;
  action: KnowledgeAction;
  entity_id: string | null;
  revision: string;
  fields_digest: string;
  reuse_entity_id: string | null;
};
export type KnowledgeCommand = {
  command_id: string;
  type: KnowledgeAction;
  payload: Record<string, unknown>;
};
export type KnowledgeReceipt = {
  command_id: string;
  status: 'completed' | 'partial' | 'rejected';
  code?: string;
  entity_id?: string;
  revision?: string;
  saved_state?: 'saved';
  projection_state?: 'pending' | 'unknown';
  reused?: boolean;
};
export type KnowledgeTransport = {
  load(entityId: string | null): Promise<KnowledgeEditorState>;
  review(
    action: KnowledgeAction,
    payload: Record<string, unknown>,
  ): Promise<KnowledgeReview>;
  execute(command: KnowledgeCommand): Promise<KnowledgeReceipt>;
  receipt(commandId: string): Promise<KnowledgeReceipt>;
};
const empty: KnowledgeFields = {
  entity_type: 'fact',
  subject: '',
  description: '',
  aliases: '',
  tags: '',
};
type State = {
  saved: KnowledgeEditorState | null;
  draft: KnowledgeFields;
  review: KnowledgeReview | null;
  receipt: KnowledgeReceipt | null;
  busy: boolean;
  pending: boolean;
  revoked: boolean;
  dirty: boolean;
  error: string;
};
/** Retain in the authenticated controller lifetime; purge on auth loss and wire pending to beforeunload. */
export function createKnowledgeEditorSession(
  entityId: string | null,
  transport: KnowledgeTransport,
  guard: () => void,
  newId: () => string = () => crypto.randomUUID(),
) {
  let target = entityId;
  let state: State = {
    saved: null,
    draft: { ...empty },
    review: null,
    receipt: null,
    busy: false,
    pending: false,
    revoked: false,
    dirty: false,
    error: '',
  };
  let epoch = 0;
  let operation: Promise<unknown> | null = null;
  // Only the currently visible original is reachable for receipt recovery.
  // A new attempt is admitted only after this one is terminal.
  let attempt: KnowledgeCommand | null = null;
  let reviewedPayload: Record<string, unknown> | null = null;
  const listeners = new Set<() => void>();
  const emit = (patch: Partial<State>) => {
    state = { ...state, ...patch };
    listeners.forEach((notify) => notify());
  };
  const authority = (ticket = epoch) => {
    if (state.revoked || ticket !== epoch)
      throw new Error('authentication_required');
    guard();
  };
  async function read<T>(call: () => Promise<T>) {
    const ticket = epoch;
    authority(ticket);
    const result = await call();
    authority(ticket);
    return result;
  }
  async function run<T>(call: () => Promise<T>) {
    authority();
    if (operation) throw new Error('knowledge_operation_busy');
    const ticket = epoch;
    emit({ busy: true, error: '' });
    const pending = Promise.resolve().then(call);
    operation = pending;
    try {
      return await pending;
    } catch (error) {
      if (ticket === epoch)
        emit({
          error:
            'The action could not be confirmed. Your draft is retained. Refresh the original command if its outcome is unknown.',
        });
      throw error;
    } finally {
      if (operation === pending) operation = null;
      if (ticket === epoch) emit({ busy: false });
    }
  }
  function accept(command: KnowledgeCommand, result: KnowledgeReceipt) {
    if (
      result.command_id !== command.command_id ||
      (result.status === 'completed' &&
        (!result.entity_id ||
          !result.revision ||
          result.saved_state !== 'saved' ||
          (target !== null && result.entity_id !== target)))
    )
      throw new Error('knowledge_receipt_changed');
    if (result.status === 'completed') target = result.entity_id!;
    emit({
      receipt: result,
      pending: result.status === 'partial',
      review: null,
    });
    // Keep draft until an explicit load; an event/read response cannot overwrite it.
  }
  const session = {
    getSnapshot: () => state,
    subscribe(notify: () => void) {
      listeners.add(notify);
      return () => {
        listeners.delete(notify);
      };
    },
    getTarget: () => target,
    setField(name: keyof KnowledgeFields, value: string) {
      authority();
      if (operation || state.pending) return;
      reviewedPayload = null;
      emit({
        draft: { ...state.draft, [name]: value },
        dirty: true,
        review: null,
      });
    },
    load: (discardDraft = false) =>
      run(async () => {
        if (state.pending || (state.dirty && !discardDraft))
          throw new Error('knowledge_draft_retained');
        const value = await read(() => transport.load(target));
        if ((value.entity?.id ?? null) !== target)
          throw new Error('knowledge_target_changed');
        reviewedPayload = null;
        emit({
          saved: value,
          draft: { ...(value.entity?.fields ?? empty) },
          dirty: false,
          review: null,
        });
      }),
    review: (action: KnowledgeAction) =>
      run(async () => {
        if (state.pending || !state.saved)
          throw new Error('knowledge_review_unavailable');
        if ((action === 'knowledge.create') !== (target === null))
          throw new Error('knowledge_target_changed');
        const payload: Record<string, unknown> = {
          entity_id: target,
          revision: state.saved.entity?.revision ?? '',
        };
        if (action === 'knowledge.create' || action === 'knowledge.edit')
          payload.fields = { ...state.draft };
        const review = await read(() =>
          transport.review(action, structuredClone(payload)),
        );
        if (
          review.action !== action ||
          review.entity_id !== target ||
          !review.review_id ||
          !review.revision ||
          !review.fields_digest
        )
          throw new Error('knowledge_review_changed');
        reviewedPayload = { ...payload, revision: review.revision };
        emit({ review });
      }),
    dismissReview() {
      authority();
      if (!operation) {
        reviewedPayload = null;
        emit({ review: null });
      }
    },
    confirm: () =>
      run(async () => {
        const review = state.review;
        if (!review || !reviewedPayload || state.pending)
          throw new Error('knowledge_review_unavailable');
        const command: KnowledgeCommand = {
          command_id: newId(),
          type: review.action,
          payload: {
            ...structuredClone(reviewedPayload),
            review_id: review.review_id,
          },
        };
        attempt = command;
        reviewedPayload = null;
        emit({ review: null, pending: true });
        const result = await read(() =>
          transport.execute(structuredClone(command)),
        );
        accept(command, result);
        return result;
      }),
    refresh: () =>
      run(async () => {
        if (!attempt) throw new Error('knowledge_operation_unavailable');
        const command = attempt;
        const result = await read(() => transport.receipt(command.command_id));
        accept(command, result);
        return result;
      }),
    purge() {
      epoch++;
      target = null;
      attempt = null;
      reviewedPayload = null;
      emit({
        saved: null,
        draft: { ...empty },
        review: null,
        receipt: null,
        revoked: true,
        busy: false,
        dirty: false,
        pending: false,
        error: '',
      });
    },
  };
  return session;
}
export type KnowledgeEditorSession = ReturnType<
  typeof createKnowledgeEditorSession
>;
export default function KnowledgeEditor({
  session,
}: {
  session: KnowledgeEditorSession;
}) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const perform = (call: () => Promise<unknown>) => {
    void call().catch(() => {});
  };
  if (state.revoked) return <p>Sign in again to open knowledge.</p>;
  const locked = state.busy || state.pending;
  return (
    <section
      className="stack"
      aria-label="Knowledge editor"
      aria-busy={state.busy}
    >
      <h2>{session.getTarget() ? 'Edit knowledge' : 'Create knowledge'}</h2>
      {!state.saved && (
        <Button disabled={locked} onClick={() => perform(() => session.load())}>
          Open editor
        </Button>
      )}
      {state.saved && (
        <>
          <Field label="Subject">
            <Input
              required
              maxLength={256}
              value={state.draft.subject}
              disabled={locked}
              onChange={(e) => session.setField('subject', e.target.value)}
            />
          </Field>
          <Field label="Entity type">
            <Select
              value={state.draft.entity_type}
              disabled={locked}
              onChange={(e) => session.setField('entity_type', e.target.value)}
            >
              {state.saved.entity_types.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Description">
            <textarea
              className="input"
              rows={5}
              maxLength={32768}
              value={state.draft.description}
              disabled={locked}
              onChange={(e) => session.setField('description', e.target.value)}
            />
          </Field>
          <Field label="Aliases (comma-separated)">
            <Input
              maxLength={4096}
              value={state.draft.aliases}
              disabled={locked}
              onChange={(e) => session.setField('aliases', e.target.value)}
            />
          </Field>
          <Field label="Tags (comma-separated)">
            <Input
              maxLength={4096}
              value={state.draft.tags}
              disabled={locked}
              onChange={(e) => session.setField('tags', e.target.value)}
            />
          </Field>
          {state.saved.entity && (
            <p>
              Saved status: {state.saved.entity.status}. Projection readiness:
              unknown.
            </p>
          )}
          <div className="field-row">
            <Button
              disabled={locked || !state.draft.subject.trim()}
              onClick={() =>
                perform(() =>
                  session.review(
                    session.getTarget() ? 'knowledge.edit' : 'knowledge.create',
                  ),
                )
              }
            >
              Review save
            </Button>
            {state.saved.entity && (
              <>
                <Button
                  disabled={locked || state.dirty}
                  onClick={() =>
                    perform(() =>
                      session.review(
                        state.saved!.entity!.status === 'archived'
                          ? 'knowledge.restore'
                          : 'knowledge.archive',
                      ),
                    )
                  }
                >
                  {state.saved.entity.status === 'archived'
                    ? 'Restore'
                    : 'Archive'}
                </Button>
                {state.saved.entity.status === 'needs_review' && (
                  <Button
                    disabled={locked || state.dirty}
                    onClick={() =>
                      perform(() => session.review('knowledge.resolve'))
                    }
                  >
                    Resolve review
                  </Button>
                )}
              </>
            )}
            <Button
              disabled={locked}
              onClick={() => perform(() => session.load(true))}
            >
              {state.dirty
                ? 'Discard draft and reload saved entry'
                : 'Reload saved entry'}
            </Button>
          </div>
        </>
      )}
      {state.review && (
        <div
          className="surface stack"
          role="group"
          aria-label="Review knowledge change"
        >
          <p>
            {state.review.reuse_entity_id
              ? 'The canonical User already exists. Reuse it without changing its content; edit it afterward to make changes.'
              : `Confirm ${state.review.action.replace('knowledge.', '')} for this knowledge entry.`}
          </p>
          <Button
            disabled={locked}
            variant="primary"
            onClick={() => perform(session.confirm)}
          >
            Confirm knowledge change
          </Button>
          <Button disabled={locked} onClick={session.dismissReview}>
            Cancel review
          </Button>
        </div>
      )}
      {state.receipt?.status === 'completed' && (
        <p role="status">
          {state.receipt.reused
            ? 'Existing knowledge reused.'
            : 'Knowledge saved.'}{' '}
          {state.receipt.projection_state === 'pending'
            ? 'Search and wiki projections remain pending.'
            : 'Projection readiness is unknown.'}{' '}
          Reload the saved entry to continue.
        </p>
      )}
      {state.receipt?.status === 'rejected' && (
        <p role="status">
          The saved entry changed. Your draft is retained for review.
        </p>
      )}
      {state.pending && (
        <p role="status">
          Outcome unknown. Do not repeat the change; refresh its original
          command.
        </p>
      )}
      {(state.pending || state.receipt) && (
        <Button disabled={state.busy} onClick={() => perform(session.refresh)}>
          Refresh original knowledge command
        </Button>
      )}
      {state.error && (
        <ErrorState title="Knowledge action unavailable">
          {state.error}
        </ErrorState>
      )}
    </section>
  );
}
