import {
  Fragment,
  useEffect,
  useId,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { Code2, ExternalLink, Minus, Plus, RotateCcw } from 'lucide-react';
import type { TranscriptRow } from '../../api/types';
import { loadLocalRuntimeScript } from '../../ui/local-runtime';
import { IconButton } from '../../ui/primitives';
import { useResolvedTheme } from '../../ui/theme';
import SafeMarkdown from './chat-parity-markdown';
import { CodeBlock } from './CodeBlock';
import {
  AnswerChecklist,
  AnswerChoices,
  AnswerFigures,
  AnswerProgress,
  AnswerTable,
  ChartReplies,
} from './answer-components/AnswerComponents';
import { AnswerVisual } from './answer-components/Visual';
import { Chart } from './RichChart';
import { SurfaceView } from './surfaces/SurfaceView';
import {
  AttachmentThumbnail,
  fileKind,
  formatBytes,
  MediaPreview,
  showsAsFileCard,
  showsAsThumbnail,
} from './MediaPreview';

type Block = TranscriptRow['blocks'][number];

declare global {
  interface Window {
    mermaid?: {
      initialize(options: Record<string, unknown>): void;
      render(id: string, source: string): Promise<{ svg: string }>;
    };
    Plotly?: {
      newPlot(
        root: HTMLElement,
        data: unknown[],
        layout: Record<string, unknown>,
        config: Record<string, unknown>,
      ): Promise<unknown>;
      purge(root: HTMLElement): void;
      toImage?(
        root: HTMLElement,
        options: Record<string, unknown>,
      ): Promise<string>;
      Plots?: { resize(root: HTMLElement): void };
    };
  }
}

const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3];

function Mermaid({ source }: { source: string }) {
  const root = useRef<HTMLDivElement>(null);
  const identity = useId().replaceAll(':', '');
  const theme = useResolvedTheme();
  const [status, setStatus] = useState<'loading' | 'ready' | 'failed'>(
    'loading',
  );
  const [showSource, setShowSource] = useState(false);
  const [zoom, setZoom] = useState(2);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; from: typeof offset } | null>(
    null,
  );
  useEffect(() => {
    let active = true;
    const element = root.current;
    setStatus('loading');
    void loadLocalRuntimeScript('mermaid.min.js', () => Boolean(window.mermaid))
      .then(async () => {
        if (!active || !element || !window.mermaid) return;
        window.mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          suppressErrorRendering: true,
          theme: theme === 'dark' ? 'dark' : 'neutral',
          fontFamily: getComputedStyle(document.documentElement)
            .getPropertyValue('--font-sans')
            .trim(),
        });
        const result = await window.mermaid.render(
          `mermaid-${identity}-${theme}`,
          source,
        );
        if (!active) return;
        element.innerHTML = result.svg;
        setStatus('ready');
      })
      .catch(() => active && setStatus('failed'));
    return () => {
      active = false;
      element?.replaceChildren();
    };
  }, [identity, source, theme]);
  const scale = ZOOM_STEPS[zoom];
  const pan = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    setOffset({
      x: drag.current.from.x + event.clientX - drag.current.x,
      y: drag.current.from.y + event.clientY - drag.current.y,
    });
  };
  return (
    <figure className="rich-block rich-mermaid" data-status={status}>
      <div className="rich-block-header">
        <span className="rich-block-title">Diagram</span>
        <span className="rich-block-tools">
          {status === 'ready' && !showSource && (
            <>
              <IconButton
                size="sm"
                label="Zoom out"
                disabled={zoom === 0}
                onClick={() => setZoom((value) => Math.max(0, value - 1))}
              >
                <Minus size={15} aria-hidden />
              </IconButton>
              <IconButton
                size="sm"
                label="Reset zoom"
                onClick={() => {
                  setZoom(2);
                  setOffset({ x: 0, y: 0 });
                }}
              >
                <RotateCcw size={14} aria-hidden />
              </IconButton>
              <IconButton
                size="sm"
                label="Zoom in"
                disabled={zoom === ZOOM_STEPS.length - 1}
                onClick={() =>
                  setZoom((value) => Math.min(ZOOM_STEPS.length - 1, value + 1))
                }
              >
                <Plus size={15} aria-hidden />
              </IconButton>
            </>
          )}
          <IconButton
            size="sm"
            label={showSource ? 'Show diagram' : 'View source'}
            pressed={showSource}
            onClick={() => setShowSource((value) => !value)}
          >
            <Code2 size={15} aria-hidden />
          </IconButton>
        </span>
      </div>
      <div
        className="rich-mermaid-viewport"
        hidden={showSource || status === 'failed'}
        data-pannable={scale > 1 ? 'true' : undefined}
        onPointerDown={(event) => {
          if (scale <= 1) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = { x: event.clientX, y: event.clientY, from: offset };
        }}
        onPointerMove={pan}
        onPointerUp={() => (drag.current = null)}
        onPointerCancel={() => (drag.current = null)}
      >
        <div
          ref={root}
          className="rich-mermaid-canvas"
          aria-hidden={status !== 'ready'}
          style={{
            transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
          }}
        />
      </div>
      {status === 'loading' && !showSource && (
        <p role="status" className="rich-block-status">
          Rendering diagram…
        </p>
      )}
      {(showSource || status === 'failed') && (
        <pre className="rich-fallback">
          <code data-language="mermaid">{source}</code>
        </pre>
      )}
      <figcaption className="visually-hidden">Mermaid diagram</figcaption>
    </figure>
  );
}

