import { afterEach, describe, expect, it, vi } from 'vitest';
import { createBrowserPlatform } from './browser';
import { createFakePlatform } from './fake';
import { selectClientPlatform } from './index';
import { createPyWebViewPlatform } from './native';
import type { MediaTransport } from './types';
import { safeExternalUrl } from './types';

const attachment = {
  attachment_ref: 'fixture',
  name: 'fixture.txt',
  mime_type: 'application/octet-stream' as const,
  size_bytes: 7,
  revision: '1',
};
const media = () =>
  ({
    upload: vi.fn().mockResolvedValue(attachment),
    download: vi.fn().mockResolvedValue(new Blob(['fixture'])),
    saveToExports: vi.fn().mockResolvedValue({
      file_name: 'conversation-export.md',
      folder: 'Row-Bot › Exports',
    }),
    revealExport: vi.fn().mockResolvedValue({ status: 'opened' }),
  }) satisfies MediaTransport;
afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

describe('browser capabilities', () => {
  it('uses browser APIs and never infers native authority from viewport or spoofed globals', async () => {
    const legacy = vi.fn();
    Object.assign(window, {
      pywebview: { api: { choose_file: legacy } },
      __ROW_BOT_NATIVE__: true,
    });
    const adapter = await selectClientPlatform(media(), undefined);
    expect(await adapter.discover()).toMatchObject({
      status: 'ok',
      value: { kind: 'browser' },
    });
    expect(await adapter.managedWindow('/app-v2/')).toMatchObject({
      status: 'unavailable',
    });
    expect(
      await adapter.buddyPlacement('tear_off', { x: 10, y: 20 }),
    ).toMatchObject({ status: 'unavailable' });
    expect(legacy).not.toHaveBeenCalled();
    // The spoofed globals would make later tests' window a desktop one.
    Reflect.deleteProperty(window, 'pywebview');
    Reflect.deleteProperty(window, '__ROW_BOT_NATIVE__');
  });

  it('returns selected File objects without local paths and cleans cancellation listeners', async () => {
    vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(
      () => undefined,
    );
    const adapter = createBrowserPlatform(media());
    const picked = adapter.selectFile();
    const input = document.querySelector('input')!;
    const file = new File(['fixture'], 'fixture.txt');
    Object.defineProperty(input, 'files', { value: [file] });
    input.dispatchEvent(new Event('change'));
    expect(await picked).toEqual({
      status: 'ok',
      value: { kind: 'file', files: [file] },
    });
    expect(document.querySelector('input')).toBeNull();
    const cancelled = adapter.selectFile();
    document.querySelector('input')!.dispatchEvent(new Event('cancel'));
    expect(await cancelled).toEqual({ status: 'cancelled' });
  });

  it('lets an attachment pick take several files, other picks one (U18)', async () => {
    vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(
      () => undefined,
    );
    const adapter = createBrowserPlatform(media());
    const attach = adapter.selectFile(undefined, {
      intentId: 'intent-a',
      intent: 'attachment',
      conversationId: 'conversation-a',
      destination: 'composer',
    });
    let input = document.querySelector('input')!;
    expect(input.multiple).toBe(true);
    const files = [new File(['a'], 'a.txt'), new File(['b'], 'b.txt')];
    Object.defineProperty(input, 'files', { value: files });
    input.dispatchEvent(new Event('change'));
    expect(await attach).toEqual({
      status: 'ok',
      value: { kind: 'file', files },
    });
    const single = adapter.selectFile();
    input = document.querySelector('input')!;
    expect(input.multiple).toBe(false);
    input.dispatchEvent(new Event('cancel'));
    await single;
  });

  it('bounds pending selection and supports abort without a file or OS window', async () => {
    vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(
      () => undefined,
    );
    const adapter = createBrowserPlatform(media());
    const abort = new AbortController();
    const selection = adapter.selectFile(abort.signal);
    expect(await adapter.selectFile()).toMatchObject({
      status: 'unavailable',
      reason: 'selection_in_progress',
    });
    abort.abort();
    expect(await selection).toEqual({ status: 'cancelled' });
    expect(document.querySelector('input')).toBeNull();
  });

  it('uses only the injected authenticated transport for upload and download', async () => {
    const transport = media();
    const adapter = createBrowserPlatform(transport);
    const file = new File(['fixture'], 'fixture.txt');
    expect(await adapter.upload('conversation', file)).toEqual({
      status: 'ok',
      value: attachment,
    });
    expect(transport.upload).toHaveBeenCalledWith(
      'conversation',
      file,
      undefined,
    );
    vi.stubGlobal(
      'URL',
      class extends URL {
        static createObjectURL = vi.fn().mockReturnValue('blob:fixture');
        static revokeObjectURL = vi.fn();
      },
    );
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(
      () => undefined,
    );
    // A page can only start a download; it never claims the file was saved.
    expect(await adapter.save('fixture', 'fixture.txt')).toEqual({
      status: 'ok',
      value: { kind: 'download' },
    });
    expect(transport.download).toHaveBeenCalledWith('fixture', undefined);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:fixture');
    vi.unstubAllGlobals();
  });

  it('declares permission failure and unavailable clipboard without hidden fallbacks', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        readText: vi.fn().mockRejectedValue(new Error('private sentinel')),
      },
    });
    const adapter = createBrowserPlatform(media());
    expect(await adapter.readClipboard()).toEqual({
      status: 'unavailable',
      reason: 'operation_failed',
    });
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: undefined,
    });
    expect(await adapter.readClipboard()).toEqual({
      status: 'unavailable',
      reason: 'unsupported',
    });
  });

  it('rejects unsafe external URLs and download paths before any effect', async () => {
    const transport = media();
    const adapter = createBrowserPlatform(transport);
    for (const url of [
      'javascript:alert(1)',
      'file:///secret',
      'https://user:secret@example.invalid',
      'https://a\\b',
      '//example.invalid',
    ]) {
      expect(safeExternalUrl(url)).toBeUndefined();
      expect(await adapter.openExternal(url)).toMatchObject({
        status: 'unavailable',
      });
    }
    expect(await adapter.save('fixture', '../secret')).toMatchObject({
      status: 'unavailable',
    });
    expect(transport.download).not.toHaveBeenCalled();
  });

  it('uses permission-controlled clipboard and an isolated external-link request', async () => {
    const readText = vi.fn().mockResolvedValue('fixture');
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { readText, writeText },
    });
    const adapter = createBrowserPlatform(media());
    expect(await adapter.readClipboard()).toEqual({
      status: 'ok',
      value: 'fixture',
    });
    expect(await adapter.writeClipboard('fixture')).toEqual({
      status: 'ok',
      value: null,
    });
    expect(writeText).toHaveBeenCalledWith('fixture');
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) {
        expect(this.rel).toBe('noopener noreferrer');
        expect(this.target).toBe('_blank');
      });
    expect(await adapter.openExternal('https://example.invalid/help')).toEqual({
      status: 'ok',
      value: null,
    });
    expect(click).toHaveBeenCalledTimes(1);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: undefined,
    });
  });

  it('does not request picker, clipboard or link interaction without user activation', async () => {
    Object.defineProperty(navigator, 'userActivation', {
      configurable: true,
      value: { isActive: false },
    });
    const adapter = createBrowserPlatform(media());
    expect(await adapter.selectFile()).toEqual({
      status: 'unavailable',
      reason: 'user_gesture_required',
    });
    expect(await adapter.readClipboard()).toEqual({
      status: 'unavailable',
      reason: 'user_gesture_required',
    });
    expect(await adapter.openExternal('https://example.invalid')).toEqual({
      status: 'unavailable',
      reason: 'user_gesture_required',
    });
    expect(document.querySelector('input')).toBeNull();
    Object.defineProperty(navigator, 'userActivation', {
      configurable: true,
      value: undefined,
    });
  });

  it('selects browser directory files only when directory inputs are supported', async () => {
    vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(
      () => undefined,
    );
    const descriptor = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      'webkitdirectory',
    );
    Object.defineProperty(HTMLInputElement.prototype, 'webkitdirectory', {
      configurable: true,
      writable: true,
      value: false,
    });
    const adapter = createBrowserPlatform(media());
    const pending = adapter.selectFolder();
    const input = document.querySelector('input')!;
    expect(input.webkitdirectory).toBe(true);
    const file = new File(['fixture'], 'fixture.txt');
    Object.defineProperty(input, 'files', { value: [file] });
    input.dispatchEvent(new Event('change'));
    expect(await pending).toEqual({
      status: 'ok',
      value: { kind: 'folder', files: [file] },
    });
    if (descriptor)
      Object.defineProperty(
        HTMLInputElement.prototype,
        'webkitdirectory',
        descriptor,
      );
    else Reflect.deleteProperty(HTMLInputElement.prototype, 'webkitdirectory');
  });
});

