import { createPyWebViewPlatform, type NativeEndpoint } from './native';
import type {
  CapabilityResult,
  ClientPlatform,
  MediaTransport,
  NativeConnection,
  SavedFile,
} from './types';
import { protect, unavailable } from './types';

/**
 * The first wait before binding again; it doubles up to 15 s, so a window
 * binds soon after its host can bind it again, however long that took.
 */
export const REBIND_RETRY_MS = 2000;
const REBIND_RETRY_MAX_MS = 15_000;

export type DesktopHost = Window & {
  __ROW_BOT_NATIVE_CLIENT__?: NativeEndpoint;
  pywebview?: {
    api?: { native_client_rebind?: () => Promise<unknown> };
    _jsApiCallback?(name: string, params: unknown, id: string): unknown;
  };
};

// The window's bridge is gone: its lease lapsed (the computer slept past
// it), its document was bound again, or the channel to the host failed.
const LOST = new Set(['native_proof_required', 'native_operation_failed']);
// The native version never started (the bridge is not bound, or its grant
// was refused before any effect): the browser's own version may run.
const NOT_STARTED = new Set([
  'native_reconnecting',
  'native_authentication_required',
]);

/**
 * A desktop window's platform (B231, B238). It runs the native versions
 * while its bridge is bound. When a call finds the bridge gone, or it never
 * arrived, the window asks the host to bind it again (exactly what loading
 * the page does; a fresh attestation then grants its authority), without
 * reloading and losing its state. Meanwhile it says "reconnecting" instead of
 * silently running the browser versions, except where those do the same job:
 * attaching files (the drop path), the clipboard and external links. A save
 * that cannot show the Save dialog is written into Exports by the server.
 */
