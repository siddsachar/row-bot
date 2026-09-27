import { Fragment, useState, type JSX, type ReactNode } from 'react';
import { Table } from 'lucide-react';
import { CopyGlyph, Hint, IconButton } from '../../ui/primitives';
import { CodeBlock } from './CodeBlock';

// Underscore emphasis never starts or ends inside a word, so snake_case
// names such as workspace_file_delete stay literal (as in CommonMark).
const INLINE =
  /(`[^`\n]+`|\[[^\]\n]+\]\([^\s)]+\)|\*\*[^*\n]+\*\*|(?<![\p{L}\p{N}_])__[^_\n]+__(?![\p{L}\p{N}_])|~~[^~\n]+~~|\*[^*\n]+\*|(?<![\p{L}\p{N}_])_[^_\n]+_(?![\p{L}\p{N}_])|https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"\]])/gu;
const CITATION = /^\[?\^?(\d{1,3})\]?$/;

function safeHref(value: string) {
  try {
    const url = new URL(value, 'https://row-bot.invalid');
    return ['http:', 'https:', 'mailto:'].includes(url.protocol) ? value : null;
  } catch {
    return null;
  }
}

function domain(href: string) {
  try {
    const url = new URL(href);
    return url.protocol === 'mailto:'
      ? url.pathname
      : url.hostname.replace(/^www\./, '');
  } catch {
    return href;
  }
}

/** Domain and path without the scheme or "www.", e.g. example.test/docs. */
function compactHref(href: string) {
  try {
    const url = new URL(href);
    const path = url.pathname === '/' ? '' : url.pathname.replace(/\/$/, '');
    return `${url.hostname.replace(/^www\./, '')}${path}${url.search}`;
  } catch {
    return href;
  }
}

/** A numbered source chip: the number and the domain, full URL on hover. */
function Citation({ number, href }: { number: string; href: string }) {
  return (
    <Hint label={href}>
      <a
        className="citation-chip"
        href={href}
        target="_blank"
        rel="noreferrer noopener"
        aria-label={`Source ${number}: ${domain(href)}`}
      >
        <span className="citation-number" aria-hidden>
          {number}
        </span>
        <span className="citation-domain" aria-hidden>
          {domain(href)}
        </span>
      </a>
    </Hint>
  );
}

function inline(text: string): ReactNode[] {
  return text.split(INLINE).map((part, index) => {
    if (!part) return null;
    if (part.startsWith('`') && part.endsWith('`'))
      return <code key={index}>{part.slice(1, -1)}</code>;
    const link = /^\[([^\]]+)\]\(([^\s)]+)\)$/.exec(part);
    if (link) {
      const href = safeHref(link[2]);
      const citation = CITATION.exec(link[1].trim());
      if (href && citation && /^https?:/i.test(href))
        return <Citation key={index} number={citation[1]} href={href} />;
      return href ? (
        <a
          key={index}
          href={href}
          target="_blank"
          rel="noreferrer noopener"
          title={domain(href)}
        >
          {link[1]}
        </a>
      ) : (
        <Fragment key={index}>{part}</Fragment>
      );
    }
    if (/^https?:\/\//i.test(part)) {
      // A bare URL is a source: a compact chip, the full address on hover.
      const href = safeHref(part);
      return href ? (
        <Hint key={index} label={href}>
          <a
            className="citation-chip"
            data-kind="link"
            href={href}
            target="_blank"
            rel="noreferrer noopener"
          >
            <span className="citation-domain">{compactHref(href)}</span>
          </a>
        </Hint>
      ) : (
        <Fragment key={index}>{part}</Fragment>
      );
    }
    if (
      (part.startsWith('**') && part.endsWith('**')) ||
      (part.startsWith('__') && part.endsWith('__'))
    )
      return <strong key={index}>{inline(part.slice(2, -2))}</strong>;
    if (part.startsWith('~~') && part.endsWith('~~'))
      return <del key={index}>{inline(part.slice(2, -2))}</del>;
    if (
      (part.startsWith('*') && part.endsWith('*')) ||
      (part.startsWith('_') && part.endsWith('_'))
    )
      return <em key={index}>{inline(part.slice(1, -1))}</em>;
    return <Fragment key={index}>{part}</Fragment>;
  });
}

function cells(line: string) {
  const source = line
    .trim()
    .replace(/^\|/, '')
    .replace(/(?<!\\)\|$/, '');
  const result: string[] = [];
  let value = '';
  for (let index = 0; index < source.length; index++) {
    if (source[index] === '\\' && source[index + 1] === '|') {
      value += '|';
      index++;
    } else if (source[index] === '|') {
      result.push(value.trim());
      value = '';
    } else {
      value += source[index];
    }
  }
  result.push(value.trim());
  return result;
}

function tableColumns(lines: string[], index: number) {
  if (index + 1 >= lines.length || !lines[index].includes('|')) return 0;
  const header = cells(lines[index]);
  const divider = cells(lines[index + 1]);
  return header.length >= 2 &&
    header.length === divider.length &&
    divider.every((cell) => /^:?-{3,}:?$/.test(cell))
    ? header.length
    : 0;
}

/** Plain cell text for CSV: inline Markdown markers removed. */
function plainCell(value: string) {
  return value
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\(([^\s)]+)\)/g, '$1')
    .replace(/(\*\*|__|~~)/g, '')
    .trim();
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

/** A scrolling table with a sticky header and "Copy as CSV". */
function MarkdownTable({
  headers,
  rows,
  copyText,
}: {
  headers: string[];
  rows: string[][];
  copyText?: (value: string) => Promise<boolean>;
}) {
  const [status, setStatus] = useState('');
  async function copy() {
    try {
      const ok = await (copyText ?? (async () => false))(
        tableCsv(headers, rows),
      );
      setStatus(ok ? 'Table copied as CSV.' : 'Copy is unavailable.');
    } catch {
      setStatus('The table could not be copied.');
    }
  }
  return (
    <div className="markdown-table">
      <div className="markdown-table-scroll" tabIndex={0}>
        <table>
          <thead>
            <tr>
              {headers.map((header, cellIndex) => (
                <th key={cellIndex}>{inline(header)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {headers.map((_, cellIndex) => (
                  <td key={cellIndex}>{inline(row[cellIndex] ?? '')}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {copyText && (
        <span className="markdown-table-tools">
          <IconButton size="sm" label="Copy as CSV" onClick={() => void copy()}>
            <CopyGlyph
              copied={status === 'Table copied as CSV.'}
              idle={Table}
            />
          </IconButton>
        </span>
      )}
      {status && (
        <small role="status" className="visually-hidden">
          {status}
        </small>
      )}
    </div>
  );
}

/**
 * A deliberately small, text-only Markdown projection. React owns escaping;
 * raw HTML is never parsed and only allow-listed link protocols become links.
 */
export default function SafeMarkdown({
  text,
  copyText,
}: {
  text: string;
  copyText?: (value: string) => Promise<boolean>;
}) {
  const lines = text.replaceAll('\r\n', '\n').split('\n');
  const blocks: ReactNode[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    // Keys use the start line so a streaming block keeps its identity.
    const start = index;
    if (!line.trim()) {
      index++;
      continue;
    }
    if (line.trimStart().startsWith('```')) {
      const language = line.trim().slice(3).trim();
      const content: string[] = [];
      index++;
      while (
        index < lines.length &&
        !lines[index].trimStart().startsWith('```')
      )
        content.push(lines[index++]);
      if (index < lines.length) index++;
      blocks.push(
        <CodeBlock
          copyText={copyText}
          language={language}
          text={content.join('\n')}
          key={`code-${start}`}
        />,
      );
      continue;
    }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      const Heading = `h${level}` as keyof JSX.IntrinsicElements;
      blocks.push(
        <Heading key={`heading-${start}`}>{inline(heading[2])}</Heading>,
      );
      index++;
      continue;
    }
    if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      blocks.push(<hr key={`rule-${start}`} />);
      index++;
      continue;
    }
    if (line.trimStart().startsWith('>')) {
      const quote: string[] = [];
      while (index < lines.length && lines[index].trimStart().startsWith('>'))
        quote.push(lines[index++].trimStart().replace(/^>\s?/, ''));
      blocks.push(
        <blockquote key={`quote-${start}`}>
          {quote.map((item, quoteIndex) => (
            <Fragment key={quoteIndex}>
              {quoteIndex > 0 && <br />}
              {inline(item)}
            </Fragment>
          ))}
        </blockquote>,
      );
      continue;
    }
    const list = /^\s*(?:([-*+])|(\d+)[.)])\s+(.+)$/.exec(line);
    if (list) {
      const ordered = Boolean(list[2]);
      const items: ReactNode[] = [];
      const pattern = ordered ? /^\s*\d+[.)]\s+(.+)$/ : /^\s*[-*+]\s+(.+)$/;
      while (index < lines.length) {
        const item = pattern.exec(lines[index]);
        if (!item) break;
        items.push(<li key={index++}>{inline(item[1])}</li>);
      }
      blocks.push(
        ordered ? (
          <ol key={`list-${start}`}>{items}</ol>
        ) : (
          <ul key={`list-${start}`}>{items}</ul>
        ),
      );
      continue;
    }
    if (tableColumns(lines, index)) {
      const headers = cells(line);
      index += 2;
      const rows: string[][] = [];
      while (
        index < lines.length &&
        lines[index].trim() &&
        lines[index].includes('|') &&
        cells(lines[index]).length === headers.length
      )
        rows.push(cells(lines[index++]));
      blocks.push(
        <MarkdownTable
          headers={headers}
          rows={rows}
          copyText={copyText}
          key={`table-${start}`}
        />,
      );
      continue;
    }
    const paragraph = [line];
    index++;
    while (
      index < lines.length &&
      lines[index].trim() &&
      !lines[index].trimStart().startsWith('```') &&
      !/^(#{1,6})\s+/.test(lines[index]) &&
      !/^\s*(?:[-*+]\s+|\d+[.)]\s+|>)/.test(lines[index]) &&
      !tableColumns(lines, index)
    )
      paragraph.push(lines[index++]);
    blocks.push(
      <p key={`paragraph-${start}`}>
        {paragraph.map((item, paragraphIndex) => (
          <Fragment key={paragraphIndex}>
            {paragraphIndex > 0 && <br />}
            {inline(item)}
          </Fragment>
        ))}
      </p>,
    );
  }
  return <>{blocks}</>;
}
