import { useEffect, useState } from 'react';
import { Power, ShieldAlert, ShieldCheck } from 'lucide-react';
import type { ApprovalSetup, ApprovalView } from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Button, Hint, Kbd, Skeleton } from '../../ui/primitives';
import { absoluteTime, humanizeToken, relativeTime } from '../../ui/format';
import FolderSetupCard from './FolderSetupCard';
import { AppIcon } from '../apps/parts';
import { WaitingSince } from './InPlaceApproval';
import { readPendingApprovalsNow } from './pending-approvals';
import {
  approvalAction,
  approvalQuestion,
  keyArgument,
  plainApprovalReason,
  readableValue,
} from './tool-activity';

type Hint_ = {
  action_label?: string;
  reason?: string;
  risk_class?: string;
  setup?: ApprovalSetup | null;
};

/** A notice's approval that is no longer waiting. */
const ANSWERED = new Set([
  'approval_expired',
  'approval_already_resolved',
  'not_found',
]);

const RISK: Record<string, string> = {
  low: 'Low risk',
  medium: 'Medium risk',
  high: 'High risk',
  critical: 'Critical risk',
};

/** The server's scope sentences, in the card's words. */
function plainScope(scope: string | null | undefined): string {
  if (!scope || /^Only this requested action will be resolved\.?$/.test(scope))
    return 'Only this action.';
  const together =
    /^(\d+) requested actions will be resolved together\.?$/.exec(scope);
  return together ? `These ${together[1]} actions, together.` : scope;
}

/** `{"file_path":"notes.txt"}` reads "File path: notes.txt"; a list or mapping reads in words. */
function plainArguments(summary: string): string[] {
  try {
    const value: unknown = JSON.parse(summary);
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      const lines = Object.entries(value).map(
        ([key, item]) => `${humanizeToken(key)}: ${readableValue(item)}`,
      );
      if (lines.length) return lines;
    }
  } catch {
    // Not JSON: show it as it is.
  }
  return [summary];
}

function ApprovalDetails({ view }: { view: ApprovalView }) {
  const risk = RISK[view.risk_class ?? ''];
  const reason = view.reason || view.summary;
  return (
    <div className="approval-detail stack">
      <p>
        {reason ? plainApprovalReason(reason) : 'Row-Bot needs your go-ahead.'}
      </p>
      <dl>
        <dt>Action</dt>
        <dd>{approvalAction(view.action_label || '', view.app)}</dd>
        {risk && (
          <>
            <dt>Risk</dt>
            <dd>{risk}</dd>
          </>
        )}
        <dt>Affects</dt>
        <dd>{plainScope(view.scope)}</dd>
        {view.safe_argument_summary && (
          <>
            <dt>With</dt>
            <dd>
              {plainArguments(view.safe_argument_summary).map((line) => (
                <span key={line} className="approval-detail-argument">
                  {line}
                </span>
              ))}
            </dd>
          </>
        )}
        {view.expires_at && (
          <>
            <dt>Waits until</dt>
            <dd>
              <time dateTime={view.expires_at} title={view.expires_at}>
                {absoluteTime(view.expires_at) || view.expires_at} (
                {relativeTime(view.expires_at)})
              </time>
            </dd>
          </>
        )}
      </dl>
    </div>
  );
}

function dialogOpen() {
  return Boolean(
    document.querySelector('[role="dialog"], [role="alertdialog"]'),
  );
}

/** Mod+Enter in a text field belongs to that field, never to an approval (B134). */
function editableTarget(target: EventTarget | null) {
  if (!(target instanceof Element)) return false;
  if (target instanceof HTMLElement && target.isContentEditable) return true;
  return Boolean(
    target.closest(
      'input, textarea, select, [contenteditable]:not([contenteditable="false"])',
    ),
  );
}

/**
 * An inline card anchored at the step that needs a decision: what will
 * happen, how risky it is, and Approve (⌘↵) / Deny. "Always allow in this
 * chat" switches the conversation to automatic approvals, then approves;
 * an app's tools still ask when its access says to.
 *
 * Turning on a tool the work needs is a setup card instead (decision 12):
 * "Turn on Web search?" with Turn on (⌘↵) / Not now, in place of sending
 * the person to Settings. A code folder the work needs is a folder card
 * (B277), answered only once the person has picked the folder.
 */
