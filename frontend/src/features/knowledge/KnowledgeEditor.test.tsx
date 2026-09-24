import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import KnowledgeEditor, {
  createKnowledgeEditorSession,
  type KnowledgeEntity,
  type KnowledgeTransport,
} from './KnowledgeEditor';
const id = '11111111-1111-4111-8111-111111111111';
const entity: KnowledgeEntity = {
  id: 'entity-one',
  revision: 'revision-one',
  fields: {
    subject: 'Synthetic subject',
    description: 'Original body',
    entity_type: 'fact',
    aliases: '',
    tags: '',
  },
  status: 'active',
  created_at: '',
  updated_at: '',
  saved_state: 'saved',
  projection_state: 'unknown',
};
function setup(target: string | null = entity.id) {
  const transport: KnowledgeTransport = {
    load: vi.fn(async () => ({
      schema_version: 1 as const,
      entity: target ? structuredClone(entity) : null,
      entity_types: ['fact', 'person'],
    })),
    review: vi.fn(async (action, payload) => ({
      action,
      entity_id: payload.entity_id as string | null,
      revision: (payload.revision as string) || 'empty-revision',
      fields_digest: 'fields-one',
      reuse_entity_id: null,
      review_id: 'review-one',
    })),
    execute: vi.fn(async (command) => ({
      command_id: command.command_id,
      status: 'completed' as const,
      entity_id: target ?? 'created-one',
      revision: 'revision-two',
      saved_state: 'saved' as const,
      projection_state: 'pending' as const,
      reused: false,
    })),
    receipt: vi.fn(async (command_id) => ({
      command_id,
      status: 'completed' as const,
      entity_id: target ?? 'created-one',
      revision: 'revision-two',
      saved_state: 'saved' as const,
      projection_state: 'pending' as const,
      reused: false,
    })),
  };
  const session = createKnowledgeEditorSession(
    target,
    transport,
    () => {},
    () => id,
  );
  return { session, transport };
}
describe('retained knowledge editor', () => {
  it('opens passively then saves one exact mutation in one click', async () => {
    const { session, transport } = setup();
    render(<KnowledgeEditor session={session} />);
    expect(transport.load).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Open editor' }));
    await screen.findByRole('textbox', { name: 'Subject' });
    fireEvent.change(screen.getByRole('textbox', { name: 'Subject' }), {
      target: { value: 'Changed subject' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save knowledge' }));
    await screen.findByText(/Knowledge saved./);
    expect(transport.execute).toHaveBeenCalledTimes(1);
    expect(session.getSnapshot().draft.subject).toBe('Changed subject');
    expect(screen.getByText(/projections remain pending/)).toBeInTheDocument();
  });
  it('retains original command and draft across lost acknowledgement and remount', async () => {
    const { session, transport } = setup();
    await session.load();
    session.setField('subject', 'My draft');
    await session.review('knowledge.edit');
    vi.mocked(transport.execute).mockRejectedValueOnce(
      new Error('lost response'),
    );
    await expect(session.confirm()).rejects.toThrow('lost response');
    const first = render(<KnowledgeEditor session={session} />);
    first.unmount();
    render(<KnowledgeEditor session={session} />);
    expect(screen.getByRole('textbox', { name: 'Subject' })).toHaveValue(
      'My draft',
    );
    expect(
      screen.getByRole('button', { name: 'Save knowledge' }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Refresh original knowledge command',
      }),
    );
    await screen.findByText(/Knowledge saved./);
    expect(transport.receipt).toHaveBeenCalledExactlyOnceWith(id);
    expect(transport.execute).toHaveBeenCalledTimes(1);
  });
  it('does not release single save ownership during pending reads or draft changes', async () => {
    const { session, transport } = setup();
    await session.load();
    await session.review('knowledge.edit');
    let resolve!: (
      value: Awaited<ReturnType<KnowledgeTransport['execute']>>,
    ) => void;
    vi.mocked(transport.execute).mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const pending = session.confirm();
    await Promise.resolve();
    session.setField('subject', 'Late input');
    await expect(session.load(true)).rejects.toThrow(
      'knowledge_operation_busy',
    );
    await expect(session.confirm()).rejects.toThrow('knowledge_operation_busy');
    resolve(await transport.receipt(id));
    await pending;
    expect(session.getSnapshot().draft.subject).toBe(entity.fields.subject);
    expect(transport.execute).toHaveBeenCalledTimes(1);
  });
  it('purges content and rejects a late saved response after auth revocation', async () => {
    const { session, transport } = setup();
    await session.load();
    await session.review('knowledge.edit');
    let resolve!: (
      value: Awaited<ReturnType<KnowledgeTransport['execute']>>,
    ) => void;
    vi.mocked(transport.execute).mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const pending = session.confirm();
    await Promise.resolve();
    session.purge();
    resolve(await transport.receipt(id));
    await expect(pending).rejects.toThrow('authentication_required');
    expect(session.getSnapshot().draft.subject).toBe('');
    expect(session.getSnapshot().receipt).toBeNull();
  });
  it('blocks replacing dirty drafts except explicit discard and reload', async () => {
    const { session } = setup();
    await session.load();
    session.setField('description', 'Keep draft');
    await expect(session.load()).rejects.toThrow('knowledge_draft_retained');
    expect(session.getSnapshot().draft.description).toBe('Keep draft');
    await session.load(true);
    expect(session.getSnapshot().draft.description).toBe('Original body');
  });
  it('invalidates reviewed fields when the draft changes', async () => {
    const { session, transport } = setup();
    await session.load();
    await session.review('knowledge.edit');
    session.setField('subject', 'Revised');
    await expect(session.confirm()).rejects.toThrow(
      'knowledge_review_unavailable',
    );
    expect(transport.execute).not.toHaveBeenCalled();
  });
  it('leaves an uncertain receipt pending rather than replaying or allowing another change', async () => {
    const { session, transport } = setup();
    await session.load();
    await session.review('knowledge.edit');
    vi.mocked(transport.execute).mockResolvedValue({
      command_id: id,
      status: 'partial',
      code: 'knowledge_outcome_uncertain',
    });
    await session.confirm();
    await expect(session.review('knowledge.archive')).rejects.toThrow(
      'knowledge_review_unavailable',
    );
    expect(session.getSnapshot().pending).toBe(true);
  });
  it('rejects a late result for another entity without replacing the current draft', async () => {
    const { session, transport } = setup();
    await session.load();
    await session.review('knowledge.edit');
    vi.mocked(transport.execute).mockResolvedValue({
      command_id: id,
      status: 'completed',
      entity_id: 'other',
      revision: 'other-revision',
      saved_state: 'saved',
    });
    await expect(session.confirm()).rejects.toThrow(
      'knowledge_receipt_changed',
    );
    expect(session.getTarget()).toBe(entity.id);
    expect(session.getSnapshot().pending).toBe(true);
  });
  it('shows canonical reuse before create confirmation and adopts only confirmed identity', async () => {
    const { session, transport } = setup(null);
    await session.load();
    session.setField('subject', 'User');
    vi.mocked(transport.review).mockResolvedValue({
      action: 'knowledge.create',
      entity_id: null,
      revision: 'existing-user-revision',
      fields_digest: 'digest',
      review_id: 'review-user',
      reuse_entity_id: 'user-one',
    });
    await session.review('knowledge.create');
    render(<KnowledgeEditor session={session} />);
    expect(session.getSnapshot().review?.reuse_entity_id).toBe('user-one');
    expect(session.getTarget()).toBeNull();
    vi.mocked(transport.execute).mockResolvedValue({
      command_id: id,
      status: 'completed',
      entity_id: 'user-one',
      revision: 'existing-user-revision',
      saved_state: 'saved',
      reused: true,
      projection_state: 'unknown',
    });
    await act(() => session.confirm());
    expect(session.getTarget()).toBe('user-one');
  });
  it.each(['archived', 'needs_review'] as const)(
    'offers retained lifecycle action for %s',
    async (status) => {
      const { session, transport } = setup();
      vi.mocked(transport.load).mockResolvedValue({
        schema_version: 1,
        entity: { ...entity, status },
        entity_types: ['fact'],
      });
      await session.load();
      render(<KnowledgeEditor session={session} />);
      fireEvent.click(
        screen.getByRole('button', {
          name: status === 'archived' ? 'Restore' : 'Resolve review',
        }),
      );
      await screen.findByText(/Knowledge saved./);
      expect(transport.execute).toHaveBeenCalledTimes(1);
    },
  );
});
