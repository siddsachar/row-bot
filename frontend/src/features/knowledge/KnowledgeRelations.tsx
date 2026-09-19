import { useSyncExternalStore } from 'react';
import { Button, ErrorState, Field, Input, Select } from '../../ui/primitives';
import type { KnowledgeEntity } from './KnowledgeEditor';
export type Relation = {
  id: string;
  source_id: string;
  target_id: string;
  relation_type: string;
  confidence: number;
  revision: string;
  peer_id: string;
  peer_subject: string;
  truncated: boolean;
};
export type RelationPage = {
  revision: string;
  items: Relation[];
  total: number | null;
  next_cursor: string | null;
  availability: 'available' | 'missing' | 'unavailable';
};
export type RelationTargetPage = {
  revision: string;
  items: { id: string; subject: string; entity_type: string }[];
  next_cursor: string | null;
  total: number | null;
  availability: 'available' | 'missing' | 'unavailable';
};
export type RelationAction =
  | 'knowledge.relation.add'
  | 'knowledge.relation.remove'
  | 'knowledge.supersede';
export type RelationReview = {
  review_id: string;
  action: RelationAction;
  entity_ids: [string, string];
  entity_revisions: [string, string];
  relation_id: string | null;
  relation_revision: string;
  intent_digest: string;
};
export type RelationCommand = {
  command_id: string;
  type: RelationAction;
  payload: Record<string, unknown>;
};
export type RelationReceipt = {
  command_id: string;
  status: 'completed' | 'rejected' | 'partial';
  code?: string;
  outcome?: 'saved' | 'removed' | 'superseded';
  entity_ids?: [string, string];
  relation_id?: string | null;
  projection_state?: 'pending' | 'unknown';
};
export type RelationTransport = {
  entity(id: string): Promise<KnowledgeEntity>;
  relations(id: string, cursor?: string): Promise<RelationPage>;
  targets(query: string, cursor?: string): Promise<RelationTargetPage>;
  review(
    action: RelationAction,
    payload: Record<string, unknown>,
  ): Promise<RelationReview>;
  execute(command: RelationCommand): Promise<RelationReceipt>;
  receipt(id: string): Promise<RelationReceipt>;
};
type State = {
  anchor: KnowledgeEntity | null;
  relations: RelationPage | null;
  targets: RelationTargetPage | null;
  selected: KnowledgeEntity | null;
  query: string;
  relationType: string;
  direction: 'outgoing' | 'incoming';
  review: RelationReview | null;
  receipt: RelationReceipt | null;
  busy: boolean;
  pending: boolean;
  revoked: boolean;
  error: string;
};
/** Root retains this bounded session through panel remount and purges on auth loss. */
export function createKnowledgeRelationsSession(
  entityId: string,
  transport: RelationTransport,
  guard: () => void,
  newId: () => string = () => crypto.randomUUID(),
) {
  let state: State = {
    anchor: null,
    relations: null,
    targets: null,
    selected: null,
    query: '',
    relationType: 'knows',
    direction: 'outgoing',
    review: null,
    receipt: null,
    busy: false,
    pending: false,
    revoked: false,
    error: '',
  };
  let epoch = 0,
    operation: Promise<unknown> | null = null,
    reviewedPayload: Record<string, unknown> | null = null;
  type Attempt = { command: RelationCommand; review: RelationReview };
  // Retain the current exact original; settled history lives in server receipts.
  // Never keep unreachable historical payloads or File references here.
  let attempt: Attempt | null = null;
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
    const promise = Promise.resolve().then(call);
    operation = promise;
    try {
      return await promise;
    } catch (error) {
      if (ticket === epoch)
        emit({
          error:
            'The relation action could not be confirmed. Selections are retained; refresh the original command if its outcome is unknown.',
        });
      throw error;
    } finally {
      if (operation === promise) operation = null;
      if (ticket === epoch) emit({ busy: false });
    }
  }
  function editable() {
    if (state.pending) throw new Error('knowledge_outcome_uncertain');
  }
  function clearReview() {
    reviewedPayload = null;
    emit({ review: null });
  }
  async function entity(id: string) {
    const value = await read(() => transport.entity(id));
    if (value.id !== id || !value.revision)
      throw new Error('knowledge_target_changed');
    return value;
  }
  async function review(
    action: RelationAction,
    payload: Record<string, unknown>,
    ids: [string, string],
  ) {
    editable();
    const value = await read(() =>
      transport.review(action, structuredClone(payload)),
    );
    if (
      value.action !== action ||
      JSON.stringify(value.entity_ids) !== JSON.stringify(ids) ||
      !value.review_id ||
      !value.intent_digest
    )
      throw new Error('knowledge_review_changed');
    reviewedPayload = payload;
    emit({ review: value });
  }
  function accept(current: Attempt, result: RelationReceipt) {
    if (
      result.command_id !== current.command.command_id ||
      (result.status === 'completed' &&
        (JSON.stringify(result.entity_ids) !==
          JSON.stringify(current.review.entity_ids) ||
          result.outcome !==
            {
              'knowledge.relation.add': 'saved',
              'knowledge.relation.remove': 'removed',
              'knowledge.supersede': 'superseded',
            }[current.command.type] ||
          (current.command.type === 'knowledge.relation.remove' &&
            result.relation_id !== current.review.relation_id) ||
          (current.command.type === 'knowledge.supersede' &&
            (result.outcome !== 'superseded' || result.relation_id !== null))))
    )
      throw new Error('knowledge_receipt_changed');
    emit({ receipt: result, pending: result.status === 'partial' });
  }
  return {
    entityId,
    getSnapshot: () => state,
    subscribe(notify: () => void) {
      listeners.add(notify);
      return () => {
        listeners.delete(notify);
      };
    },
    load: () =>
      run(async () => {
        editable();
        const anchor = await entity(entityId);
        const page = await read(() => transport.relations(entityId));
        clearReview();
        emit({ anchor, relations: page });
      }),
    nextRelations: () =>
      run(async () => {
        editable();
        const prior = state.relations;
        if (!prior?.next_cursor) throw new Error('relation_page_unavailable');
        const page = await read(() =>
          transport.relations(entityId, prior.next_cursor!),
        );
        if (page.revision !== prior.revision) throw new Error('cursor_expired');
        emit({ relations: page });
      }),
    setQuery(query: string) {
      authority();
      if (operation || state.pending) return;
      emit({ query, targets: null });
    },
    search: (next = false) =>
      run(async () => {
        editable();
        const prior = state.targets;
        if (next && !prior?.next_cursor)
          throw new Error('relation_page_unavailable');
        const page = await read(() =>
          transport.targets(
            state.query,
            next ? prior!.next_cursor! : undefined,
          ),
        );
        if (page.availability !== 'available')
          throw new Error('knowledge_targets_unavailable');
        if (next && page.revision !== prior!.revision)
          throw new Error('cursor_expired');
        emit({ targets: page });
      }),
    select: (id: string) =>
      run(async () => {
        editable();
        if (
          id === entityId ||
          !state.targets?.items.some((item) => item.id === id)
        )
          throw new Error('knowledge_target_changed');
        const selected = await entity(id);
        clearReview();
        emit({ selected });
      }),
    setRelationType(relationType: string) {
      authority();
      if (operation || state.pending) return;
      clearReview();
      emit({ relationType });
    },
    setDirection(direction: 'outgoing' | 'incoming') {
      authority();
      if (operation || state.pending) return;
      clearReview();
      emit({ direction });
    },
    reviewAdd: () =>
      run(async () => {
        editable();
        const { anchor, selected, direction, relationType } = state;
        if (!anchor || !selected)
          throw new Error('knowledge_target_unavailable');
        const [first, second] =
          direction === 'outgoing' ? [anchor, selected] : [selected, anchor];
        await review(
          'knowledge.relation.add',
          {
            source_id: first.id,
            target_id: second.id,
            source_revision: first.revision,
            target_revision: second.revision,
            relation_type: relationType,
          },
          [first.id, second.id],
        );
      }),
    reviewSupersede: () =>
      run(async () => {
        editable();
        const { anchor, selected } = state;
        if (!anchor || !selected)
          throw new Error('knowledge_target_unavailable');
        await review(
          'knowledge.supersede',
          {
            old_id: anchor.id,
            new_id: selected.id,
            old_revision: anchor.revision,
            new_revision: selected.revision,
          },
          [anchor.id, selected.id],
        );
      }),
    reviewRemove: (id: string) =>
      run(async () => {
        editable();
        const edge = state.relations?.items.find((item) => item.id === id);
        const anchor = state.anchor;
        if (!edge || !anchor) throw new Error('relation_changed');
        const peer = await entity(edge.peer_id);
        const [first, second] =
          edge.source_id === entityId ? [anchor, peer] : [peer, anchor];
        await review(
          'knowledge.relation.remove',
          {
            relation_id: edge.id,
            relation_revision: edge.revision,
            source_revision: first.revision,
            target_revision: second.revision,
          },
          [first.id, second.id],
        );
      }),
    dismissReview() {
      authority();
      if (!operation) clearReview();
    },
    confirm: () =>
      run(async () => {
        editable();
        const currentReview = state.review;
        if (!currentReview || !reviewedPayload)
          throw new Error('knowledge_review_unavailable');
        const command: RelationCommand = {
          command_id: newId(),
          type: currentReview.action,
          payload: {
            ...structuredClone(reviewedPayload),
            review_id: currentReview.review_id,
          },
        };
        const current = { command, review: currentReview };
        attempt = current;
        clearReview();
        emit({ pending: true });
        const result = await read(() =>
          transport.execute(structuredClone(command)),
        );
        accept(current, result);
        return result;
      }),
    refresh: () =>
      run(async () => {
        if (!attempt) throw new Error('knowledge_operation_unavailable');
        const current = attempt;
        const result = await read(() =>
          transport.receipt(current.command.command_id),
        );
        accept(current, result);
        return result;
      }),
    purge() {
      epoch++;
      attempt = null;
      reviewedPayload = null;
      emit({
        anchor: null,
        relations: null,
        targets: null,
        selected: null,
        query: '',
        relationType: 'knows',
        direction: 'outgoing',
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
export type KnowledgeRelationsSession = ReturnType<
  typeof createKnowledgeRelationsSession
>;
export default function KnowledgeRelations({
  session,
}: {
  session: KnowledgeRelationsSession;
}) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const perform = (call: () => Promise<unknown>) => {
    void call().catch(() => {});
  };
  if (state.revoked) return <p>Sign in again to open knowledge relations.</p>;
  const locked = state.busy || state.pending;
  return (
    <section
      className="stack capability-section"
      aria-label="Knowledge relations"
      aria-busy={state.busy}
    >
      <header className="capability-header">
        <div>
          <h2>Relations and replacement</h2>
          <p>Review linked knowledge before replacing an entity or relation.</p>
        </div>
        <div className="action-cluster">
          <Button disabled={locked} onClick={() => perform(session.load)}>
            {state.anchor ? 'Reload relations' : 'Open relations'}
          </Button>
        </div>
      </header>
      {state.anchor && (
        <>
          <p>
            {state.anchor.fields.subject} · {session.entityId}
          </p>
          {state.relations?.availability === 'unavailable' && (
            <ErrorState title="Relations unavailable">
              Reload to inspect the saved graph.
            </ErrorState>
          )}
          {state.relations?.availability === 'available' && (
            <>
              <p>
                {state.relations.total ?? 'Unknown'} saved relations.{' '}
                {state.relations.items.length} shown on this page.
              </p>
              <ul className="settings-results">
                {state.relations.items.map((edge) => (
                  <li className="surface" key={edge.id}>
                    <p>
                      {edge.source_id === session.entityId
                        ? 'Outgoing'
                        : 'Incoming'}{' '}
                      · {edge.relation_type} ·{' '}
                      {edge.peer_subject || edge.peer_id}
                    </p>
                    {edge.truncated && (
                      <p className="muted">Peer subject is shortened.</p>
                    )}
                    <Button
                      disabled={locked}
                      onClick={() =>
                        perform(() => session.reviewRemove(edge.id))
                      }
                    >
                      Review removal of {edge.relation_type}
                    </Button>
                  </li>
                ))}
              </ul>
              {state.relations.next_cursor && (
                <Button
                  disabled={locked}
                  onClick={() => perform(session.nextRelations)}
                >
                  Next relations
                </Button>
              )}
            </>
          )}
          <Field label="Find target knowledge">
            <Input
              maxLength={256}
              value={state.query}
              disabled={locked}
              onChange={(e) => session.setQuery(e.target.value)}
            />
          </Field>
          <Button
            disabled={locked}
            onClick={() => perform(() => session.search())}
          >
            Search targets
          </Button>
          {state.targets && (
            <>
              <p>{state.targets.total ?? 'Unknown'} matching targets.</p>
              <ul className="settings-results">
                {state.targets.items
                  .filter((item) => item.id !== session.entityId)
                  .map((item) => (
                    <li key={item.id}>
                      <Button
                        disabled={locked}
                        onClick={() => perform(() => session.select(item.id))}
                      >
                        {item.subject || 'Untitled knowledge'} ·{' '}
                        {item.entity_type}
                      </Button>
                    </li>
                  ))}
              </ul>
              {state.targets.next_cursor && (
                <Button
                  disabled={locked}
                  onClick={() => perform(() => session.search(true))}
                >
                  Next targets
                </Button>
              )}
            </>
          )}
          {state.selected && (
            <div className="surface stack">
              <p>
                Selected: {state.selected.fields.subject} · {state.selected.id}{' '}
                · {state.selected.status}
              </p>
              <Field label="Relation type">
                <Input
                  maxLength={64}
                  value={state.relationType}
                  disabled={locked}
                  onChange={(e) => session.setRelationType(e.target.value)}
                />
              </Field>
              <Field label="Direction">
                <Select
                  value={state.direction}
                  disabled={locked}
                  onChange={(e) =>
                    session.setDirection(
                      e.target.value as 'outgoing' | 'incoming',
                    )
                  }
                >
                  <option value="outgoing">This entry to target</option>
                  <option value="incoming">Target to this entry</option>
                </Select>
              </Field>
              <Button
                disabled={locked || !state.relationType.trim()}
                onClick={() => perform(session.reviewAdd)}
              >
                Review new relation
              </Button>
              <Button
                disabled={locked}
                onClick={() => perform(session.reviewSupersede)}
              >
                Review Supersede with selected entry
              </Button>
            </div>
          )}
        </>
      )}
      {state.review && (
        <div
          className="surface stack"
          role="group"
          aria-label="Review relation change"
        >
          <p>
            {state.review.action === 'knowledge.supersede'
              ? 'Mark this entry superseded and link the selected replacement. Both entries are retained.'
              : state.review.action === 'knowledge.relation.remove'
                ? 'Remove this reviewed relation. Both entities are retained.'
                : 'Save the reviewed directed relation. Existing normalized duplicates are reused.'}
          </p>
          <Button
            disabled={locked}
            variant="primary"
            onClick={() => perform(session.confirm)}
          >
            Confirm relation change
          </Button>
          <Button disabled={locked} onClick={session.dismissReview}>
            Cancel review
          </Button>
        </div>
      )}
      {state.receipt?.status === 'completed' && (
        <p role="status">
          Knowledge change saved. Projection readiness is{' '}
          {state.receipt.projection_state}. Reload relations to continue.
        </p>
      )}
      {state.receipt?.status === 'rejected' && (
        <p role="status">
          The relation changed or is invalid. Selections are retained; reload
          and review.
        </p>
      )}
      {state.pending && (
        <p role="status">
          Outcome unknown. Refresh the original command before another change.
        </p>
      )}
      {(state.pending || state.receipt) && (
        <Button disabled={state.busy} onClick={() => perform(session.refresh)}>
          Refresh original relation command
        </Button>
      )}
      {state.error && (
        <ErrorState title="Relation action unavailable">
          {state.error}
        </ErrorState>
      )}
    </section>
  );
}
