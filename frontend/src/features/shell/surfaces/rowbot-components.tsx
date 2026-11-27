import { useId } from 'react';
import { withUnit } from '../answer-components/AnswerComponents';
import { CodeBlock } from '../CodeBlock';
import { Chart } from '../RichChart';
import { SortableTable } from '../SortableTable';
import {
  asText,
  boundPath,
  evaluate,
  formatDate,
  type Scope,
  type SurfaceComponent,
} from './a2ui';
import { text, weighted, type Context } from './context';
import { chartOverRows, columnDecimals, fitColumns, type Column } from './fit';

/**
 * Row-Bot's own surface components (G2), beyond A2UI's basic catalog:
 * charts, tables, metrics, progress, an editable breakdown, a timeline and
 * code. Their values bind to the surface's data model like any other, so a
 * chart or a total follows a slider while the person moves it.
 */
export const ROWBOT_COMPONENTS = new Set([
  'Chart',
  'Table',
  'Metric',
  'ProgressBar',
  'Breakdown',
  'Timeline',
  'Code',
]);

type Props = { component: SurfaceComponent; context: Context; scope: Scope };

export function RowBotComponent(props: Props) {
  switch (props.component.component) {
    case 'Chart':
      return <SurfaceChart {...props} />;
    case 'Table':
      return <SurfaceTable {...props} />;
    case 'Metric':
      return <Metric {...props} />;
    case 'ProgressBar':
      return <ProgressBar {...props} />;
    case 'Breakdown':
      return <Breakdown {...props} />;
    case 'Timeline':
      return <Timeline {...props} />;
    case 'Code':
      return (
        <div className="genui-code" style={weighted(props.component)}>
          <CodeBlock
            language={String(props.component.language ?? '')}
            text={text(props.component.code, props.context, props.scope)}
          />
        </div>
      );
    default:
      return null;
  }
}

const CURRENCY = new Set(['£', '$', '€', '¥', '₹', '₩', '₽', '₺', '₪']);

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function numbers(value: unknown): (number | null)[] {
  return list(value).map((item) =>
    typeof item === 'number' && Number.isFinite(item)
      ? item
      : typeof item === 'string' && item.trim() && Number.isFinite(Number(item))
        ? Number(item)
        : null,
  );
}

function SurfaceChart({ component, context, scope }: Props) {
  const kind = String(component.kind);
  const title = text(component.title, context, scope);
  const drawn = (
    (component.series as { name: unknown; values: unknown }[]) ?? []
  ).map((item) => ({
    name: text(item.name, context, scope),
    values: item.values,
  }));
  // Over rows (a source's CSV, say), x and the values name columns.
  const overRows = chartOverRows(
    evaluate(component.rows, context.model, scope),
    component.x,
    drawn,
  );
  const x = (
    overRows ? overRows.x : list(evaluate(component.x, context.model, scope))
  ).map(asText);
  const series = overRows
    ? overRows.series.map((item) => ({
        name: item.name,
        values: numbers(item.values),
      }))
    : drawn.map((item) => ({
        name: item.name,
        values: numbers(evaluate(item.values, context.model, scope)),
      }));
  const unit = String(component.unit ?? '');
  const data =
    kind === 'pie'
      ? [
          {
            type: 'pie',
            labels: x,
            values: series[0]?.values ?? [],
            hole: 0.45,
            sort: false,
          },
        ]
      : series.map((item) => ({
          type: kind === 'bar' ? 'bar' : 'scatter',
          name: item.name,
          x,
          y: item.values,
          ...(kind === 'line' ? { mode: 'lines+markers' } : {}),
          ...(kind === 'area'
            ? {
                mode: 'lines',
                fill: component.stacked === true ? 'tonexty' : 'tozeroy',
                ...(component.stacked === true ? { stackgroup: 'one' } : {}),
              }
            : {}),
        }));
  const axis: Record<string, unknown> = { automargin: true };
  if (unit)
    axis[CURRENCY.has(unit) ? 'tickprefix' : 'ticksuffix'] =
      CURRENCY.has(unit) || unit === '%' ? unit : ` ${unit}`;
  const figure = JSON.stringify({
    data,
    layout: {
      ...(title ? { title: { text: title } } : {}),
      showlegend: kind === 'pie' || series.length > 1,
      ...(kind === 'bar'
        ? { barmode: component.stacked === true ? 'stack' : 'group' }
        : {}),
      xaxis: { automargin: true },
      yaxis: axis,
    },
  });
  // Without a title the chart is headed by what it shows, never by its first point.
  const heading =
    title ||
    series
      .map((item) => item.name)
      .filter(Boolean)
      .join(', ') ||
    'Chart';
  const words = x
    .map(
      (point, index) =>
        `${point}: ${series
          .map((item) => `${item.name} ${asText(item.values[index])}`.trim())
          .join(', ')}`,
    )
    .join('\n');
  return (
    <div className="genui-chart" style={weighted(component)}>
      <Chart figure={figure} label={`${heading}\n${words}`} />
    </div>
  );
}

function cell(value: unknown, type: string, decimals = 0): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'number' && decimals > 0 && type !== 'percent')
    return value.toLocaleString(undefined, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  if (type === 'percent' && typeof value === 'number')
    return `${asText(value)}%`;
  if (type === 'date' && typeof value === 'string') {
    const shown = formatDate(value, 'd MMM yyyy');
    return shown || value;
  }
  return asText(value);
}

