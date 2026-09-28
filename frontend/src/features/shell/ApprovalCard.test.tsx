import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import ApprovalCard from './ApprovalCard';

const approval = vi.fn();
const intent = vi.fn();
const open = vi.fn();
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: { approval, intent } }),
}));
vi.mock('../../ui/overlays', () => ({
  useOverlay: () => ({ open, notify: vi.fn(), close: vi.fn() }),
}));

const view = {
  id: 'approval-a',
  status: 'pending',
  revision: '3',
  nonce: 'nonce-a',
  action_label: 'workspace_file_delete',
  reason: 'Delete a file in the workspace.',
  risk_class: 'unknown',
  scope: '',
  safe_argument_summary: '{"file_path": "notes.txt"}',
  policy_revision: '6964',
};

beforeEach(() => {
  approval.mockReset();
  intent.mockReset();
  open.mockReset();
  approval.mockResolvedValue(view);
  intent.mockResolvedValue({ status: 'completed' });
});

async function renderCard() {
  render(<ApprovalCard id="approval-a" />);
  await screen.findByRole('button', { name: 'Approve' });
}

it('ignores Ctrl+Enter typed in a text field (B134)', async () => {
  await renderCard();
  const composer = document.createElement('textarea');
  document.body.append(composer);
  composer.focus();
  await act(async () => {
    fireEvent.keyDown(composer, { key: 'Enter', ctrlKey: true });
  });
  expect(intent).not.toHaveBeenCalled();
  const editable = document.createElement('div');
  editable.setAttribute('contenteditable', '');
  document.body.append(editable);
  await act(async () => {
    fireEvent.keyDown(editable, { key: 'Enter', metaKey: true });
  });
  expect(intent).not.toHaveBeenCalled();
  composer.remove();
  editable.remove();
});

it('approves with Ctrl+Enter when no text field has focus', async () => {
  await renderCard();
  await act(async () => {
    fireEvent.keyDown(document.body, { key: 'Enter', ctrlKey: true });
  });
  expect(intent).toHaveBeenCalledWith(
    'approval-a',
    'approval.resolve',
    { decision: 'approve', nonce: 'nonce-a' },
    '3',
  );
  expect(await screen.findByText('Approval submitted.')).toBeVisible();
});

it('explains the action in plain words, without request ids or unrated risk', async () => {
  await renderCard();
  expect(screen.queryByText(/Not classified|policy revision/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Details' }));
  const details = open.mock.calls[0][0];
  expect(details.description).toBe(
    'What Row-Bot wants to do, and what it affects.',
  );
  expect(details.title).toBe('Delete a file?');
  render(details.content);
  expect(screen.getByText('Only this action.')).toBeVisible();
  expect(screen.getByText('Delete a file')).toBeVisible();
  expect(screen.getByText('File path: notes.txt')).toBeVisible();
  expect(document.body).not.toHaveTextContent(/workspace_file_delete|\{"/);
  expect(screen.queryByText(/Risk|Request approval-a|policy revision/)).toBe(
    null,
  );
  expect(screen.queryByText(/No server expiry/)).toBeNull();
});
