import { useEffect, useState } from 'react';
import type { Token } from '../shell/syntax';
import { languageForFile } from '../shell/syntax-languages';
import type { DiffHunk, DiffLine } from './diff-model';

/** Text above this size is shown plain rather than tokenised in the panel. */
const MAX_HIGHLIGHT = 96 * 1024;

/** Shiki loads on first use, in its own chunk (as in chat code blocks). */
function highlight(code: string, language: string) {
  return import('../shell/syntax').then((module) =>
    module.highlight(code, language),
  );
}

/** Highlighting language for a workspace-relative path ('' when unknown). */
export function languageForPath(path: string): string {
  return languageForFile(path.split('/').at(-1) ?? '', '');
}

/** Highlighted lines for `text`, or null while loading or when unsupported. */
export function useHighlightedLines(
  text: string,
  language: string,
): Token[][] | null {
  const [result, setResult] = useState<{
    key: string;
    lines: Token[][] | null;
  } | null>(null);
  const key = `${language}\u0000${text}`;
  useEffect(() => {
    if (!language || !text || text.length > MAX_HIGHLIGHT) return;
    let active = true;
    highlight(text, language).then(
      (lines) => {
        if (active) setResult({ key, lines });
      },
      () => {
        if (active) setResult({ key, lines: null });
      },
    );
    return () => {
      active = false;
    };
  }, [key, language, text]);
  return result?.key === key ? result.lines : null;
}

/**
 * Tokens for every diff line. Each hunk is highlighted as two continuous
 * fragments (the old and the new side) so strings and comments that span
 * lines keep their colour; lines map back by position.
 */
export function useDiffHighlight(
  hunks: DiffHunk[],
  language: string,
): Map<DiffLine, Token[]> | null {
  const [result, setResult] = useState<{
    hunks: DiffHunk[];
    tokens: Map<DiffLine, Token[]>;
  } | null>(null);
  useEffect(() => {
    if (!language || !hunks.length) return;
    const size = hunks.reduce(
      (total, hunk) =>
        total + hunk.lines.reduce((sum, line) => sum + line.text.length, 0),
      0,
    );
    if (size > MAX_HIGHLIGHT) return;
    let active = true;
    const sides = hunks.flatMap((hunk) => {
      const before = hunk.lines.filter(
        (line) => line.kind === 'context' || line.kind === 'del',
      );
      const after = hunk.lines.filter(
        (line) => line.kind === 'context' || line.kind === 'add',
      );
      return [before, after].filter((lines) => lines.length);
    });
    Promise.all(
      sides.map((lines) =>
        highlight(lines.map((line) => line.text).join('\n'), language),
      ),
    ).then(
      (results) => {
        if (!active) return;
        const tokens = new Map<DiffLine, Token[]>();
        results.forEach((highlighted, index) => {
          if (!highlighted) return;
          sides[index].forEach((line, row) => {
            if (!tokens.has(line) && highlighted[row])
              tokens.set(line, highlighted[row]);
          });
        });
        setResult({ hunks, tokens });
      },
      () => undefined,
    );
    return () => {
      active = false;
    };
  }, [hunks, language]);
  return result?.hunks === hunks ? result.tokens : null;
}

export type { Token };
