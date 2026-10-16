import {
  createContext,
  useCallback,
  useEffect,
  useState,
  useSyncExternalStore,
} from 'react';
import type {
  BackgroundProcessOutput,
  BackgroundProcessPage,
  BackgroundProcessView,
} from '../../api/types';
import type { Tone } from '../../ui/primitives';

/**
 * Background processes (F1, F6): a server or watcher the agent started, or a
 * long command that carried on in the background. The card in the
 * conversation and the Monitor list read them from one feed per scope (a
 * conversation, or every one), so a page with many cards polls once.
 */

export type ProcessIo = {
  /** Every process, or one conversation's: running ones first. */
  load: (
    conversationId: string | undefined,
    signal: AbortSignal,
  ) => Promise<BackgroundProcessPage>;
  /** A page of output from position `after` (the oldest kept when that is gone). */
  output: (
    processId: string,
    after: number,
    limit: number,
    signal: AbortSignal,
  ) => Promise<BackgroundProcessOutput>;
  /** Stop it and everything it started; no approval. */
  stop: (processId: string) => Promise<BackgroundProcessView>;
};

export const ProcessIoContext = createContext<ProcessIo | null>(null);

/** How often a feed reads while something runs, and otherwise (the Monitor list). */
export const PROCESS_POLL_MS = 2000;
export const PROCESS_IDLE_POLL_MS = 15000;
/** A card whose process isn't listed yet asks a few more times (it may be starting). */
const WANTED_TRIES = 5;

export type ProcessFeedState = {
  items: BackgroundProcessView[];
  loaded: boolean;
  error: boolean;
  /** When the items were read (client clock), for the running time between reads. */
  readAt: number;
};

const EMPTY: ProcessFeedState = {
  items: [],
  loaded: false,
  error: false,
  readAt: 0,
};

function pageVisible() {
  return (
    typeof document === 'undefined' || document.visibilityState !== 'hidden'
  );
}

function live(view: BackgroundProcessView) {
  return view.status === 'running' || view.status === 'stopping';
}

class ProcessFeed {
  state: ProcessFeedState = EMPTY;
  load: ProcessIo['load'];
  private readonly listeners = new Set<() => void>();
  private readonly wanted = new Map<string, number>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private reading: AbortController | undefined;
  private readonly onVisibility = () => {
    if (pageVisible()) this.schedule(0);
  };

  constructor(
    readonly scope: string | undefined,
    load: ProcessIo['load'],
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

  get active() {
    return this.listeners.size > 0;
  }

  want(processId: string) {
    if (this.state.items.some((item) => item.process_id === processId)) return;
    if (!this.wanted.has(processId)) {
      this.wanted.set(processId, WANTED_TRIES);
      this.schedule(0);
    }
  }

  /** A Stop answered with the process as it is now. */
  merge(view: BackgroundProcessView) {
    const items = this.state.items.some(
      (item) => item.process_id === view.process_id,
    )
      ? this.state.items.map((item) =>
          item.process_id === view.process_id ? view : item,
        )
      : [view, ...this.state.items];
    this.publish({ ...this.state, items, readAt: Date.now() });
    this.schedule(PROCESS_POLL_MS);
  }

  refresh() {
    this.schedule(0);
  }

  private publish(state: ProcessFeedState) {
    this.state = state;
    this.listeners.forEach((listener) => listener());
  }

  private schedule(delay: number) {
    if (!this.active) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.read(), delay);
  }

