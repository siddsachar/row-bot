import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MAX_VIEW_HEIGHT, ViewBridge, type ViewHost } from './view-bridge';

function setup(overrides: Partial<ViewHost> = {}) {
  const posted: unknown[] = [];
  const contentWindow = {
    postMessage: vi.fn((message: unknown) => posted.push(message)),
  };
  const frame = { contentWindow } as unknown as HTMLIFrameElement;
  const host: ViewHost = {
    tool: { name: 'counter', title: 'Counter' },
    input: { start: 3 },
    result: {
      content: [{ type: 'text', text: '3' }],
      structuredContent: { count: 3 },
    },
    theme: () => 'dark',
    platform: 'web',
    callTool: vi.fn(async () => ({ structuredContent: { count: 4 } })),
    openLink: vi.fn(),
    onHeight: vi.fn(),
    onState: vi.fn(),
    ...overrides,
  };
  const bridge = new ViewBridge(frame, host);
  const send = (
    data: unknown,
    { source = contentWindow, origin = 'null' } = {},
  ) => bridge.handle({ data, source, origin } as unknown as MessageEvent);
  const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
  return { bridge, host, posted, send, flush, contentWindow };
}

const start = async (view: ReturnType<typeof setup>) => {
  view.send({
    jsonrpc: '2.0',
    id: 1,
    method: 'ui/initialize',
    params: { protocolVersion: '2026-01-26' },
  });
  view.send({
    jsonrpc: '2.0',
    method: 'ui/notifications/initialized',
    params: {},
  });
  await view.flush();
};

