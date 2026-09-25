import { useEffect, useState } from 'react';
import { Check, Copy, Download, WrapText } from 'lucide-react';
import { saveTextDownload } from '../../platform/download';
import { IconButton } from '../../ui/primitives';
import type { Token } from './syntax';
import { languageLabel, resolveLanguage } from './syntax-languages';

export { languageForFile } from './syntax-languages';

/** Highlighting is skipped for very large blocks; they stay plain text. */
const HIGHLIGHT_LIMIT = 200_000;

function fontStyle(value = 0) {
  return {
    fontStyle: value & 1 ? 'italic' : undefined,
    fontWeight: value & 2 ? 600 : undefined,
    textDecoration: value & 4 ? 'underline' : undefined,
  } as const;
}

/**
 * A fenced code block: language label, wrap, download and copy, with lazily
 * loaded syntax colours. Until the grammar arrives (or when the language is
 * not bundled) the exact source shows as plain text, so nothing shifts.
 */
export function CodeBlock({
  language,
  text,
  copyText,
  title,
}: {
  language: string;
  text: string;
  copyText?: (value: string) => Promise<boolean>;
  /** A visible name for the block, such as an attached file's name. */
  title?: string;
}) {
  const [status, setStatus] = useState('');
  const [copied, setCopied] = useState(false);
  const [wrap, setWrap] = useState(false);
  const [highlighted, setHighlighted] = useState<{
    source: string;
    language: string;
    lines: Token[][];
  } | null>(null);
  const known = resolveLanguage(language);
  useEffect(() => {
    if (!known || text.length > HIGHLIGHT_LIMIT) return;
    let active = true;
    // Streaming text changes often; highlight once it pauses briefly.
    const timer = window.setTimeout(() => {
      void import('./syntax')
        .then((module) => module.highlight(text, language))
        .then((lines) => {
          if (active && lines)
            setHighlighted({ source: text, language, lines });
        })
        .catch(() => undefined);
    }, 120);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [known, language, text]);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1600);
    return () => window.clearTimeout(timer);
  }, [copied]);
  const lines =
    highlighted?.source === text && highlighted.language === language
      ? highlighted.lines
      : null;
  const extension = /^[A-Za-z0-9_+-]{1,20}$/.test(language)
    ? language.toLowerCase()
    : 'txt';
  async function copy() {
    try {
      const ok = await (copyText ?? (async () => false))(text);
      setCopied(ok);
      setStatus(ok ? 'Code copied.' : 'Copy is unavailable.');
    } catch {
      setStatus('Code could not be copied.');
    }
  }
  async function download() {
    const result = await saveTextDownload(
      text,
      title && /^[\w .-]{1,80}$/.test(title) ? title : `code.${extension}`,
    );
    setStatus(
      result.status === 'ok'
        ? 'Code download prepared.'
        : 'Code download is unavailable.',
    );
  }
  return (
    <figure
      className="code-block"
      data-wrap={wrap ? 'true' : undefined}
      data-highlighted={lines ? 'true' : undefined}
    >
      <figcaption className="code-block-header">
        <span className="code-block-language">
          {title ?? languageLabel(language)}
        </span>
        <span className="code-block-actions">
          <IconButton
            size="sm"
            label="Wrap lines"
            pressed={wrap}
            onClick={() => setWrap((value) => !value)}
          >
            <WrapText size={15} aria-hidden />
          </IconButton>
          <IconButton
            size="sm"
            label="Download code"
            onClick={() => void download()}
          >
            <Download size={15} aria-hidden />
          </IconButton>
          <IconButton size="sm" label="Copy code" onClick={() => void copy()}>
            {copied ? (
              <Check size={15} aria-hidden />
            ) : (
              <Copy size={15} aria-hidden />
            )}
          </IconButton>
        </span>
      </figcaption>
      <pre className="code-sample" tabIndex={0}>
        <code data-language={language || undefined}>
          {lines
            ? lines.map((line, index) => (
                <span className="code-line" key={index}>
                  {line.map((token, part) => (
                    <span
                      key={part}
                      style={{
                        color: token.color,
                        ...fontStyle(token.fontStyle),
                      }}
                    >
                      {token.content}
                    </span>
                  ))}
                  {index < lines.length - 1 ? '\n' : ''}
                </span>
              ))
            : text}
        </code>
      </pre>
      {status && (
        <small role="status" className="code-block-status">
          {status}
        </small>
      )}
    </figure>
  );
}
