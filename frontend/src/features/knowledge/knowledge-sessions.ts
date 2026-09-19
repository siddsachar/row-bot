import type { ClientController } from '../../api/controller';
import type { KnowledgeReceipt as WireReceipt } from '../../api/types';
import {
  createKnowledgeEditorSession,
  type KnowledgeEditorSession,
  type KnowledgeReceipt,
} from './KnowledgeEditor';
import {
  createKnowledgeRelationsSession,
  type KnowledgeRelationsSession,
  type RelationReceipt,
} from './KnowledgeRelations';

const pair = (values: string[]): [string, string] => {
  if (values.length !== 2) throw new Error('protocol_incompatible');
  return [values[0], values[1]];
};

const relationReceipt = (
  value: import('../../api/types').KnowledgeRelationReceipt,
): RelationReceipt => ({
  command_id: value.command_id,
  status: value.status,
  ...(value.code ? { code: value.code } : {}),
  ...(value.outcome ? { outcome: value.outcome } : {}),
  ...(value.entity_ids ? { entity_ids: pair(value.entity_ids) } : {}),
  relation_id: value.relation_id,
  ...(value.projection_state
    ? { projection_state: value.projection_state }
    : {}),
});

function retainedRelations(session: KnowledgeRelationsSession | undefined) {
  const state = session?.getSnapshot();
  return (
    !!state &&
    (state.busy ||
      state.pending ||
      !!state.review ||
      !!state.selected ||
      !!state.query)
  );
}

const receipt = (value: WireReceipt): KnowledgeReceipt => ({
  command_id: value.command_id,
  status: value.status,
  ...(value.code ? { code: value.code } : {}),
  ...(value.entity_id ? { entity_id: value.entity_id } : {}),
  ...(value.revision ? { revision: value.revision } : {}),
  ...(value.saved_state ? { saved_state: value.saved_state } : {}),
  ...(value.projection_state
    ? { projection_state: value.projection_state }
    : {}),
  ...(value.reused !== null && value.reused !== undefined
    ? { reused: value.reused }
    : {}),
});

function retained(session: KnowledgeEditorSession) {
  const state = session.getSnapshot();
  return state.pending || state.busy || state.dirty || !!state.review;
}

/** Auth-owned drafts and original commands survive settings navigation. */
export function createKnowledgeSessions(
  controller: Pick<
    ClientController,
    | 'getSnapshot'
    | 'knowledgeEditor'
    | 'reviewKnowledge'
    | 'executeKnowledge'
    | 'knowledgeReceipt'
  > &
    Partial<
      Pick<
        ClientController,
        | 'knowledgeRelations'
        | 'reviewKnowledgeRelation'
        | 'executeKnowledgeRelation'
        | 'knowledgeRelationReceipt'
        | 'savedEntities'
      >
    >,
  capacity = 8,
) {
  const entries = new Map<string, KnowledgeEditorSession>();
  const relations = new Map<string, KnowledgeRelationsSession>();
  const listeners = new Set<() => void>();
  const identity = () => {
    const h = controller.getSnapshot().handshake;
    return h
      ? JSON.stringify([h.instance_id, h.server_epoch, h.client_session_id])
      : '';
  };
  const authentication = identity();
  let state = { selected: '', error: '', active: true, revision: 0 };
  const emit = (patch: Partial<typeof state>) => {
    state = { ...state, ...patch, revision: state.revision + 1 };
    listeners.forEach((listener) => listener());
  };
  const guard = () => {
    if (!state.active || !authentication || identity() !== authentication)
      throw new Error('authentication_required');
  };
  return {
    getSnapshot: () => state,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    entries: () => [...entries],
    selected: () => entries.get(state.selected),
    relations: () => relations.get(state.selected),
    openRelations() {
      guard();
      const editor = entries.get(state.selected);
      const target = editor?.getTarget();
      if (!target || relations.has(state.selected)) return;
      if (
        !controller.knowledgeRelations ||
        !controller.reviewKnowledgeRelation ||
        !controller.executeKnowledgeRelation ||
        !controller.knowledgeRelationReceipt ||
        !controller.savedEntities
      )
        throw new Error('unsupported_command');
      const session = createKnowledgeRelationsSession(
        target,
        {
          entity: async (id) => {
            const value = await controller.knowledgeEditor(id);
            if (!value.entity) throw new Error('knowledge_missing');
            return value.entity;
          },
          relations: controller.knowledgeRelations,
          targets: (query, cursor) =>
            controller.savedEntities!(query, undefined, cursor),
          review: async (action, payload) => {
            const value = await controller.reviewKnowledgeRelation!(
              action,
              payload,
            );
            return {
              ...value,
              entity_ids: pair(value.entity_ids),
              entity_revisions: pair(value.entity_revisions),
            };
          },
          execute: async (command) =>
            relationReceipt(
              await controller.executeKnowledgeRelation!(command),
            ),
          receipt: async (id) =>
            relationReceipt(await controller.knowledgeRelationReceipt!(id)),
        },
        guard,
      );
      relations.set(state.selected, session);
      emit({ error: '' });
      void session.load().catch(() => {});
    },
    select(key: string) {
      guard();
      if (entries.has(key)) emit({ selected: key, error: '' });
    },
    open(entity: string | null) {
      guard();
      if (entity) {
        const existing = [...entries].find(
          ([, session]) => session.getTarget() === entity,
        );
        if (existing) {
          emit({ selected: existing[0], error: '' });
          return;
        }
      }
      if (entries.size >= capacity) {
        const settled = [...entries].find(
          ([key, session]) =>
            !retained(session) && !retainedRelations(relations.get(key)),
        );
        if (!settled) {
          emit({
            error:
              'Finish, recover or discard a retained knowledge draft before opening another editor.',
          });
          return;
        }
        settled[1].purge();
        relations.get(settled[0])?.purge();
        relations.delete(settled[0]);
        entries.delete(settled[0]);
      }
      const key = crypto.randomUUID();
      const session = createKnowledgeEditorSession(
        entity,
        {
          load: controller.knowledgeEditor,
          review: controller.reviewKnowledge,
          execute: async (command) =>
            receipt(await controller.executeKnowledge(command)),
          receipt: async (command) =>
            receipt(await controller.knowledgeReceipt(command)),
        },
        guard,
      );
      entries.set(key, session);
      emit({ selected: key, error: '' });
      void session.load().catch(() => {});
    },
    close() {
      guard();
      emit({ selected: '' });
    },
    hasRetained: () =>
      state.active &&
      ([...entries.values()].some(retained) ||
        [...relations.values()].some(retainedRelations)),
    dispose() {
      entries.forEach((session) => session.purge());
      relations.forEach((session) => session.purge());
      relations.clear();
      entries.clear();
      emit({ active: false, selected: '', error: '' });
    },
  };
}

export type KnowledgeSessions = ReturnType<typeof createKnowledgeSessions>;
