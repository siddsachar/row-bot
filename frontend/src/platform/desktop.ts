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
/**
 * How long something the person asked for waits for the window to bind
 * again before it says to try again. Binding takes well under a second.
 */
export const NATIVE_ACTION_WAIT_MS = 10_000;

/**
 * How a call meets a bridge that is binding again (B406):
 * - `background`: answers "reconnecting" at once (status reads, lease
 *   renewal, the Buddy's target); it never shows the notice.
 * - `quiet`: waits for the binding without a notice (capability checks).
 * - `person`: something the person asked for. It waits for the binding,
 *   and only while it waits does the window say it is reconnecting.
 */
type Wait = 'background' | 'quiet' | 'person';

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
 * A desktop window's platform (B231, B238, B406). It runs the native
 * versions while its bridge is bound. When a call finds the bridge gone, or
 * it never arrived, the window asks the host to bind it again at once and
 * silently (exactly what loading the page does; a fresh attestation then
 * grants its authority), without reloading and losing its state. What the
 * person asked for waits for that and then runs; only a wait of a moment or
 * more says "reconnecting". It never silently runs the browser versions,
 * except where those do the same job: attaching files (the drop path), the
 * clipboard and external links. A save that still cannot show the Save
 * dialog is written into Exports by the server.
 */
export function createDesktopPlatform(
  media: MediaTransport,
  browser: ClientPlatform,
  host: DesktopHost,
  instanceId: string,
  reattest: () => Promise<string | null>,
  retryMs = REBIND_RETRY_MS,
  actionWaitMs = NATIVE_ACTION_WAIT_MS,
): ClientPlatform & {
  /** Bind the endpoint the page has, or wait for it up to `waitMs`. */
  connect(attestation: string, waitMs: number): Promise<void>;
} {
  let native: ClientPlatform | null = null;
  let bound = false;
  // The person's own calls waiting for the bridge right now.
  let waiting = 0;
  let shown: NativeConnection = 'ready';
  // The handshake's attestation, used by the first binding only.
  let first: string | null = null;
  let binding: { endpoint: NativeEndpoint; done: Promise<boolean> } | null =
    null;
  let rebinding = false;
  // The notice's listeners, and the calls waiting for a binding.
  const listeners = new Set<() => void>();
  const whenBound = new Set<() => void>();
  // Read afresh: a binding can finish while the loop below waits.
  const ready = () => bound;
  const publish = () => {
    const next = !bound && waiting > 0 ? 'reconnecting' : 'ready';
    if (shown === next) return;
    shown = next;
    listeners.forEach((listener) => listener());
  };
  const become = (next: NativeConnection) => {
    if (bound === (next === 'ready')) return;
    bound = next === 'ready';
    if (bound) [...whenBound].forEach((resume) => resume());
    publish();
  };
  // Resolves once bound, after `actionWaitMs`, or when the caller gives up.
  const bindingFinished = (signal?: AbortSignal) =>
    new Promise<void>((resolve) => {
      if (bound || signal?.aborted) return resolve();
      const finish = () => {
        whenBound.delete(finish);
        host.clearTimeout(timer);
        signal?.removeEventListener('abort', finish);
        resolve();
      };
      whenBound.add(finish);
      signal?.addEventListener('abort', finish);
      const timer = host.setTimeout(finish, actionWaitMs);
    });

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
  const rebind = (now = false) => {
    native = null;
    become('reconnecting');
    if (rebinding) {
      // A native operation is waiting: try now.
      if (now) wake?.();
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
    wait: Wait = 'person',
    signal?: AbortSignal,
  ): Promise<CapabilityResult<T>> {
    for (let attempt = 0; ; attempt += 1) {
      let current = native;
      if (!current) {
        rebind(wait !== 'background');
        if (wait === 'background') return unavailable('native_reconnecting');
        if (wait === 'person') waiting += 1;
        publish();
        try {
          await bindingFinished(signal);
        } finally {
          if (wait === 'person') waiting -= 1;
          publish();
        }
        current = native;
        if (signal?.aborted) return { status: 'cancelled' };
        if (!current) return unavailable('native_reconnecting');
      }
      const result = await operation(current);
      if (result.status !== 'unavailable' || !LOST.has(result.reason))
        return result;
      if (native === current) rebind(wait !== 'background');
      // A lapsed proof refused the call before it ran: once the window is
      // bound again, it runs (once).
      if (
        wait === 'background' ||
        attempt > 0 ||
        result.reason !== 'native_proof_required'
      )
        return unavailable('native_reconnecting');
    }
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
          whenBound.delete(settle);
          host.clearTimeout(timer);
          resolve();
        };
        const settle = () => {
          if (ready()) finish();
        };
        whenBound.add(settle);
        const timer = host.setTimeout(() => {
          finish();
          if (!ready()) rebind();
        }, waitMs);
      });
    },
    nativeConnection: {
      get: () => shown,
      subscribe: (listener) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
    },
    discover: () => run((platform) => platform.discover(), 'quiet'),
    renewNative: (attestation) =>
      run(
        (platform) =>
          platform.renewNative
            ? platform.renewNative(attestation)
            : Promise.resolve(unavailable()),
        'background',
      ),
    selectFile: async (signal, intent) => {
      // Attaching needs no native path grant: the browser's own file input
      // uploads like a drop does, so it never waits for the bridge.
      if (intent?.intent === 'attachment')
        return orBrowser(
          await run(
            (platform) => platform.selectFile(signal, intent),
            'background',
          ),
          () => browser.selectFile(signal, intent),
        );
      return run(
        (platform) => platform.selectFile(signal, intent),
        'person',
        signal,
      );
    },
    selectFolder: (signal, intent) =>
      run(
        (platform) => platform.selectFolder(signal, intent),
        'person',
        signal,
      ),
    upload: (conversationId, file, signal) =>
      browser.upload(conversationId, file, signal),
    // The browser versions do the same job at once while the bridge binds.
    readClipboard: async () =>
      orBrowser(
        await run((platform) => platform.readClipboard(), 'background'),
        browser.readClipboard,
      ),
    writeClipboard: async (text) =>
      orBrowser(
        await run((platform) => platform.writeClipboard(text), 'background'),
        () => browser.writeClipboard(text),
      ),
    openExternal: async (url) =>
      orBrowser(
        await run((platform) => platform.openExternal(url), 'background'),
        () => browser.openExternal(url),
      ),
    managedWindow: (route) => run((platform) => platform.managedWindow(route)),
    // Reading Buddy's status (polled while it is on the desktop) is not
    // something the person asked for; docking and tearing off are.
    buddyPlacement: (action, point) =>
      run(
        (platform) => platform.buddyPlacement(action, point),
        action === 'status' || action === 'ready' ? 'background' : 'person',
      ),
    publishBuddyTarget: (conversationId) =>
      run(
        (platform) => platform.publishBuddyTarget(conversationId),
        'background',
      ),
    readBuddyTarget: () =>
      run((platform) => platform.readBuddyTarget(), 'background'),
    buddyPet: (request) =>
      run((platform) => platform.buddyPet(request), 'background'),
    showMainWindow: (conversationId) =>
      run((platform) => platform.showMainWindow(conversationId)),
    moveWindow: (x, y) => native?.moveWindow(x, y) ?? false,
    openTerminal: (conversationId) =>
      run((platform) => platform.openTerminal(conversationId)),
    openExternalTerminal: (conversationId) =>
      run((platform) => platform.openExternalTerminal(conversationId)),
    save: async (reference, name, signal) => {
      const result = await run(
        (platform) => platform.save(reference, name, signal),
        'person',
        signal,
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
