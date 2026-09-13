import { expect, it, vi } from 'vitest';
import {
  createKnowledgeEditorSession,
  type KnowledgeEntity,
  type KnowledgeTransport,
} from './KnowledgeEditor';
import {
  createKnowledgeRelationsSession,
  type RelationTransport,
} from './KnowledgeRelations';
import {
  createDocumentJobsSession,
  type DocumentJobsTransport,
  type DocumentQueueItem,
} from './DocumentJobs';
import {
  createDocumentUploadsSession,
  type DocumentUploadTransport,
} from './DocumentUploads';
import { createKnowledgeSessions } from './knowledge-sessions';

const entity = (id = 'entity'): KnowledgeEntity => ({
  id,
  revision: 'revision',
  fields: {
    entity_type: 'fact',
    subject: 'Synthetic',
    description: 'Private draft',
    aliases: '',
    tags: '',
  },
  status: 'active',
  created_at: '',
  updated_at: '',
  saved_state: 'saved',
  projection_state: 'unknown',
});
function editor() {
  const transport: KnowledgeTransport = {
    load: vi.fn<KnowledgeTransport['load']>(async () => ({
      schema_version: 1,
      entity: entity(),
      entity_types: ['fact'],
    })),
    review: vi.fn<KnowledgeTransport['review']>(async (action, payload) => ({
      action,
      entity_id: payload.entity_id as string,
      revision: 'revision',
      fields_digest: 'digest',
      reuse_entity_id: null,
      review_id: 'review',
    })),
    execute: vi.fn<KnowledgeTransport['execute']>(async (c) => ({
      command_id: c.command_id,
      status: 'completed',
      entity_id: 'entity',
      revision: 'revision',
      saved_state: 'saved',
      projection_state: 'pending',
    })),
    receipt: vi.fn<KnowledgeTransport['receipt']>(async (id) => ({
      command_id: id,
      status: 'completed',
      entity_id: 'entity',
      revision: 'revision',
      saved_state: 'saved',
      projection_state: 'pending',
    })),
  };
  const session = createKnowledgeEditorSession('entity', transport, () => {});
  return {
    session,
    transport,
    prepare: async () => {
      await session.load(true);
      await session.review('knowledge.edit');
    },
  };
}
function relations() {
  const transport: RelationTransport = {
    entity: vi.fn<RelationTransport['entity']>(async (id) => entity(id)),
    relations: vi.fn<RelationTransport['relations']>(async () => ({
      revision: 'page',
      items: [],
      total: 0,
      next_cursor: null,
      availability: 'available',
    })),
    targets: vi.fn<RelationTransport['targets']>(async () => ({
      revision: 'page',
      items: [{ id: 'peer', subject: 'Peer', entity_type: 'fact' }],
      total: 1,
      next_cursor: null,
      availability: 'available',
    })),
    review: vi.fn<RelationTransport['review']>(async (action) => ({
      review_id: 'review',
      action,
      entity_ids: ['entity', 'peer'],
      entity_revisions: ['revision', 'revision'],
      relation_id: null,
      relation_revision: 'missing',
      intent_digest: 'digest',
    })),
    execute: vi.fn<RelationTransport['execute']>(async (c) => ({
      command_id: c.command_id,
      status: 'completed',
      outcome: 'saved',
      entity_ids: ['entity', 'peer'],
      relation_id: 'relation',
      projection_state: 'pending',
    })),
    receipt: vi.fn<RelationTransport['receipt']>(async (id) => ({
      command_id: id,
      status: 'completed',
      outcome: 'saved',
      entity_ids: ['entity', 'peer'],
      relation_id: 'relation',
      projection_state: 'pending',
    })),
  };
  const session = createKnowledgeRelationsSession(
    'entity',
    transport,
    () => {},
  );
  return {
    session,
    transport,
    prepare: async () => {
      await session.load();
      await session.search();
      await session.select('peer');
      await session.reviewAdd();
    },
  };
}
const batch: DocumentQueueItem = {
  id: 'batch',
  batch_id: null,
  name: '',
  status: 'queued',
  stage: null,
  pause_requested: false,
  cancel_requested: false,
  attempt: null,
  index_current: null,
  index_total: null,
  extraction_current: null,
  extraction_total: null,
  error_code: null,
  revision: 'revision',
};
function jobs() {
  const transport: DocumentJobsTransport = {
    batches: vi.fn<DocumentJobsTransport['batches']>(async () => ({
      revision: 'page',
      items: [batch],
      total: 1,
      next_cursor: null,
      availability: 'available',
    })),
    jobs: vi.fn<DocumentJobsTransport['jobs']>(async () => ({
      revision: 'page',
      items: [],
      total: 0,
      next_cursor: null,
      availability: 'available',
    })),
    review: vi.fn<DocumentJobsTransport['review']>(async (action) => ({
      review_id: 'review',
      action,
      target_id: 'batch',
      batch_ids: ['batch'],
      revision: 'revision',
      intent_digest: 'digest',
      provider_work: false,
      retains_work: true,
    })),
    execute: vi.fn<DocumentJobsTransport['execute']>(async (c) => ({
      command_id: c.command_id,
      status: 'completed',
      outcome: 'paused',
      target_id: 'batch',
      batch_ids: ['batch'],
    })),
    receipt: vi.fn<DocumentJobsTransport['receipt']>(async (id) => ({
      command_id: id,
      status: 'completed',
      outcome: 'paused',
      target_id: 'batch',
      batch_ids: ['batch'],
    })),
  };
  const session = createDocumentJobsSession(transport, () => {});
  return {
    session,
    transport,
    prepare: async () => {
      await session.load();
      await session.review('document.batch.pause', 'batch');
    },
  };
}
function uploads() {
  const transport: DocumentUploadTransport = {
    review: vi.fn<DocumentUploadTransport['review']>(async (files) => ({
      review_id: 'review',
      action: 'document.upload',
      files,
      file_count: files.length,
      total_bytes: files.reduce((total, file) => total + file.size_bytes, 0),
      intent_digest: 'digest',
      processing: 'paused',
      provider_work: false,
    })),
    upload: vi.fn<DocumentUploadTransport['upload']>(async (c) => ({
      command_id: c.command_id,
      status: 'completed',
      batch_id: 'client_' + c.command_id.replaceAll('-', ''),
      processing: 'paused',
      files: c.payload.files.map((f) => ({
        ...f,
        id: 'upload_' + 'a'.repeat(32),
        status: 'queued',
      })),
    })),
    receipt: vi.fn<DocumentUploadTransport['receipt']>(async (id) => ({
      command_id: id,
      status: 'partial',
    })),
  };
  const session = createDocumentUploadsSession(transport, () => {});
  return {
    session,
    transport,
    prepare: async () => {
      session.select([new File(['synthetic'], 'synthetic.txt')]);
      await session.review();
    },
  };
}

