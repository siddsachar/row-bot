import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import {
  createDocumentJobsSession,
  DocumentJobs,
  type DocumentJobsTransport,
  type DocumentQueueItem,
  type DocumentQueuePage,
  type DocumentControlReceipt,
} from './DocumentJobs';

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
  revision: 'batch-revision',
};
const job: DocumentQueueItem = {
  ...batch,
  id: 'job',
  batch_id: 'batch',
  name: 'Example.txt',
  stage: 'parse',
  attempt: 1,
};
function page(
  items: DocumentQueueItem[],
  next_cursor: string | null = null,
): DocumentQueuePage {
  return {
    revision: 'page-revision',
    items,
    total: items.length,
    next_cursor,
    availability: 'available',
  };
}
function fixture() {
  const transport: DocumentJobsTransport = {
    batches: vi.fn(async () => page([batch])),
    jobs: vi.fn(async () => page([job])),
    review: vi.fn(async (action, payload) => ({
      review_id: 'review',
      action,
      target_id: (payload.target_id as string) ?? null,
      batch_ids: ['batch'],
      revision: 'review-revision',
      intent_digest: 'digest',
      provider_work: action.endsWith('retry') || action.endsWith('resume'),
      retains_work:
        action.endsWith('clear_finished') || action.endsWith('retry'),
    })),
    execute: vi.fn<DocumentJobsTransport['execute']>(async (command) => ({
      command_id: command.command_id,
      status: 'completed',
      outcome: 'paused',
      target_id: 'batch',
      batch_ids: ['batch'],
      saved_status: 'paused',
      count: 1,
      retained_work: false,
    })),
    receipt: vi.fn<DocumentJobsTransport['receipt']>(async (command_id) => ({
      command_id,
      status: 'partial',
      code: 'document_outcome_uncertain',
    })),
  };
  const guard = vi.fn();
  const session = createDocumentJobsSession(
    transport,
    guard,
    () => 'original-command',
  );
  return { transport, guard, session };
}

