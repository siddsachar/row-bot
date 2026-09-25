import { memo, useEffect, useRef, useState, type ReactNode } from 'react';
import {
  Check,
  Copy,
  Pencil,
  RotateCcw,
  Square,
  TriangleAlert,
  Volume2,
  VolumeX,
} from 'lucide-react';
import type { TranscriptRow, TranscriptTraceGroup } from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Button, IconButton } from '../../ui/primitives';
import SafeMarkdown from './chat-parity-markdown';
import { MediaPreview } from './MediaPreview';
import {
  speak,
  speakableText,
  stopSpeaking,
  useLocalSpeechAvailable,
} from './read-aloud';
import TranscriptTrace from './TranscriptTrace';
import { publicBlockText, TranscriptBlocks } from './TranscriptBlocks';
import { RECOVERY_LABELS, turnError, type RecoveryAction } from './turn-errors';

const NO_BLOCKS: TranscriptRow['blocks'] = [];
const NO_MEDIA: GeneratedMedia[] = [];

export type GeneratedMedia = {
  reference: string;
  mime: string;
  /** The generating prompt, when the tool input carried one. */
  caption?: string;
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
  latest = false,
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
  /** The newest assistant turn keeps its actions visible. */
  latest?: boolean;
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
  const [expanded, setExpanded] = useState('');
  const [cursor, setCursor] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [previous, setPrevious] = useState<Array<string | undefined>>([]);
  const pageStart = useRef<string | undefined>(undefined);
  const speechOwner = `${conversationId}:${row.id}`;
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
        setError('Copy is unavailable in this browser.');
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
  const hasText = Boolean(expanded) || shown.length > 0;
  const hasActivity = Boolean(traces?.length) && Boolean(conversationId);
  const actionable = toolbar && (hasText || Boolean(copyText)) && !streaming;
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
      <div className="transcript-content">
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
          <div className="message-text">
            {expanded ? (
              <SafeMarkdown text={expanded} copyText={copyCode} />
            ) : (
              <TranscriptBlocks blocks={shown} copyText={copyCode} />
            )}
          </div>
        )}
        {hasActivity && (
          <TranscriptTrace conversation={conversationId!} groups={traces!} />
        )}
        {!!embeds.length && (
          <div className="message-embeds">
            <TranscriptBlocks blocks={embeds} copyText={copyCode} />
          </div>
        )}
        {!!media.length && (
          <div className="message-media-grid" data-count={media.length}>
            {media.map((item) => (
              <MediaPreview
                key={item.reference}
                reference={item.reference}
                mime={item.mime}
                caption={item.caption}
              />
            ))}
          </div>
        )}
        {stop.label && (
          <span className="turn-chip" data-tone="neutral">
            <Square aria-hidden />
            {stop.label}
          </span>
        )}
        {children}
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
              {copied ? (
                <Check size={15} aria-hidden />
              ) : (
                <Copy size={15} aria-hidden />
              )}
            </IconButton>
            {row.role === 'assistant' && canSpeak && (
              <IconButton
                size="sm"
                label={speaking ? 'Stop reading' : 'Read aloud'}
                pressed={speaking}
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
