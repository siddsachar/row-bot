import { useEffect, useId, useRef, useState } from 'react';
import {
  Check,
  ChevronLeft,
  Copy,
  FileText,
  PenLine,
  Send,
  ShieldCheck,
} from 'lucide-react';
import type {
  ProactiveDraftSendCard,
  ProactiveDraftView,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { copyText } from '../../platform/clipboard';
import { Button, IconButton, Input, Kbd, Skeleton } from '../../ui/primitives';
import { Drawer, ModalTask, useNotify } from '../../ui/overlays';
import { When } from '../../ui/When';
import { parseTimestamp } from '../../ui/format';
import { useMedia } from '../../ui/use-media';
import { useProactive, type ProactiveStore } from './proactive-state';
import { briefLines } from './words';

const WIDE_LAYOUT = '(min-width: 1024px)';
/** Edits wait this long after the last keystroke, then save to Row-Bot. */
export const SAVE_AFTER_MS = 700;

/** "10:04" in this device's clock. */
function clock(value: string): string {
  const date = parseTimestamp(value);
  return date
    ? date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
    : '';
}

/** "Re: Q3 numbers — to priya@example.com": what a reply is, and who it goes to. */
export function draftHeading(draft: ProactiveDraftView): string {
  if (draft.kind !== 'reply') return draft.title;
  const subject = draft.subject || draft.title;
  return draft.reply_to ? `${subject} — to ${draft.reply_to}` : subject;
}

/**
 * The approval card for sending a reply Row-Bot drafted. It always shows,
 * whatever the approval level (Row-Bot wrote the words, so only the person
 * sends them): where it goes, the subject and the full text, then Send or
 * Don't send. Ctrl/⌘ Enter sends from inside the card.
 */
export function DraftSendCard({
  card,
  busy,
  onAnswer,
}: {
  card: ProactiveDraftSendCard;
  busy: boolean;
  onAnswer(approved: boolean): void;
}) {
  return (
    <aside
      className="approval-card proactive-send-card"
      data-kind="send"
      aria-label={`Approval required: send this reply to ${card.destination}`}
      onKeyDown={(event) => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
          event.preventDefault();
          if (!busy) onAnswer(true);
        }
      }}
    >
      <span className="approval-card-icon" aria-hidden>
        <ShieldCheck />
      </span>
      <div className="approval-card-context">
        <strong>Send this reply?</strong>
        <span className="approval-card-meta">
          {card.account} · to {card.destination}
        </span>
        {card.subject && (
          <span className="proactive-send-subject">{card.subject}</span>
        )}
        <blockquote className="approval-card-memory proactive-send-text">
          {card.text}
        </blockquote>
        <small className="approval-card-meta">{card.reason}</small>
      </div>
      <div className="approval-card-actions">
        <Button disabled={busy} onClick={() => onAnswer(false)}>
          Don’t send
        </Button>
        <Button
          variant="primary"
          disabled={busy}
          aria-keyshortcuts="Control+Enter Meta+Enter"
          onClick={() => onAnswer(true)}
          data-initial-focus
        >
          <Send size={15} aria-hidden /> Send
          <span aria-hidden className="approval-card-kbd">
            <Kbd keys="Mod+Enter" />
          </span>
        </Button>
      </div>
    </aside>
  );
}

/**
 * One drafted reply or prepared brief. A reply says who it goes to and which
 * thread it answers; its recipient, subject and text are edited here and saved
 * to Row-Bot as you type (never to the mail account). Send… shows the
 * approval card in place; only approving that card sends it.
 */