describe('safe native and fake capabilities', () => {
  const nativeIntent = {
    intentId: '11111111-1111-4111-8111-111111111111',
    intent: 'fixture',
    conversationId: 'conversation-a',
    destination: 'fixture',
  };

  it('selects native only after server authorization and matching authenticated discovery', async () => {
    const endpoint = {
      dispatch: vi.fn().mockResolvedValue({
        status: 'ok',
        value: {
          kind: 'pywebview',
          platform: 'windows',
          capabilities: ['select_folder'],
          instanceId: 'instance-a',
          windowId: 'window-a',
          epoch: 1,
        },
      }),
    };
    Object.defineProperty(window, '__ROW_BOT_NATIVE_CLIENT__', {
      configurable: true,
      value: endpoint,
    });
    const authorized = await selectClientPlatform(media(), {
      native_adapter: {
        available: true,
        proof_required: true,
        instance_id: 'instance-a',
        attestation: 'a'.repeat(32),
      },
    });
    expect((await authorized.discover()).status).toBe('ok');
    expect(endpoint.dispatch).toHaveBeenCalledWith('discover', {
      attestation: 'a'.repeat(32),
    });
    endpoint.dispatch.mockResolvedValueOnce({
      status: 'ok',
      value: {
        kind: 'pywebview',
        platform: 'windows',
        capabilities: [],
        instanceId: 'other-instance',
        windowId: 'window-a',
        epoch: 1,
      },
    });
    const mismatched = await selectClientPlatform(media(), {
      native_adapter: {
        available: true,
        proof_required: true,
        instance_id: 'instance-a',
        attestation: 'b'.repeat(32),
      },
    });
    // A desktop window never falls back to the browser versions (B238).
    expect(await mismatched.discover()).toEqual({
      status: 'unavailable',
      reason: 'native_reconnecting',
    });
    expect(mismatched.nativeConnection?.get()).toBe('reconnecting');
    Reflect.deleteProperty(window, '__ROW_BOT_NATIVE_CLIENT__');
  });
  it('waits for the native loaded event before selecting the authorized adapter', async () => {
    const endpoint = {
      dispatch: vi.fn().mockResolvedValue({
        status: 'ok',
        value: {
          kind: 'pywebview',
          platform: 'windows',
          capabilities: ['buddy_placement'],
          instanceId: 'instance-a',
          windowId: 'window-a',
          epoch: 1,
        },
      }),
    };
    Object.assign(window, { pywebview: {} });
    const selecting = selectClientPlatform(media(), {
      native_adapter: {
        available: true,
        proof_required: true,
        instance_id: 'instance-a',
        attestation: 'a'.repeat(32),
      },
    });
    Object.defineProperty(window, '__ROW_BOT_NATIVE_CLIENT__', {
      configurable: true,
      value: endpoint,
    });
    window.dispatchEvent(new Event('row-bot-native-ready'));
    await expect((await selecting).discover()).resolves.toMatchObject({
      status: 'ok',
      value: { kind: 'pywebview' },
    });
    Reflect.deleteProperty(window, '__ROW_BOT_NATIVE_CLIENT__');
    Reflect.deleteProperty(window, 'pywebview');
  });
  it.each(['file', 'folder', 'save'] as const)(
    'discards the late native %s completion after abort',
    async (operation) => {
      let complete!: (value: unknown) => void;
      const endpoint = {
        dispatch: vi.fn(
          () =>
            new Promise((resolve) => {
              complete = resolve;
            }),
        ),
      };
      const adapter = createPyWebViewPlatform(
        endpoint,
        media(),
        'a'.repeat(32),
      );
      const controller = new AbortController();
      const pending =
        operation === 'file'
          ? adapter.selectFile(controller.signal, nativeIntent)
          : operation === 'folder'
            ? adapter.selectFolder(controller.signal, nativeIntent)
            : adapter.save('fixture', 'fixture.txt', controller.signal);
      controller.abort();
      complete({
        status: 'ok',
        value:
          operation === 'save'
            ? null
            : { kind: operation, reference: 'fixture' },
      });
      expect(await pending).toEqual({ status: 'cancelled' });
      expect(await adapter.selectFile(controller.signal, nativeIntent)).toEqual(
        {
          status: 'cancelled',
        },
      );
      expect(endpoint.dispatch).toHaveBeenCalledTimes(1);
    },
  );

  it('uses only the typed native endpoint and refuses path-shaped responses', async () => {
    const endpoint = {
      dispatch: vi.fn().mockResolvedValue({
        status: 'ok',
        value: { kind: 'file', reference: 'fixture' },
      }),
    };
    const adapter = createPyWebViewPlatform(endpoint, media(), 'a'.repeat(32));
    expect(await adapter.selectFile(undefined, nativeIntent)).toEqual({
      status: 'ok',
      value: { kind: 'file', reference: 'fixture' },
    });
    endpoint.dispatch.mockResolvedValue({
      status: 'ok',
      value: { kind: 'file', reference: 'C:\\private' },
    });
    expect(await adapter.selectFile(undefined, nativeIntent)).toMatchObject({
      status: 'unavailable',
      reason: 'invalid_native_response',
    });
    endpoint.dispatch.mockResolvedValue({
      status: 'unavailable',
      reason: 'native_proof_required',
    });
    expect(await adapter.readClipboard()).toMatchObject({
      status: 'unavailable',
    });
  });

  it('reports native revocation without falling back to another computer', async () => {
    const endpoint = {
      dispatch: vi.fn().mockRejectedValue(new Error('private sentinel')),
    };
    const transport = media();
    const adapter = createPyWebViewPlatform(
      endpoint,
      transport,
      'a'.repeat(32),
    );
    expect(await adapter.save('fixture', 'fixture.txt')).toEqual({
      status: 'unavailable',
      reason: 'native_operation_failed',
    });
    expect(transport.download).not.toHaveBeenCalled();
    expect(await adapter.managedWindow('/api/launcher-shutdown')).toMatchObject(
      { status: 'unavailable' },
    );
  });

  it('provides the identical fake interface with deterministic results and no effects', async () => {
    const adapter = createFakePlatform({
      readClipboard: { status: 'ok', value: 'fixture' },
    });
    expect(await adapter.readClipboard()).toEqual({
      status: 'ok',
      value: 'fixture',
    });
    expect(await adapter.selectFolder()).toEqual({
      status: 'unavailable',
      reason: 'unsupported',
    });
    expect(adapter.calls).toEqual(['readClipboard', 'selectFolder']);
  });

  it('checks native Buddy capability and validates placement responses', async () => {
    const dispatch = vi.fn().mockResolvedValue({
      status: 'ok',
      value: {
        kind: 'pywebview',
        platform: 'windows',
        capabilities: ['buddy_placement'],
        instanceId: 'instance',
        windowId: 'window',
        epoch: 1,
      },
    });
    const adapter = createPyWebViewPlatform(
      { dispatch },
      media(),
      'a'.repeat(32),
    );
    expect(
      await adapter.buddyPlacement('tear_off', { x: Number.NaN, y: 20 }),
    ).toMatchObject({ status: 'unavailable' });
    expect(dispatch).not.toHaveBeenCalled();
    dispatch
      .mockResolvedValueOnce({
        status: 'ok',
        value: {
          kind: 'pywebview',
          platform: 'windows',
          capabilities: ['buddy_placement'],
          instanceId: 'instance',
          windowId: 'window',
          epoch: 1,
        },
      })
      .mockResolvedValueOnce({
        status: 'ok',
        value: { placement: 'desktop', visible: true },
      });
    expect(
      await adapter.buddyPlacement('tear_off', { x: 500, y: -200 }),
    ).toEqual({ status: 'ok', value: { placement: 'desktop', visible: true } });
    expect(dispatch).toHaveBeenLastCalledWith('buddy_placement', {
      action: 'tear_off',
      x: 500,
      y: -200,
    });
    dispatch.mockResolvedValueOnce({
      status: 'ok',
      value: {
        kind: 'pywebview',
        platform: 'windows',
        capabilities: [],
        instanceId: 'instance',
        windowId: 'window',
        epoch: 1,
      },
    });
    expect(await adapter.buddyPlacement('dock')).toMatchObject({
      status: 'unavailable',
    });
    expect(dispatch).toHaveBeenCalledTimes(3);
  });
});

