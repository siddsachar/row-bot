import { afterEach, describe, expect, it, vi } from 'vitest';
import { fallbackDue, LIVE_FALLBACK_MS, LiveUpdates } from './live-updates';
import { ClientController } from './controller';
import { FixtureTransport } from './fixtures';
import type { ConversationPage, LiveFrame, NoticePage } from './types';

const frame = (
  revision: number,
  topics: Partial<LiveFrame['topics']> = {},
  epoch = 'server-a',
): LiveFrame => ({
  epoch,
  revision,
  topics: {
    conversations: 0,
    runs: 0,
    approvals: 0,
    helpers: 0,
    processes: 0,
    monitor: 0,
    learning: 0,
    ...topics,
  },
});

/** A live stream the test feeds frame by frame, and can drop. */
function stream() {
  let push: ((value: LiveFrame) => void) | null = null;
  let fail: ((error: Error) => void) | null = null;
  const queued: LiveFrame[] = [];
  let opened = 0;
  const transport = {
    async *observeLive(signal: AbortSignal): AsyncGenerator<LiveFrame> {
      opened += 1;
      while (!signal.aborted) {
        const next =
          queued.shift() ??
          (await new Promise<LiveFrame>((resolve, reject) => {
            push = resolve;
            fail = reject;
            signal.addEventListener('abort', () =>
              reject(new DOMException('Cancelled', 'AbortError')),
            );
          }));
        yield next;
      }
    },
  };
  return {
    transport,
    send(value: LiveFrame) {
      if (push) {
        const resolve = push;
        push = null;
        resolve(value);
      } else queued.push(value);
    },
    drop() {
      fail?.(new TypeError('Network unavailable'));
      push = null;
    },
    get opened() {
      return opened;
    },
  };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const noWait = () => Promise.resolve();

afterEach(() => vi.useRealTimers());

describe('live updates (B407)', () => {
  it('calls a topic’s subscribers when its revision moves on, and only then', async () => {
    const live = stream();
    const updates = new LiveUpdates(live.transport, noWait);
    const conversations = vi.fn();
    const approvals = vi.fn();
    const stop = updates.subscribe('conversations', conversations);
    updates.subscribe('approvals', approvals);
    updates.start();
    expect(updates.connected).toBe(false);
    // The window's first frame is where things stand: its screens read as
    // they open, so nothing reads again yet.
    live.send(frame(4, { conversations: 2, approvals: 1 }));
    await flush();
    expect(updates.connected).toBe(true);
    expect(conversations).not.toHaveBeenCalled();
    live.send(frame(5, { conversations: 3, approvals: 1 }));
    await flush();
    expect(conversations).toHaveBeenCalledTimes(1);
    expect(approvals).not.toHaveBeenCalled();
    stop();
    live.send(frame(6, { conversations: 4, approvals: 2 }));
    await flush();
    expect(conversations).toHaveBeenCalledTimes(1);
    expect(approvals).toHaveBeenCalledTimes(1);
    updates.stop();
  });

  it('reads every topic again after a reconnect, and after the window comes back', async () => {
    const live = stream();
    const updates = new LiveUpdates(live.transport, noWait);
    const runs = vi.fn();
    const monitor = vi.fn();
    updates.subscribe('runs', runs);
    updates.subscribe('monitor', monitor);
    updates.start();
    live.send(frame(1));
    await flush();
    live.send(frame(2, { learning: 1 }));
    await flush();
    expect(runs).not.toHaveBeenCalled();
    // The connection drops; nothing moved as far as the revisions say, but
    // whatever happened meanwhile is read again.
    live.drop();
    await flush();
    expect(updates.connected).toBe(false);
    live.send(frame(2, { learning: 1 }));
    await flush();
    expect(live.opened).toBe(2);
    expect(runs).toHaveBeenCalledTimes(1);
    expect(monitor).toHaveBeenCalledTimes(1);
    // Hidden, then shown again.
    updates.stop();
    updates.start();
    live.send(frame(2, { learning: 1 }));
    await flush();
    expect(runs).toHaveBeenCalledTimes(2);
    // A restarted server numbers from its own epoch: everything again.
    live.send(frame(0, {}, 'server-b'));
    await flush();
    expect(runs).toHaveBeenCalledTimes(3);
    expect(monitor).toHaveBeenCalledTimes(3);
    updates.stop();
  });

  it('long-polls from the last revision it saw when the stream never delivers', async () => {
    const asked: ({ after: number; epoch: string } | undefined)[] = [];
    const answers = [frame(7, { approvals: 3 }), frame(8, { approvals: 4 })];
    const approvals = vi.fn();
    const updates = new LiveUpdates(
      {
        // eslint-disable-next-line require-yield
        async *observeLive(): AsyncGenerator<LiveFrame> {
          throw new TypeError('Network unavailable');
        },
        pollLive: async (position, signal) => {
          asked.push(position);
          const next = answers.shift();
          if (next) return next;
          return new Promise<LiveFrame>((_, reject) =>
            signal.addEventListener('abort', () =>
              reject(new DOMException('Cancelled', 'AbortError')),
            ),
          );
        },
      },
      noWait,
    );
    updates.subscribe('approvals', approvals);
    updates.start();
    await vi.waitFor(() => expect(asked).toHaveLength(3));
    expect(asked).toEqual([
      undefined,
      { after: 7, epoch: 'server-a' },
      { after: 8, epoch: 'server-a' },
    ]);
    expect(approvals).toHaveBeenCalledTimes(1);
    updates.stop();
  });

  it('keeps a screen’s own pace while the stream is down, else a minute', () => {
    expect(fallbackDue(undefined, 0, 1000)).toBe(true);
    expect(fallbackDue({ connected: false }, 0, 1000)).toBe(true);
    expect(fallbackDue({ connected: true }, 0, LIVE_FALLBACK_MS - 1)).toBe(
      false,
    );
    expect(fallbackDue({ connected: true }, 0, LIVE_FALLBACK_MS)).toBe(true);
  });

  it('shows a conversation a background run started in the sidebar at once', async () => {
    const live = stream();
    const transport = Object.assign(
      new FixtureTransport({ scenario: 'normal' }),
      live.transport,
    );
    const controller = new ClientController(transport);
    await controller.start();
    await vi.waitFor(() =>
      expect(controller.getSnapshot().conversations.length).toBeGreaterThan(0),
    );
    live.send(frame(1));
    await flush();
    await flush();
    const listed = controller.getSnapshot().conversations;
    const started = {
      ...listed[0],
      id: 'run-morning-digest',
      title: 'Morning digest',
    };
    const listing = vi.spyOn(transport, 'listConversations').mockResolvedValue({
      items: [started, ...listed],
      has_more: false,
      next_cursor: null,
    } satisfies ConversationPage);
    live.send(frame(2, { conversations: 1, runs: 1 }));
    await vi.waitFor(() =>
      expect(controller.getSnapshot().conversations[0]?.title).toBe(
        'Morning digest',
      ),
    );
    expect(listing).toHaveBeenCalled();
    expect(controller.getSnapshot().loadingConversations).toBe(false);
    controller.dispose();
  });

  it('shows "Row-Bot sent you …" at once in a window with no conversation open', async () => {
    const live = stream();
    const epoch = '00000000-0000-4000-8000-00000000e90f';
    let sent = false;
    const transport = Object.assign(
      new FixtureTransport({ scenario: 'normal' }),
      live.transport,
      {
        async notices(): Promise<NoticePage> {
          return {
            server_epoch: epoch,
            latest: sent ? 1 : 0,
            notices: sent
              ? [
                  {
                    id: 1,
                    level: 'info' as const,
                    title: 'Row-Bot sent you q3-report.pdf',
                    message: 'Open the chat to download it.',
                    source: 'deliveries',
                    requested: false,
                    startup: false,
                    count: 1,
                    at: '2026-10-28T22:00:00Z',
                  },
                ]
              : [],
            startup_warnings: [],
          };
        },
      },
    );
    localStorage.clear();
    const controller = new ClientController(transport);
    const shown: string[] = [];
    controller.onNotice((notice) => shown.push(notice.title));
    await controller.start();
    live.send(frame(1));
    await flush();
    sent = true;
    // Not at the next half-minute read: the notices topic moving on reads them now.
    live.send(frame(2, { notices: 1 }));
    await vi.waitFor(() =>
      expect(shown).toEqual(['Row-Bot sent you q3-report.pdf']),
    );
    controller.dispose();
  });
});
