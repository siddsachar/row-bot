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
  createDocumentQueueSession,
  DocumentQueuePanel,
  type DocumentQueueController,
} from './DocumentQueuePanel';
import type {
  DocumentControlCommand,
  DocumentControlReceipt,
  DocumentJobAction,
  DocumentQueuePage,
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
const page: DocumentQueuePage = {
  revision: 'page',
  availability: 'available',
  total: 1,
  next_cursor: null,
  items: [
    {
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
      revision: 'saved',
    },
  ],
};
const receipt = (command_id: string): DocumentControlReceipt => ({
  command_id,
  status: 'completed',
  outcome: 'paused',
  target_id: 'batch',
  batch_ids: ['batch'],
  saved_status: 'paused',
  count: 1,
  retained_work: true,
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
    subscribe: (fn: () => void) => {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
    documentQueue: vi.fn<DocumentQueueController['documentQueue']>(
      async () => page,
    ),
    reviewDocumentControl: vi.fn(
      async (action: DocumentJobAction, payload: Record<string, unknown>) => ({
        review_id: 'review',
        action,
        target_id: payload.target_id as string,
        batch_ids: ['batch'],
        revision: 'reviewed',
        intent_digest: 'digest',
        provider_work: false,
        retains_work: true,
      }),
    ),
    executeDocumentControl: vi.fn(async (command: DocumentControlCommand) =>
      receipt(command.command_id),
    ),
    documentControlReceipt: vi.fn(async (command_id: string) =>
      receipt(command_id),
    ),
  } satisfies DocumentQueueController;
  const owner = createDocumentQueueSession(controller);
  return {
    owner,
    controller,
    state,
    listeners,
    notify: () => listeners.forEach((listener) => listener()),
  };
}

it('loads passively and pauses in one click with the original command retained on remount', async () => {
  const f = fixture();
  const blocked = deferred<DocumentControlReceipt>();
  f.controller.executeDocumentControl.mockImplementationOnce(
    () => blocked.promise,
  );
  const view = render(<DocumentQueuePanel owner={f.owner} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Pause' }));
  await waitFor(() =>
    expect(f.controller.executeDocumentControl).toHaveBeenCalledTimes(1),
  );
  expect(f.owner.hasRetained()).toBe(true);
  view.unmount();
  render(<DocumentQueuePanel owner={f.owner} />);
  expect(
    screen.getByRole('button', { name: 'Refresh original queue command' }),
  ).toBeDisabled();
  expect(f.controller.documentQueue).toHaveBeenCalledTimes(1);
  expect(f.controller.reviewDocumentControl).toHaveBeenCalledTimes(1);
  await act(async () =>
    blocked.resolve(
      receipt(f.controller.executeDocumentControl.mock.calls[0][0].command_id),
    ),
  );
  expect(f.controller.executeDocumentControl).toHaveBeenCalledTimes(1);
  f.owner.dispose();
});

it('keeps destructive cancellation review across conversation navigation without another passive load', async () => {
  const f = fixture();
  await f.owner.session.load();
  await f.owner.session.review('document.batch.cancel', 'batch');
  f.state.selectedConversationId = 'other';
  f.notify();
  render(<DocumentQueuePanel owner={f.owner} />);
  expect(
    screen.getByRole('button', { name: 'Confirm cancellation' }),
  ).toBeEnabled();
  expect(f.controller.documentQueue).toHaveBeenCalledTimes(1);
  expect(f.owner.hasRetained()).toBe(true);
  f.owner.dispose();
});

it('retains the exact uncertain command across remount and performs receipt-only recovery', async () => {
  const f = fixture();
  await f.owner.session.load();
  await f.owner.session.review('document.batch.pause', 'batch');
  f.controller.executeDocumentControl.mockRejectedValueOnce(
    new TypeError('lost response'),
  );
  await expect(f.owner.session.confirm()).rejects.toThrow('lost response');
  const original = f.controller.executeDocumentControl.mock.calls[0][0];
  const view = render(<DocumentQueuePanel owner={f.owner} />);
  fireEvent.click(
    screen.getByRole('button', { name: 'Refresh original queue command' }),
  );
  await waitFor(() =>
    expect(f.owner.session.getSnapshot().pending).toBe(false),
  );
  expect(f.controller.documentControlReceipt).toHaveBeenCalledWith(
    original.command_id,
  );
  expect(f.controller.executeDocumentControl).toHaveBeenCalledTimes(1);
  expect(f.controller.documentQueue).toHaveBeenCalledTimes(1);
  view.unmount();
  f.owner.dispose();
});

it.each(['resolve', 'reject'] as const)(
  'auth loss purges a late %s without another controller effect',
  async (mode) => {
    const f = fixture(),
      blocked = deferred<DocumentControlReceipt>();
    await f.owner.session.load();
    await f.owner.session.review('document.batch.pause', 'batch');
    f.controller.executeDocumentControl.mockImplementationOnce(
      () => blocked.promise,
    );
    const work = f.owner.session.confirm();
    await Promise.resolve();
    const original = f.controller.executeDocumentControl.mock.calls[0][0];
    f.state.handshake.client_session_id = 'new-session';
    f.notify();
    if (mode === 'resolve') blocked.resolve(receipt(original.command_id));
    else blocked.reject(Error('late rejection'));
    await expect(work).rejects.toThrow();
    expect(f.owner.session.getSnapshot().revoked).toBe(true);
    expect(f.owner.session.getSnapshot().receipt).toBeNull();
    expect(f.owner.hasRetained()).toBe(false);
    await expect(f.owner.session.load()).rejects.toThrow(
      'authentication_required',
    );
    expect(f.controller.executeDocumentControl).toHaveBeenCalledTimes(1);
    f.owner.dispose();
  },
);

it('disposal aborts a passive read and late data cannot repopulate the queue', async () => {
  const f = fixture(),
    blocked = deferred<DocumentQueuePage>();
  f.controller.documentQueue.mockImplementationOnce(() => blocked.promise);
  const view = render(<DocumentQueuePanel owner={f.owner} />);
  await waitFor(() =>
    expect(f.controller.documentQueue).toHaveBeenCalledOnce(),
  );
  const signal = f.controller.documentQueue.mock.calls[0][1] as AbortSignal;
  act(() => {
    f.owner.dispose();
  });
  expect(signal.aborted).toBe(true);
  await act(async () => {
    blocked.resolve(page);
    await Promise.resolve();
  });
  expect(f.owner.session.getSnapshot().batches).toBeNull();
  expect(f.listeners.size).toBe(0);
  view.unmount();
});