describe('desktop Buddy operations', () => {
  it('publishes, reads and validates the followed conversation', async () => {
    const dispatch = vi.fn().mockResolvedValue({
      status: 'ok',
      value: { conversationId: 'conversation-1', revision: 3 },
    });
    const adapter = createPyWebViewPlatform(
      { dispatch },
      media(),
      'a'.repeat(32),
    );
    expect(await adapter.publishBuddyTarget('conversation-1')).toEqual({
      status: 'ok',
      value: { conversationId: 'conversation-1', revision: 3 },
    });
    expect(dispatch).toHaveBeenLastCalledWith('buddy_follow', {
      conversationId: 'conversation-1',
    });
    await adapter.readBuddyTarget();
    expect(dispatch).toHaveBeenLastCalledWith('buddy_follow', {});
    // Ids the host would refuse never cross the bridge.
    for (const bad of ['', 'a b', '../x', 'x'.repeat(257)])
      expect(await adapter.publishBuddyTarget(bad)).toMatchObject({
        status: 'unavailable',
      });
    expect(dispatch).toHaveBeenCalledTimes(2);
    // A malformed host answer is refused.
    for (const value of [
      { conversationId: 'a b', revision: 1 },
      { conversationId: 'c', revision: -1 },
      { conversationId: 'c', revision: 1, extra: true },
      { conversationId: 'c' },
    ]) {
      dispatch.mockResolvedValueOnce({ status: 'ok', value });
      expect(await adapter.readBuddyTarget()).toMatchObject({
        status: 'unavailable',
      });
    }
  });

  it('shows the main window on a conversation and moves only through pywebview', async () => {
    const dispatch = vi.fn().mockResolvedValue({ status: 'ok', value: null });
    const callback = vi.fn();
    const adapter = createPyWebViewPlatform(
      { dispatch },
      media(),
      'a'.repeat(32),
      {
        pywebview: { _jsApiCallback: callback },
      },
    );
    expect(await adapter.showMainWindow('conversation-1')).toEqual({
      status: 'ok',
      value: null,
    });
    expect(dispatch).toHaveBeenLastCalledWith('main_window', {
      conversationId: 'conversation-1',
    });
    await adapter.showMainWindow(null);
    expect(dispatch).toHaveBeenLastCalledWith('main_window', {
      conversationId: null,
    });
    expect(await adapter.showMainWindow('bad id')).toMatchObject({
      status: 'unavailable',
    });
    expect(adapter.moveWindow(10.4, 20.6)).toBe(true);
    expect(callback).toHaveBeenCalledWith(
      'pywebviewMoveWindow',
      [10, 21],
      'move',
    );
    expect(adapter.moveWindow(Number.NaN, 1)).toBe(false);
    const hostless = createPyWebViewPlatform(
      { dispatch },
      media(),
      'a'.repeat(32),
      {},
    );
    expect(hostless.moveWindow(1, 1)).toBe(false);
  });

  it('is unavailable in browsers and scripted in the fake', async () => {
    const browser = createBrowserPlatform(media());
    expect(await browser.readBuddyTarget()).toMatchObject({
      status: 'unavailable',
    });
    expect(await browser.publishBuddyTarget('c')).toMatchObject({
      status: 'unavailable',
      reason: 'buddy_target_requires_native',
    });
    expect(await browser.showMainWindow('c')).toMatchObject({
      status: 'unavailable',
    });
    expect(browser.moveWindow(1, 1)).toBe(false);
    const fake = createFakePlatform({ moveWindow: true });
    expect(fake.moveWindow(1, 1)).toBe(true);
    expect(fake.calls).toEqual(['moveWindow']);
  });
});