it.each([
  ['editor', editor],
  ['relations', relations],
  ['document jobs', jobs],
  ['document uploads', uploads],
] as const)(
  '%s keeps accepting explicitly reviewed operations after 32 settled commands',
  async (_label, factory) => {
    const { session, prepare } = factory();
    try {
      for (let n = 0; n < 33; n += 1) {
        await prepare();
        await session.confirm();
      }
      expect(session.getSnapshot()).toMatchObject({
        pending: false,
        busy: false,
        receipt: { status: 'completed' },
      });
    } finally {
      session.purge();
    }
  },
);

it.each([
  ['editor', editor],
  ['relations', relations],
  ['document jobs', jobs],
  ['document uploads', uploads],
] as const)(
  '%s rejects original late settlement after auth purge',
  async (_label, factory) => {
    const { session, transport, prepare } = factory();
    await prepare();
    let release!: () => void;
    const pending = new Promise<void>((done) => {
      release = done;
    });
    if ('execute' in transport) {
      vi.mocked(transport.execute).mockImplementation(async () => {
        await pending;
        throw new Error('synthetic lost reply');
      });
    } else {
      vi.mocked(transport.upload).mockImplementation(async () => {
        await pending;
        throw new Error('synthetic lost reply');
      });
    }
    const sending = session.confirm();
    await Promise.resolve();
    session.purge();
    release();
    await expect(sending).rejects.toThrow();
    expect(session.getSnapshot()).toMatchObject({
      revoked: true,
      pending: false,
      busy: false,
      receipt: null,
      review: null,
    });
    expect(transport.receipt).not.toHaveBeenCalled();
  },
);

it('knowledge collection preserves a hidden dirty draft under capacity pressure and purges on disposal', async () => {
  const fixture = editor();
  const controller = {
    getSnapshot: () => ({
      handshake: {
        instance_id: 'instance',
        server_epoch: 'epoch',
        client_session_id: 'auth',
      },
    }),
    knowledgeEditor: async (id: string | null) => ({
      schema_version: 1,
      entity: id ? entity(id) : null,
      entity_types: ['fact'],
    }),
    reviewKnowledge: fixture.transport.review,
    executeKnowledge: fixture.transport.execute,
    knowledgeReceipt: fixture.transport.receipt,
  } as unknown as Parameters<typeof createKnowledgeSessions>[0];
  const owner = createKnowledgeSessions(controller, 1);
  owner.open('entity');
  const session = owner.selected()!;
  await vi.waitFor(() => expect(session.getSnapshot().busy).toBe(false));
  session.setField('subject', 'Retained across navigation');
  owner.close();
  owner.open('other');
  expect(owner.entries()).toHaveLength(1);
  expect(owner.getSnapshot().error).toContain('retained knowledge draft');
  owner.open('entity');
  expect(owner.selected()).toBe(session);
  expect(session.getSnapshot().draft.subject).toBe(
    'Retained across navigation',
  );
  owner.dispose();
  expect(session.getSnapshot()).toMatchObject({
    revoked: true,
    pending: false,
    dirty: false,
  });
  expect(owner.entries()).toHaveLength(0);
});

it.each([
  ['editor', editor],
  ['relations', relations],
  ['document jobs', jobs],
  ['document uploads', uploads],
] as const)(
  '%s retains and refreshes only the original uncertain command',
  async (_label, factory) => {
    const { session, transport, prepare } = factory();
    await prepare();
    const effect =
      'execute' in transport ? transport.execute : transport.upload;
    vi.mocked(effect).mockRejectedValueOnce(
      new Error('synthetic lost acknowledgement'),
    );
    await expect(session.confirm()).rejects.toThrow(
      'synthetic lost acknowledgement',
    );
    const original = vi.mocked(effect).mock.calls[0][0];
    expect(session.getSnapshot().pending).toBe(true);
    await expect(session.confirm()).rejects.toThrow();
    await session.refresh();
    expect(transport.receipt).toHaveBeenCalledWith(original.command_id);
    expect(effect).toHaveBeenCalledTimes(1);
    session.purge();
    await expect(session.refresh()).rejects.toThrow('authentication_required');
    expect(transport.receipt).toHaveBeenCalledTimes(1);
  },
);
