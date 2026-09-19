import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import KnowledgeRelations, {
  createKnowledgeRelationsSession,
  type Relation,
  type RelationReceipt,
  type RelationTransport,
} from './KnowledgeRelations';
import type { KnowledgeEntity } from './KnowledgeEditor';
const id = '11111111-1111-4111-8111-111111111111';
const first: KnowledgeEntity = {
  id: 'first',
  revision: 'first-revision',
  fields: {
    entity_type: 'fact',
    subject: 'First subject',
    description: 'Body',
    aliases: '',
    tags: '',
  },
  status: 'active',
  created_at: '',
  updated_at: '',
  saved_state: 'saved',
  projection_state: 'unknown',
};
const second: KnowledgeEntity = {
  ...first,
  id: 'second',
  revision: 'second-revision',
  fields: { ...first.fields, subject: 'Second subject' },
};
const edge: Relation = {
  id: 'edge-one',
  source_id: 'first',
  target_id: 'second',
  relation_type: 'knows',
  confidence: 1,
  revision: 'edge-revision',
  peer_id: 'second',
  peer_subject: 'Second subject',
  truncated: false,
};
function setup() {
  const transport: RelationTransport = {
    entity: vi.fn(async (target) =>
      structuredClone(target === 'first' ? first : second),
    ),
    relations: vi.fn(async () => ({
      revision: 'relations-one',
      items: [structuredClone(edge)],
      total: 1,
      next_cursor: null,
      availability: 'available' as const,
    })),
    targets: vi.fn(async () => ({
      revision: 'targets-one',
      availability: 'available' as const,
      items: [{ id: 'second', subject: 'Second subject', entity_type: 'fact' }],
      total: 1,
      next_cursor: null,
    })),
    review: vi.fn(async (action, payload) => ({
      review_id: 'review-one',
      action,
      entity_ids: [
        payload.source_id ?? payload.old_id ?? 'first',
        payload.target_id ?? payload.new_id ?? 'second',
      ] as [string, string],
      entity_revisions: ['first-revision', 'second-revision'] as [
        string,
        string,
      ],
      relation_id: action === 'knowledge.relation.remove' ? 'edge-one' : null,
      relation_revision: 'edge-revision',
      intent_digest: 'intent-one',
    })),
    execute: vi.fn(async (command) => ({
      command_id: command.command_id,
      status: 'completed' as const,
      outcome:
        command.type === 'knowledge.supersede'
          ? ('superseded' as const)
          : command.type === 'knowledge.relation.remove'
            ? ('removed' as const)
            : ('saved' as const),
      entity_ids: ['first', 'second'] as [string, string],
      relation_id: command.type === 'knowledge.supersede' ? null : 'edge-one',
      projection_state: 'pending' as const,
    })),
    receipt: vi.fn(async (command_id) => ({
      command_id,
      status: 'completed' as const,
      outcome: 'saved' as const,
      entity_ids: ['first', 'second'] as [string, string],
      relation_id: 'edge-one',
      projection_state: 'pending' as const,
    })),
  };
  const session = createKnowledgeRelationsSession(
    'first',
    transport,
    () => {},
    () => id,
  );
  return { transport, session };
}
async function select(context: ReturnType<typeof setup>) {
  await context.session.load();
  await context.session.search();
  await context.session.select('second');
}
describe('retained knowledge relations', () => {
  it('mounts passively and offers explicit target search, directed review and confirmation', async () => {
    const { session, transport } = setup();
    render(<KnowledgeRelations session={session} />);
    expect(transport.entity).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Open relations' }));
    await screen.findByRole('button', { name: 'Search targets' });
    fireEvent.click(screen.getByRole('button', { name: 'Search targets' }));
    fireEvent.click(
      await screen.findByRole('button', { name: 'Second subject · fact' }),
    );
    await screen.findByRole('textbox', { name: 'Relation type' });
    fireEvent.change(screen.getByRole('textbox', { name: 'Relation type' }), {
      target: { value: 'works for' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Review new relation' }),
    );
    await screen.findByRole('button', { name: 'Confirm relation change' });
    expect(transport.execute).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Confirm relation change' }),
    );
    await screen.findByText(/Knowledge change saved/);
    expect(transport.execute).toHaveBeenCalledTimes(1);
    expect(vi.mocked(transport.execute).mock.calls[0][0].payload).toMatchObject(
      {
        source_id: 'first',
        target_id: 'second',
        relation_type: 'works for',
        review_id: 'review-one',
      },
    );
  });
  it('keeps original command through response loss and remount without replay', async () => {
    const context = setup();
    await select(context);
    await context.session.reviewAdd();
    vi.mocked(context.transport.execute).mockRejectedValueOnce(
      new Error('lost'),
    );
    await expect(context.session.confirm()).rejects.toThrow('lost');
    const mounted = render(<KnowledgeRelations session={context.session} />);
    mounted.unmount();
    render(<KnowledgeRelations session={context.session} />);
    expect(
      screen.getByRole('button', { name: 'Review new relation' }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Refresh original relation command' }),
    );
    await screen.findByText(/Knowledge change saved/);
    expect(context.transport.receipt).toHaveBeenCalledExactlyOnceWith(id);
    expect(context.transport.execute).toHaveBeenCalledTimes(1);
  });
  it('reviews both exact snapshots for Supersede and retains both identities', async () => {
    const context = setup();
    await select(context);
    await context.session.reviewSupersede();
    render(<KnowledgeRelations session={context.session} />);
    expect(screen.getByText(/Both entries are retained/)).toBeInTheDocument();
    await act(() => context.session.confirm());
    expect(vi.mocked(context.transport.execute).mock.calls[0][0]).toEqual({
      command_id: id,
      type: 'knowledge.supersede',
      payload: {
        old_id: 'first',
        new_id: 'second',
        old_revision: 'first-revision',
        new_revision: 'second-revision',
        review_id: 'review-one',
      },
    });
  });
  it('removes only the reviewed edge and reads its peer snapshot', async () => {
    const context = setup();
    await context.session.load();
    await context.session.reviewRemove('edge-one');
    await context.session.confirm();
    expect(
      vi.mocked(context.transport.execute).mock.calls[0][0].payload,
    ).toMatchObject({
      relation_id: 'edge-one',
      relation_revision: 'edge-revision',
      source_revision: 'first-revision',
      target_revision: 'second-revision',
    });
  });
  it('invalidates review when selected direction or relation type changes', async () => {
    const context = setup();
    await select(context);
    await context.session.reviewAdd();
    context.session.setDirection('incoming');
    await expect(context.session.confirm()).rejects.toThrow(
      'knowledge_review_unavailable',
    );
    await context.session.reviewAdd();
    context.session.setRelationType('works_on');
    await expect(context.session.confirm()).rejects.toThrow(
      'knowledge_review_unavailable',
    );
    expect(context.transport.execute).not.toHaveBeenCalled();
  });
  it('rejects pending second dispatch and late authority responses', async () => {
    const context = setup();
    await select(context);
    await context.session.reviewAdd();
    let resolve!: (value: RelationReceipt) => void;
    vi.mocked(context.transport.execute).mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const pending = context.session.confirm();
    await Promise.resolve();
    await expect(context.session.load()).rejects.toThrow(
      'knowledge_operation_busy',
    );
    await expect(context.session.confirm()).rejects.toThrow(
      'knowledge_operation_busy',
    );
    context.session.purge();
    resolve(await context.transport.receipt(id));
    await expect(pending).rejects.toThrow('authentication_required');
    expect(context.session.getSnapshot().selected).toBeNull();
    expect(context.session.getSnapshot().anchor).toBeNull();
  });
  it('keeps unknown outcome pending and never selects a new action', async () => {
    const context = setup();
    await select(context);
    await context.session.reviewAdd();
    vi.mocked(context.transport.execute).mockResolvedValue({
      command_id: id,
      status: 'partial',
      code: 'knowledge_outcome_uncertain',
    });
    await context.session.confirm();
    await expect(context.session.reviewSupersede()).rejects.toThrow(
      'knowledge_outcome_uncertain',
    );
    expect(context.session.getSnapshot().pending).toBe(true);
  });
  it('rejects a receipt with changed endpoints or a different action outcome', async () => {
    const context = setup();
    await select(context);
    await context.session.reviewAdd();
    vi.mocked(context.transport.execute).mockResolvedValue({
      command_id: id,
      status: 'completed',
      outcome: 'removed',
      entity_ids: ['first', 'second'],
      relation_id: 'edge-one',
      projection_state: 'pending',
    });
    await expect(context.session.confirm()).rejects.toThrow(
      'knowledge_receipt_changed',
    );
    expect(context.session.getSnapshot().pending).toBe(true);
  });
  it('uses real continuation and rejects changed relation revisions', async () => {
    const context = setup();
    vi.mocked(context.transport.relations)
      .mockResolvedValueOnce({
        revision: 'one',
        items: [edge],
        total: 51,
        next_cursor: 'cursor-one',
        availability: 'available',
      })
      .mockResolvedValueOnce({
        revision: 'two',
        items: [],
        total: 50,
        next_cursor: null,
        availability: 'available',
      });
    await context.session.load();
    await expect(context.session.nextRelations()).rejects.toThrow(
      'cursor_expired',
    );
    expect(context.transport.relations).toHaveBeenLastCalledWith(
      'first',
      'cursor-one',
    );
  });
  it('clears an old target cursor when the query draft changes', async () => {
    const context = setup();
    vi.mocked(context.transport.targets).mockResolvedValue({
      revision: 'one',
      items: [],
      total: 100,
      availability: 'available',
      next_cursor: 'old-cursor',
    });
    await context.session.search();
    context.session.setQuery('Different query');
    await expect(context.session.search(true)).rejects.toThrow(
      'relation_page_unavailable',
    );
    expect(context.transport.targets).toHaveBeenCalledTimes(1);
  });
  it('rejects selecting a target not present in the current bounded result page', async () => {
    const context = setup();
    await context.session.search();
    await expect(context.session.select('unlisted')).rejects.toThrow(
      'knowledge_target_changed',
    );
    expect(context.transport.entity).not.toHaveBeenCalled();
  });
  it('uses target-to-anchor identities for an incoming relation', async () => {
    const context = setup();
    await select(context);
    context.session.setDirection('incoming');
    await context.session.reviewAdd();
    expect(context.transport.review).toHaveBeenLastCalledWith(
      'knowledge.relation.add',
      {
        source_id: 'second',
        target_id: 'first',
        source_revision: 'second-revision',
        target_revision: 'first-revision',
        relation_type: 'knows',
      },
    );
  });
  it('reports an unavailable target library instead of showing a false empty result', async () => {
    const context = setup();
    vi.mocked(context.transport.targets).mockResolvedValue({
      revision: 'unavailable',
      availability: 'unavailable',
      items: [],
      total: null,
      next_cursor: null,
    });
    await expect(context.session.search()).rejects.toThrow(
      'knowledge_targets_unavailable',
    );
    expect(context.session.getSnapshot().targets).toBeNull();
  });
});
