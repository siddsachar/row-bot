import { useEffect, useRef, useState } from 'react';
import { BookOpen, ExternalLink } from 'lucide-react';
import type { GuidePageView } from '../../api/types';
import { clientError } from '../../api/errors';
import { useOptionalRuntime, useRuntime } from '../../runtime';
import { useOptionalOverlay } from '../../ui/overlays';
import { Hint } from '../../ui/primitives';
import SafeMarkdown from './chat-parity-markdown';

const DOCS_SITE = 'https://row-bot.ai';

/** The page's intro and each `## ` section, in order, with its anchor. */
function sections(page: GuidePageView) {
  const parts: { anchor: string; text: string }[] = [];
  let anchor = '';
  let lines: string[] = [];
  let next = 0;
  const flush = () => {
    if (lines.join('').trim()) parts.push({ anchor, text: lines.join('\n') });
  };
  let fenced = false;
  for (const line of page.markdown.split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
    if (!fenced && line.startsWith('## ') && next < page.sections.length) {
      flush();
      anchor = page.sections[next].anchor;
      next += 1;
      lines = [line];
    } else lines.push(line);
  }
  flush();
  return parts;
}

/** One of Row-Bot's guides, read from the copy this install ships (offline). */
function GuideBody({ route, anchor }: { route: string; anchor: string }) {
  const { controller } = useRuntime();
  const [page, setPage] = useState<GuidePageView | null>(null);
  const [error, setError] = useState('');
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const abort = new AbortController();
    controller
      .guidePage(route, abort.signal)
      .then((found) => found && setPage(found))
      .catch((cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      });
    return () => abort.abort();
  }, [controller, route]);
  useEffect(() => {
    if (!page || !anchor) return;
    const target = body.current?.querySelector<HTMLElement>(
      `[data-guide-section="${CSS.escape(anchor)}"]`,
    );
    if (!target) return;
    target.scrollIntoView({ block: 'start' });
    target.dataset.highlighted = 'true';
    const timer = window.setTimeout(() => {
      delete target.dataset.highlighted;
    }, 2400);
    return () => window.clearTimeout(timer);
  }, [page, anchor]);
  if (error)
    return (
      <p className="guide-sheet-note" role="alert">
        {error}
      </p>
    );
  if (!page)
    return (
      <p className="guide-sheet-note" role="status">
        Opening the guide…
      </p>
    );
  return (
    <div className="guide-sheet" ref={body}>
      <div className="guide-sheet-tools">
        {page.description && (
          <p className="guide-sheet-description">{page.description}</p>
        )}
        <Hint label="Open on row-bot.ai">
          <a
            className="button ghost icon-button icon-action icon-action-sm"
            href={page.web_url}
            target="_blank"
            rel="noreferrer noopener"
            aria-label="Open on row-bot.ai"
          >
            <ExternalLink size={15} aria-hidden />
          </a>
        </Hint>
      </div>
      {sections(page).map((part) => (
        <section
          key={part.anchor || 'intro'}
          className="guide-sheet-section"
          data-guide-section={part.anchor || undefined}
        >
          <SafeMarkdown text={part.text} />
        </section>
      ))}
    </div>
  );
}

/**
 * A guide Row-Bot cited: a chip that opens the bundled page in a sheet (a
 * side drawer on a desktop, a bottom sheet on a phone), scrolled to the cited
 * section. Rendered on its own, outside the app, it is the docs site's page.
 */
export function GuideLink({ route, label }: { route: string; label: string }) {
  const runtime = useOptionalRuntime();
  const overlay = useOptionalOverlay();
  const [path, anchor = ''] = route.split('#', 2);
  if (!runtime || !overlay)
    return (
      <a
        className="guide-chip"
        href={`${DOCS_SITE}${route}`}
        target="_blank"
        rel="noreferrer noopener"
      >
        <BookOpen size={12} aria-hidden />
        <span>{label}</span>
      </a>
    );
  return (
    <Hint label="Open this guide">
      <button
        type="button"
        className="guide-chip"
        onClick={() =>
          overlay.open({
            kind: 'sheet',
            key: `guide:${route}`,
            className: 'guide-overlay',
            title: label,
            description: "Row-Bot's guide for this version",
            content: <GuideBody route={path} anchor={anchor} />,
          })
        }
      >
        <BookOpen size={12} aria-hidden />
        <span>{label}</span>
      </button>
    </Hint>
  );
}