describe('document queue retained controls', () => {
  it('does not load or mutate on mount and pauses in one click', async () => {
    const { session, transport } = fixture();
    render(<DocumentJobs session={session} />);
    expect(transport.batches).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Refresh queue' }));
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    });
    expect(transport.execute).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('status')).toHaveTextContent(
      'Saved queue outcome: paused',
    );
  });

  it('retains unknown original review and uses only receipt recovery after remount', async () => {
    const { session, transport } = fixture();
    vi.mocked(transport.execute).mockRejectedValue(
      new Error('lost acknowledgement'),
    );
    await session.load();
    await session.review('document.batch.pause', 'batch');
    await expect(session.confirm()).rejects.toThrow('lost acknowledgement');
    const first = render(<DocumentJobs session={session} />);
    first.unmount();
    render(<DocumentJobs session={session} />);
    expect(session.isPending()).toBe(true);
    await expect(session.confirm()).rejects.toThrow('document_control_pending');
    await act(async () => {
      fireEvent.click(
        screen.getByRole('button', { name: 'Refresh original queue command' }),
      );
    });
    expect(transport.receipt).toHaveBeenCalledWith('original-command');
    expect(transport.execute).toHaveBeenCalledTimes(1);
    expect(session.getSnapshot().review?.review_id).toBe('review');
  });

  it('keeps single submission ownership while the request is pending', async () => {
    const { session, transport } = fixture();
    let finish!: (value: DocumentControlReceipt) => void;
    vi.mocked(transport.execute).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    await session.load();
    await session.review('document.batch.pause', 'batch');
    const pending = session.confirm();
    await Promise.resolve();
    await expect(session.load()).rejects.toThrow('document_control_pending');
    await expect(
      session.review('document.batch.cancel', 'batch'),
    ).rejects.toThrow('document_control_pending');
    finish({ command_id: 'original-command', status: 'partial' });
    await pending;
    expect(transport.execute).toHaveBeenCalledTimes(1);
  });

  it('purges authority and ignores late queue reads', async () => {
    const { session, transport } = fixture();
    let finish!: (value: DocumentQueuePage) => void;
    vi.mocked(transport.batches).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const pending = session.load();
    await Promise.resolve();
    session.purge();
    finish(page([batch]));
    await expect(pending).rejects.toThrow('authentication_required');
    expect(session.getSnapshot().batches).toBeNull();
    await expect(session.load()).rejects.toThrow('authentication_required');
  });

  it('does not apply an old completed receipt after auth loss', async () => {
    const { session, transport } = fixture();
    let finish!: (value: DocumentControlReceipt) => void;
    vi.mocked(transport.execute).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    await session.load();
    await session.review('document.batch.pause', 'batch');
    const pending = session.confirm();
    await Promise.resolve();
    session.purge();
    finish({
      command_id: 'original-command',
      status: 'completed',
      outcome: 'paused',
      target_id: 'batch',
      batch_ids: ['batch'],
    });
    await expect(pending).rejects.toThrow('authentication_required');
    expect(session.getSnapshot().receipt).toBeNull();
  });

  it('paginates both lists and rejects a changed page revision', async () => {
    const { session, transport } = fixture();
    vi.mocked(transport.batches)
      .mockResolvedValueOnce(page([batch], 'batch-cursor'))
      .mockResolvedValueOnce(page([{ ...batch, id: 'next' }]));
    await session.load();
    await session.nextBatches();
    expect(transport.batches).toHaveBeenLastCalledWith('batch-cursor');
    vi.mocked(transport.jobs)
      .mockResolvedValueOnce(page([{ ...job, batch_id: 'next' }], 'job-cursor'))
      .mockResolvedValueOnce({ ...page([]), revision: 'changed' });
    await session.openBatch('next');
    await expect(session.nextJobs()).rejects.toThrow('document_queue_changed');
    expect(session.getSnapshot().jobs?.items[0].id).toBe('job');
  });

  it('captures only explicitly selected visible terminal batches for clearing', async () => {
    const { session, transport } = fixture();
    vi.mocked(transport.batches).mockResolvedValue(
      page([
        { ...batch, status: 'cancelled' },
        { ...batch, id: 'other' },
      ]),
    );
    await session.load();
    expect(() => session.selectFinished('other', true)).toThrow(
      'invalid_document_target',
    );
    session.selectFinished('batch', true);
    await session.review('document.jobs.clear_finished');
    expect(transport.review).toHaveBeenCalledWith(
      'document.jobs.clear_finished',
      { targets: [{ id: 'batch', revision: 'batch-revision' }] },
    );
    render(<DocumentJobs session={session} />);
    expect(
      screen.getByText(/Clearing finished removes only selected queue records/),
    ).toBeVisible();
  });

  it('shows explicit provider-work review on retry and uses the current job revision', async () => {
    const { session, transport } = fixture();
    vi.mocked(transport.jobs).mockResolvedValue(
      page([{ ...job, status: 'failed' }]),
    );
    await session.load();
    await session.openBatch('batch');
    await session.review('document.job.retry', 'job');
    expect(transport.review).toHaveBeenCalledWith('document.job.retry', {
      target_id: 'job',
      revision: 'batch-revision',
    });
    render(<DocumentJobs session={session} />);
    expect(screen.getByText(/explicitly resumes queued parsing/)).toBeVisible();
  });

  it('keeps cancellation truthfully requested while an active worker remains indexing', async () => {
    const { session, transport } = fixture();
    vi.mocked(transport.jobs).mockResolvedValue(
      page([{ ...job, status: 'indexing' }]),
    );
    vi.mocked(transport.execute).mockResolvedValue({
      command_id: 'original-command',
      status: 'completed',
      outcome: 'cancellation_requested',
      target_id: 'job',
      batch_ids: ['batch'],
      saved_status: 'indexing',
    });
    await session.load();
    await session.openBatch('batch');
    await session.review('document.job.cancel', 'job');
    await session.confirm();
    render(<DocumentJobs session={session} />);
    expect(screen.getByRole('status')).toHaveTextContent(
      'cancellation_requested. indexing',
    );
  });

  it('rejects a cross-target receipt and keeps original recovery pending', async () => {
    const { session, transport } = fixture();
    vi.mocked(transport.execute).mockResolvedValue({
      command_id: 'original-command',
      status: 'completed',
      outcome: 'paused',
      target_id: 'other',
      batch_ids: ['other'],
    });
    await session.load();
    await session.review('document.batch.pause', 'batch');
    await expect(session.confirm()).rejects.toThrow(
      'document_receipt_mismatch',
    );
    expect(session.getSnapshot().pending).toBe(true);
  });

  it('treats unavailable saved queue as an explicit failure', async () => {
    const { session, transport } = fixture();
    vi.mocked(transport.batches).mockResolvedValue({
      ...page([]),
      availability: 'unavailable',
    });
    await expect(session.load()).rejects.toThrow('document_queue_unavailable');
    expect(session.getSnapshot().batches).toBeNull();
  });
});
