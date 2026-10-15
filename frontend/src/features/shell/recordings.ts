import { createContext, useCallback, useSyncExternalStore } from 'react';
import type { RecordingPage, RecordingView } from '../../api/types';
import type { Tone } from '../../ui/primitives';
import { durationWords } from './background-processes';

/**
 * Recordings (F2): Row-Bot's browser or the screen, recorded to a video in
 * the conversation's files. Each card in a conversation reads them from one
 * feed per conversation, every second while one records or saves.
 */

export type RecordingIo = {
  load: (
    conversationId: string | undefined,
    signal: AbortSignal,
  ) => Promise<RecordingPage>;
  /** Stop it; its video is saved to the conversation. No approval. */
  stop: (recordingId: string) => Promise<RecordingView>;
};

export const RecordingIoContext = createContext<RecordingIo | null>(null);

const LIVE_POLL_MS = 1000;
/** A card whose recording isn't listed yet asks a few more times (it may be starting). */
const WANTED_TRIES = 5;

export type RecordingFeedState = {
  items: RecordingView[];
  loaded: boolean;
  error: boolean;
  /** When the items were read (client clock), for the running time between reads. */
  readAt: number;
};

const EMPTY: RecordingFeedState = {
  items: [],
  loaded: false,
  error: false,
  readAt: 0,
};

export function recordingLive(view: RecordingView) {
  return view.status === 'recording' || view.status === 'saving';
}

function pageVisible() {
  return (
    typeof document === 'undefined' || document.visibilityState !== 'hidden'
  );
}

class RecordingFeed {
  state: RecordingFeedState = EMPTY;
  load: RecordingIo['load'];
  private readonly listeners = new Set<() => void>();
  private readonly wanted = new Map<string, number>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private reading: AbortController | undefined;
  private readonly onVisibility = () => {
    if (pageVisible()) this.schedule(0);
  };

  constructor(
    readonly scope: string | undefined,
    load: RecordingIo['load'],
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

  want(recordingId: string) {
    const listed = this.state.items.find(
      (item) => item.recording_id === recordingId,
    );
    if (listed && !recordingLive(listed)) return;
    if (!this.wanted.has(recordingId)) {
      this.wanted.set(recordingId, WANTED_TRIES);
      this.schedule(0);
    }
  }

  merge(view: RecordingView) {
    const items = this.state.items.some(
      (item) => item.recording_id === view.recording_id,
    )
      ? this.state.items.map((item) =>
          item.recording_id === view.recording_id ? view : item,
        )
      : [view, ...this.state.items];
    this.publish({ ...this.state, items, readAt: Date.now() });
    this.schedule(LIVE_POLL_MS);
  }

  refresh() {
    this.schedule(0);
  }

  private publish(state: RecordingFeedState) {
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
        if (!recordingLive(item)) this.wanted.delete(item.recording_id);
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
    for (const [recordingId, tries] of this.wanted) {
      const listed = this.state.items.some(
        (item) => item.recording_id === recordingId,
      );
      if (listed) continue;
      if (tries <= 1) this.wanted.delete(recordingId);
      else this.wanted.set(recordingId, tries - 1);
    }
    const busy =
      this.state.items.some(recordingLive) ||
      [...this.wanted.keys()].some(
        (id) => !this.state.items.some((item) => item.recording_id === id),
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

const feeds = new Map<string, RecordingFeed>();

function feedFor(load: RecordingIo['load'], scope: string | undefined) {
  let feed = feeds.get(scope ?? '*');
  if (!feed) {
    feed = new RecordingFeed(scope, load);
    feeds.set(scope ?? '*', feed);
  }
  feed.load = load;
  return feed;
}

const idleSubscribe = () => () => {};
const idleSnapshot = () => EMPTY;

/** One conversation's recordings, read every second while one records or saves. */
export function useRecordings(
  io: RecordingIo | null | undefined,
  conversationId?: string,
) {
  const feed = io ? feedFor(io.load, conversationId) : null;
  const state = useSyncExternalStore(
    feed?.subscribe ?? idleSubscribe,
    feed?.getSnapshot ?? idleSnapshot,
    feed?.getSnapshot ?? idleSnapshot,
  );
  const want = useCallback(
    (recordingId: string) => feed?.want(recordingId),
    [feed],
  );
  const merge = useCallback((view: RecordingView) => feed?.merge(view), [feed]);
  const refresh = useCallback(() => feed?.refresh(), [feed]);
  return { ...state, want, merge, refresh };
}

// ---------------------------------------------------------------- words

/** "0:42", "4:05", "1:02:10": a recording's clock. */
export function clockWords(seconds: number) {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = String(total % 60).padStart(2, '0');
  return hours
    ? `${hours}:${String(minutes).padStart(2, '0')}:${rest}`
    : `${minutes}:${rest}`;
}

/** How long it has recorded, counting on between reads while it records. */
export function recordingSeconds(
  view: RecordingView,
  readAt: number,
  now: number,
) {
  return view.status === 'recording' && readAt
    ? view.seconds + Math.max(0, now - readAt) / 1000
    : view.seconds;
}

/** "Row-Bot's browser" or "the screen", as a card names what it records. */
export function recordingSubject(view: RecordingView) {
  return view.kind === 'screen' ? 'Screen recording' : 'Browser recording';
}

const STOPPED_BY: Record<RecordingView['stopped_by'], string> = {
  '': '',
  you: 'Stopped by you',
  agent: 'Stopped by Row-Bot',
  limit: 'Stopped at its time limit',
  size: 'Stopped before it got too large for the chat',
  conversation: 'Stopped with its conversation',
  quit: 'Stopped when Row-Bot closed',
  closed: 'Stopped when the browser closed',
  error: 'Stopped by a problem',
};

const PROBLEM: Record<RecordingView['problem'], string> = {
  '': '',
  nothing_recorded: 'Nothing was recorded.',
  too_large: 'The video was too large to keep in the chat.',
  not_saved: "The video couldn't be saved.",
};

/** Its state in words, with a tone: "Recording · 0:42", "Saving the video…", "0:42 · 3.1 MB". */
export function recordingStatus(
  view: RecordingView,
  seconds: number,
): { tone: Tone; label: string; pulse: boolean } {
  switch (view.status) {
    case 'recording':
      return {
        tone: 'warning',
        label: `Recording · ${clockWords(seconds)}`,
        pulse: true,
      };
    case 'saving':
      return { tone: 'neutral', label: 'Saving the video…', pulse: true };
    case 'saved':
      return {
        tone: 'success',
        label: [
          clockWords(seconds),
          view.size_bytes ? sizeWords(view.size_bytes) : '',
        ]
          .filter(Boolean)
          .join(' · '),
        pulse: false,
      };
    default:
      return {
        tone: view.problem === 'nothing_recorded' ? 'neutral' : 'danger',
        label: PROBLEM[view.problem] || "The video couldn't be saved.",
        pulse: false,
      };
  }
}

/** Why it stopped, when it isn't obvious ("Stopped at its time limit"). */
export function stoppedWords(view: RecordingView) {
  if (view.stopped_by === 'limit') {
    const minutes = Math.round(view.max_seconds / 60);
    return minutes >= 1
      ? `Stopped at its ${minutes}-minute limit`
      : `Stopped at its ${durationWords(view.max_seconds)} limit`;
  }
  return STOPPED_BY[view.stopped_by];
}

function sizeWords(bytes: number) {
  return bytes >= 1024 * 1024
    ? `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}
