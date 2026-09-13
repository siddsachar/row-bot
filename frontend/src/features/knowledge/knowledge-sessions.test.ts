import { describe, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import { createKnowledgeSessions } from './knowledge-sessions';

function controller() {
  const state = {
    handshake: {
      instance_id: 'instance',
      server_epoch: 'epoch',
      client_session_id: 'session',
    },
  };
  return {
    state,
    getSnapshot: () =>
      state as unknown as ReturnType<ClientController['getSnapshot']>,
    knowledgeEditor: vi.fn(async () => ({
      schema_version: 1 as const,
      entity: null,
      entity_types: ['fact'],
    })),
    reviewKnowledge: vi.fn(),
    executeKnowledge: vi.fn(),
    knowledgeReceipt: vi.fn(),
  };
}

describe('knowledge editor auth ownership', () => {
  it('retains relation selection under the editor owner across close and purges both on auth loss', async () => {
    const base = controller();
    const transport = {
      ...base,
      knowledgeEditor: vi.fn(async (id: string | null) => ({
        schema_version: 1 as const,
        entity_types: ['fact'],
        entity: id
          ? {
              id,
              revision: 'a'.repeat(64),
              fields: {
                subject: 'Saved entry',
                entity_type: 'fact' as const,
                description: '',
                aliases: '',
                tags: '',
              },
              status: 'active' as const,
              created_at: '',
              updated_at: '',
              saved_state: 'saved' as const,
              projection_state: 'unknown' as const,
            }
          : null,
      })),
      knowledgeRelations: vi.fn(async () => ({
        schema_version: 1 as const,
        revision: 'b'.repeat(64),
        items: [],
        total: 0,
        next_cursor: null,
        availability: 'available' as const,
      })),
      reviewKnowledgeRelation: vi.fn(),
      executeKnowledgeRelation: vi.fn(),
      knowledgeRelationReceipt: vi.fn(),
      savedEntities: vi.fn(),
    };
    const owner = createKnowledgeSessions(transport, 1);
    owner.open('entry');
    await vi.waitFor(() =>
      expect(owner.selected()?.getSnapshot().saved?.entity?.id).toBe('entry'),
    );
    owner.openRelations();
    const relations = owner.relations()!;
    await vi.waitFor(() => expect(relations.getSnapshot().busy).toBe(false));
    relations.setQuery('Unsent target search');
    owner.close();
    owner.open(null);
    expect(owner.getSnapshot().error).toContain('retained knowledge draft');
    owner.open('entry');
    expect(owner.relations()).toBe(relations);
    expect(relations.getSnapshot().query).toBe('Unsent target search');
    expect(transport.knowledgeRelations).toHaveBeenCalledTimes(1);
    expect(owner.hasRetained()).toBe(true);
    owner.dispose();
    expect(relations.getSnapshot().revoked).toBe(true);
    expect(relations.getSnapshot().query).toBe('');
    expect(transport.executeKnowledgeRelation).not.toHaveBeenCalled();
  });

  it('preserves dirty drafts across close and refuses capacity eviction until explicit discard', async () => {
    const transport = controller(),
      owner = createKnowledgeSessions(transport, 1);
    owner.open(null);
    await vi.waitFor(() =>
      expect(owner.selected()?.getSnapshot().busy).toBe(false),
    );
    const original = owner.selected()!;
    original.setField('subject', 'Unsent knowledge');
    owner.close();
    owner.open(null);
    expect(owner.getSnapshot().error).toContain('retained knowledge draft');
    expect(owner.entries()).toHaveLength(1);
    owner.select(owner.entries()[0][0]);
    expect(owner.selected()).toBe(original);
    expect(owner.selected()?.getSnapshot().draft.subject).toBe(
      'Unsent knowledge',
    );
    expect(owner.hasRetained()).toBe(true);
    await original.load(true);
    owner.open(null);
    expect(original.getSnapshot().revoked).toBe(true);
    expect(owner.entries()).toHaveLength(1);
    owner.dispose();
  });

  it('purges pending reads and ignores their late result on auth loss', async () => {
    const transport = controller();
    let resolve!: (
      value: Awaited<ReturnType<ClientController['knowledgeEditor']>>,
    ) => void;
    const pending = new Promise<
      Awaited<ReturnType<ClientController['knowledgeEditor']>>
    >((done) => {
      resolve = done;
    });
    transport.knowledgeEditor = vi.fn(
      () => pending,
    ) as typeof transport.knowledgeEditor;
    const owner = createKnowledgeSessions(transport);
    owner.open(null);
    const session = owner.selected()!;
    owner.dispose();
    resolve({ schema_version: 1, entity: null, entity_types: ['fact'] });
    await vi.waitFor(() => expect(session.getSnapshot().revoked).toBe(true));
    expect(session.getSnapshot().saved).toBeNull();
    expect(owner.hasRetained()).toBe(false);
    expect(transport.executeKnowledge).not.toHaveBeenCalled();
    expect(() => owner.open(null)).toThrow('authentication_required');
  });
});
