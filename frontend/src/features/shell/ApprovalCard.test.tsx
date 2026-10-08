import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import ApprovalCard from './ApprovalCard';
import { usePendingApprovals } from './pending-approvals';

const approval = vi.fn();
const intent = vi.fn();
const pendingApprovals = vi.fn();
const open = vi.fn();
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: { approval, intent, pendingApprovals } }),
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
  // The buttons show at once, disabled until the approval has loaded.
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Approve' })).toBeEnabled(),
  );
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
  // The shortcut listens once the approval's details have loaded, which can
  // be a moment after the buttons enable.
  await waitFor(() => {
    fireEvent.keyDown(document.body, { key: 'Enter', ctrlKey: true });
    expect(intent).toHaveBeenCalled();
  });
  expect(intent).toHaveBeenCalledOnce();
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

it('lists structured arguments in words, never as code', async () => {
  approval.mockResolvedValue({
    ...view,
    safe_argument_summary:
      '{"orders":[{"sku":"A1","qty":2,"ship_to":{"city":"Leeds"}},{"sku":"B2","qty":1}],"rush":true}',
  });
  await renderCard();
  fireEvent.click(screen.getByRole('button', { name: 'Details' }));
  render(open.mock.calls[0][0].content);
  expect(
    screen.getByText(
      'Orders: sku A1, qty 2, ship to (city Leeds); sku B2, qty 1',
    ),
  ).toBeVisible();
  expect(screen.getByText('Rush: yes')).toBeVisible();
  expect(document.body).not.toHaveTextContent(/[{[]"/);
});

it('says since when the approval waits (B255)', async () => {
  approval.mockResolvedValue({
    ...view,
    requested_at: new Date(2026, 8, 30, 9, 0).toISOString(),
  });
  await renderCard();
  expect(screen.getByText(/^Waiting since/)).toBeVisible();
});

it('asks to turn on a tool the work needs as a setup card (decision 12)', async () => {
  approval.mockResolvedValue({
    ...view,
    action_label: 'row_bot_update_setting',
    reason: 'Row-Bot needs Web Search for this.',
    setup: { kind: 'tool', label: 'Web Search' },
  });
  render(<ApprovalCard id="approval-a" onAllowInChat={vi.fn()} />);
  const card = await screen.findByRole('complementary', {
    name: 'Turn on Web Search',
  });
  expect(card).toHaveTextContent('Turn on Web Search?');
  expect(card).toHaveTextContent('Row-Bot needs Web Search for this.');
  await screen.findByRole('button', { name: 'Turn on' });
  expect(
    screen.queryByRole('button', { name: 'Always allow in this chat' }),
  ).toBeNull();
  expect(screen.queryByRole('button', { name: 'Details' })).toBeNull();
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Turn on' }));
  });
  expect(intent).toHaveBeenCalledWith(
    'approval-a',
    'approval.resolve',
    { decision: 'approve', nonce: 'nonce-a' },
    '3',
  );
  expect(await screen.findByRole('status')).toHaveTextContent(
    'Turning on Web Search…',
  );
});

it('keeps the tool off with Not now', async () => {
  approval.mockResolvedValue({
    ...view,
    setup: { kind: 'tool', label: 'Developer tools' },
  });
  render(<ApprovalCard id="approval-a" />);
  const notNow = await screen.findByRole('button', { name: 'Not now' });
  await screen.findByText('Delete a file in the workspace.');
  await act(async () => {
    fireEvent.click(notNow);
  });
  expect(intent).toHaveBeenCalledWith(
    'approval-a',
    'approval.resolve',
    { decision: 'reject', nonce: 'nonce-a' },
    '3',
  );
  expect(await screen.findByRole('status')).toHaveTextContent(
    'Developer tools stays off.',
  );
});

it('shows which app asks, with its logo and name, and still needs the person', async () => {
  approval.mockResolvedValue({
    ...view,
    action_label: 'mcp_notion_delete_page',
    reason: 'Delete a page for good.',
    app: { item_id: 'mcp:notion', name: 'Notion', icon: 'letter:N' },
  });
  await renderCard();
  const card = screen.getByRole('complementary', {
    name: /Approval required for .* in Notion$/,
  });
  expect(card).toHaveTextContent('Notion');
  expect(intent).not.toHaveBeenCalled();
});

it('re-reads the waiting approvals at once after an answer (B302)', async () => {
  pendingApprovals.mockResolvedValue({ items: [], next_cursor: null });
  function Sidebar() {
    const { page } = usePendingApprovals(pendingApprovals);
    return <p>{page ? `${page.items.length} waiting` : 'reading'}</p>;
  }
  render(<Sidebar />);
  await waitFor(() => expect(pendingApprovals).toHaveBeenCalledTimes(1));
  await renderCard();
  fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
  expect(await screen.findByText('Approval submitted.')).toBeVisible();
  expect(pendingApprovals).toHaveBeenCalledTimes(2);
});

it('offers approving the rest of the reply only for a repeatable action (F21)', async () => {
  await renderCard();
  expect(
    screen.queryByRole('button', { name: 'Approve the rest' }),
  ).not.toBeInTheDocument();
  approval.mockResolvedValue({
    ...view,
    action_label: 'developer_commit_changes',
    repeatable: true,
  });
  render(<ApprovalCard id="approval-b" />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Approve the rest' }),
  );
  await waitFor(() =>
    expect(intent).toHaveBeenCalledWith(
      'approval-b',
      'approval.resolve',
      { decision: 'approve', nonce: 'nonce-a', scope: 'turn' },
      '3',
    ),
  );
});
