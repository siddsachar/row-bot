import { useEffect, useRef, type MouseEvent } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUpRight } from 'lucide-react';
import type { PendingApproval } from '../../api/types';
import { useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Button, Hint, InlineEmpty } from '../../ui/primitives';
import ApprovalCard from './ApprovalCard';
import { unannounced, usePendingApprovals } from './pending-approvals';
import { WaitingSince } from './waiting-since';

export { WaitingSince, waitingSince } from './waiting-since';

const SOURCE_WORDS: Record<PendingApproval['source'], string> = {
  workflow: 'Workflow',
  conversation: 'Conversation',
  agent: 'Agent',
};

/** "‘Daily News’ needs your approval", or "has a question" when Row-Bot asks one (N4). */
export function waitingWords(item: Pick<PendingApproval, 'title' | 'kind'>) {
  return item.kind === 'question'
    ? `${item.title} has a question`
    : `${item.title} needs your approval`;
}

/**
 * Where a waiting approval or question is listed, it opens on its own card in a
 * dialog: an action's card shows exactly what will happen (account or recipient,
 * full text, files) with Approve and Deny; Row-Bot's question has Answer and Skip
 * (N4). Nothing is approved from a one-line summary (F31).
 */
export function ApprovalDecision({
  approvalId,
  subject,
  onResolved,
  kind = 'approval',
}: {
  approvalId: string;
  /** What waits, for the button's name. */
  subject: string;
  /** After it was answered (here or, as it turned out, elsewhere). */
  onResolved?: () => void;
  kind?: PendingApproval['kind'];
}) {
  const overlay = useOverlay();
  const question = kind === 'question';
  return (
    <Button
      variant="primary"
      className="small"
      aria-label={`${question ? 'Answer' : 'Review'}: ${subject}`}
      onClick={(event) =>
        overlay.open({
          title: question ? 'Row-Bot has a question' : 'Review and approve',
          description: subject,
          returnFocusTo: event.currentTarget,
          content: (
            <ApprovalCard
              id={approvalId}
              notice
              onResolved={() => {
                onResolved?.();
                overlay.close();
              }}
            />
          ),
        })
      }
    >
      {question ? 'Answer' : 'Review'}
    </Button>
  );
}

/**
 * Every waiting approval with Review (its card), what it is, since when it
 * waits, and a link to where it was asked. Row-Bot's questions have Answer
 * instead (N4).
 */
export function PendingApprovalList({
  items,
  onChanged,
  onNavigate,
}: {
  items: readonly PendingApproval[];
  /** After a decision: the list re-reads. */
  onChanged?: () => void;
  /** Opening where one was asked (a popover, drawer or dialog closes). */
  onNavigate?: (to: string) => (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  if (!items.length)
    return <InlineEmpty>Nothing is waiting for your approval.</InlineEmpty>;
  return (
    <ul
      className="pending-approvals"
      aria-label={
        items.some((item) => item.kind === 'question')
          ? 'Waiting for you'
          : 'Waiting for your approval'
      }
    >
      {items.map((item) => {
        const to = item.task_id
          ? `/?tab=workflows&workflow=${encodeURIComponent(item.task_id)}`
          : item.conversation_id
            ? `/conversations/${encodeURIComponent(item.conversation_id)}`
            : '';
        return (
          <li
            key={item.id}
            className="pending-approval"
            aria-label={waitingWords(item)}
          >
            <div className="pending-approval-text">
              <strong>{item.title}</strong>
              {item.what && (
                <span className="pending-approval-what">{item.what}</span>
              )}
              <small>
                {SOURCE_WORDS[item.source]} ·{' '}
                <WaitingSince value={item.requested_at} />
              </small>
            </div>
            <div className="pending-approval-actions">
              {to && (
                <Hint
                  label={
                    item.task_id ? 'Open the workflow' : 'Open the conversation'
                  }
                >
                  <Link
                    className="button ghost icon-button icon-action icon-action-sm"
                    to={to}
                    aria-label={
                      item.task_id
                        ? 'Open the workflow'
                        : 'Open the conversation'
                    }
                    onClick={onNavigate?.(to)}
                  >
                    <ArrowUpRight size={16} aria-hidden />
                  </Link>
                </Hint>
              )}
              <ApprovalDecision
                approvalId={item.id}
                subject={item.title}
                kind={item.kind}
                onResolved={onChanged}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** The list as a dialog's content, following the shared read. */
function PendingApprovalsReview() {
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const { page, refresh } = usePendingApprovals(controller.pendingApprovals);
  return (
    <PendingApprovalList
      items={page?.items ?? []}
      onChanged={refresh}
      onNavigate={() => () => overlay.close()}
    />
  );
}

/**
 * A floating notice when a new approval arrives, once per approval on this
 * device: "‘Daily News’ needs your approval · Review" opens the list. An
 * approval in the conversation on screen is not announced: its card is there.
 */
/**
 * Announce new approvals once on this device. Home's overview lists them in
 * "Needs you" already, so there they count as seen without a notice.
 */
export function useApprovalNotices(
  openConversationId: string | null,
  homeListsThem = false,
): void {
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const latest = useRef({ overlay, openConversationId, homeListsThem });
  useEffect(() => {
    latest.current = { overlay, openConversationId, homeListsThem };
  });
  const { page } = usePendingApprovals(controller.pendingApprovals);
  useEffect(() => {
    if (!page) return;
    const { overlay, openConversationId, homeListsThem } = latest.current;
    const fresh = unannounced(page.items).filter(
      (item) =>
        !openConversationId || item.conversation_id !== openConversationId,
    );
    if (!fresh.length || homeListsThem) return;
    overlay.notify(
      fresh.length === 1
        ? waitingWords({ ...fresh[0], title: `‘${fresh[0].title}’` })
        : fresh.every((item) => item.kind === 'question')
          ? `${fresh.length} questions are waiting for you`
          : `${fresh.length} approvals are waiting for you`,
      'warning',
      {
        label: 'Review',
        onAction: () =>
          latest.current.overlay.open({
            title: 'Waiting for your approval',
            description: 'Review each on its card, or open where it was asked.',
            content: <PendingApprovalsReview />,
          }),
      },
    );
  }, [page]);
}
