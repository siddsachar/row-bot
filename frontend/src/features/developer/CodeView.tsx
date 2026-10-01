import { languageForPath, useHighlightedLines } from './highlight';

function fontStyle(value = 0) {
  return {
    fontStyle: value & 1 ? 'italic' : undefined,
    fontWeight: value & 2 ? 600 : undefined,
  } as const;
}

/**
 * A read-only file section with line numbers and lazy syntax colour. The
 * labelled text holds exactly the file's text: numbers sit in a separate,
 * hidden gutter so copying or reading the text never includes them.
 */
export default function CodeView({
  path,
  text,
  label = 'File text',
  firstLine = 1,
}: {
  path: string;
  text: string;
  label?: string;
  /** Number of the first line, or 0 to hide numbers (a later section). */
  firstLine?: number;
}) {
  const lines = useHighlightedLines(text, languageForPath(path));
  const rows = text.split('\n');
  return (
    <div className="code-view" data-highlighted={lines ? 'true' : undefined}>
      {firstLine > 0 && (
        <div className="code-view-gutter" aria-hidden>
          {rows.map((_, index) => (
            <span key={index}>{firstLine + index}</span>
          ))}
        </div>
      )}
      <pre
        className="code-view-text"
        role="region"
        aria-label={label}
        tabIndex={0}
      >
        <code>
          {lines
            ? rows.map((row, index) => (
                <span className="code-view-line" key={index}>
                  {lines[index] &&
                  lines[index].map((token) => token.content).join('') === row
                    ? lines[index].map((token, part) => (
                        <span
                          key={part}
                          style={{
                            color: token.color,
                            ...fontStyle(token.fontStyle),
                          }}
                        >
                          {token.content}
                        </span>
                      ))
                    : row}
                  {index < rows.length - 1 ? '\n' : ''}
                </span>
              ))
            : text}
        </code>
      </pre>
    </div>
  );
}