function SurfaceTable({ component, context, scope }: Props) {
  const title = text(component.title, context, scope);
  const all = list(evaluate(component.rows, context.model, scope));
  const columns = fitColumns((component.columns as Column[]) ?? [], all);
  const decimals = columns.map((column) => columnDecimals(all, column.key));
  const rows = all
    .slice(0, 200)
    .map((row) =>
      columns.map((column, index) =>
        row && typeof row === 'object'
          ? cell(
              (row as Record<string, unknown>)[column.key],
              column.type,
              decimals[index],
            )
          : '',
      ),
    );
  return (
    <div className="genui-table" style={weighted(component)}>
      {title && <p className="genui-block-title">{title}</p>}
      <SortableTable
        label={title || undefined}
        headers={columns.map((column) => text(column.label, context, scope))}
        rows={rows}
      />
    </div>
  );
}

function Metric({ component, context, scope }: Props) {
  const value = evaluate(component.value, context.model, scope);
  const unit = String(component.unit ?? '');
  const delta = text(component.delta, context, scope);
  const shown =
    typeof value === 'number'
      ? withUnit(value, unit)
      : withUnit(asText(value), unit);
  return (
    <div
      className="genui-metric"
      data-tone={String(component.tone ?? 'neutral')}
      style={weighted(component)}
    >
      <span className="genui-metric-label">
        {text(component.label, context, scope)}
      </span>
      <strong className="genui-metric-value">{shown}</strong>
      {delta && <span className="genui-metric-delta">{delta}</span>}
    </div>
  );
}

function ProgressBar({ component, context, scope }: Props) {
  const label = text(component.label, context, scope);
  const value = Number(evaluate(component.value, context.model, scope)) || 0;
  const max = Number(evaluate(component.max, context.model, scope)) || 0;
  const unit = String(component.unit ?? '');
  const share = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  return (
    <div className="genui-progress" style={weighted(component)}>
      <div className="genui-progress-line">
        <span>{label}</span>
        <span className="genui-progress-value">
          {withUnit(value, unit)} of {withUnit(max, unit)}
        </span>
      </div>
      <div
        className="genui-progress-bar"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={value}
        data-over={max > 0 && value > max ? 'true' : undefined}
      >
        <span style={{ width: `${share * 100}%` }} />
      </div>
    </div>
  );
}

const SERIES = 6;
const colour = (index: number) =>
  index < SERIES ? `var(--chart-series-${index + 1})` : 'var(--text-muted)';

/** The smallest of 1, 2, 2.5, 5 times a power of ten at or above the value. */
function niceCeil(value: number): number {
  if (!(value > 0)) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].find((item) => value / power <= item) ?? 10;
  return step * power;
}

function amountText(value: number, currency: string, unit: string) {
  if (currency) {
    try {
      return new Intl.NumberFormat(undefined, {
        style: 'currency',
        currency,
        currencyDisplay: 'narrowSymbol',
        maximumFractionDigits: Number.isInteger(value) ? 0 : 2,
      }).format(value);
    } catch {
      return `${currency} ${asText(value)}`;
    }
  }
  return withUnit(value, unit);
}

/** Parts that add up to a total, each with its own slider and colour. */
function Breakdown({ component, context, scope }: Props) {
  const id = useId();
  const path = boundPath(component.items);
  const items = list(evaluate(component.items, context.model, scope))
    .filter(
      (item): item is Record<string, unknown> =>
        !!item && typeof item === 'object',
    )
    .slice(0, 12)
    .map((item) => ({
      label: asText(item.label),
      amount:
        typeof item.amount === 'number' && Number.isFinite(item.amount)
          ? item.amount
          : Number(item.amount) || 0,
    }));
  const currency = String(component.currency ?? '');
  const unit = String(component.unit ?? '');
  const total = items.reduce((sum, item) => sum + Math.max(0, item.amount), 0);
  const top =
    typeof component.max === 'number' && component.max > 0
      ? component.max
      : niceCeil(Math.max(...items.map((item) => item.amount), 1) * 2);
  const change = (index: number, amount: number) => {
    if (!path) return;
    context.set(
      path,
      items.map((item, position) =>
        position === index ? { ...item, amount } : item,
      ),
      scope,
    );
  };
  const totalLabel = text(component.totalLabel, context, scope) || 'Total';
  return (
    <div className="genui-breakdown" style={weighted(component)}>
      <div className="genui-breakdown-bar" aria-hidden>
        {items.map((item, index) => (
          <span
            key={index}
            style={{
              flexGrow: Math.max(0, item.amount),
              background: colour(index),
            }}
          />
        ))}
      </div>
      <ul>
        {items.map((item, index) => (
          <li key={index}>
            <span
              className="genui-breakdown-swatch"
              style={{ background: colour(index) }}
              aria-hidden
            />
            <label htmlFor={`${id}-${index}`}>{item.label}</label>
            <input
              id={`${id}-${index}`}
              type="range"
              min={0}
              max={top}
              step={top / 100}
              value={item.amount}
              disabled={!path}
              onChange={(event) =>
                change(index, Number(event.currentTarget.value))
              }
            />
            <output htmlFor={`${id}-${index}`}>
              {amountText(item.amount, currency, unit)}
            </output>
          </li>
        ))}
      </ul>
      <p className="genui-breakdown-total">
        <span>{totalLabel}</span>
        <strong>{amountText(total, currency, unit)}</strong>
      </p>
    </div>
  );
}

function Timeline({ component, context, scope }: Props) {
  const items = list(evaluate(component.items, context.model, scope))
    .filter(
      (item): item is Record<string, unknown> =>
        !!item && typeof item === 'object',
    )
    .slice(0, 50);
  return (
    <ol className="genui-timeline" style={weighted(component)}>
      {items.map((item, index) => (
        <li key={index}>
          <span className="genui-timeline-time">{asText(item.time)}</span>
          <span className="genui-timeline-title">{asText(item.title)}</span>
          {item.detail !== undefined && item.detail !== '' && (
            <span className="genui-timeline-detail">{asText(item.detail)}</span>
          )}
        </li>
      ))}
    </ol>
  );
}
