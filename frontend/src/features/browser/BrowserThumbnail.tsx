import { Globe, PanelRightOpen } from 'lucide-react';
import type { BrowserPaneState, TranscriptTraceItem } from '../../api/types';
import { Hint, StatusDot } from '../../ui/primitives';
import { actingWords } from './BrowserPane';
import { useBrowserLive, type LiveFrame } from './live';
import { requestBrowserPane } from './open-pane';

function Picture({
  state,
  frame,
  onOpen,
}: {
  state: BrowserPaneState | null;
  frame: LiveFrame | null;
  onOpen?: () => void;
}) {
  const site = state?.site || '';
  return (
    <button
      type="button"
      className="browser-thumb"
      onClick={onOpen}
      aria-label={site ? `Open the browser: ${site}` : 'Open the browser'}
    >
      {frame && state?.status === 'running' ? (
        <img src={frame.src} alt="" draggable={false} />
      ) : (
        <span className="browser-thumb-empty" aria-hidden>
          <Globe size={20} />
        </span>
      )}
    </button>
  );
}

/**
 * A small live picture of the conversation's page in Row-Bot's browser (an
 * approval card shows it). Tapping it opens the Browser pane. It streams small
 * and slow, and only while it shows.
 */
export function BrowserThumbnail({
  conversationId,
  onOpen,
}: {
  conversationId: string;
  onOpen?: () => void;
}) {
  const live = useBrowserLive({
    conversationId,
    view: 'thumb',
    width: 480,
    enabled: true,
  });
  return <Picture state={live.state} frame={live.frame} onOpen={onOpen} />;
}

/** What Row-Bot is doing in the browser, in a few words. */
export function browserDoing(state: BrowserPaneState | null) {
  if (!state) return '';
  if (state.sign_in) return 'Waiting for you to sign in';
  if (state.waiting) return state.waiting;
  return state.acting ? actingWords(state.acting) : '';
}

/**
 * The chat's step while a turn uses the browser: "Using the browser ·
 * homeserve.co.uk", what Row-Bot is doing, and the live picture. Tapping it
 * opens the Browser pane.
 */
export function BrowserStep({
  conversationId,
  onOpen,
}: {
  conversationId: string;
  onOpen: () => void;
}) {
  const { state, frame } = useBrowserLive({
    conversationId,
    view: 'thumb',
    width: 480,
    enabled: true,
  });
  const doing = browserDoing(state);
  return (
    <div className="browser-step">
      <Picture state={state} frame={frame} onOpen={onOpen} />
      <div className="browser-step-words">
        <span className="browser-step-title">
          <StatusDot
            tone="accent"
            pulse={Boolean(state?.acting)}
            label="Live"
          />
          Using the browser
          {state?.site ? (
            <span className="browser-step-site"> · {state.site}</span>
          ) : null}
        </span>
        {doing && <span className="browser-step-doing">{doing}</span>}
        <button type="button" className="browser-step-open" onClick={onOpen}>
          <PanelRightOpen size={14} aria-hidden />
          Open browser
        </button>
      </div>
    </div>
  );
}

// The page a browser step ended on, as its result names it: "Page: Tea (https://en.wikipedia.org/wiki/Tea)".
const PAGE_LINE = /^Page: .*\((https?:\/\/[^\s)]+)\)\s*$/gm;

/** "wikipedia.org": the site a turn's browser steps last showed, or "" when none says. */
export function siteOfSteps(steps: TranscriptTraceItem[]): string {
  for (const step of [...steps].reverse()) {
    if (step.group_kind !== 'browser') continue;
    const last = [...step.safe_summary.matchAll(PAGE_LINE)].at(-1);
    if (!last) continue;
    try {
      return new URL(last[1]).hostname.replace(/^www\./, '');
    } catch {
      continue;
    }
  }
  return '';
}

/**
 * A finished turn's browser step: "Used the browser · wikipedia.org".
 * Tapping it opens the Browser pane, as the live step does (U16).
 */
export function BrowserStepDone({
  conversationId,
  site,
}: {
  conversationId: string;
  site: string;
}) {
  return (
    <Hint label="Open browser">
      <button
        type="button"
        className="browser-step-done"
        aria-label={site ? `Open the browser: ${site}` : 'Open the browser'}
        onClick={() => requestBrowserPane(conversationId)}
      >
        <Globe size={16} aria-hidden className="browser-step-done-icon" />
        <span className="browser-step-title">
          Used the browser
          {site ? <span className="browser-step-site"> · {site}</span> : null}
        </span>
        <PanelRightOpen
          size={15}
          aria-hidden
          className="browser-step-done-open"
        />
      </button>
    </Hint>
  );
}
