import { expect, it } from 'vitest';
import { columnType, sortedOrder, type ColumnType } from './sortable-table';

function sorted(values: string[], direction: 'ascending' | 'descending') {
  const rows = values.map((value) => [value]);
  const types: ColumnType[] = [columnType(values)];
  return sortedOrder(rows, types, { column: 0, direction }).map(
    (index) => values[index],
  );
}

it('reads each column as the narrowest type every filled cell fits', () => {
  expect(columnType(['12', '1,200', '-3.5', '—'])).toBe('number');
  expect(columnType(['£950', '£1,200', '(£40)'])).toBe('currency');
  expect(columnType(['12%', '-3.5%', ''])).toBe('percent');
  expect(columnType(['2026-11-09', '2026-11-12'])).toBe('date');
  expect(columnType(['9 Nov 2026', 'Nov 12, 2026', 'March 2026'])).toBe('date');
  expect(columnType(['12', 'twelve'])).toBe('text');
  expect(columnType(['v1.2.3', '10 GB'])).toBe('text');
});

it('sorts numbers, money, percentages and dates by value, not by text', () => {
  expect(sorted(['1,200', '950', '-3', '12.5'], 'ascending')).toEqual([
    '-3',
    '12.5',
    '950',
    '1,200',
  ]);
  expect(sorted(['£950', '£1,200', '(£40)', '1.5k'], 'descending')).toEqual([
    '1.5k',
    '£1,200',
    '£950',
    '(£40)',
  ]);
  expect(sorted(['12%', '-3.5%', '100%'], 'ascending')).toEqual([
    '-3.5%',
    '12%',
    '100%',
  ]);
  expect(
    sorted(['12 Nov 2026', '9 Nov 2026', '1 Jan 2026'], 'ascending'),
  ).toEqual(['1 Jan 2026', '9 Nov 2026', '12 Nov 2026']);
});

it('sorts text naturally and keeps empty cells last in either direction', () => {
  expect(sorted(['item 10', 'Item 2', '—', 'item 1'], 'ascending')).toEqual([
    'item 1',
    'Item 2',
    'item 10',
    '—',
  ]);
  expect(sorted(['3', '', '10', 'n/a'], 'descending')).toEqual([
    '10',
    '3',
    '',
    'n/a',
  ]);
});

it('keeps equal rows in the answer’s own order', () => {
  const rows = [
    ['b', '1'],
    ['a', '1'],
    ['c', '0'],
  ];
  expect(
    sortedOrder(rows, ['text', 'number'], {
      column: 1,
      direction: 'descending',
    }),
  ).toEqual([0, 1, 2]);
  expect(sortedOrder(rows, ['text', 'number'], null)).toEqual([0, 1, 2]);
});
