/**
 * Unified diff parsing for the Developer inspector. The server sends plain
 * `git diff` text (or `+`-prefixed lines for an untracked file), paged by
 * bytes, so a page may start or end inside a hunk. Parsing never throws: text
 * it cannot place becomes a line without numbers.
 */

export type DiffLineKind = 'context' | 'add' | 'del' | 'note';

export type DiffLine = {
  kind: DiffLineKind;
  /** The line without its leading marker. */
  text: string;
  oldNumber: number | null;
  newNumber: number | null;
};

export type DiffHunk = {
  /** The raw `@@ … @@` header, or '' for a page that starts mid-hunk. */
  header: string;
  /** Function or section context after the header, if Git printed one. */
  section: string;
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  lines: DiffLine[];
};

export type ParsedDiff = {
  hunks: DiffHunk[];
  additions: number;
  deletions: number;
  /** Git reported a binary change; there are no lines to show. */
  binary: boolean;
};

const HUNK = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$/;
const META =
  /^(?:diff --git |index |--- |\+\+\+ |new file mode |deleted file mode |old mode |new mode |similarity index |rename from |rename to |copy from |copy to |dissimilarity index )/;

export function parseUnifiedDiff(text: string): ParsedDiff {
  const result: ParsedDiff = {
    hunks: [],
    additions: 0,
    deletions: 0,
    binary: false,
  };
  if (!text) return result;
  const rows = text.replace(/\r\n/g, '\n').split('\n');
  if (rows.at(-1) === '') rows.pop();
  let hunk: DiffHunk | null = null;
  let oldLine = 0;
  let newLine = 0;
  // An untracked file (every line added) or a page that starts mid-hunk has
  // no header; numbers are only known for the untracked case.
  const headerless = !rows.some((row) => HUNK.test(row));
  const untracked =
    headerless && rows.length > 0 && rows.every((row) => row.startsWith('+'));
  for (const row of rows) {
    const header = HUNK.exec(row);
    if (header) {
      hunk = {
        header: row,
        section: header[5]?.trim() ?? '',
        oldStart: Number(header[1]),
        oldLines: header[2] === undefined ? 1 : Number(header[2]),
        newStart: Number(header[3]),
        newLines: header[4] === undefined ? 1 : Number(header[4]),
        lines: [],
      };
      oldLine = hunk.oldStart;
      newLine = hunk.newStart;
      result.hunks.push(hunk);
      continue;
    }
    if (!hunk) {
      if (/^Binary files .* differ$/.test(row)) {
        result.binary = true;
        continue;
      }
      if (META.test(row)) continue;
      hunk = {
        header: '',
        section: '',
        oldStart: 0,
        oldLines: 0,
        newStart: untracked ? 1 : 0,
        newLines: 0,
        lines: [],
      };
      oldLine = 0;
      newLine = untracked ? 1 : 0;
      result.hunks.push(hunk);
    }
    const known = hunk.header !== '' || untracked;
    const marker = row[0];
    if (row.startsWith('\\')) {
      hunk.lines.push({
        kind: 'note',
        text: row.slice(1).trim(),
        oldNumber: null,
        newNumber: null,
      });
    } else if (marker === '+') {
      result.additions++;
      hunk.lines.push({
        kind: 'add',
        text: row.slice(1),
        oldNumber: null,
        newNumber: known ? newLine++ : null,
      });
    } else if (marker === '-') {
      result.deletions++;
      hunk.lines.push({
        kind: 'del',
        text: row.slice(1),
        oldNumber: known ? oldLine++ : null,
        newNumber: null,
      });
    } else if (marker === ' ' || row === '') {
      hunk.lines.push({
        kind: 'context',
        text: row.slice(1),
        oldNumber: known ? oldLine++ : null,
        newNumber: known ? newLine++ : null,
      });
    } else if (META.test(row)) {
      // A second file header inside one page (rename/copy pairs).
      hunk = null;
    } else {
      hunk.lines.push({
        kind: 'note',
        text: row,
        oldNumber: null,
        newNumber: null,
      });
    }
  }
  if (untracked && result.hunks[0]) {
    result.hunks[0].newLines = result.additions;
  }
  return result;
}

/** A run of the lines Git kept around a change, possibly folded. */
export type DiffSegment =
  { kind: 'lines'; lines: DiffLine[] } | { kind: 'fold'; lines: DiffLine[] };

/**
 * Folds long runs of unchanged lines inside a hunk, keeping `keep` lines of
 * context next to each change. Short runs stay visible.
 */
export function foldUnchanged(lines: DiffLine[], keep = 3): DiffSegment[] {
  const segments: DiffSegment[] = [];
  let index = 0;
  while (index < lines.length) {
    if (lines[index].kind !== 'context') {
      let end = index;
      while (end < lines.length && lines[end].kind !== 'context') end++;
      segments.push({ kind: 'lines', lines: lines.slice(index, end) });
      index = end;
      continue;
    }
    let end = index;
    while (end < lines.length && lines[end].kind === 'context') end++;
    const run = lines.slice(index, end);
    const leading = index === 0;
    const trailing = end === lines.length;
    const head = leading ? 0 : keep;
    const tail = trailing ? 0 : keep;
    if (run.length > head + tail + 2) {
      if (head) segments.push({ kind: 'lines', lines: run.slice(0, head) });
      segments.push({
        kind: 'fold',
        lines: run.slice(head, run.length - tail),
      });
      if (tail) segments.push({ kind: 'lines', lines: run.slice(-tail) });
    } else segments.push({ kind: 'lines', lines: run });
    index = end;
  }
  return segments;
}

/** Unchanged lines Git left out between two hunks (or before the first). */
export function gapBefore(hunks: DiffHunk[], index: number): number {
  const hunk = hunks[index];
  if (!hunk?.header) return 0;
  if (index === 0) return Math.max(0, hunk.newStart - 1);
  const previous = hunks[index - 1];
  if (!previous.header) return 0;
  return Math.max(0, hunk.oldStart - (previous.oldStart + previous.oldLines));
}

export type SplitRow = {
  left: DiffLine | null;
  right: DiffLine | null;
};

/** Pairs deletions with the additions that replace them for a split view. */
export function splitRows(lines: DiffLine[]): SplitRow[] {
  const rows: SplitRow[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (line.kind === 'context' || line.kind === 'note') {
      rows.push({ left: line, right: line });
      index++;
      continue;
    }
    const removed: DiffLine[] = [];
    const added: DiffLine[] = [];
    while (index < lines.length && lines[index].kind === 'del')
      removed.push(lines[index++]);
    while (index < lines.length && lines[index].kind === 'add')
      added.push(lines[index++]);
    const count = Math.max(removed.length, added.length);
    for (let row = 0; row < count; row++)
      rows.push({ left: removed[row] ?? null, right: added[row] ?? null });
  }
  return rows;
}
