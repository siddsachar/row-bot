import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { ArrowUp, X } from 'lucide-react';
import { IconButton } from '../../ui/primitives';
import type { ArtifactBridgeRect } from './artifact-bridge';

export type DesignSelectionState = {
  elementId: string | null;
  tag: string;
  text: string;
  rect: ArtifactBridgeRect | null;
  pageId: string;
  /** The preview the element was picked on; a new preview drops it. */
  revision: string;
};

export type AskOutcome = 'sent' | 'drafted' | 'unavailable';

const TAG_WORDS: Record<string, string> = {
  h1: 'Heading',
  h2: 'Heading',
  h3: 'Heading',
  h4: 'Heading',
  h5: 'Heading',
  h6: 'Heading',
  p: 'Paragraph',
  a: 'Link',
  button: 'Button',
  img: 'Image',
  li: 'List item',
  ul: 'List',
  ol: 'List',
  section: 'Section',
  header: 'Header',
  footer: 'Footer',
  nav: 'Navigation',
  span: 'Text',
  div: 'Block',
  figure: 'Figure',
  figcaption: 'Caption',
  blockquote: 'Quote',
  table: 'Table',
  td: 'Cell',
  th: 'Cell',
  svg: 'Graphic',
  video: 'Video',
  label: 'Label',
  input: 'Field',
};

export function elementWord(tag: string) {
  return TAG_WORDS[tag] ?? tag.toUpperCase();
}

/** The request sent to the conversation for the selected element. */
export function askText(
  selection: DesignSelectionState,
  page: { label: string; number: number; title: string },
  instruction: string,
) {
  const word = elementWord(selection.tag).toLowerCase();
  const excerpt =
    selection.text.length > 80
      ? `${selection.text.slice(0, 79)}…`
      : selection.text;
  const target = excerpt
    ? `the ${word} that reads “${excerpt}”`
    : `the selected ${word} (${selection.tag})`;
  return `In this design, on ${page.label.toLowerCase()} ${page.number} (“${page.title}”), change ${target}: ${instruction.trim()}`;
}

const CARD_WIDTH = 300;
const CARD_HEIGHT = 44;

/**
 * Edit mode: the element picked on the canvas gets a label and an anchored
 * "Ask Row-Bot to change this…" prompt. Coordinates come from the sandboxed
 * preview (its own pixels), scaled like the frame.
 */
export default function DesignSelection({
  selection,
  scale,
  offset,
  viewport,
  bounds,
  onAsk,
  onClose,
}: {
  selection: DesignSelectionState;
  scale: number;
  offset: { left: number; top: number };
  viewport: { width: number; height: number };
  /** The scaled page's full size, which can exceed the visible canvas. */
  bounds: { width: number; height: number };
  onAsk: (instruction: string) => AskOutcome;
  onClose: () => void;
}) {
  const [text, setText] = useState('');
  const [status, setStatus] = useState<AskOutcome | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const key = `${selection.pageId}:${selection.elementId}:${selection.rect?.x}:${selection.rect?.y}`;
  const [shown, setShown] = useState(key);
  if (shown !== key) {
    setShown(key);
    setText('');
    setStatus(null);
  }
  const card = useRef<HTMLFormElement>(null);
  useEffect(() => {
    input.current?.focus({ preventScroll: true });
    card.current?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
  }, [key]);
  const rect = selection.rect;
  const box = rect
    ? {
        left: offset.left + rect.x * scale,
        top: offset.top + rect.y * scale,
        width: Math.max(8, rect.w * scale),
        height: Math.max(8, rect.h * scale),
      }
    : null;
  // The card sits under the element; above it when there is no room below;
  // inside its lower edge when the element fills the page. It stays on the
  // page (which may scroll inside the canvas at larger zooms).
  const width = Math.min(CARD_WIDTH, Math.max(160, viewport.width - 16));
  const left = Math.min(
    Math.max(8, box ? box.left : 8),
    Math.max(8, bounds.width - width - 8),
  );
  const below = box ? box.top + box.height + 8 : 8;
  const above = box ? box.top - CARD_HEIGHT - 30 : -1;
  const placement: 'below' | 'above' | 'inside' = !box
    ? 'below'
    : below + CARD_HEIGHT <= bounds.height - 8
      ? 'below'
      : above >= 8
        ? 'above'
        : 'inside';
  const top =
    placement === 'below'
      ? below
      : placement === 'above'
        ? above
        : Math.max(
            8,
            Math.min(box!.top + box!.height, bounds.height) - CARD_HEIGHT - 12,
          );
  const labelTop = !box
    ? 0
    : placement !== 'above' && box.top >= 22
      ? box.top - 22
      : box.top + 4;
  const label = elementWord(selection.tag);
  const cardStyle: CSSProperties = { left, top, width };
  function submit() {
    if (!text.trim()) return;
    const outcome = onAsk(text);
    setStatus(outcome);
    if (outcome !== 'unavailable') setText('');
  }
  return (
    <>
      {box && (
        <span
          className="design-selection-label"
          style={{
            left: box.left + (labelTop > box.top ? 4 : 0),
            top: labelTop,
          }}
          aria-hidden
        >
          {label}
          {selection.text ? ` · ${selection.text.slice(0, 32)}` : ''}
        </span>
      )}
      <form
        ref={card}
        className="design-ask"
        style={cardStyle}
        role="group"
        aria-label={`Selected ${label.toLowerCase()}`}
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.stopPropagation();
            onClose();
          }
        }}
      >
        <input
          ref={input}
          className="design-ask-input"
          aria-label="Ask Row-Bot to change this"
          placeholder="Ask Row-Bot to change this…"
          maxLength={2000}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            if (status) setStatus(null);
          }}
        />
        <IconButton
          size="sm"
          variant="primary"
          label="Send to Row-Bot"
          type="submit"
          disabled={!text.trim()}
        >
          <ArrowUp size={15} aria-hidden />
        </IconButton>
        <IconButton size="sm" label="Clear selection" onClick={onClose}>
          <X size={15} aria-hidden />
        </IconButton>
        <span className="design-ask-status" role="status">
          {status === 'sent'
            ? 'Sent. Row-Bot is on it in the chat.'
            : status === 'drafted'
              ? 'Added to your message. Review it and send.'
              : status === 'unavailable'
                ? 'The chat cannot take a message right now.'
                : ''}
        </span>
      </form>
    </>
  );
}
