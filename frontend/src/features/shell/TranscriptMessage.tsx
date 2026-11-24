import { memo, useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
  MessageSquareShare,
  BadgeCheck,
  BookOpen,
  BookmarkCheck,
  BookmarkPlus,
  Copy,
  CornerDownRight,
  Cpu,
  Eye,
  GraduationCap,
  Pencil,
  Repeat2,
  RotateCcw,
  CircleStop,
  GitBranch,
  LoaderCircle,
  Scissors,
  SendHorizontal,
  TextCursor,
  TriangleAlert,
  Volume2,
  VolumeX,
  LayoutTemplate,
  MousePointerClick,
  MoreHorizontal,
  X,
} from 'lucide-react';
import type {
  ClientError,
  TranscriptRow,
  TranscriptTraceGroup,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import {
  Button,
  CopyGlyph,
  IconButton,
  Menu,
  type MenuAction,
} from '../../ui/primitives';
import { useLongPress } from './long-press';
import { AgentStubs } from './AgentStubs';
import ApprovalCard from './ApprovalCard';
import { MediaPreview } from './MediaPreview';
import { madeTitle } from './outputs';
import ErrorFix from './ErrorFix';
import {
  noteReadAloud,
  startReading,
  stopReading,
  useReadAloudReadiness,
  useReading,
} from './read-aloud';
import TranscriptTrace from './TranscriptTrace';
import { publicBlockText, TranscriptBlocks } from './TranscriptBlocks';
import {
  TranscriptCards,
  WorkingFilesRow,
  type TranscriptCard,
} from './TranscriptCards';
import type { WorkingFiles } from './transcript-model';
import type { TracedAgent } from './transcript-model';
import { TurnMarker } from './TurnMarker';
import { RECOVERY_LABELS, turnError, type RecoveryAction } from './turn-errors';
import { useTeach } from '../learning/chat-learning';
import { SkillOfferHint, useSkillOffers } from '../skills/skill-offers';
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
  /** What it is to the person (finals first); none in chats from before. */
  role?: 'final' | 'draft' | 'working';
  /** Presented by the agent (`present`): shown large as the reply's final. */
  presented?: boolean;
  /** Shown before and presented since: marked final where it already shows. */
  final?: boolean;
};

function downloadName(item: GeneratedMedia) {
  return item.presented
    ? item.label
    : item.label === 'Page screenshot'
      ? 'screenshot.png'
      : item.label === 'Contact sheet'
        ? 'contact-sheet.jpg'
        : undefined;
}

function FinalChip() {
  return (
    <span className="final-chip">
      <BadgeCheck aria-hidden />
      Final
    </span>
  );
}

/**
 * A reply's media (finals first): what it made to show along the way (AI
 * pictures and videos) in a grid, a final presented since marked where it
 * already shows; then what it presents, large, each with its name and
 * caption, where the reply ends.
 */
export function MessageMedia({ media }: { media: GeneratedMedia[] }) {
  if (!media.length) return null;
  const finals = media.filter((item) => item.presented);
  const others = media.filter((item) => !item.presented);
  return (
    <>
      {others.length > 0 && (
        <div className="message-media-grid" data-count={others.length}>
          {others.map((item) => {
            const preview = (
              <MediaPreview
                key={item.reference}
                reference={item.reference}
                mime={item.mime}
                caption={item.caption}
                label={item.label}
                downloadName={downloadName(item)}
              />
            );
            return item.final ? (
              <div key={item.reference} className="message-media-final">
                <FinalChip />
                {preview}
              </div>
            ) : (
              preview
            );
          })}
        </div>
      )}
      {finals.length > 0 && (
        <div className="message-finals">
          {finals.map((item) => {
            // Its title, as its Outputs tile names it; its file name downloads.
            const title = item.label ? madeTitle(item.label, item.mime) : '';
            return (
              <figure
                key={item.reference}
                className="message-final"
                aria-label={`Final: ${title || 'result'}`}
              >
                <figcaption className="message-final-head">
                  <FinalChip />
                  <strong title={item.label}>{title}</strong>
                </figcaption>
                {item.caption && (
                  <p className="message-final-caption">{item.caption}</p>
                )}
                <MediaPreview
                  reference={item.reference}
                  mime={item.mime}
                  label={title || item.label}
                  downloadName={downloadName(item)}
                />
              </figure>
            );
          })}
        </div>
      )}
    </>
  );
}

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

