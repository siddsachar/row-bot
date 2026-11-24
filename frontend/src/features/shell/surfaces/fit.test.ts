import { describe, expect, it } from 'vitest';
import { chartOverRows, fitColumns } from './fit';

const SPEND = [
  { date: '2026-10-02', what: 'Groceries', amount: 61.9 },
  { date: '2026-10-11', what: 'Train', amount: 487.23 },
];

describe('tables and charts fit the rows Row-Bot fills in', () => {
  it('keeps the drawn columns when the rows have every one', () => {
    const drawn = [{ key: 'amount', label: 'Spent', type: 'currency' }];
    expect(fitColumns(drawn, SPEND)).toBe(drawn);
  });

  it("uses the rows' own columns when the model guessed one, keeping its labels", () => {
    const guessed = [
      { key: 'date', label: 'Day', type: 'date' },
      { key: 'category', label: 'Category', type: 'text' },
    ];
    expect(fitColumns(guessed, SPEND)).toEqual([
      { key: 'date', label: 'Day', type: 'date' },
      { key: 'what', label: 'What', type: 'text' },
      { key: 'amount', label: 'Amount', type: 'number' },
    ]);
    expect(fitColumns([], SPEND).map((column) => column.key)).toEqual([
      'date',
      'what',
      'amount',
    ]);
  });

  it('charts named columns over rows, and the number columns when the names are wrong', () => {
    expect(
      chartOverRows(SPEND, 'date', [{ name: 'Spent', values: 'amount' }]),
    ).toEqual({
      x: ['2026-10-02', '2026-10-11'],
      series: [{ name: 'Spent', values: [61.9, 487.23] }],
    });
    expect(
      chartOverRows(SPEND, 'day', [{ name: '', values: 'total' }]),
    ).toEqual({
      x: ['2026-10-02', '2026-10-11'],
      series: [{ name: 'Amount', values: [61.9, 487.23] }],
    });
    expect(chartOverRows(undefined, 'date', [])).toBeNull();
  });
});
