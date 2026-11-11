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

it('opens a waiting approval on its own card, never approving from the list (F31)', async () => {
  const resolved = vi.fn();
  render(
    <ApprovalDecision
      approvalId="approval-news"
      subject="Daily News"
      onResolved={resolved}
    />,
  );
  expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Review: Daily News' }));
  expect(open).toHaveBeenCalledOnce();
  const dialog = open.mock.calls[0][0];
  expect(dialog.title).toBe('Review and approve');
  expect(dialog.description).toBe('Daily News');
  // The dialog holds the approval card itself: its exact action, then Approve or Deny there.
  expect(dialog.content.props.id).toBe('approval-news');
  expect(intent).not.toHaveBeenCalled();
});

it("answers Row-Bot's question on its card (N4)", () => {
  render(
    <ApprovalDecision
      approvalId="question-news"
      subject="Daily News"
      kind="question"
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Answer: Daily News' }));
  expect(open.mock.calls[0][0].title).toBe('Row-Bot has a question');
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
  expect(
    within(trip).getByRole('button', { name: 'Review: Trip planning' }),
  ).toBeEnabled();
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

function Notices({
  openConversation,
  onHome = false,
}: {
  openConversation: string | null;
  onHome?: boolean;
}) {
  useApprovalNotices(openConversation, onHome);
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

it('does not announce an approval while Home lists it under Needs you', async () => {
  pendingApprovals.mockResolvedValue(page(item({ id: 'approval-on-home' })));
  const { unmount } = render(<Notices openConversation={null} onHome />);
  await act(async () => undefined);
  expect(pendingApprovals).toHaveBeenCalled();
  expect(notify).not.toHaveBeenCalled();
  unmount();

  // Seen there, so leaving Home doesn't announce it late.
  render(<Notices openConversation={null} />);
  await act(async () => undefined);
  expect(notify).not.toHaveBeenCalled();
});

it('announces Row-Bot’s question as a question, answered on its card (N4)', async () => {
  pendingApprovals.mockResolvedValue(
    page(
      item({ id: 'question-beta', title: 'Plan the beta', kind: 'question' }),
    ),
  );
  render(<Notices openConversation={null} />);
  await act(async () => undefined);
  expect(notify.mock.calls[0][0]).toBe('‘Plan the beta’ has a question');

  render(
    <ApprovalDecision
      approvalId="question-beta"
      subject="Plan the beta"
      kind="question"
    />,
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Answer: Plan the beta' }),
  );
  expect(open.mock.calls[0][0].title).toBe('Row-Bot has a question');
  expect(intent).not.toHaveBeenCalled();
});
