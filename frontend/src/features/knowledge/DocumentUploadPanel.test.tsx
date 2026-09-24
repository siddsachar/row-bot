import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import {
  createDocumentUploadSession,
  DocumentUploadPanel,
  type DocumentUploadController,
} from './DocumentUploadPanel';
import type {
  DocumentUploadCommand,
  DocumentUploadReceipt,
} from './DocumentUploads';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}
const file = () => new File(['synthetic'], 'source.txt');
const receipt = (command: DocumentUploadCommand): DocumentUploadReceipt => ({
  command_id: command.command_id,
  status: 'completed',
  batch_id: `client_${command.command_id.replaceAll('-', '')}`,
  processing: 'paused',
  files: command.payload.files.map((item, index) => ({
    ...item,
    id: `upload_${index.toString().padStart(32, '0')}`,
    status: 'queued',
  })),
});
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
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    reviewDocumentUpload: vi.fn<
      DocumentUploadController['reviewDocumentUpload']
    >(async (files) => ({
      review_id: 'review',
      action: 'document.upload',
      files,
      file_count: files.length,
      total_bytes: files.reduce((total, item) => total + item.size_bytes, 0),
      intent_digest: 'digest',
      processing: 'paused',
      provider_work: false,
    })),
    uploadDocuments: vi.fn<DocumentUploadController['uploadDocuments']>(
      async (command) => receipt(command),
    ),
    documentUploadReceipt: vi.fn<
      DocumentUploadController['documentUploadReceipt']
    >(async (command_id) => ({ command_id, status: 'partial' })),
  } satisfies DocumentUploadController;
  return {
    state,
    controller,
    listeners,
    notify: () => listeners.forEach((fn) => fn()),
    owner: createDocumentUploadSession(controller),
  };
}

it('opens passively and retains the exact files and review across route and chat changes', async () => {
  const f = fixture();
  const original = file();
  const view = render(<DocumentUploadPanel owner={f.owner} />);
  expect(f.controller.reviewDocumentUpload).not.toHaveBeenCalled();
  expect(f.controller.uploadDocuments).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Choose documents'), {
    target: { files: [original] },
  });
  await act(async () => {
    await f.owner.session.review();
  });
  view.unmount();
  f.state.selectedConversationId = 'another-chat';
  f.notify();
  render(<DocumentUploadPanel owner={f.owner} />);
  expect(screen.getByRole('button', { name: 'Upload selected' })).toBeEnabled();
  expect(f.owner.session.getSnapshot().files[0]).toBe(original);
  expect(f.owner.hasRetained()).toBe(true);
  expect(f.controller.reviewDocumentUpload).toHaveBeenCalledTimes(1);
  expect(f.controller.uploadDocuments).not.toHaveBeenCalled();
  f.owner.dispose();
});

it('recovers the original uncertain upload by receipt only and reports a verified staged batch once', async () => {
  const f = fixture();
  const onStaged = vi.fn();
  const original = file();
  f.owner.session.select([original]);
  await f.owner.session.review();
  f.controller.uploadDocuments.mockRejectedValueOnce(
    new Error('lost acknowledgement'),
  );
  await expect(f.owner.session.confirm()).rejects.toThrow(
    'lost acknowledgement',
  );
  const command = f.controller.uploadDocuments.mock.calls[0][0];
  const view = render(
    <DocumentUploadPanel owner={f.owner} onStaged={onStaged} />,
  );
  view.unmount();
  const remount = render(
    <DocumentUploadPanel owner={f.owner} onStaged={onStaged} />,
  );
  expect(onStaged).not.toHaveBeenCalled();
  expect(f.owner.session.getSnapshot().files[0]).toBe(original);
  expect(f.controller.uploadDocuments.mock.calls[0][1][0]).toBe(original);
  f.controller.documentUploadReceipt.mockResolvedValue(receipt(command));
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', { name: 'Refresh original upload' }),
    );
  });
  await waitFor(() =>
    expect(onStaged).toHaveBeenCalledWith(receipt(command).batch_id),
  );
  remount.unmount();
  render(<DocumentUploadPanel owner={f.owner} onStaged={onStaged} />);
  await act(async () => {
    await f.owner.session.refresh();
  });
  expect(onStaged).toHaveBeenCalledTimes(1);
  expect(f.controller.documentUploadReceipt).toHaveBeenCalledWith(
    command.command_id,
  );
  expect(f.controller.uploadDocuments).toHaveBeenCalledTimes(1);
  expect(f.controller.reviewDocumentUpload).toHaveBeenCalledTimes(1);
  f.owner.dispose();
});

