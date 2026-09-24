import { useEffect, useId, useRef, useState } from 'react';
import type { TranscriptRow } from '../../api/types';
import { Button } from '../../ui/primitives';
import { loadLocalRuntimeScript } from '../../ui/local-runtime';
import SafeMarkdown from './chat-parity-markdown';
import { MediaPreview } from './MediaPreview';

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
      Plots?: { resize(root: HTMLElement): void };
    };
  }
}

function Mermaid({ source }: { source: string }) {
  const root = useRef<HTMLDivElement>(null);
  const identity = useId().replaceAll(':', '');
  const [status, setStatus] = useState<'loading' | 'ready' | 'failed'>(
    'loading',
  );
  useEffect(() => {
    let active = true;
    const element = root.current;
    void loadLocalRuntimeScript('mermaid.min.js', () => Boolean(window.mermaid))
      .then(async () => {
        if (!active || !element || !window.mermaid) return;
        window.mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          suppressErrorRendering: true,
          theme: 'dark',
        });
        const result = await window.mermaid.render(
          `mermaid-${identity}`,
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
  }, [identity, source]);
  return (
    <figure className="rich-block rich-mermaid">
      <div ref={root} aria-hidden={status !== 'ready'} />
      {status === 'loading' && <p role="status">Rendering diagram…</p>}
      {status === 'failed' && (
        <pre className="rich-fallback">
          <code data-language="mermaid">{source}</code>
        </pre>
      )}
      <figcaption>Mermaid diagram</figcaption>
    </figure>
  );
}

function Chart({ figure, label }: { figure: string; label: string }) {
  const root = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'failed'>(
    'loading',
  );
  useEffect(() => {
    let active = true;
    let observer: ResizeObserver | undefined;
    const element = root.current;
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
          { ...value.layout, autosize: true },
          { responsive: true, displaylogo: false },
        );
        if (!active) return;
        observer = new ResizeObserver(() =>
          window.Plotly?.Plots?.resize(element),
        );
        observer.observe(element);
        setStatus('ready');
      })
      .catch(() => active && setStatus('failed'));
    return () => {
      active = false;
      observer?.disconnect();
      if (element && window.Plotly) window.Plotly.purge(element);
    };
  }, [figure]);
  return (
    <figure className="rich-block rich-chart">
      <div ref={root} aria-hidden={status !== 'ready'} />
      {status === 'loading' && <p role="status">Rendering chart…</p>}
      {status === 'failed' && <p role="alert">Chart preview is unavailable.</p>}
      <figcaption>{label || 'Chart'}</figcaption>
    </figure>
  );
}

function YouTube({ block }: { block: Extract<Block, { type: 'youtube' }> }) {
  const [consented, setConsented] = useState(false);
  return (
    <figure className="rich-block rich-youtube">
      {consented ? (
        <iframe
          title={block.title}
          src={`https://www.youtube-nocookie.com/embed/${block.video_id}`}
          loading="lazy"
          referrerPolicy="no-referrer"
          sandbox="allow-scripts allow-same-origin allow-presentation"
          allowFullScreen
        />
      ) : (
        <div className="external-content-card">
          <strong>{block.title}</strong>
          <p>Loading this player contacts YouTube.</p>
          <Button onClick={() => setConsented(true)}>
            Load YouTube player
          </Button>
          <a href={block.url} target="_blank" rel="noreferrer noopener">
            Open video link
          </a>
        </div>
      )}
      <figcaption>YouTube video</figcaption>
    </figure>
  );
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function Attachment({
  block,
}: {
  block: Extract<Block, { type: 'attachment' }>;
}) {
  return (
    <figure className="rich-block rich-attachment">
      <figcaption>
        <strong>{block.name}</strong>
        <span>
          {formatBytes(block.size_bytes)} · {block.mime_type}
        </span>
      </figcaption>
      <MediaPreview
        reference={block.attachment_ref}
        mime={block.mime_type}
        label={block.name}
        downloadName={block.name}
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