describe('ViewBridge', () => {
  beforeEach(() => vi.useRealTimers());
  afterEach(() => vi.useRealTimers());

  it('answers only its own frame, and only from an opaque origin', async () => {
    const view = setup();
    view.send(
      { jsonrpc: '2.0', id: 1, method: 'ping' },
      { source: {} as never },
    );
    view.send(
      { jsonrpc: '2.0', id: 2, method: 'ping' },
      { origin: 'http://127.0.0.1:8080' },
    );
    view.send({ jsonrpc: '1.0', id: 3, method: 'ping' });
    view.send({
      jsonrpc: '2.0',
      id: 4,
      method: 'ping',
      params: { pad: 'x'.repeat(1024 * 1024) },
    });
    expect(view.posted).toEqual([]);
    view.send({ jsonrpc: '2.0', id: 5, method: 'ping' });
    expect(view.posted).toEqual([{ jsonrpc: '2.0', id: 5, result: {} }]);
  });

  it('starts the view, then sends the tool input and result in order', async () => {
    const view = setup();
    await start(view);
    const [init, input, result] = view.posted as Array<
      Record<string, Record<string, unknown>>
    >;
    expect(init.result.protocolVersion).toBe('2026-01-26');
    expect(init.result.hostContext).toMatchObject({
      theme: 'dark',
      displayMode: 'inline',
      containerDimensions: { maxHeight: MAX_VIEW_HEIGHT },
      toolInfo: { tool: { name: 'counter', title: 'Counter' } },
    });
    expect(init.result.hostCapabilities).toEqual({
      serverTools: {},
      openLinks: {},
      logging: {},
    });
    expect(input).toMatchObject({
      method: 'ui/notifications/tool-input',
      params: { arguments: { start: 3 } },
    });
    expect(result).toMatchObject({
      method: 'ui/notifications/tool-result',
      params: { structuredContent: { count: 3 } },
    });
    expect(view.host.onState).toHaveBeenCalledWith('ready');
  });

  it('passes the view’s tool calls to Row-Bot only once started, and refuses the rest', async () => {
    const view = setup();
    view.send({
      jsonrpc: '2.0',
      id: 9,
      method: 'tools/call',
      params: { name: 'increment' },
    });
    await view.flush();
    expect(view.posted.pop()).toMatchObject({ id: 9, error: { code: -32000 } });
    expect(view.host.callTool).not.toHaveBeenCalled();
    await start(view);
    view.send({
      jsonrpc: '2.0',
      id: 10,
      method: 'tools/call',
      params: { name: 'increment', arguments: { by: 1 } },
    });
    await view.flush();
    expect(view.host.callTool).toHaveBeenCalledWith('increment', { by: 1 });
    expect(view.posted.pop()).toEqual({
      jsonrpc: '2.0',
      id: 10,
      result: { structuredContent: { count: 4 } },
    });
    view.send({
      jsonrpc: '2.0',
      id: 11,
      method: 'ui/message',
      params: { role: 'user', content: [] },
    });
    view.send({
      jsonrpc: '2.0',
      id: 12,
      method: 'resources/read',
      params: { uri: 'ui://other' },
    });
    expect(view.posted.slice(-2)).toMatchObject([
      { id: 11, error: { code: -32601 } },
      { id: 12, error: { code: -32601 } },
    ]);
  });

  it('says plainly when Row-Bot did not run a call (a denial, say)', async () => {
    const view = setup({
      callTool: vi.fn(async () =>
        Promise.reject(new Error('You denied this action.')),
      ),
    });
    await start(view);
    view.send({
      jsonrpc: '2.0',
      id: 20,
      method: 'tools/call',
      params: { name: 'delete' },
    });
    await view.flush();
    expect(view.posted.pop()).toEqual({
      jsonrpc: '2.0',
      id: 20,
      error: { code: -32000, message: 'You denied this action.' },
    });
  });

  it('limits how often a view can call tools', async () => {
    const view = setup();
    await start(view);
    for (let id = 100; id < 121; id += 1)
      view.send({
        jsonrpc: '2.0',
        id,
        method: 'tools/call',
        params: { name: 'increment' },
      });
    await view.flush();
    expect(view.host.callTool).toHaveBeenCalledTimes(20);
    expect(
      view.posted.find((message) => (message as { id?: number }).id === 120),
    ).toMatchObject({ error: { code: -32000 } });
  });

  it('opens web links only, and keeps the view inline and within its height', async () => {
    const view = setup();
    await start(view);
    view.send({
      jsonrpc: '2.0',
      id: 30,
      method: 'ui/open-link',
      params: { url: 'javascript:alert(1)' },
    });
    view.send({
      jsonrpc: '2.0',
      id: 31,
      method: 'ui/open-link',
      params: { url: 'https://example.test/doc' },
    });
    view.send({
      jsonrpc: '2.0',
      id: 32,
      method: 'ui/request-display-mode',
      params: { mode: 'fullscreen' },
    });
    view.send({
      jsonrpc: '2.0',
      method: 'ui/notifications/size-changed',
      params: { height: 99999 },
    });
    view.send({
      jsonrpc: '2.0',
      method: 'ui/notifications/size-changed',
      params: { height: 2 },
    });
    await view.flush();
    expect(view.host.openLink).toHaveBeenCalledTimes(1);
    expect(view.host.openLink).toHaveBeenCalledWith('https://example.test/doc');
    expect(view.posted.slice(-3)).toMatchObject([
      { id: 30, error: { code: -32000 } },
      { id: 31, result: {} },
      { id: 32, result: { mode: 'inline' } },
    ]);
    expect(view.host.onHeight).toHaveBeenNthCalledWith(1, MAX_VIEW_HEIGHT);
    expect(view.host.onHeight).toHaveBeenNthCalledWith(2, 48);
  });

  it('opens only a few links a minute', async () => {
    const view = setup();
    await start(view);
    for (let id = 40; id < 45; id += 1)
      view.send({
        jsonrpc: '2.0',
        id,
        method: 'ui/open-link',
        params: { url: `https://example.test/${id}` },
      });
    await view.flush();
    expect(view.host.openLink).toHaveBeenCalledTimes(3);
    expect(view.posted.slice(-2)).toMatchObject([
      { id: 43, error: { code: -32000 } },
      { id: 44, error: { code: -32000 } },
    ]);
  });

  it('opens a link only right after a click in the view', async () => {
    const view = setup();
    await start(view);
    vi.stubGlobal('navigator', {
      ...navigator,
      language: 'en',
      userActivation: { isActive: false, hasBeenActive: true },
    });
    try {
      view.send({
        jsonrpc: '2.0',
        id: 50,
        method: 'ui/open-link',
        params: { url: 'https://example.test/doc' },
      });
      await view.flush();
      expect(view.host.openLink).not.toHaveBeenCalled();
      expect(view.posted.pop()).toMatchObject({
        id: 50,
        error: { code: -32000 },
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('gives up on a view that never starts, and ends one that navigates away', () => {
    vi.useFakeTimers();
    const silent = setup();
    vi.advanceTimersByTime(10_001);
    expect(silent.host.onState).toHaveBeenCalledWith(
      'failed',
      expect.stringMatching(/didn't start/),
    );
    const moved = setup();
    moved.bridge.navigated();
    expect(moved.host.onState).toHaveBeenCalledWith(
      'ended',
      expect.stringMatching(/another page/),
    );
    moved.send({ jsonrpc: '2.0', id: 1, method: 'ping' });
    expect(moved.posted).toEqual([]);
  });

  it('tells a started view when the page theme changes', async () => {
    const view = setup();
    view.bridge.themeChanged('light'); // Not started yet: nothing to tell.
    expect(view.posted).toEqual([]);
    await start(view);
    view.bridge.themeChanged('light');
    expect(view.posted.pop()).toEqual({
      jsonrpc: '2.0',
      method: 'ui/notifications/host-context-changed',
      params: { theme: 'light' },
    });
  });

  it('asks the view to finish before it is removed', async () => {
    const view = setup();
    await start(view);
    const done = view.bridge.teardown();
    const teardown = view.posted.pop() as { id: number; method: string };
    expect(teardown).toMatchObject({ method: 'ui/resource-teardown' });
    view.send({ jsonrpc: '2.0', id: teardown.id, result: {} });
    await done;
    expect(view.host.onState).toHaveBeenLastCalledWith('ended', undefined);
  });
});
