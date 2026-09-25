import { useEffect, useState } from 'react';
import { ShieldAlert } from 'lucide-react';
import type { ApprovalView } from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Button, Hint, Kbd, Skeleton } from '../../ui/primitives';
import { absoluteTime, relativeTime } from '../../ui/format';
import { approvalQuestion, keyArgument } from './tool-activity';

type Hint_ = { action_label?: string; reason?: string; risk_class?: string };

const RISK: Record<string, string> = {
  low: 'Low risk',
  medium: 'Medium risk',
  high: 'High risk',
  critical: 'Critical risk',
};

function ApprovalDetails({ view }: { view: ApprovalView }) {
  return (
    <div className="approval-detail stack">
      <p>{view.reason || view.summary || 'Approval review required.'}</p>
      <dl>
        <dt>Action</dt>
        <dd>{view.action_label}</dd>
        <dt>Risk</dt>
        <dd>{RISK[view.risk_class ?? ''] ?? 'Not classified'}</dd>
        <dt>Scope</dt>
        <dd>{view.scope || 'Only this requested action.'}</dd>
        {view.safe_argument_summary && (
          <>
            <dt>Reviewed input</dt>
            <dd>
              <code>{view.safe_argument_summary}</code>
            </dd>
          </>
        )}
        <dt>Expires</dt>
        <dd>
          {view.expires_at ? (
            <time dateTime={view.expires_at} title={view.expires_at}>
              {absoluteTime(view.expires_at) || view.expires_at} (
              {relativeTime(view.expires_at)})
            </time>
          ) : (
            'No server expiry supplied'
          )}
        </dd>
      </dl>
      <small>
        Request {view.id} · policy revision {view.policy_revision}
      </small>
    </div>
  );
}

function dialogOpen() {
  return Boolean(
    document.querySelector('[role="dialog"], [role="alertdialog"]'),
  );
}

/**
 * An inline card anchored at the step that needs a decision: what will
 * happen, how risky it is, and Approve (⌘↵) / Deny. "Always allow in this
 * chat" switches the conversation to automatic approvals, then approves.
 */
export default function ApprovalCard({
  id,
  hint,
  onAllowInChat,
}: {
  id: string;
  hint?: Hint_;
  onAllowInChat?: () => Promise<void>;
}) {
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const [view, setView] = useState<ApprovalView | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [resolution, setResolution] = useState('');
  useEffect(() => {
    const abort = new AbortController();
    void controller
      .approval(id, abort.signal)
      .then(setView)
      .catch((error) => {
        if (!abort.signal.aborted) setError(clientError(error).message);
      });
    return () => abort.abort();
  }, [controller, id]);
  async function resolve(decision: 'approve' | 'reject', allow = false) {
    if (!view || busy || resolution) return;
    setBusy(true);
    try {
      if (allow) await onAllowInChat?.();
      await controller.intent(
        id,
        'approval.resolve',
        { decision, nonce: view.nonce },
        view.revision,
      );
      setResolution(
        decision === 'approve' ? 'Approval submitted.' : 'Denial submitted.',
      );
    } catch (cause) {
      setError(clientError(cause).message);
      setBusy(false);
    }
  }
  const ready = Boolean(view) && !busy && !resolution;
  useEffect(() => {
    if (!ready) return;
    const key = (event: KeyboardEvent) => {
      if (
        event.key !== 'Enter' ||
        !(event.metaKey || event.ctrlKey) ||
        event.defaultPrevented ||
        event.isComposing ||
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
  const risk = view?.risk_class || hint?.risk_class || 'unknown';
  const argument = keyArgument(view?.safe_argument_summary);
  return (
    <aside
      className="approval-card"
      aria-label={`Approval required for ${action || 'requested action'}`}
      data-risk={risk}
    >
      <span className="approval-card-icon" aria-hidden>
        <ShieldAlert />
      </span>
      {view ? (
        <>
          <div className="approval-card-context">
            <strong>{approvalQuestion(view.action_label || '')}</strong>
            <span className="approval-card-reason">
              {view.reason || view.summary}
            </span>
            {argument && (
              <code className="approval-card-argument">{argument}</code>
            )}
            <small className="approval-card-meta">
              {[RISK[risk], view.scope].filter(Boolean).join(' · ')}
            </small>
          </div>
          <div className="approval-card-actions">
            <Button
              variant="ghost"
              disabled={busy || Boolean(resolution)}
              onClick={() =>
                overlay.open({
                  title: `Approval details · ${view.action_label}`,
                  description: 'Review bounded public context for this action.',
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
              aria-keyshortcuts="Control+Enter Meta+Enter"
              disabled={busy || Boolean(resolution)}
              onClick={() => void resolve('approve')}
            >
              Approve
              <span aria-hidden className="approval-card-kbd">
                <Kbd keys="Mod+Enter" />
              </span>
            </Button>
            {onAllowInChat && (
              <Hint label="Switch this conversation to automatic approvals and approve this request">
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
