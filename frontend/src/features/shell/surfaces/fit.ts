/**
 * Tables and charts fitted to the rows Row-Bot fills in (generative UI, G8).
 * A model often names a source's columns without seeing the file; the real
 * rows decide which columns there are. The same rules as the server's
 * `surfaces.fit_columns` and `surfaces.chart_points`, which give the words.
 */

export type Column = { key: string; label: unknown; type: string };
type Row = Record<string, unknown>;

function objects(rows: unknown): Row[] {
  return Array.isArray(rows)
    ? rows.filter(
        (row): row is Row =>
          !!row && typeof row === 'object' && !Array.isArray(row),
      )
    : [];
}

/** The columns rows of objects have, in the order they first appear. */
export function rowKeys(rows: unknown): string[] {
  const keys: string[] = [];
  for (const row of objects(rows).slice(0, 20))
    for (const key of Object.keys(row)) if (!keys.includes(key)) keys.push(key);
  return keys;
}

function numeric(rows: Row[], key: string): boolean {
  const values = rows
    .map((row) => row[key])
    .filter((value) => value !== null && value !== undefined && value !== '');
  return (
    values.length > 0 &&
    values.every((value) => typeof value === 'number' && Number.isFinite(value))
  );
}

/** "check_in" -> "Check in". */
export function keyWords(key: string): string {
  const words = key
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/[_-]/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .join(' ');
  return words.slice(0, 1).toUpperCase() + words.slice(1).toLowerCase();
}

/** A table's columns: as drawn when the rows have every one, else the rows' own (the drawn label kept). */
export function fitColumns(columns: Column[], rows: unknown): Column[] {
  const keys = rowKeys(rows);
  if (
    !keys.length ||
    (columns.length && columns.every((column) => keys.includes(column.key)))
  )
    return columns;
  const drawn = new Map(columns.map((column) => [column.key, column]));
  const all = objects(rows);
  return keys.slice(0, 12).map(
    (key) =>
      drawn.get(key) ?? {
        key,
        label: keyWords(key),
        type: numeric(all, key) ? 'number' : 'text',
      },
  );
}

/**
 * A chart over rows: x and each series' values name columns, fitted to the
 * rows' real ones (the first text column for x, the number columns for
 * values). Null when the chart isn't over rows.
 */
export function chartOverRows(
  rows: unknown,
  x: unknown,
  series: { name: string; values: unknown }[],
): { x: unknown[]; series: { name: string; values: unknown[] }[] } | null {
  const all = objects(rows).slice(0, 200);
  const keys = rowKeys(all);
  if (!keys.length) return null;
  const numbers = keys.filter((key) => numeric(all, key));
  const xKey =
    typeof x === 'string' && keys.includes(x)
      ? x
      : (keys.find((key) => !numbers.includes(key)) ?? keys[0]);
  let named = series
    .filter(
      (item): item is { name: string; values: string } =>
        typeof item.values === 'string' && keys.includes(item.values),
    )
    .map((item) => ({
      name: item.name || keyWords(item.values),
      key: item.values,
    }));
  if (!named.length)
    named = numbers
      .filter((key) => key !== xKey)
      .slice(0, 4)
      .map((key) => ({ name: keyWords(key), key }));
  return {
    x: all.map((row) => row[xKey]),
    series: named.map((item) => ({
      name: item.name,
      values: all.map((row) => row[item.key]),
    })),
  };
}
