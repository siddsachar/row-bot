/**
 * Sorting for the tables in answers: a Markdown table and a rowbot-table
 * component share it. Each column's type is read from its cells (numbers,
 * currency, percentages, dates, else text) so "£1,200" sorts after "£950"
 * and "9 Nov 2026" before "12 Nov 2026". Empty cells stay last either way.
 */
export type ColumnType = 'text' | 'number' | 'currency' | 'percent' | 'date';
export type SortDirection = 'ascending' | 'descending';
export type TableSort = { column: number; direction: SortDirection } | null;

/** Plain cell text: inline Markdown markers removed. */
export function plainCell(value: string) {
  return value
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\(([^\s)]+)\)/g, '$1')
    .replace(/(\*\*|__|~~)/g, '')
    .trim();
}

// A cell that says "nothing here" sorts with the empty ones.
const PLACEHOLDER = /^(?:|-|–|—|n\/?a|none|null|\?)$/i;
const CURRENCY = /^(?:[£$€¥₹₩₽₺₪]|[A-Z]{3}\s)|(?:[£$€¥₹]|\s[A-Z]{3})$/;
const NUMBER =
  /^[-+−]?\(?\s*(?:[£$€¥₹₩₽₺₪]|[A-Z]{3}\s)?\s*[-+−]?(?:\d{1,3}(?:[,  ]\d{3})+|\d+)(?:\.\d+)?\s*(?:[kKmMbB]|bn)?\s*(?:[£$€¥₹]|\s[A-Z]{3})?\s*%?\)?$/;
const SCALE: Record<string, number> = {
  k: 1e3,
  m: 1e6,
  b: 1e9,
  bn: 1e9,
};
const ISO_DATE = /^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?$/;
const WORD_DATE =
  /^(?:\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|[A-Za-z]{3,9}\.?\s+\d{4})$/;

function isEmpty(value: string) {
  return PLACEHOLDER.test(value.trim());
}

/** The number a cell shows, or null when it is not one number. */
export function cellNumber(value: string): number | null {
  const text = value.trim();
  if (!NUMBER.test(text)) return null;
  const negative = /^[-−]|^\(.*\)$|^\(?\s*\D*[-−]\d/.test(text);
  const scale = /(bn|[kKmMbB])\s*\D*%?\)?$/.exec(text);
  const digits = text.replace(/[^\d.]/g, '');
  const number = Number.parseFloat(digits);
  if (!Number.isFinite(number)) return null;
  const sized = scale ? number * (SCALE[scale[1].toLowerCase()] ?? 1) : number;
  return negative ? -sized : sized;
}

/** The time a cell names, or null when it is not a date. */
export function cellDate(value: string): number | null {
  const text = value.trim();
  if (!ISO_DATE.test(text) && !WORD_DATE.test(text)) return null;
  const time = Date.parse(text);
  return Number.isFinite(time) ? time : null;
}

/** A column's type from its cells: the narrowest one every filled cell fits. */
export function columnType(values: string[]): ColumnType {
  const filled = values.map(plainCell).filter((value) => !isEmpty(value));
  if (!filled.length) return 'text';
  if (filled.every((value) => cellNumber(value) !== null)) {
    if (filled.every((value) => value.trim().endsWith('%'))) return 'percent';
    if (filled.some((value) => CURRENCY.test(value.trim()))) return 'currency';
    return 'number';
  }
  if (filled.every((value) => cellDate(value) !== null)) return 'date';
  return 'text';
}

export function isNumeric(type: ColumnType) {
  return type === 'number' || type === 'currency' || type === 'percent';
}

/** Header clicks go ascending, descending, then back to the answer's own order. */
export function nextSort(current: TableSort, column: number): TableSort {
  if (current?.column !== column) return { column, direction: 'ascending' };
  return current.direction === 'ascending'
    ? { column, direction: 'descending' }
    : null;
}

const collator = new Intl.Collator(undefined, {
  numeric: true,
  sensitivity: 'base',
});

/** The rows' order (their indexes) for a sort; stable, empty cells last. */
export function sortedOrder(
  rows: string[][],
  types: ColumnType[],
  sort: TableSort,
): number[] {
  const order = rows.map((_, index) => index);
  if (!sort) return order;
  const type = types[sort.column] ?? 'text';
  const key = (row: string[]): number | string | null => {
    const value = plainCell(row[sort.column] ?? '');
    if (isEmpty(value)) return null;
    if (isNumeric(type)) return cellNumber(value);
    if (type === 'date') return cellDate(value);
    return value;
  };
  const keys = rows.map(key);
  const sign = sort.direction === 'ascending' ? 1 : -1;
  return order.sort((left, right) => {
    const a = keys[left];
    const b = keys[right];
    if (a === null || b === null)
      return a === b ? left - right : a === null ? 1 : -1;
    const compared =
      typeof a === 'number' && typeof b === 'number'
        ? a - b
        : collator.compare(String(a), String(b));
    return compared ? compared * sign : left - right;
  });
}

function csvField(value: string) {
  return /[",\n\r]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value;
}

export function tableCsv(headers: string[], rows: string[][]) {
  return [headers, ...rows]
    .map((row) =>
      headers
        .map((_, index) => csvField(plainCell(row[index] ?? '')))
        .join(','),
    )
    .join('\r\n');
}
