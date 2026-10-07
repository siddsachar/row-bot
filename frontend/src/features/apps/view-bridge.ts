/**
 * The host side of an app's view in chat (MCP Apps, spec 2026-01-26): JSON-RPC over postMessage
 * with one sandboxed frame. The frame runs with `sandbox="allow-scripts"` only, so its origin is
 * opaque ("null"): it never reaches Row-Bot's origin, cookies or storage. Only messages from that
 * frame's own window are read; anything the host does not offer is refused.
 */

const VIEW_PROTOCOL = '2026-01-26';
export const MAX_VIEW_HEIGHT = 720;
const MIN_VIEW_HEIGHT = 48;
const MAX_MESSAGE = 1024 * 1024;
const START_TIMEOUT = 10_000;
const CALLS_PER_MINUTE = 20;
const LINKS_PER_MINUTE = 3;

type ViewToolResult = {
  content?: unknown[];
  structuredContent?: Record<string, unknown> | null;
  isError?: boolean;
};

export type ViewState = 'loading' | 'ready' | 'failed' | 'ended';

export type ViewHost = {
  tool: { name: string; title: string };
  input: Record<string, unknown>;
  result: ViewToolResult | null;
  theme: () => 'light' | 'dark';
  platform: 'web' | 'desktop';
  /** A tool of the same app, called from the view: Row-Bot applies the app's access and approvals. */
  callTool: (
    name: string,
    args: Record<string, unknown>,
  ) => Promise<ViewToolResult>;
  openLink: (url: string) => void;
  onHeight: (height: number) => void;
  onState: (state: ViewState, message?: string) => void;
};

type Message = {
  jsonrpc: '2.0';
  id?: string | number;
  method?: string;
  params?: Record<string, unknown>;
  result?: unknown;
  error?: { code: number; message: string };
};

function sizeOf(value: unknown) {
  try {
    return JSON.stringify(value).length;
  } catch {
    return Infinity;
  }
}

export class ViewBridge {
  private frame: HTMLIFrameElement;
  private host: ViewHost;
  private initialized = false;
  private ended = false;
  private calls: number[] = [];
  private links: number[] = [];
  private nextId = 1;
  private waiting = new Map<string | number, (message: Message) => void>();
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(frame: HTMLIFrameElement, host: ViewHost) {
    this.frame = frame;
    this.host = host;
    this.timer = setTimeout(
      () =>
        !this.initialized &&
        this.end('failed', "This view didn't start. Try again later."),
      START_TIMEOUT,
    );
  }

  /** A message event from the window: anything not from this frame's own window is ignored. */
  handle(event: MessageEvent) {
    if (this.ended || event.source !== this.frame.contentWindow) return;
    if (event.origin !== 'null') return; // The sandbox gives the view no origin of its own.
    const message = event.data as Message;
    if (
      !message ||
      typeof message !== 'object' ||
      message.jsonrpc !== '2.0' ||
      sizeOf(message) > MAX_MESSAGE
    )
      return;
    if (message.method === undefined) {
      if (message.id !== undefined) this.waiting.get(message.id)?.(message);
      return;
    }
    if (message.id === undefined) this.notification(message);
    else void this.request(message);
  }

  /** The page's theme changed: a started view hears it (`ui/notifications/host-context-changed`). */
  themeChanged(theme: 'light' | 'dark') {
    if (!this.initialized || this.ended) return;
    this.post({
      jsonrpc: '2.0',
      method: 'ui/notifications/host-context-changed',
      params: { theme },
    });
  }

  /** The view loaded another page: the view is gone. */
  navigated() {
    this.end(
      'ended',
      'This view tried to open another page, so it was closed.',
    );
  }

  /** Ask the view to finish (`ui/resource-teardown`), then let it go. */
  async teardown() {
    if (this.ended) return;
    if (this.initialized) {
      const id = `host-${this.nextId++}`;
      await Promise.race([
        new Promise<void>((resolve) => {
          this.waiting.set(id, () => resolve());
          this.post({
            jsonrpc: '2.0',
            id,
            method: 'ui/resource-teardown',
            params: {},
          });
        }),
        new Promise((resolve) => setTimeout(resolve, 1000)),
      ]);
    }
    this.end('ended');
  }

  private end(state: ViewState, message?: string) {
    if (this.ended) return;
    this.ended = true;
    if (this.timer) clearTimeout(this.timer);
    this.waiting.clear();
    this.host.onState(state, message);
  }