/** What a refused fork says under its control (B5). */
function forkFailure(cause: unknown) {
  const error = clientError(cause);
  return error.code === 'generation_active'
    ? "Couldn't fork: the conversation is busy. Try when the reply ends."
    : `Couldn't fork: ${error.message}`;
}

/**
 * Edit and resend (B5): the person's message as an editable field, in place.
 * Send continues in a new conversation with everything before the message;
 * this conversation stays as it is. Enter sends, Escape cancels.
 */
function MessageEditor({
  initial,
  files,
  onSend,
  onCancel,
}: {
  initial: string;
  files: string[];
  onSend: (text: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [text, setText] = useState(initial);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const field = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const node = field.current;
    if (!node) return;
    node.focus();
    node.setSelectionRange(node.value.length, node.value.length);
  }, []);
  async function send() {
    if (!text.trim() || sending) return;
    setSending(true);
    setError('');
    try {
      await onSend(text);
    } catch (cause) {
      setError(forkFailure(cause));
      setSending(false);
    }
  }
  return (
    <form
      className="message-edit"
      aria-busy={sending}
      onSubmit={(event) => {
        event.preventDefault();
        void send();
      }}
    >
      <textarea
        ref={field}
        className="input"
        aria-label="Edit your message"
        maxLength={200000}
        rows={Math.min(8, Math.max(2, text.split('\n').length))}
        value={text}
        disabled={sending}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            onCancel();
          } else if (
            event.key === 'Enter' &&
            !event.shiftKey &&
            !event.metaKey &&
            !event.ctrlKey &&
            !event.nativeEvent.isComposing
          ) {
            event.preventDefault();
            void send();
          }
        }}
      />
      <div className="message-edit-footer">
        <small className="message-edit-hint">
          {files.length
            ? `Sends in a new conversation, with ${files.join(', ')}. This one stays as it is.`
            : 'Sends in a new conversation. This one stays as it is.'}
        </small>
        <div className="message-edit-actions">
          <IconButton
            size="sm"
            label="Cancel"
            disabled={sending}
            onClick={onCancel}
          >
            <X size={15} aria-hidden />
          </IconButton>
          <IconButton
            size="sm"
            type="submit"
            variant="primary"
            label="Send in a new conversation"
            disabled={sending || !text.trim()}
          >
            {sending ? (
              <LoaderCircle size={15} className="spin" aria-hidden />
            ) : (
              <SendHorizontal size={15} aria-hidden />
            )}
          </IconButton>
        </div>
      </div>
      {error && (
        <p role="alert" className="message-error">
          {error}
        </p>
      )}
    </form>
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
  working,
  agents = NO_AGENTS,
  turnStart = false,
  latest = false,
  computer,
  offerBrowser = false,
  streaming = false,
  onRetry,
  onEditResend,
  onFork,
  onRecover,
  toolbar = true,
  pressOnly = false,
  usedTools = false,
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
  /** What the turn made on the way (finals first), folded under its steps. */
  working?: WorkingFiles;
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
  /** Edit and resend (B5): a new conversation from before this message of the person's, sent the new text. */
  onEditResend?: (messageId: string, text: string) => Promise<void>;
  /** Fork from here (B5): a new conversation from the end of this reply's turn. */
  onFork?: (messageId: string) => Promise<void>;
  /** Next steps for a runtime error row (switch model, providers, new chat). */
  onRecover?: (action: RecoveryAction) => void;
  /** Actions show once per turn: on user rows and at an assistant turn's end. */
  toolbar?: boolean;
  /**
   * A phone's older message (D14): no icon row; a long-press, a right-click
   * or the context-menu key opens the same actions as a sheet.
   */
  pressOnly?: boolean;
  /** What Copy copies: the whole assistant turn at its last row. */
  copyText?: string;
  /** This row ends an assistant turn that used tools: it can be saved as a skill (C1). */
  usedTools?: boolean;
  children?: ReactNode;
}) {
  const { controller, platform } = useRuntime();
  // Teach Row-Bot, while Learning (preview) is on and uses this chat.
  const teach = useTeach();
  // Save as a skill (C1), in the person's own chats.
  const skillOffers = useSkillOffers();
  const [expanded, setExpanded] = useState('');
  const [cursor, setCursor] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const [readFailure, setReadFailure] = useState<ClientError | null>(null);
  const [editing, setEditing] = useState(false);
  const [forking, setForking] = useState(false);
  const [previous, setPrevious] = useState<Array<string | undefined>>([]);
  const pageStart = useRef<string | undefined>(undefined);
  const speechOwner = `${conversationId}:${row.id}`;
  // Read aloud speaks with the Voice settings' speech output (Kokoro). It
  // stays visible while that voice isn't installed, and says so (U28).
  const reading = useReading(speechOwner);
  const speaking = reading !== 'idle';
  const readiness = useReadAloudReadiness(controller);
  const voiceMissing = readiness === 'kokoro_model_missing';
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
  useEffect(() => () => stopReading(speechOwner), [speechOwner]);
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
      stopReading(speechOwner);
      return;
    }
    setReadFailure(null);
    const message = row.message_id;
    if (!conversationId || !message) {
      setReadFailure(clientError({ code: 'not_found' }));
      return;
    }
    startReading(speechOwner, (part, signal) =>
      controller.readAloud(conversationId, message, part, signal),
    ).then(
      () => noteReadAloud(controller, 'available'),
      (cause: unknown) => {
        const failure =
          cause instanceof Error && cause.message === 'audio_playback_failed'
            ? {
                code: cause.message,
                message: "This browser couldn't play the reply.",
                recovery: 'retry' as const,
              }
            : clientError(cause);
        if (failure.code === 'kokoro_model_missing')
          noteReadAloud(controller, failure.code);
        setReadFailure(failure);
      },
    );
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
  const actionable =
    toolbar && (hasText || Boolean(copyText)) && !streaming && !editing;
  const messageId = row.message_id ?? '';
  const canEdit = row.role === 'user' && Boolean(onEditResend && messageId);
  const canFork = row.role === 'assistant' && Boolean(onFork && messageId);
  const canSaveSkill =
    row.role === 'assistant' &&
    usedTools &&
    Boolean(skillOffers && messageId) &&
    !failure;
  const skillOffer = canSaveSkill ? skillOffers?.offer(messageId) : undefined;
  const savedSkill =
    skillOffer?.status === 'saved' ? skillOffer.skill_id : null;
  const saveSkill = () =>
    savedSkill
      ? skillOffers?.openSkill(savedSkill)
      : skillOffers?.save(messageId);
  const saveSkillLabel = savedSkill
    ? `Saved as a skill: ${skillOffer?.draft?.display_name || skillOffer?.task_name || savedSkill}`
    : skillOffer?.status === 'drafted'
      ? 'Open skill draft'
      : 'Save as skill';
  const files = shown.flatMap((block) =>
    block.type === 'attachment' ? [block.name] : [],
  );
  async function fork() {
    if (!onFork || forking) return;
    setForking(true);
    setError('');
    try {
      await onFork(messageId);
    } catch (cause) {
      setError(forkFailure(cause));
    } finally {
      setForking(false);
    }
  }
  // A finger resting on a message opens its actions as a sheet (D10); its
  // text is selectable again once Select text is chosen.
  const article = useRef<HTMLElement>(null);
  const [pressed, setPressed] = useState(false);
  const [selecting, setSelecting] = useState(false);
  useLongPress(
    article,
    () => {
      setSelecting(false);
      setPressed(true);
    },
    actionable,
  );
  function selectText() {
    setSelecting(true);
    requestAnimationFrame(() => {
      const node = article.current?.querySelector('.message-text');
      const selection = window.getSelection();
      if (!node || !selection) return;
      const range = document.createRange();
      range.selectNodeContents(node);
      selection.removeAllRanges();
      selection.addRange(range);
    });
  }
  // The toolbar's actions with its icons, so the sheet reads the same.
  const pressActions: MenuAction[] = [
    {
      label: 'Copy message',
      icon: <Copy size={16} />,
      onSelect: () => void copy(),
    },
    ...(hasText
      ? [
          {
            label: 'Select text',
            icon: <TextCursor size={16} />,
            onSelect: () => {},
            afterClose: selectText,
          },
        ]
      : []),
    ...(row.role === 'assistant'
      ? [
          {
            label: speaking
              ? 'Stop reading'
              : voiceMissing
                ? 'Read aloud · needs the Kokoro voice'
                : 'Read aloud',
            icon: speaking ? <VolumeX size={16} /> : <Volume2 size={16} />,
            onSelect: readAloud,
          },
        ]
      : []),
    ...(row.role === 'assistant' && onRetry
      ? [{ label: 'Retry', icon: <RotateCcw size={16} />, onSelect: onRetry }]
      : []),
    ...(canFork
      ? [
          {
            label: 'Fork from here',
            icon: <GitBranch size={16} />,
            onSelect: () => void fork(),
          },
        ]
      : []),
    ...(canSaveSkill
      ? [
          {
            label: saveSkillLabel,
            icon: savedSkill ? (
              <BookmarkCheck size={16} />
            ) : (
              <BookmarkPlus size={16} />
            ),
            onSelect: saveSkill,
          },
        ]
      : []),
    ...(row.role === 'assistant' && teach && row.message_id
      ? [
          {
            label: 'Teach Row-Bot',
            icon: <GraduationCap size={16} />,
            onSelect: () => teach({ messageId: row.message_id ?? '' }),
          },
        ]
      : []),
    ...(canEdit
      ? [
          {
            label: 'Edit and resend',
            icon: <Pencil size={16} />,
            onSelect: () => setEditing(true),
          },
        ]
      : []),
  ];
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
  if (row.note === 'watch')
    // A watch that ran out (B4): Row-Bot's own quiet line, with no model
    // turn behind it.
    return (
      <div
        className="transcript-note"
        role="note"
        data-note="watch"
        data-message-id={row.message_id ?? row.id}
        data-row-id={row.id}
      >
        <Eye aria-hidden />
        <span>{visibleText(shown)}</span>
      </div>
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
      ref={article}
      className={`message message-${row.role}`}
      aria-label={`${author} message`}
      data-selecting={selecting ? 'true' : undefined}
      data-message-id={row.message_id ?? row.id}
      data-row-id={row.id}
      data-latest={latest ? 'true' : undefined}
      data-streaming={streaming ? 'true' : undefined}
      data-empty={hasText ? undefined : 'true'}
      tabIndex={-1}
      onContextMenu={
        pressOnly && actionable
          ? (event) => {
              // Text the person selected keeps the browser's own menu.
              if (selecting || window.getSelection()?.toString()) return;
              event.preventDefault();
              setPressed(true);
            }
          : undefined
      }
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
        {row.note === 'surface' && (
          // A press on a button of an interface Row-Bot drew: the button's words and what it sent.
          <small className="message-source">
            <MousePointerClick size={14} aria-hidden />
            Pressed in an interface
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
        {editing && onEditResend ? (
          <MessageEditor
            initial={shown
              .filter((block) => block.type !== 'attachment')
              .map(publicBlockText)
              .filter(Boolean)
              .join('\n')}
            files={files}
            onCancel={() => setEditing(false)}
            onSend={async (value) => {
              await onEditResend(messageId, value);
              setEditing(false);
            }}
          />
        ) : null}
        {hasText && !failure && !editing && (
          <div
            className="message-text"
            data-paged={expanded ? 'true' : undefined}
          >
            {/* A page is an arbitrary text/plain slice of a large message: it
                can cut through Markdown, so it is shown exactly as sent. */}
            {expanded || (
              <TranscriptBlocks
                blocks={shown}
                copyText={copyCode}
                conversationId={conversationId ?? undefined}
                messageId={row.message_id ?? undefined}
              />
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
        {working && (
          <WorkingFilesRow working={working} conversation={conversationId} />
        )}
        <AgentStubs agents={agents} />
        {!!embeds.length && (
          <div className="message-embeds">
            <TranscriptBlocks blocks={embeds} copyText={copyCode} />
          </div>
        )}
        <TranscriptCards cards={cards} conversation={conversationId} />
        <MessageMedia media={media} />
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
        {actionable && !pressOnly && (
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
                title={
                  voiceMissing && !speaking
                    ? 'Read aloud needs the Kokoro voice'
                    : undefined
                }
                pressed={speaking}
                aria-busy={reading === 'loading' || undefined}
                data-unavailable={voiceMissing ? 'true' : undefined}
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
            {canFork && (
              <IconButton
                size="sm"
                label="Fork from here"
                title="Continue from this reply in a new conversation"
                disabled={forking}
                onClick={() => void fork()}
              >
                {forking ? (
                  <LoaderCircle size={15} className="spin" aria-hidden />
                ) : (
                  <GitBranch size={15} aria-hidden />
                )}
              </IconButton>
            )}
            {canSaveSkill && (
              <IconButton
                size="sm"
                label={saveSkillLabel}
                title={
                  savedSkill
                    ? 'Open it in Settings › Skills'
                    : 'Save how Row-Bot did this as a skill it can reuse'
                }
                data-skill-offer={skillOffer?.status}
                onClick={saveSkill}
              >
                {savedSkill ? (
                  <BookmarkCheck size={15} aria-hidden />
                ) : (
                  <BookmarkPlus size={15} aria-hidden />
                )}
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
            {canEdit && (
              <IconButton
                size="sm"
                label="Edit and resend"
                title="Edit this message and continue in a new conversation; this one stays as it is"
                onClick={() => setEditing(true)}
              >
                <Pencil size={15} aria-hidden />
              </IconButton>
            )}
          </div>
        )}
        {actionable && latest && skillOffer && (
          <SkillOfferHint offer={skillOffer} />
        )}
        {actionable && (
          // Opened by a long-press on the message (or a right-click on a
          // phone's older one); out of the tab order, since the toolbar's
          // buttons are the same actions.
          <Menu
            label="More for this message"
            iconOnly
            variant="ghost"
            className="message-press-menu"
            tabIndex={-1}
            open={pressed}
            onOpenChange={setPressed}
            actions={pressActions}
          >
            <MoreHorizontal size={15} aria-hidden />
          </Menu>
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
        {readFailure &&
          (readFailure.code === 'kokoro_model_missing' ? (
            // A voice still to install is a setup step, not an error.
            <p role="status" className="message-fix-line">
              <span className="status-indicator" data-tone="warning">
                <span className="status-indicator-dot" aria-hidden />
                <span>{readFailure.message}</span>
              </span>
              {readFailure.action?.kind === 'open_setting' && (
                <ErrorFix action={readFailure.action} />
              )}
            </p>
          ) : (
            <p role="alert" className="message-error message-fix-line">
              <span>{readFailure.message}</span>
              {readFailure.action?.kind === 'open_setting' && (
                <ErrorFix action={readFailure.action} />
              )}
            </p>
          ))}
      </div>
    </article>
  );
});
