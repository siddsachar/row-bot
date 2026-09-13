import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import DocumentRemoval, {
  createDocumentRemovalSession,
  type DocumentRemovalTransport,
  type DocumentRemovalReceipt,
} from './DocumentRemoval';

const commandId = '11111111-1111-4111-8111-111111111111';
const retryId = '22222222-2222-4222-8222-222222222222';
const removalId = commandId.replaceAll('-', '');
function receipt(
  id = commandId,
  status: 'complete' | 'partial' = 'complete',
): DocumentRemovalReceipt {
  return {
    command_id: id,
    status: status === 'complete' ? 'completed' : 'partial',
    removal: {
      removal_id: removalId,
      document_id: 'document-one',
      status,
      removed: status === 'complete',
      derived_entities_removed: 2,
      retained_copy_count: 3,
      retained_kinds: ['document_source'],
      stages: [{ stage: 'source', status: 'complete' }],
      stage_counts: {
        complete: 1,
        partial: status === 'partial' ? 1 : 0,
        pending: 0,
      },
      failure_codes: [],
    },
  };
}
function transport(): DocumentRemovalTransport {
  return {
    review: vi.fn(async (document_id) => ({
      review_id: 'review-one',
      document_id,
      source_revision: 'source-one',
      source_count: 1,
      retains_copies: true as const,
    })),
    reviewRetry: vi.fn(async (source_command_id) => ({
      review_id: 'review-two',
      document_id: 'document-one',
      source_revision: 'partial-one',
      source_count: 1,
      retains_copies: true as const,
      source_command_id,
      removal_id: removalId,
    })),
    execute: vi.fn(async (command) => receipt(command.command_id)),
    receipt: vi.fn(async (id) => receipt(id)),
  };
}
function setup(api = transport()) {
  const session = createDocumentRemovalSession(
    'document-one',
    api,
    () => {},
    vi.fn().mockReturnValueOnce(commandId).mockReturnValue(retryId),
  );
  return { api, session };
}
describe('reviewed document removal', () => {
  it('mounts passively and dispatches only after explicit review and confirmation', async () => {
    const { api, session } = setup();
    render(<DocumentRemoval session={session} label="the selected document" />);
    expect(api.review).not.toHaveBeenCalled();
    expect(api.execute).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Review document removal' }),
    );
    await screen.findByRole('button', { name: 'Confirm document removal' });
    expect(api.execute).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Confirm document removal' }),
    );
    await screen.findByText('Removal complete.');
    expect(api.execute).toHaveBeenCalledExactlyOnceWith({
      command_id: commandId,
      type: 'document.remove',
      payload: {
        document_id: 'document-one',
        source_revision: 'source-one',
        review_id: 'review-one',
      },
    });
    expect(
      screen.getByText('Retained recovery references: 3'),
    ).toBeInTheDocument();
  });
  it('keeps the original command through lost acknowledgement and panel remount without redispatch', async () => {
    const { api, session } = setup();
    vi.mocked(api.execute).mockRejectedValueOnce(new Error('response lost'));
    await session.review();
    await expect(session.confirm()).rejects.toThrow('response lost');
    const mounted = render(
      <DocumentRemoval session={session} label="the selected document" />,
    );
    expect(session.getSnapshot().pending).toBe(true);
    mounted.unmount();
    render(<DocumentRemoval session={session} label="the selected document" />);
    expect(
      screen.queryByRole('button', { name: 'Review document removal' }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Refresh original removal' }),
    );
    await screen.findByText('Removal complete.');
    expect(api.receipt).toHaveBeenCalledExactlyOnceWith(commandId);
    expect(api.execute).toHaveBeenCalledTimes(1);
  });
  it('requires a new explicit retry review while preserving the original removal ID', async () => {
    const { api, session } = setup();
    vi.mocked(api.execute).mockResolvedValueOnce(receipt(commandId, 'partial'));
    await session.review();
    await session.confirm();
    await expect(session.review()).rejects.toThrow('document_retry_required');
    await session.refresh();
    expect(api.execute).toHaveBeenCalledTimes(1);
    // Keep a saved partial receipt for an explicit retry; refresh itself cannot retry.
    vi.mocked(api.receipt).mockResolvedValue(receipt(commandId, 'partial'));
    await session.refresh();
    await session.reviewRetry();
    await session.confirm();
    expect(api.execute).toHaveBeenLastCalledWith({
      command_id: retryId,
      type: 'document.removal.retry',
      payload: {
        source_command_id: commandId,
        review_id: 'review-two',
      },
    });
    expect(session.getSnapshot().receipt?.removal?.removal_id).toBe(removalId);
  });
  it('does not release submission ownership while a command is waiting', async () => {
    const { api, session } = setup();
    let finish!: (value: DocumentRemovalReceipt) => void;
    vi.mocked(api.execute).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    await session.review();
    const pending = session.confirm();
    await waitFor(() => expect(api.execute).toHaveBeenCalledTimes(1));
    await expect(session.confirm()).rejects.toThrow('document_operation_busy');
    finish(receipt());
    await pending;
    expect(api.execute).toHaveBeenCalledTimes(1);
  });
  it('purges private pending state and refuses a late response after authentication loss', async () => {
    const { api, session } = setup();
    let finish!: (value: DocumentRemovalReceipt) => void;
    vi.mocked(api.execute).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    await session.review();
    const pending = session.confirm();
    await waitFor(() => expect(api.execute).toHaveBeenCalledTimes(1));
    session.purge();
    finish(receipt());
    await expect(pending).rejects.toThrow('authentication_required');
    expect(session.getSnapshot()).toMatchObject({
      review: null,
      receipt: null,
      pending: false,
      revoked: true,
    });
    await expect(session.refresh()).rejects.toThrow('authentication_required');
  });
  it('rejects receipts for a different resource and retains uncertain original ownership', async () => {
    const { api, session } = setup();
    const foreign = receipt();
    foreign.removal!.document_id = 'foreign';
    vi.mocked(api.execute).mockResolvedValue(foreign);
    await session.review();
    await expect(session.confirm()).rejects.toThrow('document_receipt_changed');
    expect(session.getSnapshot().pending).toBe(true);
    expect(session.getSnapshot().receipt).toBeNull();
  });
  it('rejects changed review identity before any dispatch', async () => {
    const { api, session } = setup();
    vi.mocked(api.review).mockResolvedValue({
      review_id: 'review',
      source_revision: 'source',
      document_id: 'foreign',
      source_count: 1,
      retains_copies: true,
    });
    await expect(session.review()).rejects.toThrow('document_review_changed');
    expect(api.execute).not.toHaveBeenCalled();
    expect(session.getSnapshot().review).toBeNull();
  });
  it('allows a fresh review after a confirmed before-effect rejection', async () => {
    const { api, session } = setup();
    vi.mocked(api.execute).mockResolvedValueOnce({
      command_id: commandId,
      status: 'rejected',
      code: 'document_action_rejected',
    });
    await session.review();
    await session.confirm();
    render(<DocumentRemoval session={session} label="the selected document" />);
    expect(
      screen.getByText(/rejected before removal started/),
    ).toBeInTheDocument();
    await act(async () => session.review());
    expect(
      screen.getByRole('button', { name: 'Confirm document removal' }),
    ).toBeInTheDocument();
  });
});
