import {
  useEffect,
  useId,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import {
  Code2,
  Download,
  ExternalLink,
  Maximize2,
  Minus,
  Plus,
  RotateCcw,
  Table,
} from 'lucide-react';
import type { TranscriptRow } from '../../api/types';
import { loadLocalRuntimeScript } from '../../ui/local-runtime';
import { useOverlay } from '../../ui/overlays';
import { IconButton } from '../../ui/primitives';
import { useResolvedTheme } from '../../ui/theme';
import SafeMarkdown from './chat-parity-markdown';
import { chartLayout, figureTable } from './chart-embed';
import { CodeBlock } from './CodeBlock';
import {
  fileKind,
  formatBytes,
  MediaPreview,
  showsAsFileCard,
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
      downloadImage?(
        root: HTMLElement,
        options: Record<string, unknown>,
      ): Promise<unknown>;
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

function ChartTable({ data }: { data: unknown[] }) {
  const table = figureTable(data);
  if (!table) return <p>This chart has no tabular data.</p>;
  return (
    <div className="markdown-table-scroll chart-table" tabIndex={0}>
      <table>
        <thead>
          <tr>
            {table.headers.map((header, index) => (
              <th key={index}>{header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, index) => (
            <tr key={index}>
              {row.map((value, cell) => (
                <td key={cell}>{value}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Plot({
  figure,
  onFigure,
  className,
}: {
  figure: string;
  onFigure?: (value: { data: unknown[]; root: HTMLElement } | null) => void;
  className?: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const theme = useResolvedTheme();
  const [status, setStatus] = useState<'loading' | 'ready' | 'failed'>(
    'loading',
  );
  const report = useRef(onFigure);
  report.current = onFigure;
  useEffect(() => {
    let active = true;
    let observer: ResizeObserver | undefined;
    const element = root.current;
    setStatus('loading');
    void loadLocalRuntimeScript('plotly.min.js', () => Boolean(window.Plotly))
      .then(async () => {
        const parsed: unknown = JSON.parse(figure);
        if (
          !active ||
          !element ||
          !window.Plotly ||
          !parsed ||
          typeof parsed !== 'object' ||
          !Array.isArray((parsed as { data?: unknown }).data) ||
          typeof (parsed as { layout?: unknown }).layout !== 'object'
        )
          throw new Error('chart_invalid');
        const value = parsed as {
          data: unknown[];
          layout: Record<string, unknown>;
        };
        await window.Plotly.newPlot(
          element,
          value.data,
          chartLayout(value.layout, getComputedStyle(document.documentElement)),
          { responsive: true, displaylogo: false, displayModeBar: false },
        );
        if (!active) return;
        observer = new ResizeObserver(() =>
          window.Plotly?.Plots?.resize(element),
        );
        observer.observe(element);
        setStatus('ready');
        report.current?.({ data: value.data, root: element });
      })
      .catch(() => active && setStatus('failed'));
    return () => {
      active = false;
      observer?.disconnect();
      report.current?.(null);
      if (element && window.Plotly) window.Plotly.purge(element);
    };
  }, [figure, theme]);
  return (
    <>
      <div
        ref={root}
        className={`rich-chart-plot ${className ?? ''}`}
        aria-hidden={status !== 'ready'}
        data-status={status}
      />
      {status === 'loading' && (
        <p role="status" className="rich-block-status">
          Rendering chart…
        </p>
      )}
      {status === 'failed' && (
        <p role="alert" className="rich-block-status">
          Chart preview is unavailable.
        </p>
      )}
    </>
  );
}

function Chart({ figure, label }: { figure: string; label: string }) {
  const overlay = useOverlay();
  const [plot, setPlot] = useState<{
    data: unknown[];
    root: HTMLElement;
  } | null>(null);
  const [showTable, setShowTable] = useState(false);
  const title = (() => {
    try {
      const layout = (JSON.parse(figure) as { layout?: { title?: unknown } })
        .layout;
      const value = layout?.title;
      return typeof value === 'string'
        ? value
        : typeof (value as { text?: unknown })?.text === 'string'
          ? (value as { text: string }).text
          : '';
    } catch {
      return '';
    }
  })();
  return (
    <figure className="rich-block rich-chart">
      <div className="rich-block-header">
        <span className="rich-block-title">{title || label || 'Chart'}</span>
        <span className="rich-block-tools">
          <IconButton
            size="sm"
            label={showTable ? 'Show chart' : 'Show data table'}
            pressed={showTable}
            disabled={!plot}
            onClick={() => setShowTable((value) => !value)}
          >
            <Table size={15} aria-hidden />
          </IconButton>
          <IconButton
            size="sm"
            label="Download PNG"
            disabled={!plot || !window.Plotly?.downloadImage}
            onClick={() =>
              plot &&
              void window.Plotly?.downloadImage?.(plot.root, {
                format: 'png',
                filename: (title || 'chart').slice(0, 60),
                scale: 2,
              })
            }
          >
            <Download size={15} aria-hidden />
          </IconButton>
          <IconButton
            size="sm"
            label="Expand chart"
            disabled={!plot}
            onClick={() =>
              overlay.open({
                title: title || label || 'Chart',
                description: '',
                className: 'chart-dialog',
                content: <Plot figure={figure} className="rich-chart-large" />,
              })
            }
          >
            <Maximize2 size={15} aria-hidden />
          </IconButton>
        </span>
      </div>
      <div hidden={showTable}>
        <Plot figure={figure} onFigure={setPlot} />
      </div>
      {showTable && plot && <ChartTable data={plot.data} />}
      <figcaption className="rich-block-caption">{label || 'Chart'}</figcaption>
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
      <MediaPreview
        reference={block.attachment_ref}
        mime={block.mime_type}
        label={block.name}
        downloadName={block.name}
        size={block.size_bytes}
      />
    </figure>
  );
}

export function publicBlockText(block: Block) {
  switch (block.type) {
    case 'text':
    case 'markdown':
    case 'mermaid':
    case 'chart':
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
}: {
  blocks: TranscriptRow['blocks'];
  copyText?: (value: string) => Promise<boolean>;
}) {
  return (
    <>
      {blocks.map((block, index) => {
        const key = 'id' in block ? block.id : `stream-${index}`;
        if (block.type === 'text' || block.type === 'markdown')
          return (
            <SafeMarkdown text={block.text} copyText={copyText} key={key} />
          );
        if (block.type === 'mermaid')
          return <Mermaid source={block.source} key={key} />;
        if (block.type === 'chart')
          return (
            <Chart figure={block.figure_json} label={block.text} key={key} />
          );
        if (block.type === 'youtube')
          return <YouTube block={block} key={key} />;
        return <Attachment block={block} key={key} />;
      })}
    </>
  );
}

export { CodeBlock };
