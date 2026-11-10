import { useEffect, useState } from 'react';
import {
  Folder,
  HatGlasses,
  Pencil,
  Power,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
} from 'lucide-react';
import type {
  ApprovalAnswer,
  ApprovalQuestion,
  ApprovalSetup,
  ApprovalView,
  XPostEdit,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Button, Hint, IconButton, Kbd, Skeleton } from '../../ui/primitives';
import { absoluteTime, humanizeToken, relativeTime } from '../../ui/format';
import FolderSetupCard from './FolderSetupCard';
import { BrowserThumbnail } from '../browser/BrowserThumbnail';
import { ComputerThumbnail } from './ComputerStep';
import { requestBrowserPane } from '../browser/open-pane';
import QuestionCard from './QuestionCard';
import XPostDetails, { XPostEditor } from './XPostDetails';
import { resolveApproval } from './resolve-approval';
import { AppIcon } from '../apps/parts';
import { WaitingSince } from './waiting-since';
import { readPendingApprovalsNow } from './pending-approvals';
import {
  approvalAction,
  rawToolName,
  approvalQuestion,
  keyArgument,
  plainApprovalReason,
  readableValue,
} from './tool-activity';

// The Ctrl ↵ hint only where a hardware keyboard is likely: a fine pointer on a wide layout, never a phone
// or a touch screen (B389). The shortcut itself works wherever a keyboard is.
const KEYBOARD_LAYOUT = '(pointer: fine) and (min-width: 641px)';
function useKeyboardLayout() {
  const read = () =>
    typeof matchMedia === 'function'
      ? matchMedia(KEYBOARD_LAYOUT).matches
      : false;
  const [keyboard, setKeyboard] = useState(read);
  useEffect(() => {
    if (typeof matchMedia !== 'function') return;
    const media = matchMedia(KEYBOARD_LAYOUT);
    const changed = () => setKeyboard(media.matches);
    media.addEventListener('change', changed);
    changed();
    return () => media.removeEventListener('change', changed);
  }, []);
  return keyboard;
}

type Hint_ = {
  action_label?: string;
  reason?: string;
  risk_class?: string;
  setup?: ApprovalSetup | null;
  reviewer_reason?: string;
  question?: string;
  questions?: ApprovalQuestion[];
  memory_text?: string;
};

/** Under Auto: why the reviewer asked the person rather than approving it (quiet, one line). */
function ReviewerNote({ reason }: { reason?: string }) {
  if (!reason) return null;
  return (
    <small className="approval-card-meta approval-card-reviewer">
      <ShieldQuestion aria-hidden size={13} />
      The reviewer asked you: {reason}
    </small>
  );
}

/** An action in Row-Bot's browser ("Browser Click"): its card shows the page. */
function browserAction(label: string | undefined) {
  return /^browser[ _]/i.test(label ?? '');
}

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

