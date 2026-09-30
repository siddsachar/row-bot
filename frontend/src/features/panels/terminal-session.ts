import { Terminal, type ITheme } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { clientError } from '../../api/errors';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform } from '../../platform';
import { detectShortcutPlatform } from '../../ui/format';

type TerminalApi = Pick<
  ClientController,
  'terminalRead' | 'terminalInput' | 'terminalResize' | 'terminalDisconnect'
>;

export type TerminalState = {
  status: 'idle' | 'connecting' | 'open' | 'failed' | 'ended';
  /** Why the terminal could not open or stopped answering. */
  error: string;
  /** Reconnect can help (the desktop app is reconnecting, a read failed). */
  recoverable: boolean;
  /** The terminal has shown output, so a failure keeps it on screen. */
  started: boolean;
  /** Output from while the terminal was away is no longer available. */
  truncated: boolean;
};

// What Ctrl+C types. The desktop app turns it, sent on its own, into
// stopping the running command (ConPTY ignores a typed Ctrl+C).
export const INTERRUPT = '\x03';
// The server takes at most 16 KB of input at a time: 4,096 characters of up
// to four UTF-8 bytes each.
const INPUT_CHARACTERS = 4096;
const MIN_COLS = 20;
const MAX_COLS = 500;
const MIN_ROWS = 5;
const MAX_ROWS = 200;

/** Split typed or pasted text into pieces the server accepts, in order. */
function pieces(text: string): string[] {
  const result: string[] = [];
  let start = 0;
  while (start < text.length) {
    let end = Math.min(text.length, start + INPUT_CHARACTERS);
    // Never split an emoji or another character outside the BMP.
    if (end < text.length && /[\uD800-\uDBFF]/.test(text[end - 1])) end -= 1;
    result.push(text.slice(start, end));
    start = end;
  }
  return result;
}

/** The app's current colours and monospace font, read from its tokens. */
function terminalTheme(): { theme: ITheme; fontFamily?: string } {
  const style = getComputedStyle(document.documentElement);
  const read = (name: string) => style.getPropertyValue(name).trim();
  const accent = read('--accent-solid');
  const background = read('--code-background');
  const entries: [keyof ITheme, string][] = [
    ['background', background],
    ['foreground', read('--code-text')],
    ['cursor', accent],
    ['cursorAccent', background],
    ['selectionBackground', accent && `${accent}66`],
  ];
  return {
    theme: Object.fromEntries(entries.filter(([, value]) => value)),
    fontFamily: read('--font-mono') || undefined,
  };
}

/**
 * One terminal for the page: the xterm instance, its scrollback and the
 * desktop terminal it shows. It outlives the dock, so closing and opening
 * the dock keeps everything on screen and reads on from where it was.
 */
export class TerminalSession {
  readonly terminal: Terminal;
  private readonly fit = new FitAddon();
  private readonly listeners = new Set<() => void>();
  private state: TerminalState = {
    status: 'idle',
    error: '',
    recoverable: false,
    started: false,
    truncated: false,
  };
  private lease = '';
  private cursor = 0;
  /** Typed text not yet sent; an interrupt always goes on its own. */
  private queue: string[] = [];
  private sending = false;
  private reading: AbortController | null = null;
  private wake: (() => void) | null = null;
  private inputAt = 0;
  private outputAt = 0;
  private sentSize = '';
  private resizeTimer = 0;
  private focusPending = false;