describe('Open in your terminal', () => {
  it('sends only the conversation id and accepts only an empty success', async () => {
    const dispatch = vi.fn().mockResolvedValue({ status: 'ok', value: null });
    const adapter = createPyWebViewPlatform(
      { dispatch },
      media(),
      'a'.repeat(32),
    );
    expect(await adapter.openExternalTerminal('conversation-1')).toEqual({
      status: 'ok',
      value: null,
    });
    expect(dispatch).toHaveBeenLastCalledWith('terminal_external', {
      conversationId: 'conversation-1',
    });
    expect(await adapter.openExternalTerminal(null)).toEqual({
      status: 'ok',
      value: null,
    });
    expect(dispatch).toHaveBeenLastCalledWith('terminal_external', {
      conversationId: null,
    });
    expect(await adapter.openExternalTerminal('../elsewhere')).toMatchObject({
      status: 'unavailable',
      reason: 'invalid_conversation',
    });
    expect(dispatch).toHaveBeenCalledTimes(2);
    dispatch.mockResolvedValueOnce({ status: 'ok', value: 'C:\\Users' });
    expect(await adapter.openExternalTerminal(null)).toMatchObject({
      status: 'unavailable',
      reason: 'invalid_native_response',
    });
  });

  it('is unavailable in browsers and scripted in the fake', async () => {
    expect(
      await createBrowserPlatform(media()).openExternalTerminal('c'),
    ).toEqual({ status: 'unavailable', reason: 'terminal_requires_native' });
    const fake = createFakePlatform({
      openExternalTerminal: { status: 'ok', value: null },
    });
    expect(await fake.openExternalTerminal('c')).toEqual({
      status: 'ok',
      value: null,
    });
    expect(fake.calls).toEqual(['openExternalTerminal']);
  });
});