  private post(message: Message) {
    // An opaque origin can't be named; the frame window itself is the only recipient.
    this.frame.contentWindow?.postMessage(message, '*');
  }

  private reply(id: string | number, result: unknown) {
    this.post({ jsonrpc: '2.0', id, result });
  }

  private refuse(id: string | number, code: number, message: string) {
    this.post({ jsonrpc: '2.0', id, error: { code, message } });
  }

  private notification(message: Message) {
    const params = message.params ?? {};
    if (message.method === 'ui/notifications/initialized') {
      if (this.initialized) return;
      this.initialized = true;
      if (this.timer) clearTimeout(this.timer);
      this.host.onState('ready');
      this.post({
        jsonrpc: '2.0',
        method: 'ui/notifications/tool-input',
        params: { arguments: this.host.input },
      });
      this.post(
        this.host.result
          ? {
              jsonrpc: '2.0',
              method: 'ui/notifications/tool-result',
              params: this.host.result,
            }
          : {
              jsonrpc: '2.0',
              method: 'ui/notifications/tool-cancelled',
              params: { reason: 'The tool did not finish.' },
            },
      );
    } else if (message.method === 'ui/notifications/size-changed') {
      const height = Number(params.height);
      if (Number.isFinite(height))
        this.host.onHeight(
          Math.min(
            MAX_VIEW_HEIGHT,
            Math.max(MIN_VIEW_HEIGHT, Math.ceil(height)),
          ),
        );
    }
  }

  private async request(message: Message) {
    const id = message.id!;
    const params = message.params ?? {};
    switch (message.method) {
      case 'ui/initialize':
        return this.reply(id, {
          protocolVersion: VIEW_PROTOCOL,
          hostInfo: { name: 'Row-Bot', version: '1' },
          hostCapabilities: { serverTools: {}, openLinks: {}, logging: {} },
          hostContext: {
            toolInfo: { tool: this.host.tool },
            theme: this.host.theme(),
            displayMode: 'inline',
            availableDisplayModes: ['inline'],
            containerDimensions: { maxHeight: MAX_VIEW_HEIGHT },
            locale: navigator.language,
            timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            platform: this.host.platform,
          },
        });
      case 'ping':
        return this.reply(id, {});
      case 'ui/request-display-mode':
        return this.reply(id, { mode: 'inline' });
      case 'ui/open-link': {
        const url = typeof params.url === 'string' ? params.url : '';
        if (
          !URL.canParse(url) ||
          !['http:', 'https:'].includes(new URL(url).protocol)
        )
          return this.refuse(id, -32000, 'Only web links can be opened.');
        // Never so often that a view could flood the desktop with browser windows (refused tries count
        // too), and only right after the person clicks in this view: the click activates the page and
        // focuses this view's own frame. An engine that can't tell (no `userActivation`) opens none.
        const now = Date.now();
        this.links = this.links.filter((at) => now - at < 60_000);
        if (this.links.length >= LINKS_PER_MINUTE)
          return this.refuse(id, -32000, 'Too many links from this view.');
        this.links.push(now);
        if (
          navigator.userActivation?.isActive !== true ||
          document.activeElement !== this.frame
        )
          return this.refuse(
            id,
            -32000,
            'Links open only when you click in the view.',
          );
        this.host.openLink(url);
        return this.reply(id, {});
      }
      case 'tools/call': {
        if (!this.initialized)
          return this.refuse(id, -32000, 'The view has not started yet.');
        const now = Date.now();
        this.calls = this.calls.filter((at) => now - at < 60_000);
        if (this.calls.length >= CALLS_PER_MINUTE)
          return this.refuse(
            id,
            -32000,
            'Too many requests from this view. Wait a moment.',
          );
        this.calls.push(now);
        const name = typeof params.name === 'string' ? params.name : '';
        const args =
          params.arguments &&
          typeof params.arguments === 'object' &&
          !Array.isArray(params.arguments)
            ? (params.arguments as Record<string, unknown>)
            : {};
        try {
          const result = await this.host.callTool(name, args);
          if (!this.ended) this.reply(id, result);
        } catch (cause) {
          if (!this.ended)
            this.refuse(
              id,
              -32000,
              cause instanceof Error ? cause.message : 'The tool did not run.',
            );
        }
        return;
      }
      default:
        return this.refuse(id, -32601, 'Method not found');
    }
  }
}
