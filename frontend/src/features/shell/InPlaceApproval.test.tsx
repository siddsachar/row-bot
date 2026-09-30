import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { PendingApproval, PendingApprovalPage } from '../../api/types';
import {
  ApprovalDecision,
  PendingApprovalList,
  useApprovalNotices,
  waitingSince,
} from './InPlaceApproval';

const approval = vi.fn();
const intent = vi.fn();
const pendingApprovals = vi.fn();
const notify = vi.fn();
const open = vi.fn();
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: { approval, intent, pendingApprovals } }),
}));
vi.mock('../../ui/overlays', () => ({
  useOverlay: () => ({ open, notify, close: vi.fn() }),
}));

function item(patch: Partial<PendingApproval>): PendingApproval {
  return {
    id: 'approval-news',
    source: 'workflow',
    title: 'Daily News',
    what: 'Send today’s news?',
    requested_at: '2026-09-30T09:00:00',
    expires_at: null,
    conversation_id: null,
    task_id: 'task-news',
    ...patch,
  };
}

function page(...items: PendingApproval[]): PendingApprovalPage {
  return { schema_version: 1, items, total: items.length };
}

beforeEach(() => {
  approval.mockResolvedValue({
    id: 'approval-news',
    revision: '0',
    nonce: 'nonce-news',
  });
  intent.mockResolvedValue({ status: 'completed' });
});

afterEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

it('answers in place with the same reviewed command as the approval card', async () => {
  const resolved = vi.fn();
  render(
    <ApprovalDecision
      approvalId="approval-news"
      subject="Daily News"
      onResolved={resolved}
    />,
  );
  const answer = screen.getByRole('group', { name: 'Answer: Daily News' });
  await act(async () =>
    fireEvent.click(within(answer).getByRole('button', { name: 'Approve' })),
  );
  expect(approval).toHaveBeenCalledWith('approval-news');
  expect(intent).toHaveBeenCalledWith(
    'approval-news',
    'approval.resolve',
    { decision: 'approve', nonce: 'nonce-news' },
    '0',
  );
  expect(screen.getByRole('status')).toHaveTextContent('Approved');
  expect(resolved).toHaveBeenCalledOnce();
});

it('denies in place, and an approval answered elsewhere just leaves the list', async () => {
  const resolved = vi.fn();
  const { unmount } = render(
    <ApprovalDecision approvalId="approval-news" subject="Daily News" />,
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Deny' })),
  );
  expect(intent).toHaveBeenCalledWith(
    'approval-news',
    'approval.resolve',
    { decision: 'reject', nonce: 'nonce-news' },
    '0',
  );
  expect(screen.getByRole('status')).toHaveTextContent('Denied');
  unmount();

  approval.mockRejectedValueOnce({ code: 'approval_already_resolved' });
  render(
    <ApprovalDecision
      approvalId="approval-news"
      subject="Daily News"
      onResolved={resolved}
    />,
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Approve' })),
  );
  expect(screen.getByRole('status')).toHaveTextContent('Already answered');
  expect(resolved).toHaveBeenCalledOnce();
  expect(intent).toHaveBeenCalledOnce();
});

it('lists each approval with what it is, since when it waits, and where it was asked', () => {
  render(
    <MemoryRouter>
      <PendingApprovalList
        items={[
          item({}),
          item({
            id: 'approval-trip',
            source: 'conversation',
            title: 'Trip planning',
            what: 'Row-Bot wants to run a command.',
            conversation_id: 'conversation-trip',
            task_id: null,
          }),
        ]}
      />
    </MemoryRouter>,
  );
  const news = screen.getByRole('listitem', {
    name: 'Daily News needs your approval',
  });
  expect(news).toHaveTextContent('Send today’s news?');
  expect(news).toHaveTextContent(/Workflow · Waiting since/);
  expect(
    within(news).getByRole('link', { name: 'Open the workflow' }),
  ).toHaveAttribute('href', '/?tab=workflows&workflow=task-news');
  const trip = screen.getByRole('listitem', {
    name: 'Trip planning needs your approval',
  });
  expect(
    within(trip).getByRole('link', { name: 'Open the conversation' }),
  ).toHaveAttribute('href', '/conversations/conversation-trip');
  expect(within(trip).getByRole('button', { name: 'Approve' })).toBeEnabled();
});

it('says since when an approval waits in plain words', () => {
  const now = new Date('2026-09-30T15:00:00');
  // Times follow the person's locale ("9:00 AM" or "09:00").
  expect(waitingSince('2026-09-30T09:00:00', now)).toMatch(
    /^Waiting since 0?9:00(\s?AM)?$/,
  );
  expect(waitingSince('2026-09-29T09:00:00', now)).toMatch(
    /^Waiting since yesterday, 0?9:00(\s?AM)?$/,
  );
  expect(waitingSince('2026-09-20T09:00:00', now)).toMatch(
    /^Waiting since .*20.*2026.*0?9:00/,
  );
});

function Notices({ openConversation }: { openConversation: string | null }) {
  useApprovalNotices(openConversation);
  return null;
}

it('announces a new approval once on this device, with Review opening the list', async () => {
  pendingApprovals.mockResolvedValue(page(item({ id: 'approval-once' })));
  const { unmount } = render(<Notices openConversation={null} />);
  await act(async () => undefined);
  expect(notify).toHaveBeenCalledOnce();
  const [text, tone, action] = notify.mock.calls[0];
  expect([text, tone, action.label]).toEqual([
    '‘Daily News’ needs your approval',
    'warning',
    'Review',
  ]);
  action.onAction();
  expect(open.mock.calls[0][0].title).toBe('Waiting for your approval');
  unmount();

  // Opening the app again doesn't repeat it; a new approval is announced.
  pendingApprovals.mockResolvedValue(
    page(
      item({ id: 'approval-once' }),
      item({ id: 'approval-next', title: 'Weekly report' }),
    ),
  );
  render(<Notices openConversation={null} />);
  await act(async () => undefined);
  expect(notify).toHaveBeenCalledTimes(2);
  expect(notify.mock.calls[1][0]).toBe('‘Weekly report’ needs your approval');
});

it('does not announce an approval in the conversation on screen', async () => {
  pendingApprovals.mockResolvedValue(
    page(
      item({
        id: 'approval-here',
        source: 'conversation',
        conversation_id: 'conversation-open',
        task_id: null,
      }),
    ),
  );
  render(<Notices openConversation="conversation-open" />);
  await act(async () => undefined);
  expect(pendingApprovals).toHaveBeenCalled();
  expect(notify).not.toHaveBeenCalled();
});
