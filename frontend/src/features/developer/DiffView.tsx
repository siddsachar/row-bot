import { Fragment, useMemo, useState, type ReactNode } from 'react';
import { ChevronsUpDown } from 'lucide-react';
import {
  foldUnchanged,
  gapBefore,
  parseUnifiedDiff,
  splitRows,
  type DiffLine,
} from './diff-model';
import { languageForPath, useDiffHighlight, type Token } from './highlight';

export type DiffMode = 'unified' | 'split';

function fontStyle(value = 0) {
  return {
    fontStyle: value & 1 ? 'italic' : undefined,
    fontWeight: value & 2 ? 600 : undefined,
  } as const;
}

function Code({ line, tokens }: { line: DiffLine; tokens?: Token[] }) {
  if (!tokens) return <>{line.text}</>;
  return (
    <>
      {tokens.map((token, index) => (
        <span
          key={index}
          style={{ color: token.color, ...fontStyle(token.fontStyle) }}
        >
          {token.content}
        </span>
      ))}
    </>
  );
}

const MARK: Record<DiffLine['kind'], { sign: string; word: string }> = {
  add: { sign: '+', word: 'Added' },
  del: { sign: '−', word: 'Removed' },
  context: { sign: '', word: '' },
  note: { sign: '', word: '' },
};

function Marker({ kind }: { kind: DiffLine['kind'] }) {
  const mark = MARK[kind];
  return (
    <td className="diff-marker">
      <span aria-hidden>{mark.sign}</span>
      {mark.word && <span className="visually-hidden">{mark.word}</span>}
    </td>
  );
}

function Number_({ value }: { value: number | null }) {
  return (
    <td className="diff-number" aria-hidden>
      {value ?? ''}
    </td>
  );
}

function plural(count: number, word: string) {
  return `${count.toLocaleString()} ${word}${count === 1 ? '' : 's'}`;
}

/**
 * A read-only diff: unified or side by side, unchanged runs folded behind a
 * "Show N unchanged lines" row, syntax colour when the grammar is bundled.
 * The text stays selectable and every changed line says what happened.
 */
export default function DiffView({
  path,
  text,
  mode,
}: {
  path: string;
  text: string;
  mode: DiffMode;
}) {
  const diff = useMemo(() => parseUnifiedDiff(text), [text]);
  const tokens = useDiffHighlight(diff.hunks, languageForPath(path));
  const [opened, setOpened] = useState<{
    text: string;
    keys: Set<string>;
  }>({ text, keys: new Set() });
  const open = opened.text === text ? opened.keys : new Set<string>();
  const split = mode === 'split';
  const columns = 4;
  if (diff.binary)
    return <p className="diff-empty">Binary file changed. No text to show.</p>;
  if (!diff.hunks.length)
    return <p className="diff-empty">No textual changes in this view.</p>;

  function row(line: DiffLine, key: string): ReactNode {
    const code = <Code line={line} tokens={tokens?.get(line)} />;
    if (line.kind === 'note')
      return (
        <tr key={key} className="diff-row" data-kind="note">
          <td colSpan={columns} className="diff-note">
            {line.text}
          </td>
        </tr>
      );
    return (
      <tr key={key} className="diff-row" data-kind={line.kind}>
        <Number_ value={line.oldNumber} />
        <Number_ value={line.newNumber} />
        <Marker kind={line.kind} />
        <td className="diff-code">{code}</td>
      </tr>
    );
  }

  function splitRow(
    left: DiffLine | null,
    right: DiffLine | null,
    key: string,
  ) {
    if (left?.kind === 'note')
      return (
        <tr key={key} className="diff-row" data-kind="note">
          <td colSpan={columns} className="diff-note">
            {left.text}
          </td>
        </tr>
      );
    const cell = (line: DiffLine | null, side: 'old' | 'new') =>
      line ? (
        <>
          <Number_ value={side === 'old' ? line.oldNumber : line.newNumber} />
          <td className="diff-code" data-kind={line.kind}>
            {line.kind !== 'context' && (
              <span className="visually-hidden">{MARK[line.kind].word} </span>
            )}
            <Code line={line} tokens={tokens?.get(line)} />
          </td>
        </>
      ) : (
        <>
          <td className="diff-number" aria-hidden />
          <td className="diff-code" data-kind="empty" />
        </>
      );
    return (
      <tr key={key} className="diff-row" data-split>
        {cell(left, 'old')}
        {cell(right, 'new')}
      </tr>
    );
  }

  return (
    <table
      className="diff-table"
      data-mode={mode}
      data-highlighted={tokens ? 'true' : undefined}
      aria-label={`Changes in ${path}`}
    >
      <colgroup>
        {split ? (
          <>
            <col className="diff-col-number" />
            <col />
            <col className="diff-col-number" />
            <col />
          </>
        ) : (
          <>
            <col className="diff-col-number" />
            <col className="diff-col-number" />
            <col className="diff-col-marker" />
            <col />
          </>
        )}
      </colgroup>
      {diff.hunks.map((hunk, hunkIndex) => {
        const gap = gapBefore(diff.hunks, hunkIndex);
        const segments = foldUnchanged(hunk.lines);
        return (
          <tbody key={hunkIndex}>
            {gap > 0 && (
              <tr className="diff-gap">
                <td colSpan={columns}>{plural(gap, 'unchanged line')}</td>
              </tr>
            )}
            {hunk.header && (
              <tr className="diff-hunk">
                <td colSpan={columns}>
                  <span aria-hidden>
                    @@ −{hunk.oldStart},{hunk.oldLines} +{hunk.newStart},
                    {hunk.newLines} @@
                  </span>
                  <span className="visually-hidden">
                    Lines {hunk.newStart} to{' '}
                    {hunk.newStart + Math.max(0, hunk.newLines - 1)}
                  </span>
                  {hunk.section && (
                    <span className="diff-hunk-section">{hunk.section}</span>
                  )}
                </td>
              </tr>
            )}
            {segments.map((segment, segmentIndex) => {
              const key = `${hunkIndex}:${segmentIndex}`;
              if (segment.kind === 'fold' && !open.has(key))
                return (
                  <tr className="diff-fold" key={key}>
                    <td colSpan={columns}>
                      <button
                        type="button"
                        className="diff-fold-button"
                        onClick={() =>
                          setOpened({
                            text,
                            keys: new Set([...open, key]),
                          })
                        }
                      >
                        <ChevronsUpDown size={13} aria-hidden />
                        Show {plural(segment.lines.length, 'unchanged line')}
                      </button>
                    </td>
                  </tr>
                );
              return (
                <Fragment key={key}>
                  {split
                    ? splitRows(segment.lines).map((pair, index) =>
                        splitRow(pair.left, pair.right, `${key}:${index}`),
                      )
                    : segment.lines.map((line, index) =>
                        row(line, `${key}:${index}`),
                      )}
                </Fragment>
              );
            })}
          </tbody>
        );
      })}
    </table>
  );
}