export function createDesktopPlatform(
  media: MediaTransport,
  browser: ClientPlatform,
  host: DesktopHost,
  instanceId: string,
  reattest: () => Promise<string | null>,
  retryMs = REBIND_RETRY_MS,
): ClientPlatform & {
  /** Bind the endpoint the page has, or wait for it up to `waitMs`. */
  connect(attestation: string, waitMs: number): Promise<void>;
} {
  let native: ClientPlatform | null = null;
  let connection: NativeConnection = 'reconnecting';
  // The handshake's attestation, used by the first binding only.
  let first: string | null = null;
  let binding: { endpoint: NativeEndpoint; done: Promise<boolean> } | null =
    null;
  let rebinding = false;
  const listeners = new Set<() => void>();
  // Read afresh: a binding can finish while the loop below waits.
  const ready = () => connection === 'ready';
  const become = (next: NativeConnection) => {
    if (connection === next) return;
    connection = next;
    listeners.forEach((listener) => listener());
  };

  const bind = (endpoint: NativeEndpoint): Promise<boolean> => {
    if (binding?.endpoint === endpoint) return binding.done;
    const done = (async () => {
      try {
        const attestation = first ?? (await reattest());
        first = null;
        if (!attestation) return false;
        const candidate = createPyWebViewPlatform(
          endpoint,
          media,
          attestation,
          host,
          reattest,
        );
        const found = await candidate.discover();
        if (
          found.status !== 'ok' ||
          found.value.kind !== 'pywebview' ||
          found.value.instanceId !== instanceId ||
          host.__ROW_BOT_NATIVE_CLIENT__ !== endpoint
        )
          return false;
        native = candidate;
        become('ready');
        return true;
      } catch {
        return false;
      } finally {
        if (binding?.endpoint === endpoint) binding = null;
      }
    })();
    binding = { endpoint, done };
    return done;
  };
  // The host injects a new endpoint when the page loads or is bound again.
  host.addEventListener('row-bot-native-ready', () => {
    const endpoint = host.__ROW_BOT_NATIVE_CLIENT__;
    if (!ready() && endpoint) void bind(endpoint);
  });

  let wait = retryMs;
  // Ends the current wait between attempts early.
  let wake: (() => void) | null = null;
  const rebind = () => {
    native = null;
    become('reconnecting');
    if (rebinding) {
      // A native operation is wanted now: try now.
      wake?.();
      return;
    }
    rebinding = true;
    wait = retryMs;
    void (async () => {
      while (!ready()) {
        try {
          await host.pywebview?.api?.native_client_rebind?.();
        } catch {
          /* The host is asked again after the wait. */
        }
        const endpoint = host.__ROW_BOT_NATIVE_CLIENT__;
        if (ready() || (endpoint && (await bind(endpoint)))) break;
        const delay = wait;
        wait = Math.min(wait * 2, REBIND_RETRY_MAX_MS);
        await new Promise<void>((resolve) => {
          const done = () => {
            host.clearTimeout(timer);
            wake = null;
            resolve();
          };
          const timer = host.setTimeout(done, delay);
          wake = done;
        });
      }
      rebinding = false;
    })();
  };
  // A window shown or focused again (after sleep, or hidden behind Buddy,
  // where its timers crawl) tries at once and backs off from the start.
  const retryNow = () => {
    if (!rebinding || host.document.visibilityState === 'hidden') return;
    wait = retryMs;
    wake?.();
  };
  host.addEventListener('focus', retryNow);
  host.document.addEventListener('visibilitychange', retryNow);

  async function run<T>(
    operation: (platform: ClientPlatform) => Promise<CapabilityResult<T>>,
  ): Promise<CapabilityResult<T>> {
    const current = native;
    if (!current) {
      rebind();
      return unavailable('native_reconnecting');
    }
    const result = await operation(current);
    if (result.status === 'unavailable' && LOST.has(result.reason)) {
      if (native === current) rebind();
      return unavailable('native_reconnecting');
    }
    return result;
  }
  const notStarted = (result: CapabilityResult<unknown>) =>
    result.status === 'unavailable' && NOT_STARTED.has(result.reason);
  async function orBrowser<T>(
    result: CapabilityResult<T>,
    fallback: () => Promise<CapabilityResult<T>>,
  ): Promise<CapabilityResult<T>> {
    return notStarted(result) ? fallback() : result;
  }

  return {
    connect: (attestation, waitMs) => {
      first = attestation;
      const endpoint = host.__ROW_BOT_NATIVE_CLIENT__;
      if (endpoint)
        return bind(endpoint).then((bound) => {
          if (!bound) rebind();
        });
      // pywebview installs the endpoint when the navigation completes,
      // often after a cold start's handshake (B95).
      return new Promise<void>((resolve) => {
        const finish = () => {
          listeners.delete(settle);
          host.clearTimeout(timer);
          resolve();
        };
        const settle = () => {
          if (ready()) finish();
        };
        listeners.add(settle);
        const timer = host.setTimeout(() => {
          finish();
          if (!ready()) rebind();
        }, waitMs);
      });
    },
    nativeConnection: {
      get: () => connection,
      subscribe: (listener) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
    },
    discover: () => run((platform) => platform.discover()),
    renewNative: (attestation) =>
      run((platform) =>
        platform.renewNative
          ? platform.renewNative(attestation)
          : Promise.resolve(unavailable()),
      ),
    selectFile: async (signal, intent) => {
      const result = await run((platform) =>
        platform.selectFile(signal, intent),
      );
      // Attaching needs no native path grant: the browser's own file input
      // uploads like a drop does.
      return intent?.intent === 'attachment'
        ? orBrowser(result, () => browser.selectFile(signal, intent))
        : result;
    },
    selectFolder: (signal, intent) =>
      run((platform) => platform.selectFolder(signal, intent)),
    upload: (conversationId, file, signal) =>
      browser.upload(conversationId, file, signal),
    readClipboard: async () =>
      orBrowser(
        await run((platform) => platform.readClipboard()),
        browser.readClipboard,
      ),
    writeClipboard: async (text) =>
      orBrowser(await run((platform) => platform.writeClipboard(text)), () =>
        browser.writeClipboard(text),
      ),
    openExternal: async (url) =>
      orBrowser(await run((platform) => platform.openExternal(url)), () =>
        browser.openExternal(url),
      ),
    managedWindow: (route) => run((platform) => platform.managedWindow(route)),
    buddyPlacement: (action, point) =>
      run((platform) => platform.buddyPlacement(action, point)),
    publishBuddyTarget: (conversationId) =>
      run((platform) => platform.publishBuddyTarget(conversationId)),
    readBuddyTarget: () => run((platform) => platform.readBuddyTarget()),
    showMainWindow: (conversationId) =>
      run((platform) => platform.showMainWindow(conversationId)),
    moveWindow: (x, y) => native?.moveWindow(x, y) ?? false,
    openTerminal: (conversationId) =>
      run((platform) => platform.openTerminal(conversationId)),
    openExternalTerminal: (conversationId) =>
      run((platform) => platform.openExternalTerminal(conversationId)),
    save: async (reference, name, signal) => {
      const result = await run((platform) =>
        platform.save(reference, name, signal),
      );
      if (!notStarted(result)) return result;
      // No Save dialog while the bridge reconnects: the server writes the
      // file into Exports, and says which file.
      return protect<SavedFile>(async () => {
        const saved = await media.saveToExports(reference, signal);
        return {
          kind: 'exports',
          fileName: saved.file_name,
          folder: saved.folder,
          reveal: async () =>
            (await media.revealExport(saved.file_name)).status === 'opened',
        };
      });
    },
  };
}