  private async read() {
    if (!this.active || !pageVisible()) return;
    this.reading?.abort();
    const abort = new AbortController();
    this.reading = abort;
    try {
      const page = await this.load(this.scope, abort.signal);
      if (abort.signal.aborted) return;
      for (const item of page.items) this.wanted.delete(item.process_id);
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
    for (const [processId, tries] of this.wanted)
      if (tries <= 1) this.wanted.delete(processId);
      else this.wanted.set(processId, tries - 1);
    const busy = this.state.items.some(live) || this.wanted.size > 0;
    // A conversation's cards stop reading once everything there has ended.
    if (busy) this.schedule(PROCESS_POLL_MS);
    else if (this.scope === undefined) this.schedule(PROCESS_IDLE_POLL_MS);
  }

  private dispose() {
    clearTimeout(this.timer);
    this.reading?.abort();
    document.removeEventListener('visibilitychange', this.onVisibility);
    feeds.delete(key(this.scope));
  }
}

const feeds = new Map<string, ProcessFeed>();
const key = (scope: string | undefined) => scope ?? '*';

function feedFor(load: ProcessIo['load'], scope: string | undefined) {
  let feed = feeds.get(key(scope));
  if (!feed) {
    feed = new ProcessFeed(scope, load);
    feeds.set(key(scope), feed);
  }
  feed.load = load;
  return feed;
}

const idleSubscribe = () => () => {};
const idleSnapshot = () => EMPTY;

/**
 * The processes of one conversation (or, without one, every process), read
 * every 2 seconds while something runs and the page shows.
 */
export function useBackgroundProcesses(
  io: ProcessIo | null | undefined,
  conversationId?: string,
) {
  const feed = io ? feedFor(io.load, conversationId) : null;
  const state = useSyncExternalStore(
    feed?.subscribe ?? idleSubscribe,
    feed?.getSnapshot ?? idleSnapshot,
    feed?.getSnapshot ?? idleSnapshot,
  );
  const want = useCallback(
    (processId: string) => feed?.want(processId),
    [feed],
  );
  const merge = useCallback(
    (view: BackgroundProcessView) => feed?.merge(view),
    [feed],
  );
  const refresh = useCallback(() => feed?.refresh(), [feed]);
  return { ...state, want, merge, refresh };
}

/**
 * Which process's output is open from the conversation. It lives outside the
 * card: when the turn settles, the live card is replaced by the settled one,
 * and the open output must stay open across that swap.
 */
let openOutput = '';
const openListeners = new Set<() => void>();
function subscribeOpenOutput(listener: () => void) {
  openListeners.add(listener);
  return () => {
    openListeners.delete(listener);
  };
}
export function setOpenOutput(processId: string) {
  if (openOutput === processId) return;
  openOutput = processId;
  openListeners.forEach((listener) => listener());
}
/** Whether this process's output is open, and a way to open or close it. */
export function useOpenOutput(processId: string) {
  const open = useSyncExternalStore(
    subscribeOpenOutput,
    () => openOutput === processId,
    () => false,
  );
  const setOpen = useCallback(
    (next: boolean) => {
      if (next) setOpenOutput(processId);
      else if (openOutput === processId) setOpenOutput('');
    },
    [processId],
  );
  return [open, setOpen] as const;
}

/** The time now, once a second while `ticking` (a running time on screen). */
export function useNow(ticking: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [ticking]);
  return now;
}

// ---------------------------------------------------------------- words

/** "12s", "3m 12s", "1h 4m". */
export function durationWords(seconds: number) {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60)
    return total % 60 ? `${minutes}m ${total % 60}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 ? `${hours}h ${minutes % 60}m` : `${hours}h`;
}

/** How long it has run, counting on between reads while it runs. */
export function runningSeconds(
  view: BackgroundProcessView,
  readAt: number,
  now: number,
) {
  return live(view) && readAt
    ? view.runtime_seconds + Math.max(0, now - readAt) / 1000
    : view.runtime_seconds;
}

/** What people call it: the name the agent gave it, else its command. */
export function processName(view: BackgroundProcessView) {
  return view.name || view.command;
}

/** "localhost:5173" for http://localhost:5173. */
export function shortAddress(url: string) {
  return url.replace(/^https?:\/\//, '');
}

const STOPPED_BY: Record<BackgroundProcessView['stopped_by'], string> = {
  '': '',
  you: 'by you',
  agent: 'by Row-Bot',
  quit: 'when Row-Bot closed',
  restart: 'when Row-Bot restarted',
  conversation: 'with its conversation',
  limit: 'at the limit',
};

/** Its state in words, with a tone: "Running · 3m 12s", "Finished in 2m", "Failed after 12s". */
export function processStatus(
  view: BackgroundProcessView,
  seconds: number,
): { tone: Tone; label: string; pulse: boolean } {
  const took = durationWords(seconds);
  switch (view.status) {
    case 'running':
      return { tone: 'accent', label: `Running · ${took}`, pulse: true };
    case 'stopping':
      return { tone: 'warning', label: 'Stopping…', pulse: true };
    case 'finished':
      return { tone: 'success', label: `Finished in ${took}`, pulse: false };
    case 'failed':
      return {
        tone: 'danger',
        label:
          view.exit_code === null ? "Couldn't start" : `Failed after ${took}`,
        pulse: false,
      };
    default: {
      const by = STOPPED_BY[view.stopped_by];
      return {
        tone: 'neutral',
        label: by ? `Stopped ${by} after ${took}` : `Stopped after ${took}`,
        pulse: false,
      };
    }
  }
}

/** Running or stopping: it can still be stopped and its output still grows. */
export function processLive(view: BackgroundProcessView) {
  return live(view);
}