function YouTube({ block }: { block: Extract<Block, { type: 'youtube' }> }) {
  const [failed, setFailed] = useState(false);
  return (
    <figure className="rich-block rich-youtube">
      <div className="youtube-player">
        <iframe
          title={block.title}
          src={`https://www.youtube-nocookie.com/embed/${block.video_id}`}
          referrerPolicy="strict-origin-when-cross-origin"
          sandbox="allow-scripts allow-same-origin allow-presentation"
          allow="encrypted-media; picture-in-picture"
          onError={() => setFailed(true)}
          allowFullScreen
        />
      </div>
      <figcaption className="rich-youtube-caption">
        <span className="rich-youtube-title">
          {failed
            ? 'YouTube player is unavailable.'
            : block.title || 'YouTube video'}
        </span>
        <a
          className="rich-youtube-open"
          href={block.url}
          target="_blank"
          rel="noreferrer noopener"
          aria-label="Open on YouTube"
          title="Open on YouTube"
        >
          <ExternalLink aria-hidden />
        </a>
      </figcaption>
    </figure>
  );
}

function Attachment({
  block,
}: {
  block: Extract<Block, { type: 'attachment' }>;
}) {
  return (
    <figure className="rich-block rich-attachment">
      {/* A file card already names the file and its size. */}
      {!showsAsFileCard(block.mime_type) && (
        <figcaption className="rich-attachment-caption">
          <strong>{block.name}</strong>
          <span>
            {formatBytes(block.size_bytes)} ·{' '}
            {fileKind(block.mime_type, block.name)}
          </span>
        </figcaption>
      )}
      {showsAsThumbnail(block.mime_type) ? (
        <AttachmentThumbnail
          reference={block.attachment_ref}
          mime={block.mime_type}
          name={block.name}
          size={block.size_bytes}
        />
      ) : (
        <MediaPreview
          reference={block.attachment_ref}
          mime={block.mime_type}
          label={block.name}
          downloadName={block.name}
          size={block.size_bytes}
        />
      )}
    </figure>
  );
}

export function publicBlockText(block: Block) {
  switch (block.type) {
    case 'text':
    case 'markdown':
    case 'mermaid':
    case 'chart':
    case 'table':
    case 'checklist':
    case 'figures':
    case 'progress':
    case 'choices':
    case 'visual':
    case 'surface':
      return block.text;
    case 'youtube':
      return `${block.title}\n${block.url}`;
    case 'attachment':
      return block.name;
  }
}

export function TranscriptBlocks({
  blocks,
  copyText,
  conversationId,
  messageId,
}: {
  blocks: TranscriptRow['blocks'];
  copyText?: (value: string) => Promise<boolean>;
  /** Where a reply button's words are sent: this conversation's composer. */
  conversationId?: string;
  /** The reply these blocks belong to: a reply button's tap names it. */
  messageId?: string;
}) {
  const target = { conversationId, messageId };
  return (
    <>
      {blocks.map((block, index) => {
        const key = 'id' in block ? block.id : `stream-${index}`;
        switch (block.type) {
          case 'text':
          case 'markdown':
            return (
              <SafeMarkdown
                text={block.text}
                copyText={copyText}
                // Text still arriving: a component's fence waits for its end.
                streaming={block.type === 'text'}
                // A saved block's task-list ticks are kept for the person.
                tasks={
                  block.type === 'markdown'
                    ? { conversationId, blockId: block.id }
                    : undefined
                }
                key={key}
              />
            );
          case 'mermaid':
            return <Mermaid source={block.source} key={key} />;
          case 'chart':
            return (
              <Fragment key={key}>
                <Chart figure={block.figure_json} label={block.text} />
                {!!block.actions?.length && (
                  <ChartReplies
                    blockId={block.id}
                    actions={block.actions}
                    target={target}
                  />
                )}
              </Fragment>
            );
          case 'table':
            return (
              <AnswerTable
                block={block}
                target={target}
                copyText={copyText}
                key={key}
              />
            );
          case 'checklist':
            return <AnswerChecklist block={block} target={target} key={key} />;
          case 'figures':
            return <AnswerFigures block={block} target={target} key={key} />;
          case 'progress':
            return <AnswerProgress block={block} target={target} key={key} />;
          case 'choices':
            return <AnswerChoices block={block} target={target} key={key} />;
          case 'visual':
            return (
              <AnswerVisual
                block={block}
                conversationId={conversationId}
                copyText={copyText}
                key={key}
              />
            );
          case 'surface':
            return (
              <SurfaceView
                json={block.surface_json}
                blockId={block.id}
                target={target}
                key={key}
              />
            );
          case 'youtube':
            return <YouTube block={block} key={key} />;
          case 'attachment':
            return <Attachment block={block} key={key} />;
        }
      })}
    </>
  );
}

export { CodeBlock };
