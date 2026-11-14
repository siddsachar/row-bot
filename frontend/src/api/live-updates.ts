import type { LiveFrame } from './types';

/**
 * App-wide live updates (B407): one push channel for background state.
 *
 * Background work changes what the sidebar, Home and Monitor show without the
 * person asking: a workflow run starts, a helper finishes, an approval is
 * raised in another chat. The server keeps a revision per *topic* and sends
 * every open window the revisions (names and numbers only, never content) on
 * one authenticated stream. When a topic moves on, whatever shows it reads it
 * again at once through its normal API.
 *
 * Topics (the server's `row_bot.application.live_updates` lists who publishes):
 * - `conversations`: the conversation list (created, renamed, pinned,
 *   deleted; a conversation starting, waiting on the person or settling).
 * - `runs`: workflow runs started, finished, failed, stopped or waiting.
 * - `approvals`: approvals and questions raised, answered or expired.
 * - `helpers`: helper (agent) runs started, changing state or ended.
 * - `processes`: background processes started, stopped or ended.
 * - `monitor`: what Monitor lists as needing the person.
 * - `models`: the saved model catalog: refreshed, or a provider connected or
 *   disconnected (B411).
 * - `notices`: a new background notice, for windows with no conversation
 *   stream to carry it (Home, a new chat).
 * - `learning`: Row-Bot Learning (preview): a version, a comparison, the
 *   vault, a setting or a personal model's use changed.
 * - `proactive`: Proactive Row-Bot: a moment made, shown, settled or changed,
 *   a rule, a draft or an Activity line.
 *
 * Subscribe with `controller.live.subscribe(topic, onChange)`, which returns
 * an unsubscribe, or in a component with `useLiveUpdate(topics, onChange)`
 * from `src/live.ts`. `onChange` carries nothing: read the state again.
 * The window's first connection is where things stand (its screens read as
 * they open); every later one (a reconnect, the window coming back into view,
 * a new server) fires every topic once, so nothing that changed meanwhile
 * stays missed. Keep a slow timer (a minute) only as a fallback for when the
 * stream is unavailable (`fallbackDue`).
 */
export type LiveTopic =
  | 'conversations'
  | 'runs'
  | 'approvals'
  | 'helpers'
  | 'processes'
  | 'monitor'
  | 'models'
  | 'notices'
  | 'learning'
  | 'proactive';

/** How often a screen re-reads anyway, for when the live stream is unavailable. */
export const LIVE_FALLBACK_MS = 60_000;

/**
 * Whether a timer read is due: always while the live stream is down (the
 * screen's own pace keeps it current), otherwise only once a minute since
 * `readAt`, as a fallback.
 */
export function fallbackDue(
  live: Pick<LiveUpdates, 'connected'> | null | undefined,
  readAt: number,
  now = Date.now(),
): boolean {
  return !live?.connected || now - readAt >= LIVE_FALLBACK_MS;
}

export type LiveTransport = {
  observeLive?(signal: AbortSignal): AsyncIterable<LiveFrame>;
  pollLive?(
    position: { after: number; epoch: string } | undefined,
    signal: AbortSignal,
  ): Promise<LiveFrame>;
};

const RETRY_MS = [1000, 2000, 5000, 10_000, 30_000];

const aborted = (error: unknown) =>
  (error as { name?: string } | null)?.name === 'AbortError';

export class LiveUpdates {
  private listeners = new Map<LiveTopic, Set<() => void>>();
  private seen: LiveFrame | null = null;
  // The next frame starts a later connection: every topic reads again.
  private resync = false;
  private receiving = false;
  private run: AbortController | null = null;
  private failures = 0;
  private streamFailures = 0;

  constructor(
    private readonly transport: LiveTransport,
    private readonly wait: (
      ms: number,
      signal: AbortSignal,
    ) => Promise<void> = (ms, signal) =>
      new Promise((resolve) => {
        const timer = setTimeout(resolve, ms);
        signal.addEventListener(
          'abort',
          () => {
            clearTimeout(timer);
            resolve();
          },
          { once: true },
        );
      }),
    private readonly random: () => number = Math.random,
  ) {}

  /** Call `onChange` whenever `topic` changes; returns the unsubscribe. */
  subscribe(topic: LiveTopic, onChange: () => void): () => void {
    let listeners = this.listeners.get(topic);
    if (!listeners) {
      listeners = new Set();
      this.listeners.set(topic, listeners);
    }
    listeners.add(onChange);
    return () => {
      listeners.delete(onChange);
    };
  }

  /** Whether frames are arriving now. */
  get connected(): boolean {
    return this.receiving;
  }

  /** Open the stream (once); a later connection resyncs every topic. */
  start(): void {
    if (this.run || (!this.transport.observeLive && !this.transport.pollLive))
      return;
    const run = new AbortController();
    this.run = run;
    this.resync = true;
    void this.loop(run.signal);
  }

  stop(): void {
    this.run?.abort();
    this.run = null;
    this.receiving = false;
  }

  private async loop(signal: AbortSignal): Promise<void> {
    this.failures = 0;
    while (!signal.aborted) {
      const streaming = Boolean(
        this.transport.observeLive && this.streamFailures < 2,
      );
      let framed = false;
      try {
        if (streaming) {
          for await (const frame of this.transport.observeLive!(signal)) {
            if (signal.aborted) return;
            framed = true;
            this.streamFailures = 0;
            this.receive(frame);
          }
          if (signal.aborted) return;
          throw new Error('stream ended');
        }
        if (!this.transport.pollLive) throw new Error('unavailable');
        const position = this.seen
          ? { after: this.seen.revision, epoch: this.seen.epoch }
          : undefined;
        this.receive(await this.transport.pollLive(position, signal));
      } catch (error) {
        if (signal.aborted || aborted(error)) return;
        // A stream that never delivers twice (a proxy holding it back, say):
        // long-poll instead.
        if (streaming && !framed) this.streamFailures += 1;
        this.failures += 1;
        this.receiving = false;
        // The next frame is a reconnect: read everything again.
        this.resync = true;
        const delay = RETRY_MS[Math.min(this.failures, RETRY_MS.length) - 1];
        await this.wait(delay * (0.8 + this.random() * 0.4), signal);
      }
    }
  }

  private receive(frame: LiveFrame): void {
    const previous = this.seen;
    this.seen = frame;
    this.failures = 0;
    this.receiving = true;
    const everything = this.resync || previous?.epoch !== frame.epoch;
    this.resync = false;
    // The window's first frame is where things stand: its screens read as
    // they open.
    if (!previous) return;
    for (const [topic, listeners] of this.listeners) {
      if (
        !everything &&
        (frame.topics[topic] ?? 0) === (previous?.topics[topic] ?? 0)
      )
        continue;
      for (const listener of [...listeners]) {
        try {
          listener();
        } catch {
          // One screen's failure never stops the others hearing it.
        }
      }
    }
  }
}
