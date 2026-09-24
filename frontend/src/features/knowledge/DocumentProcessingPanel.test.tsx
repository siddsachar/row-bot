import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import {
  createDocumentProcessingSession,
  DocumentProcessingPanel,
  type DocumentProcessingController,
  type DocumentProcessingReceipt,
  type DocumentProcessingReview,
} from './DocumentProcessingPanel';
import {
  createDocumentJobsSession,
  DocumentJobs,
  type DocumentQueueItem,
} from './DocumentJobs';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function fixture() {
  const state = {
    handshake: {
      instance_id: 'instance',
      server_epoch: 'epoch',
      client_session_id: 'session',
    },
    selectedConversationId: 'chat',
  };
  const listeners = new Set<() => void>();
  const controller = {
    getSnapshot: () =>
      state as unknown as ReturnType<ClientController['getSnapshot']>,
    subscribe: (fn: () => void) => {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
    reviewDocumentProcessing: vi.fn<
      DocumentProcessingController['reviewDocumentProcessing']
    >(async (conversation_id, batch_id) => ({
      schema_version: 1,
      action: 'document.batch.process',
      conversation_id,
      batch_id,
      revision: 'full-reviewed-scope',
      policy_digest: 'policy',
      provider_work: true,
      knowledge_projection_scope: 'saved_knowledge',
      review_id: 'review',
      chat: {
        provider_id: 'openai',
        model_ref: 'model:openai:synthetic',
        execution_location: 'remote',
      },
      embedding: { provider: 'local', execution_location: 'local' },
    })),
    executeDocumentProcessing: vi.fn<
      DocumentProcessingController['executeDocumentProcessing']
    >(async (_, original) => ({
      command_id: original.command_id,
      status: 'completed',
      batch_id: original.payload.batch_id,
      processing: 'admitted',
    })),
    documentProcessingReceipt: vi.fn<
      DocumentProcessingController['documentProcessingReceipt']
    >(async (_, command_id) => ({
      command_id,
      status: 'completed',
      batch_id: 'client_batch',
      processing: 'admitted',
    })),
  } satisfies DocumentProcessingController;
  let sequence = 0;
  const owner = createDocumentProcessingSession(
    controller,
    () => `command-${++sequence}`,
  );
  owner.select('chat', 'client_batch', 'row-revision');
  return {
    owner,
    controller,
    state,
    notify: () => listeners.forEach((fn) => fn()),
  };
}

it('starts processing in one click with the exact reviewed full scope', async () => {
  const f = fixture();
  const admitted = vi.fn();
  render(<DocumentProcessingPanel owner={f.owner} onAdmitted={admitted} />);
  expect(f.controller.reviewDocumentProcessing).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Start processing' }));
  await screen.findByText(/Processing admitted. Documents may still/);
  expect(f.controller.executeDocumentProcessing).toHaveBeenCalledWith('chat', {
    command_id: 'command-1',
    type: 'document.batch.process',
    payload: {
      conversation_id: 'chat',
      batch_id: 'client_batch',
      revision: 'full-reviewed-scope',
      review_id: 'review',
    },
  });
  expect(admitted).toHaveBeenCalledTimes(1);
  f.owner.dispose();
});

it('retains exact review and original across remount and conversation navigation', async () => {
  const f = fixture();
  const response = deferred<DocumentProcessingReceipt>();
  f.controller.executeDocumentProcessing.mockReturnValue(response.promise);
  await f.owner.review();
  const view = render(<DocumentProcessingPanel owner={f.owner} />);
  view.unmount();
  f.state.selectedConversationId = 'other';
  f.notify();
  render(<DocumentProcessingPanel owner={f.owner} />);
  expect(screen.getByText('Conversation: chat')).toBeVisible();
  let running!: Promise<void>;
  act(() => {
    running = f.owner.confirm();
  });
  expect(() => f.owner.select('other', 'client_other', 'revision')).toThrow(
    'document_processing_pending',
  );
  await act(async () => {
    response.resolve({ command_id: 'command-1', status: 'partial' });
    await running;
  });
  expect(f.owner.getSnapshot().pending).toBe(true);
  await act(() => f.owner.refresh());
  expect(f.controller.documentProcessingReceipt).toHaveBeenCalledWith(
    'chat',
    'command-1',
  );
  expect(f.controller.executeDocumentProcessing).toHaveBeenCalledTimes(1);
  f.owner.dispose();
});

it('keeps uncertain original after lost response and reads only its receipt', async () => {
  const f = fixture();
  f.controller.executeDocumentProcessing.mockRejectedValue(
    new Error('lost response'),
  );
  await f.owner.review();
  await expect(f.owner.confirm()).rejects.toThrow('lost response');
  await expect(f.owner.confirm()).rejects.toThrow(
    'document_processing_pending',
  );
  expect(() => f.owner.select('chat', 'client_other', 'new')).toThrow();
  render(<DocumentProcessingPanel owner={f.owner} />);
  expect(
    screen.queryByRole('button', { name: 'Start processing' }),
  ).not.toBeInTheDocument();
  await act(() => f.owner.refresh());
  expect(f.controller.executeDocumentProcessing).toHaveBeenCalledTimes(1);
  expect(f.owner.getSnapshot().pending).toBe(false);
  f.owner.dispose();
});

it.each(['success', 'failure'])(
  'purges after auth loss and ignores late %s',
  async (outcome) => {
    const f = fixture();
    const response = deferred<DocumentProcessingReceipt>();
    f.controller.executeDocumentProcessing.mockReturnValue(response.promise);
    await f.owner.review();
    const running = f.owner.confirm().catch(() => undefined);
    f.state.handshake.client_session_id = 'replacement';
    f.notify();
    if (outcome === 'success')
      response.resolve({
        command_id: 'command-1',
        status: 'completed',
        batch_id: 'client_batch',
        processing: 'admitted',
      });
    else response.reject(new Error('late failure'));
    await running;
    expect(f.owner.getSnapshot()).toMatchObject({
      revoked: true,
      original: null,
      review: null,
      selection: null,
      error: '',
    });
    expect(f.owner.hasRetained()).toBe(false);
    f.owner.dispose();
  },
);

it('does not restore a late review after disposal', async () => {
  const f = fixture();
  const response = deferred<DocumentProcessingReview>();
  const valid = await f.controller.reviewDocumentProcessing(
    'chat',
    'client_batch',
    'row',
  );
  f.controller.reviewDocumentProcessing.mockReturnValue(response.promise);
  const running = f.owner.review().catch(() => undefined);
  f.owner.dispose();
  response.resolve(valid);
  await running;
  expect(f.owner.getSnapshot().review).toBeNull();
});

it.each(['command', 'batch', 'outcome'])(
  'rejects a mismatched completed %s receipt and never notifies admitted',
  async (kind) => {
    const f = fixture();
    f.controller.executeDocumentProcessing.mockImplementation(
      async (_, original) => ({
        command_id: kind === 'command' ? 'foreign' : original.command_id,
        status: 'completed',
        batch_id: kind === 'batch' ? 'foreign' : 'client_batch',
        ...(kind === 'outcome' ? {} : { processing: 'admitted' as const }),
      }),
    );
    await f.owner.review();
    await expect(f.owner.confirm()).rejects.toThrow(
      'document_processing_receipt_mismatch',
    );
    const admitted = vi.fn();
    render(<DocumentProcessingPanel owner={f.owner} onAdmitted={admitted} />);
    expect(admitted).not.toHaveBeenCalled();
    expect(f.owner.getSnapshot().pending).toBe(true);
    f.owner.dispose();
  },
);

it('does not retain a terminal attempt history or exhaust a lifetime cap', async () => {
  const f = fixture();
  for (let index = 0; index < 40; index++) {
    f.owner.select('chat', `client_${index}`, `revision-${index}`);
    await f.owner.review();
    await f.owner.confirm();
  }
  expect(f.controller.executeDocumentProcessing).toHaveBeenCalledTimes(40);
  expect(f.owner.getSnapshot().original?.command_id).toBe('command-40');
  f.owner.dispose();
});

it('refuses a review returned for another conversation before command creation', async () => {
  const f = fixture();
  const value = await f.controller.reviewDocumentProcessing(
    'other',
    'client_batch',
    'row',
  );
  f.controller.reviewDocumentProcessing.mockResolvedValue(value);
  await expect(f.owner.review()).rejects.toThrow(
    'document_processing_review_mismatch',
  );
  expect(f.owner.getSnapshot().review).toBeNull();
  expect(f.owner.getSnapshot().original).toBeNull();
  expect(f.controller.executeDocumentProcessing).not.toHaveBeenCalled();
  f.owner.dispose();
});

it('notifies verified admission once across remount and repeated receipt reads', async () => {
  const f = fixture();
  await f.owner.review();
  await f.owner.confirm();
  const admitted = vi.fn();
  const view = render(
    <DocumentProcessingPanel owner={f.owner} onAdmitted={admitted} />,
  );
  expect(admitted).toHaveBeenCalledTimes(1);
  view.unmount();
  render(<DocumentProcessingPanel owner={f.owner} onAdmitted={admitted} />);
  await act(() => f.owner.refresh());
  expect(admitted).toHaveBeenCalledTimes(1);
  expect(f.controller.executeDocumentProcessing).toHaveBeenCalledTimes(1);
  f.owner.dispose();
});

it('offers processing only for eligible paused client batches without dispatching', async () => {
  const item: DocumentQueueItem = {
    id: 'client_batch',
    batch_id: null,
    name: '',
    status: 'paused',
    stage: null,
    pause_requested: true,
    cancel_requested: false,
    attempt: null,
    index_current: null,
    index_total: null,
    extraction_current: null,
    extraction_total: null,
    error_code: null,
    revision: 'saved',
  };
  const session = createDocumentJobsSession(
    {
      batches: async () => ({
        availability: 'available',
        items: [
          item,
          { ...item, id: 'legacy' },
          { ...item, id: 'client_cancelled', cancel_requested: true },
          { ...item, id: 'client_running', status: 'extracting' },
        ],
        next_cursor: null,
        total: 4,
        revision: 'page',
      }),
      jobs: vi.fn(),
      review: vi.fn(),
      execute: vi.fn(),
      receipt: vi.fn(),
    },
    () => undefined,
  );
  await session.load();
  const select = vi.fn();
  render(<DocumentJobs session={session} onProcess={select} />);
  expect(screen.getAllByRole('button', { name: /^Process / })).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: 'Process client_batch' }));
  expect(select).toHaveBeenCalledWith(item);
});
