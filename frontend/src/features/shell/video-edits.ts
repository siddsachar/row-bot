import { createContext, useCallback, useSyncExternalStore } from 'react';
import type { VideoEditPage, VideoEditView } from '../../api/types';
import type { Tone } from '../../ui/primitives';
import { clockWords } from './recordings';

/**
 * Video edits (F3): an edited video, or stills from one, made in the
 * background and saved to the conversation's files. Each card in a
 * conversation reads them from one feed per conversation, every second while
 * one is still being made.
 */

export type VideoEditIo = {
  load: (
    conversationId: string | undefined,
    signal: AbortSignal,
  ) => Promise<VideoEditPage>;
  /** Stop it; nothing is saved. No approval. */
  stop: (editId: string) => Promise<VideoEditView>;
};

export const VideoEditIoContext = createContext<VideoEditIo | null>(null);

const LIVE_POLL_MS = 1000;
/** A card whose edit isn't listed yet asks a few more times (it may be starting). */
const WANTED_TRIES = 5;

export type VideoEditFeedState = {
  items: VideoEditView[];
  loaded: boolean;
  error: boolean;
  /** When the items were read (client clock), for the running time between reads. */
  readAt: number;
};

const EMPTY: VideoEditFeedState = {
  items: [],
  loaded: false,
  error: false,
  readAt: 0,
};

export function editLive(view: VideoEditView) {
  return (
    view.status === 'getting_ffmpeg' ||
    view.status === 'preparing' ||
    view.status === 'editing' ||
    view.status === 'saving'
  );
}

function pageVisible() {
  return (
    typeof document === 'undefined' || document.visibilityState !== 'hidden'
  );
}

class VideoEditFeed {
  state: VideoEditFeedState = EMPTY;
  load: VideoEditIo['load'];
  private readonly listeners = new Set<() => void>();
  private readonly wanted = new Map<string, number>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private reading: AbortController | undefined;
  private readonly onVisibility = () => {
    if (pageVisible()) this.schedule(0);
  };

  constructor(
    readonly scope: string | undefined,
    load: VideoEditIo['load'],
  ) {
    this.load = load;
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    if (this.listeners.size === 1) {
      document.addEventListener('visibilitychange', this.onVisibility);
      this.schedule(0);
    }
    return () => {
      this.listeners.delete(listener);
      if (!this.listeners.size) this.dispose();
    };
  };

  getSnapshot = () => this.state;

  want(editId: string) {
    const listed = this.state.items.find((item) => item.edit_id === editId);
    if (listed && !editLive(listed)) return;
    if (!this.wanted.has(editId)) {
      this.wanted.set(editId, WANTED_TRIES);
      this.schedule(0);
    }
  }

  merge(view: VideoEditView) {
    const items = this.state.items.some((item) => item.edit_id === view.edit_id)
      ? this.state.items.map((item) =>
          item.edit_id === view.edit_id ? view : item,
        )
      : [view, ...this.state.items];
    this.publish({ ...this.state, items, readAt: Date.now() });
    this.schedule(LIVE_POLL_MS);
  }

  refresh() {
    this.schedule(0);
  }

  private publish(state: VideoEditFeedState) {
    this.state = state;
    this.listeners.forEach((listener) => listener());
  }

  private schedule(delay: number) {
    if (!this.listeners.size) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.read(), delay);
  }

  private async read() {
    if (!this.listeners.size || !pageVisible()) return;
    this.reading?.abort();
    const abort = new AbortController();
    this.reading = abort;
    try {
      const page = await this.load(this.scope, abort.signal);
      if (abort.signal.aborted) return;
      for (const item of page.items)
        if (!editLive(item)) this.wanted.delete(item.edit_id);
      this.publish({
        items: page.items,
        loaded: true,
        error: false,
        readAt: Date.now(),
      });
    } catch {
      if (abort.signal.aborted) return;
      this.publish({ ...this.state, loaded: true, error: true });
    }
    for (const [editId, tries] of this.wanted) {
      if (this.state.items.some((item) => item.edit_id === editId)) continue;
      if (tries <= 1) this.wanted.delete(editId);
      else this.wanted.set(editId, tries - 1);
    }
    const busy =
      this.state.items.some(editLive) ||
      [...this.wanted.keys()].some(
        (id) => !this.state.items.some((item) => item.edit_id === id),
      );
    if (busy) this.schedule(LIVE_POLL_MS);
  }

  private dispose() {
    clearTimeout(this.timer);
    this.reading?.abort();
    document.removeEventListener('visibilitychange', this.onVisibility);
    feeds.delete(this.scope ?? '*');
  }
}

const feeds = new Map<string, VideoEditFeed>();

