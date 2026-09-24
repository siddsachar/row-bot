import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import {
  createDocumentUploadsSession,
  DocumentUploads,
  type DocumentUploadTransport,
  type DocumentUploadReceipt,
} from './DocumentUploads';

const commandId = '11111111-1111-4111-8111-111111111111';
const source = () => new File(['example'], 'example.txt');
function fixture() {
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
    upload: vi.fn<DocumentUploadTransport['upload']>(async (command) => ({
      command_id: command.command_id,
      status: 'completed',
      batch_id: `client_${command.command_id.replaceAll('-', '')}`,
      processing: 'paused',
      files: command.payload.files.map((file, index) => ({
        ...file,
        id: `upload_${index.toString().padStart(32, '0')}`,
        status: 'queued',
      })),
    })),
    receipt: vi.fn<DocumentUploadTransport['receipt']>(async (command_id) => ({
      command_id,
      status: 'partial',
      code: 'document_upload_uncertain',
    })),
  };
  const guard = vi.fn();
  return {
    transport,
    guard,
    session: createDocumentUploadsSession(transport, guard, () => commandId),
  };
}

describe('document upload retained staging', () => {
  it('mounts passively and requires selection and review before upload', async () => {
    const { transport, session } = fixture();
    render(<DocumentUploads session={session} />);
    expect(transport.review).not.toHaveBeenCalled();
    expect(transport.upload).not.toHaveBeenCalled();
    expect(
      screen.getByRole('button', { name: 'Upload selected' }),
    ).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Choose documents'), {
      target: { files: [source()] },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Upload selected' }));
    });
    expect(screen.getByRole('status')).toHaveTextContent('The batch is paused');
  });

  it('passes the original files without reading or buffering their contents', async () => {
    const { transport, session } = fixture();
    const file = source();
    Object.defineProperty(file, 'arrayBuffer', {
      value: vi.fn(() => {
        throw new Error('unexpected buffer');
      }),
    });
    session.select([file]);
    await session.review();
    await session.confirm();
    expect(vi.mocked(transport.upload).mock.calls[0][1][0]).toBe(file);
    expect(session.getSnapshot().files[0]).toBe(file);
  });

  it('retains an unknown original upload across remount and only reads its receipt', async () => {
    const { transport, session } = fixture();
    vi.mocked(transport.upload).mockRejectedValue(
      new Error('lost acknowledgement'),
    );
    const file = source();
    session.select([file]);
    await session.review();
    await expect(session.confirm()).rejects.toThrow('lost acknowledgement');
    const first = render(<DocumentUploads session={session} />);
    first.unmount();
    render(<DocumentUploads session={session} />);
    expect(session.getSnapshot().files[0]).toBe(file);
    expect(screen.getByLabelText('Choose documents')).toBeDisabled();
    await act(async () => {
      fireEvent.click(
        screen.getByRole('button', { name: 'Refresh original upload' }),
      );
    });
    expect(transport.receipt).toHaveBeenCalledWith(commandId);
    expect(transport.upload).toHaveBeenCalledTimes(1);
    expect(session.getSnapshot().review?.review_id).toBe('review');
  });

  it('blocks selection and duplicate submission while an upload request remains active', async () => {
    const { transport, session } = fixture();
    let finish!: (value: DocumentUploadReceipt) => void;
    vi.mocked(transport.upload).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    session.select([source()]);
    await session.review();
    const pending = session.confirm();
    await Promise.resolve();
    expect(() => session.select([source()])).toThrow('document_upload_pending');
    await expect(session.confirm()).rejects.toThrow('document_upload_pending');
    finish({ command_id: commandId, status: 'partial' });
    await pending;
    expect(transport.upload).toHaveBeenCalledTimes(1);
    expect(session.isPending()).toBe(true);
  });

  it('rejects a changed review file set and does not upload', async () => {
    const { transport, session } = fixture();
    const original = transport.review;
    vi.mocked(transport.review).mockImplementationOnce(async (files) => ({
      ...(await original(files)),
      files: [{ name: 'other.txt', size_bytes: 7 }],
    }));
    session.select([source()]);
    await expect(session.review()).rejects.toThrow(
      'document_upload_review_mismatch',
    );
    expect(transport.upload).not.toHaveBeenCalled();
  });

  it('rejects a receipt for another batch and retains recovery state', async () => {
    const { transport, session } = fixture();
    vi.mocked(transport.upload).mockResolvedValue({
      command_id: commandId,
      status: 'completed',
      batch_id: 'another-batch',
      processing: 'paused',
      files: [],
    });
    session.select([source()]);
    await session.review();
    await expect(session.confirm()).rejects.toThrow(
      'document_upload_receipt_mismatch',
    );
    expect(session.getSnapshot().pending).toBe(true);
  });

  it('purges files and ignores late successful responses after auth loss', async () => {
    const { transport, session } = fixture();
    let finish!: (value: DocumentUploadReceipt) => void;
    vi.mocked(transport.upload).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    session.select([source()]);
    await session.review();
    const pending = session.confirm();
    await Promise.resolve();
    session.purge();
    finish({ command_id: commandId, status: 'partial' });
    await expect(pending).rejects.toThrow('authentication_required');
    expect(session.getSnapshot().files).toEqual([]);
    expect(session.getSnapshot().receipt).toBeNull();
    await expect(session.refresh()).rejects.toThrow('authentication_required');
  });

  it.each([
    new File([], 'empty.txt'),
    new File(['x'], 'unsupported.exe'),
    new File(['x'], 'C:\\private\\file.txt'),
  ])('refuses invalid file selection and preserves prior files', (invalid) => {
    const { session } = fixture();
    const file = source();
    session.select([file]);
    expect(() => session.select([invalid])).toThrow('invalid_document_upload');
    expect(session.getSnapshot().files[0]).toBe(file);
  });

  it('reports duplicate retention from the saved receipt', async () => {
    const { transport, session } = fixture();
    vi.mocked(transport.upload).mockResolvedValue({
      command_id: commandId,
      status: 'completed',
      batch_id: `client_${commandId.replaceAll('-', '')}`,
      processing: 'paused',
      files: [
        {
          id: `upload_${'a'.repeat(32)}`,
          name: 'example.txt',
          size_bytes: 7,
          status: 'skipped_duplicate',
        },
      ],
    });
    session.select([source()]);
    await session.review();
    await session.confirm();
    render(<DocumentUploads session={session} />);
    expect(screen.getByText(/Duplicate detected; retained copy/)).toBeVisible();
  });
});
