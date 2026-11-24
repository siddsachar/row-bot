import { useEffect, useRef, useState } from 'react';
import { Download, Maximize2, Table } from 'lucide-react';
import { loadLocalRuntimeScript } from '../../ui/local-runtime';
import { useOverlay } from '../../ui/overlays';
import { IconButton } from '../../ui/primitives';
import { useResolvedTheme } from '../../ui/theme';
import { chartLayout, chartTraces, figureTable } from './chart-embed';
import { saveProblem, useSaveFile } from './save-file';

/**
 * An inline Plotly chart: the app's theme, a data-table view, full screen and
 * saving as a picture. Used by a reply's chart blocks and by surfaces.
 */
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
          chartTraces(value.data),
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

export function Chart({ figure, label }: { figure: string; label: string }) {
  const overlay = useOverlay();
  const saveFile = useSaveFile();
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
  const caption = label.split('\n')[0] || 'Chart';
  // The chart as a PNG, saved the one way every download saves (5.3.3).
  async function savePicture() {
    if (!plot || !saveFile || !window.Plotly?.toImage) return;
    let picture: string;
    try {
      picture = await window.Plotly.toImage(plot.root, {
        format: 'png',
        scale: 2,
      });
    } catch {
      overlay.notify('Couldn’t make a picture of the chart.', 'warning');
      return;
    }
    const name = `${(title || 'chart').replace(/[^A-Za-z0-9 ._-]+/g, '-').slice(0, 60)}.png`;
    const result = await saveFile({
      kind: 'content',
      name,
      content_base64: picture.slice(picture.indexOf(',') + 1),
    });
    const problem = saveProblem(result, 'the chart');
    if (problem) overlay.notify(problem, 'warning');
  }
  return (
    <figure className="rich-block rich-chart">
      <div className="rich-block-header">
        <span className="rich-block-title">{title || caption}</span>
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
          {saveFile && (
            <IconButton
              size="sm"
              label="Download PNG"
              disabled={!plot || !window.Plotly?.toImage}
              onClick={() => void savePicture()}
            >
              <Download size={15} aria-hidden />
            </IconButton>
          )}
          <IconButton
            size="sm"
            label="Expand chart"
            disabled={!plot}
            onClick={() =>
              overlay.open({
                title: title || caption,
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
      {/* The text's first line; the rest is the data, for copying. */}
      {caption !== title && (
        <figcaption className="rich-block-caption">{caption}</figcaption>
      )}
    </figure>
  );
}