export function DraftView({
  store,
  draft,
  onBack,
}: {
  store: ProactiveStore;
  draft: ProactiveDraftView;
  onBack?: () => void;
}) {
  const notify = useNotify();
  const id = useId();
  const reply = draft.kind === 'reply';
  const [to, setTo] = useState(draft.reply_to ?? '');
  const [subject, setSubject] = useState(draft.subject ?? '');
  const [body, setBody] = useState(draft.body);
  const [saved, setSaved] = useState(draft.updated_at ?? '');
  const [dirty, setDirty] = useState(false);
  const [card, setCard] = useState<ProactiveDraftSendCard | null>(
    draft.send ?? null,
  );
  const [blocked, setBlocked] = useState(draft.send_blocked ?? '');
  const [sentAt, setSentAt] = useState(draft.sent_at ?? '');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latest = useRef({ to, subject, body });
  latest.current = { to, subject, body };
  const sent = Boolean(sentAt);

  async function save(): Promise<boolean> {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    try {
      const next = await store.io.saveDraft(draft.draft_id, latest.current);
      store.draftChanged(next);
      setSaved(next.updated_at ?? '');
      setBlocked(next.send_blocked ?? '');
      setCard(next.send ?? null);
      setDirty(false);
      return true;
    } catch (cause) {
      setProblem(clientError(cause).message);
      return false;
    }
  }
  useEffect(
    () => () => {
      if (timer.current) {
        clearTimeout(timer.current);
        // Leaving with an edit not yet saved: save it now.
        void store.io
          .saveDraft(draft.draft_id, latest.current)
          .then((next) => store.draftChanged(next))
          .catch(() => undefined);
      }
    },
    [store, draft.draft_id],
  );
  const edit = (apply: () => void) => {
    apply();
    setDirty(true);
    setProblem('');
    setCard(null);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void save(), SAVE_AFTER_MS);
  };

  async function askToSend() {
    setBusy(true);
    setProblem('');
    try {
      if (dirty && !(await save())) return;
      const next = await store.io.askToSend(draft.draft_id);
      store.draftChanged(next);
      setCard(next.send ?? null);
      setBlocked(next.send_blocked ?? '');
    } catch (cause) {
      setProblem(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  async function answer(approved: boolean) {
    if (!card) return;
    setBusy(true);
    setProblem('');
    try {
      const result = await store.io.answerSend(draft.draft_id, {
        binding: card.binding,
        approved,
      });
      store.draftChanged(result.draft);
      setCard(result.draft.send ?? null);
      setBlocked(result.draft.send_blocked ?? '');
      if (result.outcome === 'sent') {
        setSentAt(result.draft.sent_at ?? '');
        notify(result.words);
      } else if (result.outcome !== 'denied') setProblem(result.words);
    } catch (cause) {
      setProblem(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  const lines = reply ? [] : briefLines(draft.body);
  return (
    <article className="proactive-draft" aria-labelledby={`${id}-title`}>
      <header className="proactive-draft-head">
        {onBack && (
          <IconButton size="sm" label="All drafts" onClick={onBack}>
            <ChevronLeft size={16} aria-hidden />
          </IconButton>
        )}
        <div className="proactive-draft-heading">
          <h3 id={`${id}-title`}>
            {reply ? subject || draft.title : draft.title}
          </h3>
          <p>
            {reply
              ? [
                  draft.thread ? `Reply in “${draft.thread}”` : 'Reply',
                  draft.app,
                ]
                  .filter(Boolean)
                  .join(' · ')
              : 'Prepared brief'}{' '}
            · <When value={draft.created_at} fallback="" />
          </p>
        </div>
        <div className="proactive-draft-actions">
          <IconButton
            size="sm"
            label="Copy"
            onClick={() =>
              void copyText(body).then((copied) =>
                notify(
                  copied ? 'Copied' : 'Couldn’t copy',
                  copied ? undefined : 'warning',
                ),
              )
            }
          >
            <Copy size={15} aria-hidden />
          </IconButton>
        </div>
      </header>
      {reply ? (
        <>
          <div className="proactive-draft-fields">
            <label htmlFor={`${id}-to`}>To</label>
            <Input
              id={`${id}-to`}
              type="email"
              value={to}
              readOnly={sent || Boolean(card)}
              placeholder="name@example.com"
              onChange={(event) => edit(() => setTo(event.target.value))}
            />
            <label htmlFor={`${id}-subject`}>Subject</label>
            <Input
              id={`${id}-subject`}
              value={subject}
              readOnly={sent || Boolean(card)}
              onChange={(event) => edit(() => setSubject(event.target.value))}
            />
          </div>
          <label className="visually-hidden" htmlFor={`${id}-body`}>
            Reply text
          </label>
          <textarea
            id={`${id}-body`}
            className="input proactive-draft-body"
            value={body}
            rows={10}
            readOnly={sent || Boolean(card)}
            onChange={(event) => edit(() => setBody(event.target.value))}
            onBlur={() => {
              if (dirty) void save();
            }}
          />
          {card && !sent && (
            <DraftSendCard card={card} busy={busy} onAnswer={answer} />
          )}
          {problem && (
            <p className="proactive-error" role="alert">
              {problem}
            </p>
          )}
          <div className="proactive-draft-foot">
            <p className="proactive-draft-note" role="status">
              {sent ? (
                <>
                  <Check size={14} aria-hidden /> Sent {clock(sentAt)}
                </>
              ) : dirty ? (
                'Saving…'
              ) : saved ? (
                `Saved ${clock(saved)}`
              ) : (
                'Nothing is sent until you approve it.'
              )}
            </p>
            {!sent && !card && (
              <Button
                variant="primary"
                className="small"
                disabled={busy || Boolean(blocked && !dirty)}
                onClick={() => void askToSend()}
              >
                <Send size={15} aria-hidden /> Send…
              </Button>
            )}
          </div>
          {!sent && !card && blocked && !dirty && (
            <p className="proactive-draft-blocked">{blocked}</p>
          )}
        </>
      ) : lines.length ? (
        <ul className="proactive-brief-lines">
          {lines.map((line, index) => (
            <li key={index}>{line}</li>
          ))}
        </ul>
      ) : (
        <p className="proactive-draft-text">{draft.body}</p>
      )}
    </article>
  );
}

/** Drafted replies and prepared briefs, newest first; one opens to read, edit and send. */
export function DraftsList({
  store,
  openId,
  onOpen,
}: {
  store: ProactiveStore;
  openId: string | null;
  onOpen(draftId: string | null): void;
}) {
  const { view } = useProactive(store.io);
  const [single, setSingle] = useState<ProactiveDraftView | null>(null);
  const [error, setError] = useState('');
  useEffect(() => store.watchDrafts(), [store]);
  const listed = view.drafts?.find((draft) => draft.draft_id === openId);
  // A draft named by a moment may be newer than the list: read it on its own.
  useEffect(() => {
    setError('');
    if (!openId || listed) {
      setSingle(null);
      return;
    }
    const request = new AbortController();
    store.io
      .draft(openId, request.signal)
      .then((draft) => {
        if (!request.signal.aborted) setSingle(draft);
      })
      .catch((cause) => {
        if (!request.signal.aborted) setError(clientError(cause).message);
      });
    return () => request.abort();
  }, [store, openId, listed]);
  const open = listed ?? single;
  if (openId)
    return error ? (
      <p className="proactive-error" role="alert">
        {error}
      </p>
    ) : open ? (
      <DraftView
        key={open.draft_id}
        store={store}
        draft={open}
        onBack={() => onOpen(null)}
      />
    ) : (
      <Skeleton label="Opening the draft" />
    );
  if (!view.drafts) return <Skeleton label="Reading drafts" />;
  if (!view.drafts.length)
    return (
      <p className="proactive-quiet-line">
        No drafts. When a reply is worth writing, Row-Bot drafts it here.
        Nothing is sent without you.
      </p>
    );
  return (
    <ul className="proactive-drafts" aria-label="Drafts">
      {view.drafts.map((draft) => (
        <li key={draft.draft_id}>
          <button
            type="button"
            className="overview-row-button proactive-draft-row"
            onClick={() => onOpen(draft.draft_id)}
          >
            <span
              className="overview-tile"
              data-tone={draft.kind === 'reply' ? 'accent' : '4'}
              aria-hidden
            >
              {draft.kind === 'reply' ? (
                <PenLine size={16} />
              ) : (
                <FileText size={16} />
              )}
            </span>
            <span className="overview-row-text">
              <span className="overview-row-title">{draftHeading(draft)}</span>
              <span className="overview-row-meta">
                {draft.kind !== 'reply'
                  ? 'Brief'
                  : draft.sent_at
                    ? 'Sent'
                    : 'Reply, not sent'}{' '}
                · <When value={draft.created_at} fallback="" />
              </span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

const DRAFTS_DESCRIPTION =
  'Replies Row-Bot wrote for you. Nothing is sent until you approve it.';

/**
 * Drafts: a non-modal drawer on a wide window (For you stays visible), a
 * sheet from the bottom below 1024px.
 */
export function DraftsDrawer({
  store,
  open,
  draftId,
  onOpenDraft,
  onClose,
}: {
  store: ProactiveStore;
  open: boolean;
  draftId: string | null;
  onOpenDraft(draftId: string | null): void;
  onClose(): void;
}) {
  const wide = useMedia(WIDE_LAYOUT);
  const list = open && (
    <DraftsList store={store} openId={draftId} onOpen={onOpenDraft} />
  );
  return wide ? (
    <Drawer
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title="Drafts"
      description={DRAFTS_DESCRIPTION}
      className="proactive-drafts-drawer"
    >
      {list}
    </Drawer>
  ) : (
    <ModalTask
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      kind="sheet"
      className="proactive-sheet"
      title="Drafts"
      description={DRAFTS_DESCRIPTION}
    >
      {list}
    </ModalTask>
  );
}
