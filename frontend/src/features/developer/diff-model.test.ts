import { describe, expect, it } from 'vitest';
import {
  foldUnchanged,
  gapBefore,
  parseUnifiedDiff,
  splitRows,
} from './diff-model';

const sample = [
  'diff --git a/app.py b/app.py',
  'index 1111111..2222222 100644',
  '--- a/app.py',
  '+++ b/app.py',
  '@@ -1,4 +1,5 @@ def main():',
  ' import os',
  '-print("old")',
  '+print("new")',
  '+print("more")',
  ' ',
  ' done()',
  '@@ -20,2 +21,2 @@',
  '-x = 1',
  '+x = 2',
  ' y = 3',
  '\\ No newline at end of file',
  '',
].join('\n');

describe('parseUnifiedDiff', () => {
  it('numbers lines per side and counts changes', () => {
    const diff = parseUnifiedDiff(sample);
    expect(diff.additions).toBe(3);
    expect(diff.deletions).toBe(2);
    expect(diff.hunks).toHaveLength(2);
    const [first, second] = diff.hunks;
    expect(first.section).toBe('def main():');
    expect(first.lines.map((line) => line.kind)).toEqual([
      'context',
      'del',
      'add',
      'add',
      'context',
      'context',
    ]);
    expect(first.lines[1]).toMatchObject({
      text: 'print("old")',
      oldNumber: 2,
      newNumber: null,
    });
    expect(first.lines[3]).toMatchObject({ oldNumber: null, newNumber: 3 });
    expect(first.lines[5]).toMatchObject({ oldNumber: 4, newNumber: 5 });
    expect(second.lines.at(-1)).toMatchObject({
      kind: 'note',
      text: 'No newline at end of file',
    });
  });

  it('treats an untracked file as one added hunk from line 1', () => {
    const diff = parseUnifiedDiff('+first\n+second');
    expect(diff.additions).toBe(2);
    expect(diff.hunks[0].lines.map((line) => line.newNumber)).toEqual([1, 2]);
    expect(diff.hunks[0].newLines).toBe(2);
  });

  it('keeps a page that starts inside a hunk without inventing numbers', () => {
    const diff = parseUnifiedDiff(' kept\n-gone\n+new');
    expect(diff.hunks[0].header).toBe('');
    expect(diff.hunks[0].lines.every((line) => line.newNumber === null)).toBe(
      true,
    );
    expect(diff.hunks[0].lines.every((line) => line.oldNumber === null)).toBe(
      true,
    );
  });

  it('reports binary changes and ignores CRLF', () => {
    expect(
      parseUnifiedDiff(
        'diff --git a/logo.png b/logo.png\r\nBinary files a/logo.png and b/logo.png differ\r\n',
      ).binary,
    ).toBe(true);
    expect(parseUnifiedDiff('').hunks).toEqual([]);
  });
});

describe('foldUnchanged', () => {
  it('folds long unchanged runs and keeps context near changes', () => {
    const lines = parseUnifiedDiff(
      [
        '@@ -1,12 +1,12 @@',
        ...Array.from({ length: 10 }, (_, index) => ` line ${index + 1}`),
        '-old',
        '+new',
        ' tail',
      ].join('\n'),
    ).hunks[0].lines;
    const segments = foldUnchanged(lines, 3);
    expect(segments.map((segment) => segment.kind)).toEqual([
      'fold',
      'lines',
      'lines',
      'lines',
    ]);
    expect(segments[0].lines).toHaveLength(7);
    expect(segments[1].lines.map((line) => line.text)).toEqual([
      'line 8',
      'line 9',
      'line 10',
    ]);
  });

  it('keeps short runs visible', () => {
    const lines = parseUnifiedDiff('@@ -1,3 +1,3 @@\n a\n-b\n+c\n d').hunks[0]
      .lines;
    expect(
      foldUnchanged(lines).every((segment) => segment.kind === 'lines'),
    ).toBe(true);
  });
});

describe('gapBefore and splitRows', () => {
  it('measures the unchanged lines Git left out between hunks', () => {
    const diff = parseUnifiedDiff(sample);
    expect(gapBefore(diff.hunks, 0)).toBe(0);
    expect(gapBefore(diff.hunks, 1)).toBe(15);
  });

  it('pairs removed and added lines side by side', () => {
    const rows = splitRows(parseUnifiedDiff(sample).hunks[0].lines);
    expect(
      rows.map((row) => [row.left?.text ?? null, row.right?.text ?? null]),
    ).toEqual([
      ['import os', 'import os'],
      ['print("old")', 'print("new")'],
      [null, 'print("more")'],
      ['', ''],
      ['done()', 'done()'],
    ]);
  });
});
