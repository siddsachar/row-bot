/**
 * Pure helpers for inline Plotly charts: a theme template drawn from the
 * app's chart tokens (so charts follow light and dark), and a data table
 * view of the figure's traces.
 */

type Trace = {
  type?: string;
  name?: string;
  x?: unknown[];
  y?: unknown[];
  labels?: unknown[];
  values?: unknown[];
};

function token(styles: CSSStyleDeclaration, name: string, fallback: string) {
  return styles.getPropertyValue(`--${name}`).trim() || fallback;
}

/** Layout overrides that replace the tool's own template with the app's. */
export function chartLayout(
  layout: Record<string, unknown>,
  styles: CSSStyleDeclaration,
): Record<string, unknown> {
  const text = token(styles, 'text-secondary', '#BCC4CF');
  const muted = token(styles, 'text-muted', '#98A2B1');
  const grid = token(styles, 'border-subtle', '#252C37');
  const colorway = [1, 2, 3, 4, 5, 6].map((index) =>
    token(styles, `chart-series-${index}`, '#92B5D8'),
  );
  const font = token(styles, 'font-sans', 'sans-serif');
  const axis = {
    gridcolor: grid,
    linecolor: grid,
    zerolinecolor: grid,
    tickcolor: grid,
    tickfont: { color: muted },
    title: { font: { color: muted } },
  };
  const template = {
    layout: {
      colorway,
      font: { family: font, color: text, size: 12 },
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      xaxis: axis,
      yaxis: axis,
      legend: { font: { color: text }, bgcolor: 'rgba(0,0,0,0)' },
      title: { font: { color: token(styles, 'text-primary', '#E8EBF0') } },
    },
  };
  // A figure's own hover colours can be unreadable (a pale series name on light
  // grey, B294): the app's label wins over the tool's.
  const hoverlabel = {
    bgcolor: token(styles, 'surface-overlay', '#1C222C'),
    bordercolor: grid,
    font: { color: token(styles, 'text-primary', '#E8EBF0') },
    namelength: -1,
  };
  // The card header shows the title; the plot keeps its full height. A fixed
  // width or height would stop it filling the card or the Expand dialog (B290).
  const {
    template: _template,
    title: _title,
    width: _width,
    height: _height,
    ...rest
  } = layout;
  void _template;
  void _title;
  void _width;
  void _height;
  return {
    ...rest,
    template,
    hoverlabel,
    // The tool's figure may carry its own dark backgrounds; the card owns them.
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { family: font, color: text, size: 12 },
    margin: { l: 48, r: 16, t: 16, b: 40 },
    autosize: true,
  };
}

function cell(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'number')
    return Number.isInteger(value)
      ? value.toLocaleString('en-GB')
      : value.toLocaleString('en-GB', { maximumFractionDigits: 4 });
  return String(value);
}

/** Traces as one table: a shared x column where the traces agree. */
export function figureTable(data: unknown[]): {
  headers: string[];
  rows: string[][];
} | null {
  const traces = data.filter(
    (trace): trace is Trace => Boolean(trace) && typeof trace === 'object',
  );
  const pie = traces.find((trace) => trace.labels && trace.values);
  if (pie)
    return {
      headers: ['Label', pie.name || 'Value'],
      rows: (pie.labels ?? []).map((label, index) => [
        cell(label),
        cell(pie.values?.[index]),
      ]),
    };
  const series = traces.filter((trace) => Array.isArray(trace.y));
  if (!series.length) return null;
  const keys: unknown[] = [];
  const seen = new Set<string>();
  for (const trace of series)
    (trace.x ?? trace.y!.map((_, index) => index)).forEach((value) => {
      const key = String(value);
      if (!seen.has(key)) {
        seen.add(key);
        keys.push(value);
      }
    });
  if (keys.length > 500) keys.length = 500;
  return {
    headers: [
      'x',
      ...series.map((trace, index) => trace.name || `Series ${index + 1}`),
    ],
    rows: keys.map((key) => [
      cell(key),
      ...series.map((trace) => {
        const xs = trace.x ?? trace.y!.map((_, index) => index);
        const at = xs.findIndex((value) => String(value) === String(key));
        return at < 0 ? '' : cell(trace.y![at]);
      }),
    ]),
  };
}