describe('native selection at a cold start (B95)', () => {
  const nativeAdapter = {
    native_adapter: {
      available: true,
      proof_required: true as const,
      instance_id: 'instance-a',
      attestation: 'a'.repeat(32),
    },
  };
  const discovering = () => ({
    dispatch: vi.fn().mockResolvedValue({
      status: 'ok',
      value: {
        kind: 'pywebview',
        platform: 'windows',
        capabilities: ['buddy_placement'],
        instanceId: 'instance-a',
        windowId: 'window-a',
        epoch: 1,
      },
    }),
  });
  afterEach(() => {
    vi.useRealTimers();
    Reflect.deleteProperty(window, '__ROW_BOT_NATIVE_CLIENT__');
    Reflect.deleteProperty(window, 'chrome');
    Reflect.deleteProperty(window, 'webkit');
  });

  it('waits in a WebView2 page for a bridge that arrives after the handshake', async () => {
    vi.useFakeTimers();
    // WebView2 exposes chrome.webview from the first script; window.pywebview
    // and the bridge only arrive when the navigation completes.
    Object.assign(window, { chrome: { webview: {} } });
    let chosen: Awaited<ReturnType<typeof selectClientPlatform>> | null = null;
    void selectClientPlatform(media(), nativeAdapter).then((value) => {
      chosen = value;
    });
    await vi.advanceTimersByTimeAsync(2000);
    expect(chosen).toBeNull();
    Object.defineProperty(window, '__ROW_BOT_NATIVE_CLIENT__', {
      configurable: true,
      value: discovering(),
    });
    window.dispatchEvent(new Event('row-bot-native-ready'));
    await vi.advanceTimersByTimeAsync(0);
    await expect(chosen!.discover()).resolves.toMatchObject({
      status: 'ok',
      value: { kind: 'pywebview' },
    });
  });

  it('recognises WKWebView and stops waiting after the bound, reconnecting', async () => {
    vi.useFakeTimers();
    Object.assign(window, { webkit: { messageHandlers: { jsBridge: {} } } });
    let chosen: Awaited<ReturnType<typeof selectClientPlatform>> | null = null;
    void selectClientPlatform(media(), nativeAdapter).then((value) => {
      chosen = value;
    });
    await vi.advanceTimersByTimeAsync(7999);
    expect(chosen).toBeNull();
    await vi.advanceTimersByTimeAsync(1);
    await expect(chosen!.discover()).resolves.toEqual({
      status: 'unavailable',
      reason: 'native_reconnecting',
    });
  });

  it('never waits in an ordinary browser, even for the local owner', async () => {
    vi.useFakeTimers();
    let chosen: Awaited<ReturnType<typeof selectClientPlatform>> | null = null;
    void selectClientPlatform(media(), nativeAdapter).then((value) => {
      chosen = value;
    });
    await vi.advanceTimersByTimeAsync(0);
    await expect(chosen!.discover()).resolves.toMatchObject({
      value: { kind: 'browser' },
    });
  });
});

describe('native lease renewal (B99)', () => {
  it('exchanges a fresh attestation and uses it from then on', async () => {
    const info = {
      status: 'ok',
      value: {
        kind: 'pywebview',
        platform: 'windows',
        capabilities: ['buddy_placement'],
        instanceId: 'instance',
        windowId: 'window',
        epoch: 1,
      },
    };
    const dispatch = vi.fn().mockResolvedValue(info);
    const adapter = createPyWebViewPlatform(
      { dispatch },
      media(),
      'a'.repeat(32),
    );
    expect(await adapter.renewNative!('b'.repeat(32))).toMatchObject({
      status: 'ok',
    });
    expect(dispatch).toHaveBeenLastCalledWith('discover', {
      attestation: 'b'.repeat(32),
    });
    await adapter.discover();
    expect(dispatch).toHaveBeenLastCalledWith('discover', {
      attestation: 'b'.repeat(32),
    });
    expect(await adapter.renewNative!('bad attestation')).toMatchObject({
      status: 'unavailable',
    });
    dispatch.mockResolvedValueOnce({
      status: 'unavailable',
      reason: 'native_proof_required',
    });
    expect(await adapter.renewNative!('c'.repeat(32))).toEqual({
      status: 'unavailable',
      reason: 'native_proof_required',
    });
    await adapter.discover();
    expect(dispatch).toHaveBeenLastCalledWith('discover', {
      attestation: 'b'.repeat(32),
    });
    dispatch.mockResolvedValueOnce({
      status: 'unavailable',
      reason: 'invalid_request',
    });
    expect(await adapter.readBuddyTarget()).toEqual({
      status: 'unavailable',
      reason: 'native_operation_unavailable',
    });
  });
});