  constructor(
    private readonly api: TerminalApi,
    private readonly platform: ClientPlatform,
  ) {
    const reduceMotion = window.matchMedia(
      '(prefers-reduced-motion: reduce)',
    ).matches;
    const { theme, fontFamily } = terminalTheme();
    this.terminal = new Terminal({
      cols: 120,
      rows: 30,
      theme,
      fontFamily,
      fontSize: 13,
      scrollback: 5000,
      cursorBlink: !reduceMotion,
      // Keeps every colour a program uses readable on either theme.
      minimumContrastRatio: 4.5,
      // Hyperlinks printed by a program open through the reviewed
      // external-link path, never a new window of this page.
      linkHandler: {
        activate: (_event, uri) => void this.platform.openExternal(uri),
      },
    });
    this.terminal.loadAddon(this.fit);
    const mac = detectShortcutPlatform() === 'mac';
    this.terminal.attachCustomKeyEventHandler((event) => {
      if (event.type !== 'keydown') return true;
      const plain =
        event.ctrlKey && !event.altKey && !event.metaKey && !event.shiftKey;
      const key = event.key.toLowerCase();
      // Selected text copies, as in a terminal (the browser's copy event);
      // otherwise Ctrl+C stops the running command.
      if (plain && key === 'c' && this.terminal.hasSelection()) return false;
      // Ctrl+V pastes through the browser's paste event instead of typing ^V.
      if (plain && key === 'v' && !mac) return false;
      return true;
    });
    this.terminal.onData((data) => this.type(data));
    this.terminal.onResize(() => this.sendSize());
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  snapshot = () => this.state;

  private update(change: Partial<TerminalState>) {
    this.state = { ...this.state, ...change };
    for (const listener of this.listeners) listener();
  }

  /** Show the terminal in `host` until the returned function is called. */
  attach(host: HTMLElement): () => void {
    let attached = true;
    const show = () => {
      if (!attached) return;
      // The first dock opens the terminal; later ones take its element over.
      if (this.terminal.element) host.append(this.terminal.element);
      else this.terminal.open(host);
      this.layout();
      this.read();
      if (this.focusPending) this.focus();
    };
    // xterm measures its cells once, when it opens: the bundled monospace
    // font loads on first use, so the first opening waits for it.
    const fonts = document.fonts as FontFaceSet | undefined;
    const family = this.terminal.options.fontFamily;
    if (this.terminal.element || !fonts || !family) show();
    else
      void fonts
        .load(`${this.terminal.options.fontSize}px ${family}`)
        .catch(() => [])
        .then(show);
    return () => {
      attached = false;
      this.reading?.abort();
      this.reading = null;
      this.terminal.element?.remove();
    };
  }

  /** Open the desktop terminal once; later calls keep the same one. */
  async connect(conversationId: string | null): Promise<void> {
    if (this.lease || this.state.status === 'connecting') return;
    this.update({ status: 'connecting', error: '', recoverable: false });
    const result = await this.platform.openTerminal(conversationId);
    if (result.status !== 'ok') {
      // The real reason, not "needs the desktop app" inside it (B238).
      const reason = result.status === 'unavailable' ? result.reason : '';
      this.update({
        status: 'failed',
        recoverable: reason !== 'terminal_requires_native',
        error:
          reason === 'terminal_requires_native'
            ? 'The terminal needs the Row-Bot desktop app.'
            : reason === 'native_reconnecting'
              ? 'Desktop features are reconnecting. Try again in a moment.'
              : 'Row-Bot couldn’t start the terminal. Try again.',
      });
      return;
    }
    this.lease = result.value.terminalId;
    this.sentSize = '';
    this.update({ status: 'open' });
    this.sendSize();
    this.read();
  }

  /** Open a new desktop terminal; the screen and its scrollback stay. */
  async reconnect(conversationId: string | null): Promise<void> {
    const previous = this.lease;
    this.lease = '';
    this.queue = [];
    this.reading?.abort();
    this.reading = null;
    if (previous) void this.api.terminalDisconnect(previous).catch(() => {});
    this.update({ status: 'idle' });
    await this.connect(conversationId);
  }

  interrupt() {
    this.type(INTERRUPT);
  }

  /** Clears what is shown; the read position stays, so it never returns. */
  clear() {
    this.terminal.clear();
    this.update({ truncated: false });
  }

  focus() {
    // Asked before the terminal is on screen, it takes focus when it shows.
    this.focusPending = !this.terminal.element?.isConnected;
    this.terminal.focus();
  }

  applyTheme() {
    const { theme, fontFamily } = terminalTheme();
    this.terminal.options.theme = theme;
    if (fontFamily) this.terminal.options.fontFamily = fontFamily;
  }

  /** Fit the grid to the dock (the server allows 20–500 by 5–200). */
  layout() {
    const size = this.fit.proposeDimensions();
    // A hidden dock has no size to fit.
    if (!size || !Number.isFinite(size.cols) || !Number.isFinite(size.rows))
      return;
    const cols = Math.min(MAX_COLS, Math.max(MIN_COLS, size.cols));
    const rows = Math.min(MAX_ROWS, Math.max(MIN_ROWS, size.rows));
    if (cols !== this.terminal.cols || rows !== this.terminal.rows)
      this.terminal.resize(cols, rows);
  }

  // A dragged divider resizes many times a second: the shell hears the
  // size it settles on.
  private sendSize() {
    window.clearTimeout(this.resizeTimer);
    this.resizeTimer = window.setTimeout(() => {
      const { cols, rows } = this.terminal;
      const size = `${cols}x${rows}`;
      if (!this.lease || size === this.sentSize) return;
      this.sentSize = size;
      this.api.terminalResize(this.lease, cols, rows).catch((cause) => {
        this.sentSize = '';
        this.fail(cause);
      });
    }, 100);
  }

  private type(data: string) {
    if (!this.lease) return;
    const last = this.queue.length - 1;
    if (data !== INTERRUPT && last >= 0 && this.queue[last] !== INTERRUPT)
      this.queue[last] += data;
    else this.queue.push(data);
    void this.send();
  }

  // One request at a time keeps keys in order; keys typed meanwhile go
  // together in the next one.
  private async send() {
    if (this.sending) return;
    this.sending = true;
    try {
      while (this.queue.length && this.lease) {
        const lease = this.lease;
        const data = this.queue.shift()!;
        for (const piece of data === INTERRUPT ? [data] : pieces(data))
          await this.api.terminalInput(lease, piece);
        this.inputAt = Date.now();
        this.wake?.();
      }
    } catch (cause) {
      this.queue = [];
      this.fail(cause);
    } finally {
      this.sending = false;
    }
  }

  private fail(cause: unknown) {
    this.update({
      status: 'failed',
      error: clientError(cause).message,
      recoverable: true,
    });
  }

  // Output is read at once while more is waiting, every frame just after a
  // key (its echo), a little slower while output flows, and a few times a
  // second when nothing happens.
  private delay(pending: boolean): number {
    const now = Date.now();
    if (pending) return 0;
    if (now - this.inputAt < 500) return 16;
    if (now - this.outputAt < 1000) return 50;
    return 250;
  }

  private read() {
    if (!this.lease || !this.terminal.element?.isConnected || this.reading)
      return;
    const reading = new AbortController();
    this.reading = reading;
    const lease = this.lease;
    void (async () => {
      while (!reading.signal.aborted && this.lease === lease) {
        let value;
        try {
          value = await this.api.terminalRead(
            lease,
            this.cursor,
            reading.signal,
          );
        } catch (cause) {
          if (!reading.signal.aborted) this.fail(cause);
          break;
        }
        if (reading.signal.aborted) break;
        this.cursor = value.cursor;
        const data = value.frames.map((frame) => frame.data).join('');
        if (data) {
          this.terminal.write(data);
          this.outputAt = Date.now();
          if (!this.state.started) this.update({ started: true });
        }
        if (value.truncated) this.update({ truncated: true });
        if (value.status === 'stopped') {
          this.update({ status: 'ended' });
          break;
        }
        await new Promise<void>((resolve) => {
          const timer = window.setTimeout(
            done,
            this.delay(value.latest > value.cursor),
          );
          function done() {
            window.clearTimeout(timer);
            resolve();
          }
          this.wake = done;
          reading.signal.addEventListener('abort', done, { once: true });
        });
        this.wake = null;
      }
      if (this.reading === reading) this.reading = null;
    })();
  }
}

const sessions = new WeakMap<
  TerminalApi,
  WeakMap<ClientPlatform, TerminalSession>
>();

/** The page's terminal for this connection and desktop bridge. */
export function terminalSession(
  api: TerminalApi,
  platform: ClientPlatform,
): TerminalSession {
  let byPlatform = sessions.get(api);
  if (!byPlatform) {
    byPlatform = new WeakMap();
    sessions.set(api, byPlatform);
  }
  let session = byPlatform.get(platform);
  if (!session) {
    session = new TerminalSession(api, platform);
    byPlatform.set(platform, session);
  }
  return session;
}
