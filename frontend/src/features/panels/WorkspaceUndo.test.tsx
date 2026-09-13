import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import WorkspaceUndo, {
  WorkspaceUndoSession,
  type WorkspaceUndoProps,
  type WorkspaceUndoReview,
  type WorkspaceUndoResult,
} from './WorkspaceUndo';

const review: WorkspaceUndoReview = {
  resource_id: 'workspace',
  conversation_id: 'chat',
  resource_revision: 'r',
  binding_id: 'binding',
  binding_revision: '1',
  change_set_id: 'change',
  change_set_revision: 'a'.repeat(64),
  host_revision: 'b'.repeat(64),
  policy_revision: 'c'.repeat(64),
  policy_decision: 'ask',
  approval_required: true,
  files: ['<script>text.txt'],
  directories_retained: ['new/nested'],
  action_digest: 'd'.repeat(64),
  nonce: 'original-nonce',
};
const result = (
  id: string,
  extra: Partial<WorkspaceUndoResult> = {},
): WorkspaceUndoResult => ({
  command_id: id,
  resource_id: 'workspace',
  conversation_id: 'chat',
  change_set_id: 'change',
  status: 'undone',
  files_restored: ['<script>text.txt'],
  ledger_saved: true,
  reverted: true,
  ...extra,
});
function props(extra: Partial<WorkspaceUndoProps> = {}): WorkspaceUndoProps {
  return {
    scope: 'auth:chat:binding',
    changeSetId: 'change',
    review: vi.fn().mockResolvedValue(review),
    apply: vi.fn<WorkspaceUndoProps['apply']>(async (_review, id) =>
      result(id),
    ),
    recover: vi.fn<WorkspaceUndoProps['recover']>(async (_review, id) =>
      result(id),
    ),
    receipt: vi.fn().mockResolvedValue(null),
    onUndone: vi.fn(),
    ...extra,
  };
}
async function reviewed() {
  fireEvent.click(screen.getByRole('button', { name: 'Review Undo' }));
  await screen.findByRole('button', { name: 'Undo these changes' });
}
it('requires an explicit review and confirmation, displays plain names and retained folders, and cancels without an effect', async () => {
  const io = props();
  render(<WorkspaceUndo {...io} />);
  expect(io.review).not.toHaveBeenCalled();
  await reviewed();
  expect(screen.getByText('<script>text.txt')).toBeInTheDocument();
  expect(screen.getByText('new/nested')).toBeInTheDocument();
  expect(document.querySelector('section script')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel review' }));
  expect(io.apply).not.toHaveBeenCalled();
  expect(screen.getByRole('status')).toHaveTextContent('Files are unchanged');
});
it('preserves the original immutable nonce and command across partial recovery and remount', async () => {
  const session = new WorkspaceUndoSession('auth:chat:binding');
  const mutable = {
    ...review,
    files: [...review.files],
    directories_retained: [...review.directories_retained],
  };
  const io = props({
    session,
    review: vi.fn().mockResolvedValue(mutable),
    apply: vi.fn<WorkspaceUndoProps['apply']>(async (_review, id) =>
      result(id, { status: 'partial', reverted: false, ledger_saved: false }),
    ),
  });
  const first = render(<WorkspaceUndo {...io} />);
  await reviewed();
  mutable.nonce = 'changed';
  mutable.files[0] = 'other.txt';
  fireEvent.click(screen.getByRole('button', { name: 'Undo these changes' }));
  await screen.findByText(/Undo is incomplete/);
  expect(session.hasRetained()).toBe(true);
  const [original, command] = vi.mocked(io.apply).mock.calls[0];
  expect(original.nonce).toBe('original-nonce');
  expect(original.files).toEqual(['<script>text.txt']);
  first.unmount();
  render(<WorkspaceUndo {...io} changeSetId="new-selection" />);
  expect(screen.getByText('Change set: change')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Retry original Undo' }));
  await screen.findByText(/Original files restored/);
  expect(io.recover).toHaveBeenCalledWith(original, command);
  expect(io.review).toHaveBeenCalledTimes(1);
  expect(session.hasRetained()).toBe(false);
});
it('admits one in-flight operation and settles it after panel unmount', async () => {
  let settle!: (value: WorkspaceUndoResult) => void;
  const session = new WorkspaceUndoSession('auth:chat:binding');
  const io = props({
    session,
    apply: vi.fn(
      () =>
        new Promise<WorkspaceUndoResult>((resolve) => {
          settle = resolve;
        }),
    ),
  });
  const first = render(<WorkspaceUndo {...io} />);
  await reviewed();
  const button = screen.getByRole('button', { name: 'Undo these changes' });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(io.apply).toHaveBeenCalledTimes(1);
  const command = vi.mocked(io.apply).mock.calls[0][1];
  first.unmount();
  await act(async () => settle(result(command)));
  render(<WorkspaceUndo {...io} />);
  expect(screen.getByText(/Original files restored/)).toBeInTheDocument();
  expect(io.onUndone).toHaveBeenCalledTimes(1);
});
it('keeps an uncertain original on an absent or mismatched receipt without resending', async () => {
  const io = props({
    apply: vi.fn().mockRejectedValue(new Error('response lost')),
  });
  render(<WorkspaceUndo {...io} />);
  await reviewed();
  fireEvent.click(screen.getByRole('button', { name: 'Undo these changes' }));
  await screen.findByText(/outcome is unconfirmed/);
  fireEvent.click(screen.getByRole('button', { name: 'Check Undo receipt' }));
  await screen.findByText(/No confirmed receipt/);
  vi.mocked(io.receipt).mockResolvedValue(result('another-command'));
  fireEvent.click(screen.getByRole('button', { name: 'Check Undo receipt' }));
  await waitFor(() => expect(io.receipt).toHaveBeenCalledTimes(2));
  expect(io.apply).toHaveBeenCalledTimes(1);
  expect(io.recover).not.toHaveBeenCalled();
  expect(
    screen.getByRole('button', { name: 'Retry original Undo' }),
  ).toBeEnabled();
});
it('disposal purges private review and ignores late settlement; another auth scope cannot reuse it', async () => {
  let settle!: (value: WorkspaceUndoResult) => void;
  const session = new WorkspaceUndoSession('auth:chat:binding');
  const io = props({
    session,
    apply: vi.fn(
      () =>
        new Promise<WorkspaceUndoResult>((resolve) => {
          settle = resolve;
        }),
    ),
  });
  const view = render(<WorkspaceUndo {...io} />);
  await reviewed();
  fireEvent.click(screen.getByRole('button', { name: 'Undo these changes' }));
  const command = vi.mocked(io.apply).mock.calls[0][1];
  act(() => session.dispose());
  await act(async () => settle(result(command)));
  view.rerender(<WorkspaceUndo {...io} scope="other-auth:chat:binding" />);
  expect(screen.queryByText('<script>text.txt')).toBeNull();
  expect(session.hasRetained()).toBe(false);
  expect(io.onUndone).not.toHaveBeenCalled();
  expect(session.getSnapshot().pending).toBeNull();
});
it('blocks confirmation when policy denies and fences a cancelled late review', async () => {
  let resolve!: (value: WorkspaceUndoReview) => void;
  const session = new WorkspaceUndoSession('auth:chat:binding');
  const io = props({
    session,
    review: vi.fn().mockResolvedValue({ ...review, policy_decision: 'block' }),
  });
  render(<WorkspaceUndo {...io} />);
  await reviewed();
  expect(
    screen.getByRole('button', { name: 'Undo these changes' }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel review' }));
  vi.mocked(io.review).mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Review Undo' }));
  act(() => session.cancelReview());
  await act(async () => resolve(review));
  expect(
    screen.queryByRole('button', { name: 'Undo these changes' }),
  ).toBeNull();
  expect(io.apply).not.toHaveBeenCalled();
});