describe('refused native grants (B102)', () => {
  const info = {
    status: 'ok',
    value: {
      kind: 'pywebview',
      platform: 'windows',
      capabilities: ['buddy_placement', 'buddy_follow'],
      instanceId: 'instance',
      windowId: 'window',
      epoch: 1,
    },
  };
  const refused = {
    status: 'unavailable',
    reason: 'native_authentication_required',
  };

  it('exchanges one fresh attestation and retries once', async () => {
    const target = {
      status: 'ok',
      value: { conversationId: null, revision: 3 },
    };
    const dispatch = vi
      .fn()
      .mockResolvedValueOnce(refused)
      .mockResolvedValueOnce(refused)
      .mockResolvedValueOnce(info)
      .mockResolvedValue(target);
    const reattest = vi.fn().mockResolvedValue('b'.repeat(32));
    const adapter = createPyWebViewPlatform(
      { dispatch },
      media(),
      'a'.repeat(32),
      undefined,
      reattest,
    );
    const [first, second] = await Promise.all([
      adapter.readBuddyTarget(),
      adapter.readBuddyTarget(),
    ]);
    expect(first).toEqual(target);
    expect(second).toEqual(target);
    // Both refused calls shared one exchange.
    expect(reattest).toHaveBeenCalledTimes(1);
    expect(dispatch.mock.calls.slice(0, 3)).toEqual([
      ['buddy_follow', {}],
      ['buddy_follow', {}],
      ['discover', { attestation: 'b'.repeat(32) }],
    ]);
    dispatch.mockResolvedValue(info);
    await adapter.discover();
    expect(dispatch).toHaveBeenLastCalledWith('discover', {
      attestation: 'b'.repeat(32),
    });
  });

  it('gives up after one retry and never retries a lost document', async () => {
    const dispatch = vi
      .fn()
      .mockResolvedValueOnce(refused)
      .mockResolvedValueOnce(info)
      .mockResolvedValueOnce(refused);
    const reattest = vi.fn().mockResolvedValue('b'.repeat(32));
    const adapter = createPyWebViewPlatform(
      { dispatch },
      media(),
      'a'.repeat(32),
      undefined,
      reattest,
    );
    expect(await adapter.writeClipboard('fixture')).toEqual(refused);
    expect(dispatch).toHaveBeenCalledTimes(3);

    dispatch.mockReset().mockResolvedValue({
      status: 'unavailable',
      reason: 'native_proof_required',
    });
    reattest.mockClear();
    expect(await adapter.readBuddyTarget()).toEqual({
      status: 'unavailable',
      reason: 'native_proof_required',
    });
    expect(reattest).not.toHaveBeenCalled();
    expect(dispatch).toHaveBeenCalledTimes(1);
  });

  it('keeps the held attestation when a fresh one is refused or missing', async () => {
    const dispatch = vi
      .fn()
      .mockResolvedValueOnce(refused)
      .mockResolvedValueOnce(refused)
      .mockResolvedValueOnce(refused)
      .mockResolvedValue(info);
    const reattest = vi
      .fn()
      .mockResolvedValueOnce('c'.repeat(32))
      .mockResolvedValueOnce(null);
    const adapter = createPyWebViewPlatform(
      { dispatch },
      media(),
      'a'.repeat(32),
      undefined,
      reattest,
    );
    expect(await adapter.readClipboard()).toEqual(refused);
    expect(await adapter.readClipboard()).toEqual(refused);
    await adapter.discover();
    expect(dispatch).toHaveBeenLastCalledWith('discover', {
      attestation: 'a'.repeat(32),
    });
    // Without a source there is nothing to exchange.
    const plain = createPyWebViewPlatform(
      { dispatch: vi.fn().mockResolvedValue(refused) },
      media(),
      'a'.repeat(32),
    );
    expect(await plain.readBuddyTarget()).toEqual(refused);
  });

  it('recovers a cold start whose first attestation predates a policy change', async () => {
    const dispatch = vi
      .fn()
      .mockResolvedValueOnce(refused)
      .mockResolvedValue(info);
    Object.defineProperty(window, '__ROW_BOT_NATIVE_CLIENT__', {
      configurable: true,
      value: { dispatch },
    });
    const reattest = vi.fn().mockResolvedValue('b'.repeat(32));
    const chosen = await selectClientPlatform(
      media(),
      {
        native_adapter: {
          available: true,
          proof_required: true,
          instance_id: 'instance',
          attestation: 'a'.repeat(32),
        },
      },
      window,
      reattest,
    );
    delete window.__ROW_BOT_NATIVE_CLIENT__;
    expect(await chosen.discover()).toMatchObject({
      value: { kind: 'pywebview' },
    });
    expect(dispatch.mock.calls.slice(0, 3)).toEqual([
      ['discover', { attestation: 'a'.repeat(32) }],
      ['discover', { attestation: 'b'.repeat(32) }],
      ['discover', { attestation: 'b'.repeat(32) }],
    ]);
  });
});