export default function ApprovalCard({
  id,
  hint,
  conversationId,
  onAllowInChat,
  onResolved,
  notice = false,
}: {
  id: string;
  hint?: Hint_;
  /** The conversation a folder card binds the picked folder to. */
  conversationId?: string;
  onAllowInChat?: () => Promise<void>;
  /** After the decision was accepted (a delegated agent's thread re-reads). */
  onResolved?: () => void;
  /**
   * The card sits on a delegated agent's notice in its parent conversation
   * (B162): no keyboard shortcut, and one quiet line once it was answered.
   */
  notice?: boolean;
}) {
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const [view, setView] = useState<ApprovalView | null>(null);
  const [error, setError] = useState('');
  const [answered, setAnswered] = useState(false);
  const [busy, setBusy] = useState(false);
  const [resolution, setResolution] = useState('');
  useEffect(() => {
    const abort = new AbortController();
    void controller
      .approval(id, abort.signal)
      .then(setView)
      .catch((error) => {
        if (abort.signal.aborted) return;
        const failure = clientError(error);
        if (notice && ANSWERED.has(failure.code)) setAnswered(true);
        else setError(failure.message);
      });
    return () => abort.abort();
  }, [controller, id, notice]);
  async function resolve(
    decision: 'approve' | 'reject',
    allow = false,
    // 'turn' also approves later actions of this kind until the reply ends (F21).
    scope: 'once' | 'turn' = 'once',
  ) {
    if (!view || busy || resolution) return;
    setBusy(true);
    try {
      if (allow) await onAllowInChat?.();
      await controller.intent(
        id,
        'approval.resolve',
        scope === 'turn'
          ? { decision, nonce: view.nonce, scope }
          : { decision, nonce: view.nonce },
        view.revision,
      );
      setResolution(
        decision === 'approve' ? 'Approval submitted.' : 'Denial submitted.',
      );
      readPendingApprovalsNow(controller.pendingApprovals);
      onResolved?.();
    } catch (cause) {
      setError(clientError(cause).message);
      setBusy(false);
    }
  }
  const setup = view?.setup ?? hint?.setup ?? null;
  // A folder card never answers without a picked folder, so no shortcut.
  const folderCard = setup?.kind === 'folder' || setup?.kind === 'clone';
  const ready = Boolean(view) && !busy && !resolution && !notice && !folderCard;
  useEffect(() => {
    if (!ready) return;
    const key = (event: KeyboardEvent) => {
      if (
        event.key !== 'Enter' ||
        !(event.metaKey || event.ctrlKey) ||
        event.defaultPrevented ||
        event.isComposing ||
        editableTarget(event.target) ||
        dialogOpen()
      )
        return;
      event.preventDefault();
      void resolve('approve');
    };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  });
  const action = view?.action_label || hint?.action_label || '';
  // The app asking, shown as the catalog knows it.
  const app = view?.app ?? null;
  const risk = view?.risk_class || hint?.risk_class || 'unknown';
  const argument = keyArgument(view?.safe_argument_summary);
  if (answered)
    return (
      <p className="approval-card-answered">
        <ShieldCheck aria-hidden />
        This request was answered.
      </p>
    );
  if (setup && (setup.kind === 'folder' || setup.kind === 'clone'))
    return (
      <FolderSetupCard
        setup={{ ...setup, kind: setup.kind }}
        reason={view?.reason || hint?.reason || ''}
        conversationId={notice ? undefined : conversationId}
        ready={Boolean(view) && !busy && !resolution}
        resolution={resolution}
        error={error}
        onDecide={(decision) => resolve(decision)}
      />
    );
  if (setup)
    return (
      <aside
        className="approval-card"
        data-kind="setup"
        aria-label={`Turn on ${setup.label}`}
      >
        <span className="approval-card-icon" aria-hidden>
          <Power />
        </span>
        <div className="approval-card-context">
          <strong>Turn on {setup.label}?</strong>
          <span className="approval-card-reason">
            {view?.reason ||
              hint?.reason ||
              `Row-Bot needs ${setup.label} for this.`}
          </span>
        </div>
        <div className="approval-card-actions">
          <Button
            disabled={!view || busy || Boolean(resolution)}
            onClick={() => void resolve('reject')}
          >
            Not now
          </Button>
          <Button
            variant="primary"
            aria-keyshortcuts="Control+Enter Meta+Enter"
            disabled={!view || busy || Boolean(resolution)}
            onClick={() => void resolve('approve')}
          >
            Turn on
            <span aria-hidden className="approval-card-kbd">
              <Kbd keys="Mod+Enter" />
            </span>
          </Button>
        </div>
        {resolution && (
          <p role="status" className="approval-card-status">
            {resolution === 'Approval submitted.'
              ? `Turning on ${setup.label}…`
              : `${setup.label} stays off.`}
          </p>
        )}
        {error && (
          <p role="alert" className="approval-card-status">
            {error}
          </p>
        )}
      </aside>
    );
  return (
    <aside
      className="approval-card"
      aria-label={`Approval required for ${action || 'requested action'}${app ? ` in ${app.name}` : ''}`}
      data-risk={risk}
    >
      <span className="approval-card-icon" aria-hidden>
        {app ? <AppIcon icon={app.icon} size={24} /> : <ShieldAlert />}
      </span>
      {view ? (
        <>
          <div className="approval-card-context">
            {app && <span className="approval-card-app">{app.name}</span>}
            <strong>{approvalQuestion(view.action_label || '', app)}</strong>
            <span className="approval-card-reason">
              {plainApprovalReason(view.reason || view.summary || '')}
            </span>
            {argument && (
              <code className="approval-card-argument">{argument}</code>
            )}
            {RISK[risk] && (
              <small className="approval-card-meta">{RISK[risk]}</small>
            )}
            {view.requested_at && (
              <small className="approval-card-meta">
                <WaitingSince value={view.requested_at} />
              </small>
            )}
          </div>
          <div className="approval-card-actions">
            <Button
              variant="ghost"
              disabled={busy || Boolean(resolution)}
              onClick={() =>
                overlay.open({
                  title: approvalQuestion(view.action_label || '', app),
                  description: 'What Row-Bot wants to do, and what it affects.',
                  content: <ApprovalDetails view={view} />,
                })
              }
            >
              Details
            </Button>
            <Button
              disabled={busy || Boolean(resolution)}
              onClick={() => void resolve('reject')}
            >
              Deny
            </Button>
            <Button
              variant="primary"
              aria-label="Approve"
              aria-keyshortcuts={
                notice ? undefined : 'Control+Enter Meta+Enter'
              }
              disabled={busy || Boolean(resolution)}
              onClick={() => void resolve('approve')}
            >
              Approve
              {!notice && (
                <span aria-hidden className="approval-card-kbd">
                  <Kbd keys="Mod+Enter" />
                </span>
              )}
            </Button>
            {view.repeatable && (
              <Hint label="Approve this and the rest of this kind until this reply ends">
                <Button
                  variant="ghost"
                  disabled={busy || Boolean(resolution)}
                  onClick={() => void resolve('approve', false, 'turn')}
                >
                  Approve the rest
                </Button>
              </Hint>
            )}
            {onAllowInChat && (
              <Hint label="Approves this and switches this chat to Auto. Apps still ask when their access says to.">
                <Button
                  variant="ghost"
                  className="approval-card-allow"
                  disabled={busy || Boolean(resolution)}
                  onClick={() => void resolve('approve', true)}
                >
                  Always allow in this chat
                </Button>
              </Hint>
            )}
          </div>
        </>
      ) : (
        <div className="approval-card-context">
          <strong>
            {action ? approvalQuestion(action) : 'Approval needed'}
          </strong>
          <span className="approval-card-reason">
            {hint?.reason || 'Loading approval context…'}
          </span>
          <Skeleton label="Loading current approval" />
        </div>
      )}
      {resolution && (
        <p role="status" className="approval-card-status">
          {resolution}
        </p>
      )}
      {error && (
        <p role="alert" className="approval-card-status">
          {error}
        </p>
      )}
    </aside>
  );
}

/**
 * The same card for an approval the page asks for itself (a custom tool's
 * test command, parity row 34): what will run, why it needs approval, and
 * Deny / Approve. Nothing runs until Approve.
 */
export function CommandApprovalCard({
  question,
  reason,
  command,
  busy = false,
  onApprove,
  onDeny,
}: {
  question: string;
  reason: string;
  command: string;
  busy?: boolean;
  onApprove: () => void;
  onDeny: () => void;
}) {
  return (
    <aside
      className="approval-card"
      aria-label={`Approval required: ${question}`}
      data-risk="medium"
    >
      <span className="approval-card-icon" aria-hidden>
        <ShieldAlert />
      </span>
      <div className="approval-card-context">
        <strong>{question}</strong>
        <span className="approval-card-reason">{reason}</span>
        <code className="approval-card-argument">{command}</code>
      </div>
      <div className="approval-card-actions">
        <Button disabled={busy} onClick={onDeny}>
          Deny
        </Button>
        <Button
          variant="primary"
          aria-label="Approve"
          disabled={busy}
          onClick={onApprove}
        >
          Approve
        </Button>
      </div>
    </aside>
  );
}
