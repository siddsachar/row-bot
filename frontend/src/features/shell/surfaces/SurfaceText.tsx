import type { CSSProperties, ReactNode } from 'react';

/**
 * A surface's Text: the simple Markdown A2UI allows (headings, lists, bold,
 * italic, code), drawn as plain elements. Links, images and HTML stay words:
 * a surface never loads or opens anything from its text.
 */
export function SurfaceText({
  text,
  caption,
  style,
  ...aria
}: {
  text: string;
  caption?: boolean;
  style?: CSSProperties;
  'aria-label'?: string;
  'aria-hidden'?: boolean;
  'aria-live'?: 'polite' | 'assertive';
  'aria-description'?: string;
}) {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (!list.length) return;
    const items = list;
    list = [];
    blocks.push(
      <ul key={`list-${blocks.length}`}>
        {items.map((item, index) => (
          <li key={index}>{inline(item)}</li>
        ))}
      </ul>,
    );
  };
  for (const line of text.split('\n')) {
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    if (bullet) {
      list.push(bullet[1]);
      continue;
    }
    flush();
    if (heading) {
      const level = heading[1].length;
      blocks.push(
        <p
          key={blocks.length}
          className="genui-heading"
          data-level={level}
          role="heading"
          aria-level={level + 2}
        >
          {inline(heading[2])}
        </p>,
      );
    } else if (line.trim()) {
      blocks.push(<p key={blocks.length}>{inline(line)}</p>);
    }
  }
  flush();
  return (
    <div
      className="genui-text"
      data-variant={caption ? 'caption' : 'body'}
      style={style}
      {...aria}
    >
      {blocks}
    </div>
  );
}

const INLINE =
  /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;

function inline(value: string): ReactNode[] {
  return value.split(INLINE).map((part, index) => {
    if (/^(\*\*|__).+(\*\*|__)$/.test(part))
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (/^`.+`$/.test(part))
      return <code key={index}>{part.slice(1, -1)}</code>;
    if (/^([*_]).+\1$/.test(part))
      return <em key={index}>{part.slice(1, -1)}</em>;
    return part;
  });
}
