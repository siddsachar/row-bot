import { useEffect, useState } from 'react';
import { MessageCircleQuestion, ShieldAlert } from 'lucide-react';
import type { ApprovalView, PendingApproval } from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Button } from '../../ui/primitives';
import { openApprovalSheet, WaitingSince } from '../shell/InPlaceApproval';
import { readPendingApprovalsNow } from '../shell/pending-approvals';
import { resolveApproval } from '../shell/resolve-approval';
import { approvalQuestion } from '../shell/tool-activity';

/** The action in the card's own words: "Commit changes?", "Turn on Web Search?". */
function actionWords(view: ApprovalView): string {
  if (view.setup?.kind === 'tool') return `Turn on ${view.setup.label}?`;
  if (view.setting_change) return `Change ${view.setting_change.label}?`;
  return (
    view.question ||
    approvalQuestion(view.action_label ?? '', view.app ?? undefined)
  );
}

/**
 * Whether Deny and Approve may sit on Home's card. Anything that needs more
 * than yes or no (a question, a folder to pick, a post to read in full) or
 * costs money opens its own card in the sheet instead.
 */
function answersInline(view: ApprovalView): boolean {
  return (
    !view.questions?.length &&
    view.setup?.kind !== 'folder' &&
    view.setup?.kind !== 'clone' &&
    !view.x_post &&
    !view.price
  );
}

/**
 * A waiting approval on the phone's Home (D4): the action, the chat it is in
 * and why, with Deny and Approve, answered exactly as its card answers them
 * (the nonce of the view shown, bound to its revision). A tap on the card
 * opens that card in a sheet with every other choice; Always allow and Allow
 * in this chat are never on Home.
 */
export default function PhoneApprovalCard({
  item,
  onChanged,
}: {
  item: PendingApproval;
  /** After it was answered: Needs you reads again. */
  onChanged: () => void;
}) {
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const [view, setView] = useState<ApprovalView | null>(null);
  const [busy, setBusy] = useState(false);
  const [answered, setAnswered] = useState('');
  const [error, setError] = useState('');
  const question = item.kind === 'question';
  useEffect(() => {
    if (question) return;
    const abort = new AbortController();
    controller.approval(item.id, abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) setView(value);
      },
      () => undefined,
    );
    return () => abort.abort();
  }, [controller, item.id, question]);
  const action = question
    ? 'Row-Bot has a question'
    : view
      ? actionWords(view)
      : 'Approval needed';
  const inline = Boolean(view && answersInline(view));
  const reason = view?.reason?.trim() || item.what;

  async function decide(decision: 'approve' | 'reject') {
    if (!view || busy || answered) return;
    setBusy(true);
    setError('');
    try {
      await resolveApproval(controller, item.id, view, { decision });
      setAnswered(decision === 'approve' ? 'Approved.' : 'Denied.');
      readPendingApprovalsNow(controller.pendingApprovals);
      onChanged();
    } catch (cause) {
      setError(clientError(cause).message);
      setBusy(false);
    }
  }

  return (
    <li
      className="phone-approval"
      data-kind={question ? 'question' : 'approval'}
    >
      <button
        type="button"
        className="phone-approval-open"
        aria-label={`Open ${action}, ${item.title}`}
        onClick={(event) =>
          openApprovalSheet(overlay, {
            approvalId: item.id,
            subject: item.title,
            kind: item.kind,
            onResolved: onChanged,
            returnFocusTo: event.currentTarget,
          })
        }
      >
        <span className="overview-tile" data-tone="warning" aria-hidden>
          {question ? (
            <MessageCircleQuestion size={16} />
          ) : (
            <ShieldAlert size={16} />
          )}
        </span>
        <span className="phone-approval-text">
          <strong className="phone-approval-action">{action}</strong>
          <span className="phone-approval-where">
            {item.title}
            {item.requested_at && (
              <>
                {' · '}
                <WaitingSince value={item.requested_at} />
              </>
            )}
          </span>
          {reason && <span className="phone-approval-reason">{reason}</span>}
        </span>
      </button>
      {answered ? (
        <p role="status" className="phone-approval-status">
          {answered}
        </p>
      ) : (
        inline && (
          <div className="phone-approval-decide">
            <Button
              aria-label={`Deny: ${action}`}
              disabled={busy}
              onClick={() => void decide('reject')}
            >
              Deny
            </Button>
            <Button
              variant="primary"
              aria-label={`Approve: ${action}`}
              disabled={busy}
              onClick={() => void decide('approve')}
            >
              Approve
            </Button>
          </div>
        )
      )}
      {error && (
        <p role="alert" className="phone-approval-status">
          {error}
        </p>
      )}
    </li>
  );
}
