import { useEffect, useId, useState } from 'react';
import { ChevronLeft, Copy, FileText, PenLine, Send } from 'lucide-react';
import type { ProactiveDraftView } from '../../api/types';
import { clientError } from '../../api/errors';
import { copyText } from '../../platform/clipboard';
import { Button, IconButton, Skeleton } from '../../ui/primitives';
import { Drawer, ModalTask, useNotify } from '../../ui/overlays';
import { When } from '../../ui/When';
import { useMedia } from '../../ui/use-media';

const WIDE_LAYOUT = '(min-width: 1024px)';
import { useProactive, type ProactiveStore } from './proactive-state';
import { briefLines } from './words';

/** Edits to a drafted reply, kept while the app is open (drafts are never saved back or sent). */
const edits = new Map<string, string>();

/** The words a new chat starts with, waiting in its composer: the person sends it. */
export function sendRequest(draft: ProactiveDraftView, body: string): string {
  return `Please send this reply (“${draft.title}”):\n\n${body}`;
}

/**
 * One drafted reply or prepared brief. A reply can be edited here; it is
 * saved, never sent. "Send from a chat" opens a new chat with it waiting in
 * the composer, so sending is the person's own message, under their
 * approval level, like any other.
 */
export function DraftView({
  draft,
  onBack,
  onSendFromChat,
}: {
  draft: ProactiveDraftView;
  onBack?: () => void;
  /** Absent where no chat can be started (then only Copy). */
  onSendFromChat?: (text: string) => void;
}) {
  const notify = useNotify();
  const id = useId();
  const [body, setBody] = useState(
    () => edits.get(draft.draft_id) ?? draft.body,
  );
  const [saved, setSaved] = useState<Date | null>(null);
  const reply = draft.kind === 'reply';
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
          <h3 id={`${id}-title`}>{draft.title}</h3>
          <p>
            {reply ? 'Reply drafted, not sent' : 'Prepared brief'} ·{' '}
            <When value={draft.created_at} fallback="" />
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
          <label className="visually-hidden" htmlFor={`${id}-body`}>
            Reply text
          </label>
          <textarea
            id={`${id}-body`}
            className="input proactive-draft-body"
            value={body}
            rows={10}
            onChange={(event) => {
              setBody(event.target.value);
              edits.set(draft.draft_id, event.target.value);
              setSaved(new Date());
            }}
          />
          <div className="proactive-draft-foot">
            <p className="proactive-draft-note">
              {saved
                ? `Saved ${saved.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`
                : 'Nothing is sent until you approve it.'}
            </p>
            {onSendFromChat && (
              <Button
                variant="primary"
                className="small"
                onClick={() => onSendFromChat(sendRequest(draft, body))}
              >
                <Send size={15} aria-hidden /> Send…
              </Button>
            )}
          </div>
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

/** Drafted replies and prepared briefs, newest first; one opens to read and edit. */
export function DraftsList({
  store,
  openId,
  onOpen,
  onSendFromChat,
}: {
  store: ProactiveStore;
  openId: string | null;
  onOpen(draftId: string | null): void;
  onSendFromChat?: (text: string) => void;
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
        draft={open}
        onBack={() => onOpen(null)}
        onSendFromChat={onSendFromChat}
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
              <span className="overview-row-title">{draft.title}</span>
              <span className="overview-row-meta">
                {draft.kind === 'reply' ? 'Reply' : 'Brief'} ·{' '}
                <When value={draft.created_at} fallback="" />
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
  onSendFromChat,
}: {
  store: ProactiveStore;
  open: boolean;
  draftId: string | null;
  onOpenDraft(draftId: string | null): void;
  onClose(): void;
  onSendFromChat?: (text: string) => void;
}) {
  const wide = useMedia(WIDE_LAYOUT);
  const list = open && (
    <DraftsList
      store={store}
      openId={draftId}
      onOpen={onOpenDraft}
      onSendFromChat={
        onSendFromChat &&
        ((text) => {
          onClose();
          onSendFromChat(text);
        })
      }
    />
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
