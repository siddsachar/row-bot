import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import WorkspaceImports, {
  WorkspaceImportsSession,
  type WorkspaceImportsProps,
  type WorkspaceImportPage,
  type WorkspaceImportReview,
  type WorkspaceImportResult,
} from './WorkspaceImports';

const row = {
  pending_change_id: 'pending-one',
  revision: 'a'.repeat(64),
  file_count: 1,
  imported: false,
  created_at: '2026-01-01T12:00:00Z',
};
const page: WorkspaceImportPage = {
  items: [row],
  snapshot_revision: 'b'.repeat(64),
  next_cursor: null,
  total: 1,
};
const reviewed: WorkspaceImportReview = {
  resource_id: 'workspace-one',
  conversation_id: 'chat-one',
  resource_revision: 'resource-revision',
  binding_id: 'binding-one',
  binding_revision: 'binding-revision',
  pending_change_id: row.pending_change_id,
  pending_revision: row.revision,
  patch_digest: 'c'.repeat(64),
  host_revision: 'd'.repeat(64),
  git_policy_revision: 'e'.repeat(64),
  policy_revision: 'f'.repeat(64),
  policy_decision: 'ask',
  approval_required: true,
  files: ['file.txt'],
  action_digest: '1'.repeat(64),
  nonce: 'original-review-nonce',
};
function result(
  commandId: string,
  override: Partial<WorkspaceImportResult> = {},
): WorkspaceImportResult {
  return {
    command_id: commandId,
    resource_id: reviewed.resource_id,
    conversation_id: reviewed.conversation_id,
    pending_change_id: row.pending_change_id,
    status: 'imported',
    files_applied: ['file.txt'],
    change_set_id: 'change-set-one',
    ledger_saved: true,
    imported: true,
    code: '',
    ...override,
  };
}
function props(
  overrides: Partial<WorkspaceImportsProps> = {},
): WorkspaceImportsProps {
  return {
    scope: 'auth:chat-one:workspace-one:binding-one',
    load: vi.fn().mockResolvedValue(page),
    patch: vi.fn<WorkspaceImportsProps['patch']>(async (item) => ({
      pending_change_id: item.pending_change_id,
      revision: item.revision,
      text: '<script>untrusted patch text</script>',
      next_offset: null,
    })),
    review: vi.fn().mockResolvedValue(reviewed),
    apply: vi.fn<WorkspaceImportsProps['apply']>(async (_review, id) =>
      result(id),
    ),
    recover: vi.fn<WorkspaceImportsProps['recover']>(async (_review, id) =>
      result(id),
    ),
    receipt: vi.fn().mockResolvedValue(null),
    onImported: vi.fn(),
    ...overrides,
  };
}
async function selectPatch() {
  fireEvent.click(
    await screen.findByRole('button', { name: /Pending · 1 file/ }),
  );
  await screen.findByLabelText('Saved patch');
}
it('opens only saved metadata and renders plain patch text without importing', async () => {
  const p = props();
  render(<WorkspaceImports {...p} />);
  await screen.findByText('1 saved change');
  expect(p.review).not.toHaveBeenCalled();
  expect(p.apply).not.toHaveBeenCalled();
  await selectPatch();
  expect(screen.getByLabelText('Saved patch')).toHaveValue(
    '<script>untrusted patch text</script>',
  );
  expect(document.querySelector('section script')).toBeNull();
  expect(p.apply).not.toHaveBeenCalled();
});
it('retains exact reviewed folder creation with the original one-click import', async () => {
  const intent = { ...reviewed, directories: ['new', 'new/nested'] };
  const p = props({ review: vi.fn().mockResolvedValue(intent) });
  render(<WorkspaceImports {...p} />);
  await selectPatch();
  fireEvent.click(
    screen.getByRole('button', { name: 'Import selected changes' }),
  );
  await waitFor(() => expect(p.apply).toHaveBeenCalledTimes(1));
  expect(vi.mocked(p.apply).mock.calls[0][0]).toEqual(intent);
  expect(vi.mocked(p.apply).mock.calls[0][0]).not.toBe(intent);
});
it('submits only once and retains the exact review, command and late result across remount', async () => {
  let finish!: (value: WorkspaceImportResult) => void;
  const p = props({
    apply: vi.fn(
      () =>
        new Promise<WorkspaceImportResult>((resolve) => {
          finish = resolve;
        }),
    ),
  });
  const session = new WorkspaceImportsSession(p.scope);
  const view = render(<WorkspaceImports {...p} session={session} />);
  await selectPatch();
  const importButton = screen.getByRole('button', {
    name: 'Import selected changes',
  });
  fireEvent.click(importButton);
  fireEvent.click(importButton);
  await waitFor(() => expect(p.apply).toHaveBeenCalledTimes(1));
  expect(session.hasRetained()).toBe(true);
  const [original, command] = vi.mocked(p.apply).mock.calls[0];
  expect(original).toEqual(reviewed);
  expect(original).not.toBe(reviewed);
  view.unmount();
  await act(async () => finish(result(command)));
  render(<WorkspaceImports {...p} session={session} />);
  await screen.findByText(/Sandbox changes imported/);
  expect(p.load).toHaveBeenCalledTimes(1);
  expect(p.onImported).toHaveBeenCalledTimes(1);
  expect(session.hasRetained()).toBe(false);
});
it('retains uncertainty and explicitly recovers only the original reviewed command', async () => {
  const p = props({
    apply: vi.fn().mockRejectedValue({ code: 'operation_uncertain' }),
  });
  const session = new WorkspaceImportsSession(p.scope);
  const view = render(<WorkspaceImports {...p} session={session} />);
  await selectPatch();
  fireEvent.click(
    screen.getByRole('button', { name: 'Import selected changes' }),
  );
  await screen.findByText(/outcome is unconfirmed/);
  const original = vi.mocked(p.apply).mock.calls[0];
  view.unmount();
  render(<WorkspaceImports {...p} session={session} />);
  expect(
    screen.getByRole('button', { name: 'Reload sandbox changes' }),
  ).toBeDisabled();
  expect(
    screen.queryByRole('button', { name: 'Cancel review' }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Check import receipt' }));
  await screen.findByText(/original receipt is still unconfirmed/);
  expect(session.hasRetained()).toBe(true);
  expect(p.apply).toHaveBeenCalledTimes(1);
  fireEvent.click(
    screen.getByRole('button', { name: 'Recover original import' }),
  );
  await screen.findByText(/Sandbox changes imported/);
  expect(p.recover).toHaveBeenCalledExactlyOnceWith(...original);
  expect(p.review).toHaveBeenCalledTimes(1);
});
it('keeps partial publication and missing history retained instead of claiming completion', async () => {
  const p = props({
    apply: vi.fn<WorkspaceImportsProps['apply']>(async (_r, id) =>
      result(id, { status: 'partial', ledger_saved: false, imported: true }),
    ),
  });
  const session = new WorkspaceImportsSession(p.scope);
  render(<WorkspaceImports {...p} session={session} />);
  await selectPatch();
  fireEvent.click(
    screen.getByRole('button', { name: 'Import selected changes' }),
  );
  await screen.findByText(/Some import stages remain unconfirmed/);
  expect(
    screen.getByText(/Change history: unconfirmed. Import marker: saved/),
  ).toBeInTheDocument();
  expect(p.onImported).not.toHaveBeenCalled();
  expect(session.hasRetained()).toBe(true);
});
it('rejects a receipt for another command and masks late settlement after auth disposal', async () => {
  const p = props({
    apply: vi.fn().mockRejectedValue({ code: 'operation_uncertain' }),
    receipt: vi.fn().mockResolvedValue(result('unrelated-command')),
  });
  const session = new WorkspaceImportsSession(p.scope);
  const view = render(<WorkspaceImports {...p} session={session} />);
  await selectPatch();
  fireEvent.click(
    screen.getByRole('button', { name: 'Import selected changes' }),
  );
  await screen.findByText(/outcome is unconfirmed/);
  fireEvent.click(screen.getByRole('button', { name: 'Check import receipt' }));
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Check import receipt' }),
    ).toBeEnabled(),
  );
  expect(p.onImported).not.toHaveBeenCalled();
  expect(session.hasRetained()).toBe(true);
  let finish!: (value: WorkspaceImportResult) => void;
  p.recover = vi.fn(
    () =>
      new Promise<WorkspaceImportResult>((resolve) => {
        finish = resolve;
      }),
  );
  view.rerender(<WorkspaceImports {...p} session={session} />);
  fireEvent.click(
    screen.getByRole('button', { name: 'Recover original import' }),
  );
  const command = vi.mocked(p.apply).mock.calls[0][1];
  act(() => session.dispose());
  await act(async () => finish(result(command)));
  expect(screen.getByText('Workspace access changed')).toBeInTheDocument();
  expect(screen.queryByLabelText('Saved patch')).not.toBeInTheDocument();
  expect(p.onImported).not.toHaveBeenCalled();
});
it('refuses to expose an existing session in a different resource binding', async () => {
  const p = props();
  const session = new WorkspaceImportsSession(p.scope);
  const view = render(<WorkspaceImports {...p} session={session} />);
  await selectPatch();
  view.rerender(
    <WorkspaceImports {...p} scope="auth:other-binding" session={session} />,
  );
  expect(screen.getByText('Workspace access changed')).toBeInTheDocument();
  expect(screen.queryByLabelText('Saved patch')).not.toBeInTheDocument();
  expect(p.apply).not.toHaveBeenCalled();
});
it('bounds visible rows at 200 while every forward page remains reachable and reload resets the window', async () => {
  const p = props({
    load: vi.fn<WorkspaceImportsProps['load']>(async (cursor) => {
      const start = Number(cursor ?? 0);
      return {
        ...page,
        total: 303,
        next_cursor: start === 300 ? null : String(start + 100),
        items: Array.from(
          { length: Math.min(100, 303 - start) },
          (_, index) => ({
            ...row,
            pending_change_id: `pending-${start + index}`,
            created_at: `change ${start + index}`,
          }),
        ),
      };
    }),
  });
  render(<WorkspaceImports {...p} />);
  await screen.findByText('303 saved changes');
  for (let index = 0; index < 3; index++) {
    fireEvent.click(screen.getByRole('button', { name: 'Load more changes' }));
    await waitFor(() => expect(p.load).toHaveBeenCalledTimes(index + 2));
    await waitFor(() =>
      expect(
        screen.queryByText('Reading saved sandbox changes'),
      ).not.toBeInTheDocument(),
    );
  }
  const buttons = screen.getAllByRole('button', { name: /Pending · 1 file/ });
  expect(buttons).toHaveLength(200);
  expect(buttons[0]).toHaveTextContent('change 103');
  expect(buttons.at(-1)).toHaveTextContent('change 302');
  expect(
    screen.queryByRole('button', { name: 'Load more changes' }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/latest 200 loaded changes/)).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Reload sandbox changes' }),
  );
  await waitFor(() =>
    expect(
      screen.getAllByRole('button', { name: /Pending · 1 file/ }),
    ).toHaveLength(100),
  );
  expect(
    screen.queryByText(/latest 200 loaded changes/),
  ).not.toBeInTheDocument();
});
it('pages the patch without appending unbounded text and rejects stale page revisions', async () => {
  const p = props({
    patch: vi.fn<WorkspaceImportsProps['patch']>(async (item, offset) => ({
      pending_change_id: item.pending_change_id,
      revision: item.revision,
      text: offset ? 'second patch page' : 'first patch page',
      next_offset: offset ? null : 16384,
    })),
  });
  render(<WorkspaceImports {...p} />);
  fireEvent.click(
    await screen.findByRole('button', { name: /Pending · 1 file/ }),
  );
  await screen.findByLabelText('Saved patch');
  fireEvent.click(screen.getByRole('button', { name: 'Next patch page' }));
  await waitFor(() =>
    expect(screen.getByLabelText('Saved patch')).toHaveValue(
      'second patch page',
    ),
  );
  expect(screen.getByText(/starting at character 16385/)).toBeInTheDocument();
  vi.mocked(p.patch).mockResolvedValueOnce({
    pending_change_id: row.pending_change_id,
    revision: 'changed',
    text: 'stale private data',
    next_offset: null,
  });
  fireEvent.click(screen.getByRole('button', { name: 'First patch page' }));
  await screen.findByText(/saved revision changed/);
  expect(screen.getByLabelText('Saved patch')).toHaveValue('second patch page');
  expect(
    screen.getByRole('button', { name: 'Import selected changes' }),
  ).toBeDisabled();
});
it('does not import when policy blocks the action', async () => {
  const p = props({
    review: vi.fn().mockResolvedValue({
      ...reviewed,
      policy_decision: 'block',
      approval_required: false,
    }),
  });
  render(<WorkspaceImports {...p} />);
  await selectPatch();
  fireEvent.click(
    screen.getByRole('button', { name: 'Import selected changes' }),
  );
  expect(
    await screen.findByText(/policy blocks this import/),
  ).toBeInTheDocument();
  expect(p.apply).not.toHaveBeenCalled();
});
