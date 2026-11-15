import { useEffect, useRef, useState } from 'react';
import { Code2, Maximize2, PanelRightOpen } from 'lucide-react';
import type { TranscriptRow } from '../../../api/types';
import { clientError } from '../../../api/errors';
import { useOptionalRuntime } from '../../../runtime';
import { useOverlay } from '../../../ui/overlays';
import { CopyGlyph, IconButton } from '../../../ui/primitives';
import { useResolvedTheme } from '../../../ui/theme';
import { requestPagePanel } from '../../pages/open-page';
import { pageTheme } from '../../pages/page-theme';

type VisualBlock = Extract<TranscriptRow['blocks'][number], { type: 'visual' }>;

const HEIGHT = 'rowbot-visual-height';
const SHORTEST = 120;
const TALLEST = 900;
// Expanded, it may grow to its content; the dialog scrolls past the screen's height.
const TALLEST_EXPANDED = 4000;

/**
 * One visual in its sandboxed frame, made the way a page's frame is: Row-Bot
 * serves its document once, under a policy with no network, to a frame with
 * `sandbox="allow-scripts"` only (no same origin, forms, pop-ups, downloads
 * or top navigation). The chat reads one message from it, its height, and
 * only from this frame; it tells the frame the theme. A visual that tries to
 * leave its page is stopped there.
 */
function VisualFrame({
  source,
  title,
  conversationId,
  expanded = false,
}: {
  source: string;
  title: string;
  conversationId?: string;
  expanded?: boolean;
}) {
  const controller = useOptionalRuntime()?.controller;
  const frame = useRef<HTMLIFrameElement>(null);
  const mode = useResolvedTheme() === 'dark' ? 'dark' : 'light';
  const [url, setUrl] = useState('');
  const [problem, setProblem] = useState('');
  const [height, setHeight] = useState(200);
  const [left, setLeft] = useState(false);
  const loads = useRef(0);
  const shownMode = useRef(mode);
  shownMode.current = mode;

  useEffect(() => {
    if (!controller || !conversationId) return;
    const abort = new AbortController();
    loads.current = 0;
    setUrl('');
    setProblem('');
    controller
      .visualFrame(conversationId, source, pageTheme(), abort.signal)
      .then(
        (made) => !abort.signal.aborted && setUrl(made.frame_url),
        (cause) =>
          !abort.signal.aborted && setProblem(clientError(cause).message),
      );
    return () => abort.abort();
  }, [controller, conversationId, source]);

  useEffect(() => {
    function listen(event: MessageEvent) {
      if (!frame.current || event.source !== frame.current.contentWindow)
        return;
      const data = event.data as { type?: unknown; h?: unknown } | null;
      if (
        !data ||
        data.type !== HEIGHT ||
        typeof data.h !== 'number' ||
        !Number.isFinite(data.h)
      )
        return;
      setHeight(
        Math.min(
          expanded ? TALLEST_EXPANDED : TALLEST,
          Math.max(SHORTEST, Math.ceil(data.h)),
        ),
      );
    }
    window.addEventListener('message', listen);
    return () => window.removeEventListener('message', listen);
  }, [expanded]);

  // The frame follows the app's theme without loading again.
  useEffect(() => {
    frame.current?.contentWindow?.postMessage(
      { type: 'rowbot-visual-theme', mode },
      '*',
    );
  }, [mode]);

  if (left)
    return (
      <p role="alert" className="rich-block-status">
        This visual tried to open another page, so it was stopped.
      </p>
    );
  if (problem)
    return (
      <p role="alert" className="rich-block-status">
        This visual couldn’t be shown. {problem}
      </p>
    );
  if (!url)
    return (
      <p role="status" className="rich-block-status">
        Drawing the visual…
      </p>
    );
  return (
    <iframe
      key={url}
      ref={frame}
      className="answer-visual-frame"
      title={title}
      src={url}
      sandbox="allow-scripts"
      allow="clipboard-read 'none'; clipboard-write 'none'"
      referrerPolicy="no-referrer"
      style={{ height }}
      data-expanded={expanded ? 'true' : undefined}
      onLoad={(event) => {
        loads.current += 1;
        if (loads.current > 1) setLeft(true);
        else
          event.currentTarget.contentWindow?.postMessage(
            { type: 'rowbot-visual-theme', mode: shownMode.current },
            '*',
          );
      }}
    />
  );
}

/**
 * A rowbot-visual in a reply: a diagram or small interactive explanation
 * Row-Bot drew, labelled as made by Row-Bot, with Expand, Open as page (the
 * Pages panel, same sandbox) and Copy source.
 */
export function AnswerVisual({
  block,
  conversationId,
  copyText,
}: {
  block: VisualBlock;
  conversationId?: string;
  copyText?: (value: string) => Promise<boolean>;
}) {
  const overlay = useOverlay();
  const controller = useOptionalRuntime()?.controller;
  const [copied, setCopied] = useState(false);
  const [opening, setOpening] = useState(false);
  const title = block.title || 'Visual';
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1600);
    return () => window.clearTimeout(timer);
  }, [copied]);
  async function openAsPage() {
    if (!controller || !conversationId) return;
    setOpening(true);
    try {
      const page = await controller.createVisualPage(
        conversationId,
        title,
        block.source,
      );
      requestPagePanel({
        conversationId,
        pageId: page.page_id,
        title: page.title,
      });
    } catch (cause) {
      overlay.notify(clientError(cause).message, 'warning');
    } finally {
      setOpening(false);
    }
  }
  return (
    <figure className="rich-block answer-visual">
      <div className="rich-block-header">
        <span className="rich-block-title">{title}</span>
        <span className="answer-visual-maker">Made by Row-Bot</span>
        <span className="rich-block-tools">
          <IconButton
            size="sm"
            label="Expand"
            onClick={() =>
              overlay.open({
                title,
                description: 'Made by Row-Bot',
                className: 'visual-dialog',
                content: (
                  <VisualFrame
                    source={block.source}
                    title={title}
                    conversationId={conversationId}
                    expanded
                  />
                ),
              })
            }
          >
            <Maximize2 size={15} aria-hidden />
          </IconButton>
          {controller && conversationId && (
            <IconButton
              size="sm"
              label="Open as page"
              disabled={opening}
              onClick={() => void openAsPage()}
            >
              <PanelRightOpen size={15} aria-hidden />
            </IconButton>
          )}
          {copyText && (
            <IconButton
              size="sm"
              label="Copy source"
              onClick={() =>
                void copyText(block.source).then((ok) => setCopied(ok))
              }
            >
              <CopyGlyph copied={copied} idle={Code2} />
            </IconButton>
          )}
        </span>
      </div>
      <VisualFrame
        source={block.source}
        title={title}
        conversationId={conversationId}
      />
    </figure>
  );
}
