import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ApprovalView, PendingApproval } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import PhoneApprovalCard from './PhoneApprovalCard';

// Approving from the phone's Home (D4) is security-sensitive: the card answers
// exactly as the approval's own card does (the nonce of the view shown, bound
// to its revision, through approval.resolve), and nothing else is on it.

const runtime = vi.hoisted(() => ({
  approval: vi.fn(),
  intent: vi.fn(),
  pendingApprovals: vi.fn(),
}));
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: runtime, platform: {} }),
}));

function item(patch: Partial<PendingApproval> = {}): PendingApproval {
  return {
    id: 'approval-commit',
    source: 'conversation',
    kind: 'approval',
    title: 'Release notes cleanup',
    what: 'Commit 3 changed files',
    requested_at: new Date().toISOString(),
    expires_at: null,
    conversation_id: 'chat-release',
    task_id: null,
    ...patch,
  };
}

function view(patch: Partial<ApprovalView> = {}): ApprovalView {
  return {
    id: 'approval-commit',
    status: 'pending',
    revision: 'rev-7',
    nonce: 'nonce-7',
    policy_revision: 'policy-1',
    action_label: 'developer_commit_changes',
    reason: 'Commit 3 changed files: Tidy the release notes',
    repeatable: true,
    rule_eligible: true,
    ...patch,
  };
}

function show(patch: Partial<PendingApproval> = {}) {
  const onChanged = vi.fn();
  render(
    <MemoryRouter>
      <OverlayProvider>
        <ul>
          <PhoneApprovalCard item={item(patch)} onChanged={onChanged} />
        </ul>
      </OverlayProvider>
    </MemoryRouter>,
  );
  return { onChanged };
}

beforeEach(() => {
  runtime.approval.mockResolvedValue(view());
  runtime.intent.mockResolvedValue({ status: 'completed' });
});
afterEach(() => vi.clearAllMocks());

it('shows the action, the chat it is in and why, with Deny and Approve only', async () => {
  show();
  const approve = await screen.findByRole('button', {
    name: 'Approve: Commit changes?',
  });
  const card = approve.closest('li')!;
  expect(within(card).getByText('Commit changes?')).toBeVisible();
  expect(within(card).getByText(/^Release notes cleanup/)).toBeVisible();
  expect(
    within(card).getByText('Commit 3 changed files: Tidy the release notes'),
  ).toBeVisible();
  expect(
    within(card)
      .getAllByRole('button')
      .map((button) => button.textContent || button.getAttribute('aria-label')),
  ).toEqual([expect.stringContaining('Commit changes?'), 'Deny', 'Approve']);
  // Always allow, Allow in this chat and Auto are never on Home.
  expect(
    within(card).queryByText(/Always allow|Allow in this chat|Auto/),
  ).toBeNull();
  expect(runtime.approval).toHaveBeenCalledWith(
    'approval-commit',
    expect.any(AbortSignal),
  );
});

it('Approve answers through approval.resolve with the nonce and revision it showed', async () => {
  const { onChanged } = show();
  await userEvent.click(
    await screen.findByRole('button', { name: 'Approve: Commit changes?' }),
  );
  expect(runtime.intent).toHaveBeenCalledTimes(1);
  expect(runtime.intent).toHaveBeenCalledWith(
    'approval-commit',
    'approval.resolve',
    { decision: 'approve', nonce: 'nonce-7' },
    'rev-7',
  );
  expect(await screen.findByRole('status')).toHaveTextContent('Approved.');
  expect(screen.queryByRole('button', { name: /^Approve/ })).toBeNull();
  expect(onChanged).toHaveBeenCalledTimes(1);
});

it('Deny answers through approval.resolve with a rejection, once', async () => {
  const { onChanged } = show();
  const deny = await screen.findByRole('button', {
    name: 'Deny: Commit changes?',
  });
  await userEvent.click(deny);
  expect(runtime.intent).toHaveBeenCalledTimes(1);
  expect(runtime.intent).toHaveBeenCalledWith(
    'approval-commit',
    'approval.resolve',
    { decision: 'reject', nonce: 'nonce-7' },
    'rev-7',
  );
  expect(await screen.findByRole('status')).toHaveTextContent('Denied.');
  expect(onChanged).toHaveBeenCalledTimes(1);
});

it('says why when the answer is refused, and keeps both choices', async () => {
  runtime.intent.mockRejectedValue({
    code: 'approval_already_resolved',
    message: 'This approval was already answered.',
  });
  const { onChanged } = show();
  await userEvent.click(
    await screen.findByRole('button', { name: 'Approve: Commit changes?' }),
  );
  expect(await screen.findByRole('alert')).not.toBeEmptyDOMElement();
  expect(
    screen.getByRole('button', { name: 'Approve: Commit changes?' }),
  ).toBeEnabled();
  expect(onChanged).not.toHaveBeenCalled();
});

it('a tap on the card opens its own card in a sheet with the other choices', async () => {
  show();
  await screen.findByRole('button', { name: 'Approve: Commit changes?' });
  await userEvent.click(
    screen.getByRole('button', {
      name: 'Open Commit changes?, Release notes cleanup',
    }),
  );
  const sheet = await screen.findByRole('dialog', {
    name: 'Review and approve',
  });
  expect(
    await within(sheet).findByRole('complementary', {
      name: /Approval required/,
    }),
  ).toBeVisible();
  expect(runtime.intent).not.toHaveBeenCalled();
});

it.each<[string, Partial<ApprovalView>]>([
  [
    'a question',
    { questions: [{ id: 'q', question: 'Which?', options: [] }] as never },
  ],
  [
    'a folder to choose',
    { setup: { kind: 'folder', label: 'Folder' } as never },
  ],
  ['a post to read in full', { x_post: { text: 'Hello' } as never }],
  ['a cost', { price: { amount: 0.04 } as never }],
])('%s opens in the sheet: no Approve on Home', async (_case, patch) => {
  runtime.approval.mockResolvedValue(view(patch));
  show();
  await waitFor(() => expect(runtime.approval).toHaveBeenCalled());
  await screen.findByRole('button', { name: /^Open / });
  expect(screen.queryByRole('button', { name: /^Approve/ })).toBeNull();
  expect(screen.queryByRole('button', { name: /^Deny/ })).toBeNull();
});

it('Row-Bot’s question says so and is answered in the sheet', async () => {
  show({ kind: 'question', title: 'Trip plan' });
  expect(
    screen.getByRole('button', {
      name: 'Open Row-Bot has a question, Trip plan',
    }),
  ).toBeVisible();
  expect(runtime.approval).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: /^Approve/ })).toBeNull();
});