it.each(['partial', 'rejected', 'mismatched'] as const)(
  'does not refresh a queue from a %s receipt',
  async (status) => {
    const f = fixture();
    const onStaged = vi.fn();
    f.owner.session.select([file()]);
    await f.owner.session.review();
    f.controller.uploadDocuments.mockImplementationOnce(async (command) =>
      status === 'mismatched'
        ? { ...receipt(command), batch_id: 'foreign-batch' }
        : { command_id: command.command_id, status },
    );
    await f.owner.session.confirm().catch(() => undefined);
    render(<DocumentUploadPanel owner={f.owner} onStaged={onStaged} />);
    expect(onStaged).not.toHaveBeenCalled();
    f.owner.dispose();
  },
);

it.each(['instance_id', 'server_epoch', 'client_session_id'] as const)(
  'purges files and late upload results when %s changes',
  async (field) => {
    const f = fixture();
    const pending = deferred<DocumentUploadReceipt>();
    const onStaged = vi.fn();
    f.owner.session.select([file()]);
    await f.owner.session.review();
    f.controller.uploadDocuments.mockReturnValueOnce(pending.promise);
    const operation = f.owner.session.confirm();
    await Promise.resolve();
    const command = f.controller.uploadDocuments.mock.calls[0][0];
    f.state.handshake[field] = 'changed';
    f.notify();
    pending.resolve(receipt(command));
    await expect(operation).rejects.toThrow('authentication_required');
    render(<DocumentUploadPanel owner={f.owner} onStaged={onStaged} />);
    expect(f.owner.session.getSnapshot().files).toEqual([]);
    expect(f.owner.session.getSnapshot().receipt).toBeNull();
    expect(f.owner.hasRetained()).toBe(false);
    expect(onStaged).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Choose documents')).toBeDisabled();
    f.owner.dispose();
  },
);

it('disposes the subscription and ignores a late review without retaining file references', async () => {
  const f = fixture();
  const review = await f.controller.reviewDocumentUpload([
    { name: 'source.txt', size_bytes: 9 },
  ]);
  const pending = deferred<typeof review>();
  f.controller.reviewDocumentUpload.mockReturnValueOnce(pending.promise);
  f.owner.session.select([file()]);
  const operation = f.owner.session.review();
  await Promise.resolve();
  f.owner.dispose();
  pending.resolve(review);
  await expect(operation).rejects.toThrow('authentication_required');
  expect(f.listeners.size).toBe(0);
  expect(f.owner.session.getSnapshot().files).toEqual([]);
  expect(f.owner.session.getSnapshot().review).toBeNull();
  expect(f.owner.hasRetained()).toBe(false);
});

it('rechecks authority even without a controller notification before queue refresh', async () => {
  const f = fixture();
  const onStaged = vi.fn();
  f.owner.session.select([file()]);
  await f.owner.session.review();
  await f.owner.session.confirm();
  f.state.handshake.client_session_id = 'other';
  render(<DocumentUploadPanel owner={f.owner} onStaged={onStaged} />);
  expect(onStaged).not.toHaveBeenCalled();
  expect(f.owner.session.getSnapshot().files).toEqual([]);
  f.owner.dispose();
});
