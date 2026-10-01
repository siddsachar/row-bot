import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUpRight, Check, X } from 'lucide-react';
import type { PendingApproval } from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Hint, IconButton, InlineEmpty } from '../../ui/primitives';
import { absoluteTime, parseTimestamp } from '../../ui/format';
import { unannounced, usePendingApprovals } from './pending-approvals';

/** A request that is no longer waiting: someone answered it elsewhere. */
const ANSWERED = new Set([
  'approval_expired',
  'approval_already_resolved',
  'not_found',
]);

const SOURCE_WORDS: Record<PendingApproval['source'], string> = {
  workflow: 'Workflow',
  conversation: 'Conversation',
  agent: 'Agent',
};

/** "Waiting since 9:00 AM", "… since yesterday, 9:00 AM", or with the date. */
export function waitingSince(value: string, now = new Date()): string {
  const date = parseTimestamp(value);
  if (!date) return '';
  const time = new Intl.DateTimeFormat(undefined, {
    timeStyle: 'short',
  }).format(date);
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === now.toDateString())
    return `Waiting since ${time}`;
  if (date.toDateString() === yesterday.toDateString())
    return `Waiting since yesterday, ${time}`;
  return `Waiting since ${absoluteTime(date)}`;
}

export function WaitingSince({ value }: { value: string | null | undefined }) {
  const words = value ? waitingSince(value) : '';
  if (!words) return null;
  return (
    <time dateTime={value!} title={absoluteTime(value)}>
      {words}
    </time>
  );
}

/**
 * Approve / Deny for one pending approval, in place (B255; Phase 18 puts it
 * on Overview). It sends the same reviewed command as the approval card: a
 * fresh view binds this session to the exact stored action, then
 * `approval.resolve` answers it.
 */
export function ApprovalDecision({
  approvalId,
  subject,
  onResolved,
}: {
  approvalId: string;
  /** What is being approved, for the buttons' group name. */
  subject: string;
  /** After it was answered (here or, as it turned out, elsewhere). */
  onResolved?: () => void;
}) {
  const { controller } = useRuntime();
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState('');
  const [error, setError] = useState('');
  async function decide(decision: 'approve' | 'reject') {
    if (busy || done) return;
    setBusy(true);
    setError('');
    try {
      const view = await controller.approval(approvalId);
      await controller.intent(
        approvalId,
        'approval.resolve',
        { decision, nonce: view.nonce },
        view.revision,
      );
      setDone(decision === 'approve' ? 'Approved' : 'Denied');
      onResolved?.();
    } catch (cause) {
      const failure = clientError(cause);
      if (ANSWERED.has(failure.code)) {
        setDone('Already answered');
        onResolved?.();
      } else setError(failure.message);
    } finally {
      setBusy(false);
    }
  }
  if (done)
    return (
      <p role="status" className="approval-decision-done">
        {done}
      </p>
    );
  return (
    <div
      className="approval-decision"
      role="group"
      aria-label={`Answer: ${subject}`}
    >
      <IconButton
        label="Deny"
        size="sm"
        disabled={busy}
        onClick={() => void decide('reject')}
      >
        <X size={16} aria-hidden />
      </IconButton>
      <IconButton
        label="Approve"
        size="sm"
        variant="primary"
        disabled={busy}
        onClick={() => void decide('approve')}
      >
        <Check size={16} aria-hidden />
      </IconButton>
      {error && (
        <p role="alert" className="approval-decision-error">
          {error}
        </p>
      )}
    </div>
  );
}

/**
 * Every waiting approval with Approve / Deny in place, what it is, since
 * when it waits, and a link to where it was asked.
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
    <ul className="pending-approvals" aria-label="Waiting for your approval">
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
            aria-label={`${item.title} needs your approval`}
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
export function useApprovalNotices(openConversationId: string | null): void {
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const latest = useRef({ overlay, openConversationId });
  useEffect(() => {
    latest.current = { overlay, openConversationId };
  });
  const { page } = usePendingApprovals(controller.pendingApprovals);
  useEffect(() => {
    if (!page) return;
    const { overlay, openConversationId } = latest.current;
    const fresh = unannounced(page.items).filter(
      (item) =>
        !openConversationId || item.conversation_id !== openConversationId,
    );
    if (!fresh.length) return;
    overlay.notify(
      fresh.length === 1
        ? `‘${fresh[0].title}’ needs your approval`
        : `${fresh.length} approvals are waiting for you`,
      'warning',
      {
        label: 'Review',
        onAction: () =>
          latest.current.overlay.open({
            title: 'Waiting for your approval',
            description: 'Approve or deny here, or open where it was asked.',
            content: <PendingApprovalsReview />,
          }),
      },
    );
  }, [page]);
}