describe('desktop windows never run the browser versions silently (B231, B238)', () => {
  const nativeAdapter = {
    native_adapter: {
      available: true,
      proof_required: true as const,
      instance_id: 'instance',
      attestation: 'a'.repeat(32),
    },
  };
  const info = {
    status: 'ok',
    value: {
      kind: 'pywebview',
      platform: 'windows',
      capabilities: ['buddy_placement', 'save', 'select_folder'],
      instanceId: 'instance',
      windowId: 'window',
      epoch: 1,
    },
  };
  const reconnecting = { status: 'unavailable', reason: 'native_reconnecting' };
  const lapsed = { status: 'unavailable', reason: 'native_proof_required' };
  const status = {
    status: 'ok',
    value: { placement: 'docked', visible: true },
  };
  type Endpoint = { dispatch: ReturnType<typeof vi.fn> };
  type Host = EventTarget & {
    __ROW_BOT_NATIVE_CLIENT__?: Endpoint;
    pywebview?: { api: { native_client_rebind: ReturnType<typeof vi.fn> } };
  };
  // A pywebview window of its own, so no other test hears its events.
  const desktopWindow = (): Host =>
    Object.assign(new EventTarget(), {
      document,
      navigator,
      chrome: { webview: {} },
      setTimeout: (handler: () => void, ms?: number) =>
        window.setTimeout(handler, ms),
      clearTimeout: (id?: number) => window.clearTimeout(id),
    });
  const asWindow = (host: Host) => host as unknown as Window;
  // The host binds the document again: a new endpoint and the ready event.
  const rebindingHost = (host: Host, next: () => Endpoint) => {
    host.pywebview = {
      api: {
        native_client_rebind: vi.fn(async () => {
          host.__ROW_BOT_NATIVE_CLIENT__ = next();
          host.dispatchEvent(new Event('row-bot-native-ready'));
          return { status: 'ok' };
        }),
      },
    };
    return host.pywebview.api.native_client_rebind;
  };
  afterEach(() => vi.useRealTimers());

  it('says it is reconnecting while a slow bridge loads, saves into Exports, then binds it', async () => {
    vi.useFakeTimers();
    const host = desktopWindow();
    const transport = media();
    let chosen: Awaited<ReturnType<typeof selectClientPlatform>> | null = null;
    void selectClientPlatform(
      transport,
      nativeAdapter,
      asWindow(host),
      async () => 'b'.repeat(32),
    ).then((value) => {
      chosen = value;
    });
    await vi.advanceTimersByTimeAsync(8000);
    expect(chosen!.nativeConnection?.get()).toBe('reconnecting');
    expect(await chosen!.discover()).toEqual(reconnecting);
    expect(await chosen!.buddyPlacement('status')).toEqual(reconnecting);
    expect(await chosen!.openTerminal(null)).toEqual(reconnecting);
    // No browser download (the desktop window cancels those): the server
    // writes the export into Exports and says which file.
    const saved = await chosen!.save('conversation-1:export-1', 'export.md');
    expect(saved).toMatchObject({
      status: 'ok',
      value: {
        kind: 'exports',
        fileName: 'conversation-export.md',
        folder: 'Row-Bot › Exports',
      },
    });
    expect(transport.download).not.toHaveBeenCalled();
    expect(transport.saveToExports).toHaveBeenCalledWith(
      'conversation-1:export-1',
      undefined,
    );
    if (saved.status === 'ok' && saved.value.kind === 'exports')
      expect(await saved.value.reveal()).toBe(true);
    expect(transport.revealExport).toHaveBeenCalledWith(
      'conversation-export.md',
    );

    const endpoint = { dispatch: vi.fn().mockResolvedValue(info) };
    host.__ROW_BOT_NATIVE_CLIENT__ = endpoint;
    host.dispatchEvent(new Event('row-bot-native-ready'));
    await vi.advanceTimersByTimeAsync(0);
    expect(chosen!.nativeConnection?.get()).toBe('ready');
    expect(await chosen!.discover()).toMatchObject({
      status: 'ok',
      value: { kind: 'pywebview' },
    });
    expect(endpoint.dispatch).toHaveBeenCalledWith('discover', {
      attestation: 'a'.repeat(32),
    });
  });

  it('binds again with a fresh attestation when the first discovery fails', async () => {
    const host = desktopWindow();
    host.__ROW_BOT_NATIVE_CLIENT__ = {
      dispatch: vi.fn().mockResolvedValue(lapsed),
    };
    const fresh = { dispatch: vi.fn().mockResolvedValue(info) };
    const rebind = rebindingHost(host, () => fresh);
    const reattest = vi.fn().mockResolvedValue('b'.repeat(32));
    const chosen = await selectClientPlatform(
      media(),
      nativeAdapter,
      asWindow(host),
      reattest,
    );
    await vi.waitFor(() =>
      expect(chosen.nativeConnection?.get()).toBe('ready'),
    );
    expect(rebind).toHaveBeenCalledTimes(1);
    expect(fresh.dispatch).toHaveBeenCalledWith('discover', {
      attestation: 'b'.repeat(32),
    });
    expect(await chosen.discover()).toMatchObject({ status: 'ok' });
  });

  it('binds a window whose lease lapsed again, without reloading it', async () => {
    const host = desktopWindow();
    const first = { dispatch: vi.fn().mockResolvedValue(info) };
    host.__ROW_BOT_NATIVE_CLIENT__ = first;
    const second = {
      dispatch: vi.fn(async (operation: string) =>
        operation === 'buddy_placement' ? status : info,
      ),
    };
    const rebind = rebindingHost(host, () => second);
    const reattest = vi.fn().mockResolvedValue('c'.repeat(32));
    const chosen = await selectClientPlatform(
      media(),
      nativeAdapter,
      asWindow(host),
      reattest,
    );
    expect(chosen.nativeConnection?.get()).toBe('ready');
    const seen: string[] = [];
    chosen.nativeConnection!.subscribe(() =>
      seen.push(chosen.nativeConnection!.get()),
    );
    // The computer slept past the lease: every call answers "proof required".
    first.dispatch.mockResolvedValue(lapsed);
    expect(await chosen.renewNative!('d'.repeat(32))).toEqual(reconnecting);
    await vi.waitFor(() =>
      expect(chosen.nativeConnection?.get()).toBe('ready'),
    );
    expect(seen).toEqual(['reconnecting', 'ready']);
    expect(rebind).toHaveBeenCalledTimes(1);
    expect(second.dispatch).toHaveBeenCalledWith('discover', {
      attestation: 'c'.repeat(32),
    });
    expect(await chosen.buddyPlacement('status')).toEqual(status);
  });

  // The host refuses every binding until `healthy`; the window starts lost.
  const outage = async (host: Host) => {
    vi.useFakeTimers();
    let healthy = false;
    host.__ROW_BOT_NATIVE_CLIENT__ = {
      dispatch: vi.fn().mockResolvedValue(lapsed),
    };
    const rebind = rebindingHost(host, () => ({
      dispatch: vi.fn(async () => (healthy ? info : lapsed)),
    }));
    const chosen = await selectClientPlatform(
      media(),
      nativeAdapter,
      asWindow(host),
      async () => 'b'.repeat(32),
    );
    return { chosen, rebind, recover: () => (healthy = true) };
  };

  it.each(['focus', 'visibilitychange'] as const)(
    'binds again at once on %s instead of waiting out its backoff (B231)',
    async (event) => {
      const host = desktopWindow();
      const page = document.implementation.createHTMLDocument('desktop');
      Object.defineProperty(page, 'visibilityState', { value: 'visible' });
      Object.assign(host, { document: page });
      const { chosen, rebind, recover } = await outage(host);
      await vi.advanceTimersByTimeAsync(20_000);
      expect(chosen.nativeConnection?.get()).toBe('reconnecting');
      const attempts = rebind.mock.calls.length;
      recover();
      (event === 'focus' ? host : page).dispatchEvent(new Event(event));
      await vi.advanceTimersByTimeAsync(0);
      expect(chosen.nativeConnection?.get()).toBe('ready');
      expect(rebind).toHaveBeenCalledTimes(attempts + 1);
    },
  );

  it('binds again at once when a native operation is wanted while reconnecting', async () => {
    const { chosen, recover } = await outage(desktopWindow());
    await vi.advanceTimersByTimeAsync(20_000);
    recover();
    expect(await chosen.buddyPlacement('status')).toEqual(reconnecting);
    await vi.advanceTimersByTimeAsync(0);
    expect(chosen.nativeConnection?.get()).toBe('ready');
  });

  it('binds within 15 s of the host recovering, however long it was lost', async () => {
    const { chosen, recover } = await outage(desktopWindow());
    await vi.advanceTimersByTimeAsync(125_000);
    expect(chosen.nativeConnection?.get()).toBe('reconnecting');
    recover();
    await vi.advanceTimersByTimeAsync(15_000);
    expect(chosen.nativeConnection?.get()).toBe('ready');
  });

  it('attaches through the browser file input while reconnecting; a folder pick says so', async () => {
    const host = desktopWindow();
    host.__ROW_BOT_NATIVE_CLIENT__ = {
      dispatch: vi.fn().mockResolvedValue(lapsed),
    };
    const chosen = await selectClientPlatform(
      media(),
      nativeAdapter,
      asWindow(host),
    );
    expect(chosen.nativeConnection?.get()).toBe('reconnecting');
    vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(
      () => undefined,
    );
    const intent = {
      intentId: 'intent',
      intent: 'attachment',
      conversationId: 'conversation-1',
      destination: 'composer',
    };
    const picked = chosen.selectFile(undefined, intent);
    await vi.waitFor(() =>
      expect(document.querySelector('input')).not.toBeNull(),
    );
    const input = document.querySelector('input')!;
    const file = new File(['fixture'], 'fixture.txt');
    Object.defineProperty(input, 'files', { value: [file] });
    input.dispatchEvent(new Event('change'));
    expect(await picked).toEqual({
      status: 'ok',
      value: { kind: 'file', files: [file] },
    });
    expect(
      await chosen.selectFolder(undefined, { ...intent, intent: 'workspace' }),
    ).toEqual(reconnecting);
  });

  it('reports a native save truthfully and never saves elsewhere after a failed write', async () => {
    const host = desktopWindow();
    const dispatch = vi.fn().mockResolvedValue(info);
    host.__ROW_BOT_NATIVE_CLIENT__ = { dispatch };
    const transport = media();
    const chosen = await selectClientPlatform(
      transport,
      nativeAdapter,
      asWindow(host),
    );
    dispatch.mockResolvedValueOnce({ status: 'ok', value: null });
    expect(await chosen.save('conversation-1:export-1', 'export.md')).toEqual({
      status: 'ok',
      value: { kind: 'file' },
    });
    dispatch.mockResolvedValueOnce({ status: 'cancelled' });
    expect(await chosen.save('conversation-1:export-1', 'export.md')).toEqual({
      status: 'cancelled',
    });
    dispatch.mockResolvedValueOnce({
      status: 'unavailable',
      reason: 'save_failed',
    });
    expect(await chosen.save('conversation-1:export-1', 'export.md')).toEqual({
      status: 'unavailable',
      reason: 'save_failed',
    });
    expect(transport.saveToExports).not.toHaveBeenCalled();
    expect(transport.download).not.toHaveBeenCalled();
  });
});

describe('downloads in a desktop window (B275)', () => {
  it('never reports a download pywebview would cancel as started', async () => {
    const { saveBrowserDownload } = await import('./download');
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click');
    const desktop = Object.assign(Object.create(window), {
      chrome: { webview: {} },
      document,
      navigator: { userActivation: { isActive: true } },
    }) as Window;
    const load = vi.fn().mockResolvedValue(new Blob(['x']));

    const result = await saveBrowserDownload(
      load,
      'code.py',
      undefined,
      desktop,
    );

    expect(result).toEqual({
      status: 'unavailable',
      reason: 'desktop_download_unavailable',
    });
    expect(load).not.toHaveBeenCalled();
    expect(click).not.toHaveBeenCalled();
  });
});