function feedFor(load: VideoEditIo['load'], scope: string | undefined) {
  let feed = feeds.get(scope ?? '*');
  if (!feed) {
    feed = new VideoEditFeed(scope, load);
    feeds.set(scope ?? '*', feed);
  }
  feed.load = load;
  return feed;
}

const idleSubscribe = () => () => {};
const idleSnapshot = () => EMPTY;

/** One conversation's video edits, read every second while one is being made. */
export function useVideoEdits(
  io: VideoEditIo | null | undefined,
  conversationId?: string,
) {
  const feed = io ? feedFor(io.load, conversationId) : null;
  const state = useSyncExternalStore(
    feed?.subscribe ?? idleSubscribe,
    feed?.getSnapshot ?? idleSnapshot,
    feed?.getSnapshot ?? idleSnapshot,
  );
  const want = useCallback((editId: string) => feed?.want(editId), [feed]);
  const merge = useCallback((view: VideoEditView) => feed?.merge(view), [feed]);
  const refresh = useCallback(() => feed?.refresh(), [feed]);
  return { ...state, want, merge, refresh };
}

// ---------------------------------------------------------------- words

/** "12 MB", "640 KB". */
export function sizeWords(bytes: number) {
  return bytes >= 1024 * 1024
    ? `${Math.round(bytes / (1024 * 1024))} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** How long it has run, counting on between reads while it is being made. */
export function editSeconds(view: VideoEditView, readAt: number, now: number) {
  return editLive(view) && readAt
    ? view.seconds + Math.max(0, now - readAt) / 1000
    : view.seconds;
}

/** What the card is called: what the edit does ("Trimmed and sped up 2×"), or getting ffmpeg first. */
export function editTitle(view: VideoEditView) {
  if (view.status === 'getting_ffmpeg')
    return 'Getting ffmpeg for video editing';
  return view.summary || (view.kind === 'frames' ? 'Stills' : 'Video edit');
}

const STOPPED_BY: Record<VideoEditView['stopped_by'], string> = {
  '': 'Stopped',
  you: 'Stopped by you',
  agent: 'Stopped by Row-Bot',
  conversation: 'Stopped with its conversation',
  quit: 'Stopped when Row-Bot closed',
};

const PROBLEM: Record<VideoEditView['problem'], string> = {
  '': "The edit didn't work",
  ffmpeg_unavailable: "ffmpeg isn't available",
  download_failed: "ffmpeg couldn't be downloaded",
  edit_failed: "The edit didn't work",
  too_large: 'Too large for the chat',
  not_saved: "The result couldn't be saved",
};

/** Its state in words, with a tone: "Editing · 40%", "Getting ffmpeg · 12 of 30 MB", "0:06 · 570 KB". */
export function editStatus(
  view: VideoEditView,
  seconds: number,
): { tone: Tone; label: string; pulse: boolean } {
  switch (view.status) {
    case 'getting_ffmpeg':
      return {
        tone: 'accent',
        label:
          view.download_total && view.download_received !== null
            ? `Downloading · ${sizeWords(view.download_received)} of ${sizeWords(view.download_total)}`
            : 'Downloading…',
        pulse: true,
      };
    case 'preparing':
      return { tone: 'accent', label: 'Getting ready…', pulse: true };
    case 'editing':
      return {
        tone: 'accent',
        label:
          view.kind === 'frames'
            ? 'Taking the stills…'
            : view.progress !== null
              ? `Editing · ${Math.round(view.progress * 100)}%`
              : `Editing · ${clockWords(seconds)}`,
        pulse: true,
      };
    case 'saving':
      return {
        tone: 'neutral',
        label:
          view.kind === 'frames' ? 'Saving the stills…' : 'Saving the video…',
        pulse: true,
      };
    case 'saved':
      return {
        tone: 'success',
        label:
          view.kind === 'frames'
            ? `${view.frame_count} still${view.frame_count === 1 ? '' : 's'}`
            : [
                view.duration_seconds !== null
                  ? clockWords(view.duration_seconds)
                  : '',
                view.size_bytes ? sizeWords(view.size_bytes) : '',
              ]
                .filter(Boolean)
                .join(' · '),
        pulse: false,
      };
    case 'stopped':
      return {
        tone: 'neutral',
        label: STOPPED_BY[view.stopped_by],
        pulse: false,
      };
    default:
      return { tone: 'danger', label: PROBLEM[view.problem], pulse: false };
  }
}

/** Why it failed, in the person's words, when it says more than the label; ffmpeg's own words stay in Details. */
export function failureWords(view: VideoEditView) {
  if (view.status !== 'failed') return '';
  if (view.problem === 'edit_failed')
    return 'Row-Bot will see what went wrong and can try again.';
  return view.detail;
}
