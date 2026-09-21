import { Fragment, useState, type JSX, type ReactNode } from 'react';
import { saveTextDownload } from '../../platform/download';
import { Button } from '../../ui/primitives';

const INLINE =
  /(`[^`\n]+`|\[[^\]\n]+\]\([^\s)]+\)|\*\*[^*\n]+\*\*|__[^_\n]+__|~~[^~\n]+~~|\*[^*\n]+\*|_[^_\n]+_)/g;

function safeHref(value: string) {
  try {
    const url = new URL(value, 'https://row-bot.invalid');
    return ['http:', 'https:', 'mailto:'].includes(url.protocol) ? value : null;
  } catch {
    return null;
  }
}

function inline(text: string): ReactNode[] {
  return text.split(INLINE).map((part, index) => {
    if (!part) return null;
    if (part.startsWith('`') && part.endsWith('`'))
      return <code key={index}>{part.slice(1, -1)}</code>;
    const link = /^\[([^\]]+)\]\(([^\s)]+)\)$/.exec(part);
    if (link) {
      const href = safeHref(link[2]);
      return href ? (
        <a key={index} href={href} target="_blank" rel="noreferrer noopener">
          {link[1]}
        </a>
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

function isTableDivider(line: string) {
  return /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}

function cells(line: string) {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim());
}

function CodeBlock({
  language,
  text,
  copyText,
}: {
  language: string;
  text: string;
  copyText?: (value: string) => Promise<boolean>;
}) {
  const [status, setStatus] = useState('');
  const extension = /^[A-Za-z0-9_+-]{1,20}$/.test(language)
    ? language.toLowerCase()
    : 'txt';
  async function copy() {
    try {
      setStatus(
        (await (copyText ?? (async () => false))(text))
          ? 'Code copied.'
          : 'Copy is unavailable.',
      );
    } catch {
      setStatus('Code could not be copied.');
    }
  }
  async function download() {
    const result = await saveTextDownload(text, `code.${extension}`);
    setStatus(
      result.status === 'ok'
        ? 'Code download prepared.'
        : 'Code download is unavailable.',
    );
  }
  return (
    <figure className="code-block">
      <figcaption>
        <span>{language || 'Plain text'}</span>
        <span className="code-block-actions">
          <Button onClick={() => void copy()}>Copy code</Button>
          <Button onClick={() => void download()}>Download code</Button>
        </span>
      </figcaption>
      <pre className="code-sample">
        <code data-language={language || undefined}>{text}</code>
      </pre>
      {status && <small role="status">{status}</small>}
    </figure>
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
          key={`code-${index}`}
        />,
      );
      continue;
    }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      const Heading = `h${level}` as keyof JSX.IntrinsicElements;
      blocks.push(
        <Heading key={`heading-${index}`}>{inline(heading[2])}</Heading>,
      );
      index++;
      continue;
    }
    if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      blocks.push(<hr key={`rule-${index}`} />);
      index++;
      continue;
    }
    if (line.trimStart().startsWith('>')) {
      const quote: string[] = [];
      while (index < lines.length && lines[index].trimStart().startsWith('>'))
        quote.push(lines[index++].trimStart().replace(/^>\s?/, ''));
      blocks.push(
        <blockquote key={`quote-${index}`}>
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
          <ol key={`list-${index}`}>{items}</ol>
        ) : (
          <ul key={`list-${index}`}>{items}</ul>
        ),
      );
      continue;
    }
    if (
      index + 1 < lines.length &&
      line.includes('|') &&
      isTableDivider(lines[index + 1])
    ) {
      const headers = cells(line);
      index += 2;
      const rows: string[][] = [];
      while (index < lines.length && lines[index].includes('|'))
        rows.push(cells(lines[index++]));
      blocks.push(
        <table key={`table-${index}`}>
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
        </table>,
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
      !/^\s*(?:[-*+]\s+|\d+[.)]\s+|>)/.test(lines[index])
    )
      paragraph.push(lines[index++]);
    blocks.push(
      <p key={`paragraph-${index}`}>
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
