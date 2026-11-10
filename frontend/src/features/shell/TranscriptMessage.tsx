import { memo, useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
  MessageSquareShare,
  BookOpen,
  CornerDownRight,
  Cpu,
  GraduationCap,
  Pencil,
  Repeat2,
  RotateCcw,
  CircleStop,
  Scissors,
  TriangleAlert,
  Volume2,
  VolumeX,
  LayoutTemplate,
} from 'lucide-react';
import type { TranscriptRow, TranscriptTraceGroup } from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Button, CopyGlyph, IconButton } from '../../ui/primitives';
import { AgentStubs } from './AgentStubs';
import ApprovalCard from './ApprovalCard';
import { MediaPreview } from './MediaPreview';
import {
  speak,
  speakableText,
  stopSpeaking,
  useLocalSpeechAvailable,
} from './read-aloud';
import TranscriptTrace from './TranscriptTrace';
import { publicBlockText, TranscriptBlocks } from './TranscriptBlocks';
import { TranscriptCards, type TranscriptCard } from './TranscriptCards';
import type { TracedAgent } from './transcript-model';
import { TurnMarker } from './TurnMarker';
import { RECOVERY_LABELS, turnError, type RecoveryAction } from './turn-errors';
import { useTeach } from '../learning/chat-learning';
import { copyUnavailableMessage } from '../../platform/clipboard';

const NO_BLOCKS: TranscriptRow['blocks'] = [];
const NO_MEDIA: GeneratedMedia[] = [];
const NO_CARDS: TranscriptCard[] = [];
const NO_AGENTS: TracedAgent[] = [];

export type GeneratedMedia = {
  reference: string;
  mime: string;
  /** The generating prompt, when the tool input carried one. */
  caption?: string;
  /** What it is, when it isn't a generated result ("Page screenshot"). */
  label?: string;
};

// The runtime appends a marker such as "⏹️ *[Stopped]*" to interrupted text.
const STOPPED =
  /\s*(?:⏹️?\s*)?\*\[(Stopped|Browser task stopped|Computer task stopped|Browser automation paused for takeover)\]\*\s*$/;

/** Split a trailing stop marker off the last text block. */
export function stoppedMarker(blocks: TranscriptRow['blocks']): {
  blocks: TranscriptRow['blocks'];
  label: string;
} {
  const last = blocks.at(-1);
  if (!last || (last.type !== 'text' && last.type !== 'markdown'))
    return { blocks, label: '' };
  const match = STOPPED.exec(last.text);
  if (!match) return { blocks, label: '' };
  const text = last.text.slice(0, match.index);
  return {
    blocks: text.trim()
      ? [...blocks.slice(0, -1), { ...last, text }]
      : blocks.slice(0, -1),
    label: match[1],
  };
}

function visibleText(blocks: TranscriptRow['blocks']) {
  return blocks.map(publicBlockText).filter(Boolean).join('\n');
}