/** What one approval lets happen, in the card's words: "This command only." (U7). */
function plainScope(scope: string | null | undefined, action: string): string {
  if (!scope || /^Only this requested action will be resolved\.?$/.test(scope))
    return /\bcommand\b/i.test(action)
      ? 'This command only.'
      : 'This action only.';
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

/** An app tool's reason is written in words already, as labelled fields (B332); others read their call in words. */
function reasonWords(view: ApprovalView): string {
  const reason = view.reason || view.summary || '';
  return fieldsWritten(view) ? reason : plainApprovalReason(reason);
}

/** Whether the server wrote an app tool's arguments as labelled fields (one from before names its tool first). */
function fieldsWritten(view: ApprovalView): boolean {
  return Boolean(view.app?.tool && !/^mcp_/.test(view.reason || ''));
}

function ApprovalDetails({ view }: { view: ApprovalView }) {
  const risk = RISK[view.risk_class ?? ''];
  const reason = reasonWords(view);
  const action = approvalAction(view.action_label || '', view.app);
  return (
    <div className="approval-detail stack">
      <p className="approval-detail-reason">
        {reason || 'Row-Bot needs your go-ahead.'}
      </p>
      <dl>
        <dt>Action</dt>
        <dd>{action}</dd>
        {view.folder && (
          // By the folder's name, as Settings names it; never its path.
          <>
            <dt>Runs in</dt>
            <dd className="approval-detail-folder">
              <Folder size={14} aria-hidden />
              {view.folder}
            </dd>
          </>
        )}
        {rawToolName(view.action_label || '') && (
          <>
            <dt>Tool</dt>
            <dd>
              <code>{rawToolName(view.action_label || '')}</code>
            </dd>
          </>
        )}
        {risk && (
          <>
            <dt>Risk</dt>
            <dd>{risk}</dd>
          </>
        )}
        <dt>This approval covers</dt>
        <dd>{plainScope(view.scope, action)}</dd>
        {view.reviewer_reason && (
          <>
            <dt>Reviewer</dt>
            <dd>Asked you: {view.reviewer_reason}</dd>
          </>
        )}
        {view.safe_argument_summary && !fieldsWritten(view) && (
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
 * happen, how risky it is, and Approve (⌘↵) / Deny. Under Auto it says, quietly,
 * why the reviewer asked rather than approving. "Use Auto in this chat"
 * switches the conversation to reviewed Auto, then approves.
 *
 * Turning on a tool the work needs is a setup card instead (decision 12):
 * "Turn on Web search?" with Turn on (⌘↵) / Not now, in place of sending
 * the person to Settings. A code folder the work needs is a folder card
 * (B277), answered only once the person has picked the folder.
 *
 * Row-Bot's question with options (N4) is a question card instead: answered
 * or skipped, never approved.
 *
 * A memory write in an incognito chat (N11) asks "Save to memory? This chat
 * is incognito." with the exact text, and Save / Don't save: only the person
 * answers it, so it offers neither Auto nor the rest of the turn.
 */
export default function ApprovalCard({
  id,
  hint,
  conversationId,
  onAllowInChat,
  onResolved,
  notice = false,
  shortcut = true,
  onLater,
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
  /** False where several cards share a page (Home's Review): Ctrl+Enter answers none of them. */
  shortcut?: boolean;
  /** Not now: Home's Review sets the card aside; it keeps waiting. */
  onLater?: () => void;
}) {
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const [view, setView] = useState<ApprovalView | null>(null);
  const [error, setError] = useState('');
  const [answered, setAnswered] = useState(false);
  const [busy, setBusy] = useState(false);
  const [resolution, setResolution] = useState('');
  const [editing, setEditing] = useState(false);
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
    // 'chat' also approves later actions of this kind in this conversation (F21, 5.3.1 B2).
    scope: 'once' | 'chat' = 'once',
  ) {
    if (!view || busy || resolution) return;
    setBusy(true);
    try {
      if (allow) await onAllowInChat?.();
      await resolveApproval(
        controller,
        id,
        view,
        scope === 'chat' ? { decision, scope } : { decision },
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
  /** An X post edited on its card (G2): Row-Bot asks again with it. */
  async function saveEdit(edit: XPostEdit) {
    if (!view || busy || resolution) return;
    setBusy(true);
    try {
      await resolveApproval(controller, id, view, { decision: 'edit', edit });
      setEditing(false);
      setResolution('Edited. Row-Bot asks again with your version.');
      readPendingApprovalsNow(controller.pendingApprovals);
      onResolved?.();
    } catch (cause) {
      setError(clientError(cause).message);
      setBusy(false);
    }
  }
  /** A question's answers (one per question), or null to skip it. */
  async function answer(answers: ApprovalAnswer[] | null) {
    if (!view || busy || resolution) return;
    setBusy(true);
    try {
      await resolveApproval(
        controller,
        id,
        view,
        answers ? { decision: 'approve', answers } : { decision: 'reject' },
      );
      setResolution(
        answers
          ? 'Answered. Row-Bot goes on with your answer.'
          : 'Skipped. Row-Bot goes on with its recommendation.',
      );
      readPendingApprovalsNow(controller.pendingApprovals);
      onResolved?.();
    } catch (cause) {
      setError(clientError(cause).message);
      setBusy(false);
    }
  }
  const setup = view?.setup ?? hint?.setup ?? null;
  const questions = view?.questions?.length
    ? view.questions
    : (hint?.questions ?? []);
  // A folder card never answers without a picked folder, and a question is
  // never approved, so neither takes the shortcut.
  const folderCard = setup?.kind === 'folder' || setup?.kind === 'clone';
  const ready =
    Boolean(view) &&
    !busy &&
    !resolution &&
    !notice &&
    shortcut &&
    !editing &&
    !folderCard &&
    !questions.length;
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
  const keyboard = useKeyboardLayout();
  const action = view?.action_label || hint?.action_label || '';
  // The app asking, shown as the catalog knows it.
  const app = view?.app ?? null;
  // The tool's own question when it has one ("Allow Row-Bot's browser to open localhost:5173 in this chat?").
  const question =
    view?.question ||
    hint?.question ||
    approvalQuestion(view?.action_label || action, app);
  const risk = view?.risk_class || hint?.risk_class || 'unknown';
  // A post on X (N7) shows exactly what goes out, and as whom, in place of the reason.
  const post = view?.x_post ?? null;
  // The reason names every field already when an app's tool asks; the key argument would repeat one.
  const argument =
    post || (view && fieldsWritten(view))
      ? ''
      : keyArgument(view?.safe_argument_summary);
  if (answered)
    return (
      <p className="approval-card-answered">
        <ShieldCheck aria-hidden />
        This request was answered.
      </p>
    );
  if (questions.length)
    return (
      <QuestionCard
        questions={questions}
        ready={Boolean(view) && !busy && !resolution}
        resolution={resolution}
        error={error}
        notice={notice}
        onSubmit={answer}
      />
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
  const memoryText = view?.memory_text || hint?.memory_text || '';
  if (memoryText)
    return (
      <aside
        className="approval-card"
        data-kind="memory"
        aria-label={`Approval required: ${question}`}
      >
        <span className="approval-card-icon" aria-hidden>
          <HatGlasses />
        </span>
        <div className="approval-card-context">
          <strong>{question}</strong>
          <blockquote className="approval-card-memory">{memoryText}</blockquote>
          {view?.requested_at && (
            <small className="approval-card-meta">
              <WaitingSince value={view.requested_at} />
            </small>
          )}
        </div>
        <div className="approval-card-actions">
          <Button
            disabled={!view || busy || Boolean(resolution)}
            onClick={() => void resolve('reject')}
          >
            Don’t save
          </Button>
          <Button
            variant="primary"
            aria-keyshortcuts={notice ? undefined : 'Control+Enter Meta+Enter'}
            disabled={!view || busy || Boolean(resolution)}
            onClick={() => void resolve('approve')}
          >
            Save
            {!notice && keyboard && (
              <span aria-hidden className="approval-card-kbd">
                <Kbd keys="Mod+Enter" />
              </span>
            )}
          </Button>
        </div>
        {resolution && (
          <p role="status" className="approval-card-status">
            {resolution === 'Approval submitted.'
              ? 'Saving to memory…'
              : 'Not saved to memory.'}
          </p>
        )}
        {error && (
          <p role="alert" className="approval-card-status">
            {error}
          </p>
        )}
      </aside>
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
            {keyboard && (
              <span aria-hidden className="approval-card-kbd">
                <Kbd keys="Mod+Enter" />
              </span>
            )}
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
      aria-label={
        view?.question || hint?.question
          ? `Approval required: ${question}`
          : `Approval required for ${app?.tool || (rawToolName(action) ? approvalAction(action) : action) || 'requested action'}${app ? ` in ${app.name}` : ''}`
      }
      data-risk={risk}
    >
      <span className="approval-card-icon" aria-hidden>
        {app ? <AppIcon icon={app.icon} size={24} /> : <ShieldAlert />}
      </span>
      {view ? (
        <>
          <div className="approval-card-context">
            {app && <span className="approval-card-app">{app.name}</span>}
            <strong>{question}</strong>
            {conversationId && browserAction(view.action_label) && (
              <BrowserThumbnail
                conversationId={conversationId}
                onOpen={() => requestBrowserPane(conversationId)}
              />
            )}
            {conversationId && view.action_label === 'Computer activity' && (
              <ComputerThumbnail conversationId={conversationId} />
            )}
            {post && editing ? (
              <XPostEditor
                post={post}
                busy={busy}
                onSave={(edit) => void saveEdit(edit)}
                onCancel={() => setEditing(false)}
              />
            ) : post ? (
              <XPostDetails post={post} />
            ) : (
              <span
                className="approval-card-reason"
                data-fields={fieldsWritten(view) || undefined}
              >
                {reasonWords(view)}
              </span>
            )}
            {argument && (
              <code className="approval-card-argument">{argument}</code>
            )}
            <ReviewerNote
              reason={view.reviewer_reason || hint?.reviewer_reason}
            />
            {RISK[risk] && (
              <small className="approval-card-meta">{RISK[risk]}</small>
            )}
            {view.requested_at && (
              <small className="approval-card-meta">
                <WaitingSince value={view.requested_at} />
              </small>
            )}
          </div>
          <div className="approval-card-actions" hidden={editing}>
            {onLater && (
              <Button
                variant="ghost"
                disabled={busy || Boolean(resolution)}
                onClick={onLater}
              >
                Not now
              </Button>
            )}
            {post && post.action !== 'delete' && !notice && (
              <IconButton
                label="Edit the post"
                disabled={busy || Boolean(resolution)}
                onClick={() => setEditing(true)}
              >
                <Pencil size={16} aria-hidden />
              </IconButton>
            )}
            <Button
              variant="ghost"
              disabled={busy || Boolean(resolution)}
              onClick={() =>
                overlay.open({
                  title: question,
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
              aria-label={post?.post_at ? 'Approve for then' : 'Approve'}
              aria-keyshortcuts={
                notice || !shortcut ? undefined : 'Control+Enter Meta+Enter'
              }
              disabled={busy || Boolean(resolution)}
              onClick={() => void resolve('approve')}
            >
              {post?.post_at ? 'Approve for then' : 'Approve'}
              {!notice && shortcut && keyboard && (
                <span aria-hidden className="approval-card-kbd">
                  <Kbd keys="Mod+Enter" />
                </span>
              )}
            </Button>
            {view.repeatable && (
              <Hint label="Approves this, and actions like it later in this chat, without asking. Sending, posting, deleting and paying still ask.">
                <Button
                  variant="ghost"
                  disabled={busy || Boolean(resolution)}
                  onClick={() => void resolve('approve', false, 'chat')}
                >
                  Allow in this chat
                </Button>
              </Hint>
            )}
            {onAllowInChat && (
              <Hint label="Approves this and switches this chat to Auto: a reviewer approves what matches your request, and anything else still asks you.">
                <Button
                  variant="ghost"
                  className="approval-card-allow"
                  disabled={busy || Boolean(resolution)}
                  onClick={() => void resolve('approve', true)}
                >
                  Use Auto in this chat
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