/** Why a workflow run didn't finish, without repeating the heading. */
function runFailureDetail(notice: string) {
  return notice.replace(/^The run didn't finish[.:]?\s*/, '');
}

/**
 * A message from another conversation: who sent it, linked, and what it
 * said. Never the person's bubble: it isn't theirs.
 */
function LinkedNote({
  from,
  kind,
  text,
  messageId,
  rowId,
}: {
  from: { id: string; title: string };
  kind: 'message' | 'reply';
  text: string;
  messageId?: string;
  rowId?: string;
}) {
  return (
    <div
      className="transcript-note"
      role="note"
      data-note="linked"
      data-message-id={messageId}
      data-row-id={rowId}
    >
      <MessageSquareShare aria-hidden />
      <span>
        <strong>
          {kind === 'reply' ? 'Reply from ' : 'From '}
          <Link to={`/conversations/${encodeURIComponent(from.id)}`}>
            {from.title}
          </Link>
        </strong>
        <span className="transcript-note-body">{text}</span>
      </span>
    </div>
  );
}

/**
 * One transcript row. User turns are right-aligned bubbles; assistant turns
 * are unlabeled prose with their tool activity, embeds and generated media.
 * Actions (copy, read aloud, retry, edit) sit in a quiet bar under the turn.
 */
export const TranscriptMessage = memo(function TranscriptMessage({
  row,
  conversationId,
  traces,
  embeds = NO_BLOCKS,
  media = NO_MEDIA,
  cards = NO_CARDS,
  earlierCards = NO_CARDS,
  agents = NO_AGENTS,
  turnStart = false,
  latest = false,
  computer,
  offerBrowser = false,
  streaming = false,
  onRetry,
  onEdit,
  onRecover,
  toolbar = true,
  copyText,
  children,
}: {
  row: TranscriptRow;
  conversationId: string | null;
  /** Tool activity for this turn (its own traces plus folded neighbours). */
  traces?: TranscriptTraceGroup[];
  /** Charts produced by this row's folded tool results (B4). */
  embeds?: TranscriptRow['blocks'];
  /** Generated media, rendered once here (B22). */
  media?: GeneratedMedia[];
  /** A design or code folder the turn created, a connection it needs. */
  cards?: TranscriptCard[];
  /** Earlier versions of the reply's newest video, folded under it. */
  earlierCards?: TranscriptCard[];
  /** Agents the turn started, shown as live stubs (B241). */
  agents?: TracedAgent[];
  /** This row begins a speaker's turn: it carries their marker (B271). */
  turnStart?: boolean;
  /** The newest assistant turn keeps its actions visible. */
  latest?: boolean;
  /** What computer use is doing now, for a computer step in progress. */
  computer?: string;
  /** This turn used Row-Bot's browser: its last activity row offers the pane (U16). */
  offerBrowser?: boolean;
  /** Text is still arriving for this row. */
  streaming?: boolean;
  onRetry?: () => void;
  onEdit?: (text: string) => void;
  /** Next steps for a runtime error row (switch model, providers, new chat). */
  onRecover?: (action: RecoveryAction) => void;
  /** Actions show once per turn: on user rows and at an assistant turn's end. */
  toolbar?: boolean;
  /** What Copy copies: the whole assistant turn at its last row. */
  copyText?: string;
  children?: ReactNode;
}) {
  const { controller, platform } = useRuntime();
  // Teach Row-Bot, while Learning (preview) is on and uses this chat.
  const teach = useTeach();
  const [expanded, setExpanded] = useState('');
  const [cursor, setCursor] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [previous, setPrevious] = useState<Array<string | undefined>>([]);
  const pageStart = useRef<string | undefined>(undefined);
  const speechOwner = `${conversationId}:${row.id}`;
  // Read aloud stays visible without an on-device voice; pressing it says
  // what is missing instead of the button silently vanishing (U28).
  const canSpeak = useLocalSpeechAvailable();
  const author =
    row.role === 'user'
      ? 'You'
      : row.role === 'assistant'
        ? 'Row-Bot'
        : 'Tool result';
  const stop = stoppedMarker(row.blocks);
  const shown = stop.blocks;
  const text = expanded || visibleText(shown);
  const failure =
    row.role === 'assistant' && !expanded ? turnError(text) : null;
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1600);
    return () => window.clearTimeout(timer);
  }, [copied]);
  useEffect(() => () => stopSpeaking(speechOwner), [speechOwner]);
  async function copy() {
    try {
      const result = await platform.writeClipboard(copyText || text);
      if (result.status !== 'ok') {
        setCopied(false);
        setError(copyUnavailableMessage());
        return;
      }
      setCopied(true);
      setError('');
    } catch {
      setCopied(false);
      setError('The visible message could not be copied.');
    }
  }
  async function copyCode(value: string) {
    try {
      return (await platform.writeClipboard(value)).status === 'ok';
    } catch {
      return false;
    }
  }
  function readAloud() {
    if (speaking) {
      stopSpeaking(speechOwner);
      setSpeaking(false);
      return;
    }
    if (speak(speechOwner, speakableText(text), () => setSpeaking(false)))
      setSpeaking(true);
    else setError('Read aloud needs a voice installed on this device.');
  }
  async function more() {
    const identity = conversationId;
    if (!identity || !row.content_ref) return;
    setBusy(true);
    try {
      const page = await controller.messageText(
        identity,
        row.content_ref,
        cursor,
      );
      if (controller.getSnapshot().selectedConversationId !== identity) return;
      if (expanded)
        setPrevious((pages) => [...pages, pageStart.current].slice(-100));
      pageStart.current = cursor;
      setExpanded(
        new TextDecoder().decode(
          Uint8Array.from(atob(page.data), (c) => c.charCodeAt(0)),
        ),
      );
      setCursor(page.next_cursor ?? undefined);
      setError('');
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }
  // A step that only used a tool saves empty words: nothing to show for them.
  const hasText =
    Boolean(expanded) ||
    shown.some(
      (block) =>
        (block.type !== 'text' && block.type !== 'markdown') ||
        Boolean(publicBlockText(block).trim()),
    );
  const hasActivity = Boolean(traces?.length) && Boolean(conversationId);
  const actionable = toolbar && (hasText || Boolean(copyText)) && !streaming;
  if (row.note === 'continuation')
    // A server-started step: a goal's next turn, work continuing in a design
    // or code folder the assistant just created, or a workflow run's line,
    // with what other conversations left for that run and, when the run
    // didn't finish, why.
    return (
      <>
        <div
          className="transcript-note"
          role="note"
          data-message-id={row.message_id ?? row.id}
          data-row-id={row.id}
        >
          <Repeat2 aria-hidden />
          <span>{visibleText(shown)}</span>
        </div>
        {row.held?.map((item, index) => (
          <LinkedNote
            key={`${item.id}:${index}`}
            from={item}
            kind="message"
            text={item.text}
          />
        ))}
        {row.run_failure ? (
          <div
            className="transcript-note transcript-note-failure"
            role="note"
            data-note="run_failure"
          >
            <TriangleAlert aria-hidden />
            <span>
              <strong>The run didn't finish</strong>
              {runFailureDetail(row.run_failure) ? (
                <span className="transcript-note-body">
                  {runFailureDetail(row.run_failure)}
                </span>
              ) : null}
            </span>
          </div>
        ) : null}
      </>
    );
  if (row.note === 'personal_model_fallback' || row.note === 'backup_model')
    // Learning (preview): the person's personal model got stuck on this turn
    // and their main model finished it; or the backup model took over a turn
    // the main model couldn't answer (#140). A quiet line, like a continuation.
    return (
      <div
        className="transcript-note"
        role="note"
        data-note={row.note}
        data-message-id={row.message_id ?? row.id}
        data-row-id={row.id}
      >
        <Cpu aria-hidden />
        <span>{visibleText(shown)}</span>
      </div>
    );
  if (row.note === 'linked' && row.linked_from)
    // A message from another conversation (5.3.1 C): who sent it, linked,
    // and what it said. Never the person's bubble: it isn't theirs.
    return (
      <LinkedNote
        from={row.linked_from}
        kind={row.linked_from.kind ?? 'message'}
        text={visibleText(shown)}
        messageId={row.message_id ?? row.id}
        rowId={row.id}
      />
    );
  if (row.note === 'agent_task' || row.note === 'agent_guidance')
    // A delegated agent's thread: what the parent asked it to do, never its
    // internal handoff prompt as the person's bubble (B167).
    return (
      <div
        className="transcript-note"
        role="note"
        data-note={row.note}
        data-message-id={row.message_id ?? row.id}
        data-row-id={row.id}
      >
        <CornerDownRight aria-hidden />
        <span>
          <strong>
            {row.note === 'agent_task'
              ? 'Task from the parent conversation'
              : 'Message from the parent conversation'}
          </strong>
          <span className="transcript-note-body">{visibleText(shown)}</span>
        </span>
      </div>
    );
  return (
    <article
      className={`message message-${row.role}`}
      aria-label={`${author} message`}
      data-message-id={row.message_id ?? row.id}
      data-row-id={row.id}
      data-latest={latest ? 'true' : undefined}
      data-streaming={streaming ? 'true' : undefined}
      data-empty={hasText ? undefined : 'true'}
      tabIndex={-1}
    >
      {turnStart && row.role !== 'tool' && <TurnMarker role={row.role} />}
      <div className="transcript-content">
        {row.note === 'page' && (
          // The person's Send under a page (N5): their decisions there, in words.
          <small className="message-source">
            <LayoutTemplate size={14} aria-hidden />
            Sent from a page
          </small>
        )}
        {row.content_status && row.content_status !== 'inline' && (
          <small className="message-delivery-state">
            {row.content_status === 'lazy'
              ? 'Paged content'
              : 'Oversized content'}
          </small>
        )}
        {failure && (
          <div className="turn-notice" data-tone="danger">
            <TriangleAlert className="turn-notice-icon" aria-hidden />
            <div className="turn-notice-text">
              <strong>{failure.cause}</strong>
              {failure.detail && <span>{failure.detail}</span>}
            </div>
            {onRecover && (
              <div className="turn-notice-actions">
                {failure.actions
                  .filter((action) => action !== 'retry' || onRetry)
                  .map((action, index) => (
                    <Button
                      key={action}
                      variant={index === 0 ? 'secondary' : 'ghost'}
                      onClick={() =>
                        action === 'retry' ? onRetry?.() : onRecover(action)
                      }
                    >
                      {RECOVERY_LABELS[action]}
                    </Button>
                  ))}
              </div>
            )}
          </div>
        )}
        {hasText && !failure && (
          <div
            className="message-text"
            data-paged={expanded ? 'true' : undefined}
          >
            {/* A page is an arbitrary text/plain slice of a large message: it
                can cut through Markdown, so it is shown exactly as sent. */}
            {expanded || (
              <TranscriptBlocks blocks={shown} copyText={copyCode} />
            )}
          </div>
        )}
        {hasActivity && (
          <TranscriptTrace
            conversation={conversationId!}
            groups={traces!}
            computer={computer}
            offerBrowser={offerBrowser}
          />
        )}
        <AgentStubs agents={agents} />
        {!!embeds.length && (
          <div className="message-embeds">
            <TranscriptBlocks blocks={embeds} copyText={copyCode} />
          </div>
        )}
        <TranscriptCards
          cards={cards}
          earlier={earlierCards}
          conversation={conversationId}
        />
        {!!media.length && (
          <div className="message-media-grid" data-count={media.length}>
            {media.map((item) => (
              <MediaPreview
                key={item.reference}
                reference={item.reference}
                mime={item.mime}
                caption={item.caption}
                label={item.label}
                downloadName={
                  item.label === 'Page screenshot'
                    ? 'screenshot.png'
                    : item.label === 'Contact sheet'
                      ? 'contact-sheet.jpg'
                      : undefined
                }
              />
            ))}
          </div>
        )}
        {stop.label && (
          <span className="turn-chip" data-tone="neutral">
            <CircleStop aria-hidden />
            {stop.label}
          </span>
        )}
        {row.answered_by === 'personal_model' && hasText && !failure && (
          // Learning (preview): a quiet line, never a badge on the reply.
          <span
            className="turn-chip"
            data-tone="neutral"
            data-kind="personal-model"
          >
            <Cpu aria-hidden />
            Answered by your personal model
          </span>
        )}
        {row.used_lesson && hasText && !failure && (
          // Learning (preview): its turn recalled a lesson in use; quiet, never a badge.
          <span className="turn-chip" data-tone="neutral" data-kind="lesson">
            <BookOpen aria-hidden />
            Used a lesson
          </span>
        )}
        {row.cut_short && (
          // The provider stopped this reply at its output token limit.
          <span
            className="turn-chip"
            data-tone="neutral"
            data-kind="cut-short"
            title="Ask to continue, or for a shorter answer."
          >
            <Scissors aria-hidden />
            Cut short by the output limit
          </span>
        )}
        {children}
        {row.approval_id && (
          // A delegated agent asked its parent for a go-ahead (B162).
          <ApprovalCard id={row.approval_id} notice />
        )}
        {((row.content_status === 'lazy' && (!expanded || cursor)) ||
          previous.length > 0) && (
          <div className="message-actions">
            {row.content_status === 'lazy' && (!expanded || cursor) && (
              <Button
                variant="ghost"
                onClick={() => void more()}
                disabled={busy}
              >
                {cursor ? 'Load next content page' : 'Load message content'}
              </Button>
            )}
            {!!previous.length && (
              <Button
                variant="ghost"
                onClick={() => {
                  const start = previous.at(-1);
                  setPrevious((pages) => pages.slice(0, -1));
                  setExpanded('');
                  setCursor(start);
                  setCopied(false);
                }}
              >
                Previous message portion
              </Button>
            )}
          </div>
        )}
        {actionable && (
          <div
            className="message-toolbar"
            role="toolbar"
            aria-label={`${author} message actions`}
          >
            <IconButton
              size="sm"
              label={copied ? 'Copied message' : 'Copy message'}
              onClick={() => void copy()}
            >
              <CopyGlyph copied={copied} />
            </IconButton>
            {row.role === 'assistant' && (
              <IconButton
                size="sm"
                label={speaking ? 'Stop reading' : 'Read aloud'}
                pressed={speaking}
                data-unavailable={canSpeak ? undefined : 'true'}
                onClick={readAloud}
              >
                {speaking ? (
                  <VolumeX size={15} aria-hidden />
                ) : (
                  <Volume2 size={15} aria-hidden />
                )}
              </IconButton>
            )}
            {row.role === 'assistant' && onRetry && (
              <IconButton
                size="sm"
                label="Retry"
                title="Send your previous message again"
                onClick={onRetry}
              >
                <RotateCcw size={15} aria-hidden />
              </IconButton>
            )}
            {row.role === 'assistant' && teach && row.message_id && (
              <IconButton
                size="sm"
                label="Teach Row-Bot"
                onClick={() => teach({ messageId: row.message_id ?? '' })}
              >
                <GraduationCap size={15} aria-hidden />
              </IconButton>
            )}
            {row.role === 'user' && onEdit && (
              <IconButton
                size="sm"
                label="Edit as a new message"
                onClick={() => onEdit(text)}
              >
                <Pencil size={15} aria-hidden />
              </IconButton>
            )}
          </div>
        )}
        {copied && (
          <small role="status" className="visually-hidden">
            Visible message copied.
          </small>
        )}
        {error && (
          <p role="alert" className="message-error">
            {error}
          </p>
        )}
      </div>
    </article>
  );
});
