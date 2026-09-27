import type { HandshakeView } from '../api/types';
import { createBrowserPlatform } from './browser';
import { createPyWebViewPlatform, type NativeEndpoint } from './native';
import type { ClientPlatform, MediaTransport } from './types';

export type {
  BuddyPlacement,
  BuddyPlacementAction,
  BuddyTarget,
  CapabilityResult,
  ClientPlatform,
  MediaTransport,
  PlatformInfo,
  Selection,
  SelectionIntent,
} from './types';
export { createBrowserPlatform } from './browser';
export { createPyWebViewPlatform } from './native';
export { nativeConversationId } from './types';

declare global {
  interface Window {
    __ROW_BOT_NATIVE_CLIENT__?: NativeEndpoint;
  }
}

/** How long a native window waits for pywebview to finish loading the page. */
export const NATIVE_READY_WAIT_MS = 8000;

/**
 * A page inside a pywebview window. `window.pywebview` arrives late, but the
 * embedding engines expose their message channels from the first script:
 * WebView2 (`chrome.webview`) and WKWebView/WebKitGTK (`webkit.messageHandlers
 * .jsBridge`, the handler pywebview registers). Browsers have neither.
 */
export function pywebviewHost(target: Window): boolean {
  const host = target as Window & {
    chrome?: { webview?: unknown };
    webkit?: { messageHandlers?: { jsBridge?: unknown } };
  };
  return (
    'pywebview' in target ||
    Boolean(host.chrome?.webview) ||
    Boolean(host.webkit?.messageHandlers?.jsBridge)
  );
}

export async function selectClientPlatform(
  media: MediaTransport,
  handshake: Pick<HandshakeView, 'native_adapter'> | null | undefined,
  target: Window = window,
): Promise<ClientPlatform> {
  const browser = createBrowserPlatform(media, target);
  const authorization = handshake?.native_adapter;
  let endpoint = target.__ROW_BOT_NATIVE_CLIENT__;
  // pywebview installs its API and the document-bound endpoint only when the
  // navigation completes, often after a cold start's handshake (B95). This
  // bounded wait grants no authority without the server attestation and
  // native discovery.
  if (!endpoint && authorization?.available && pywebviewHost(target)) {
    await new Promise<void>((resolve) => {
      const finished = () => {
        target.removeEventListener('row-bot-native-ready', finished);
        target.clearTimeout(timeout);
        resolve();
      };
      const timeout = target.setTimeout(finished, NATIVE_READY_WAIT_MS);
      target.addEventListener('row-bot-native-ready', finished, { once: true });
      if (target.__ROW_BOT_NATIVE_CLIENT__) finished();
    });
    endpoint = target.__ROW_BOT_NATIVE_CLIENT__;
  }
  if (
    !authorization?.available ||
    !authorization.attestation ||
    !authorization.instance_id ||
    !endpoint
  )
    return browser;
  const native = createPyWebViewPlatform(
    endpoint,
    media,
    authorization.attestation,
    target as Parameters<typeof createPyWebViewPlatform>[3],
  );
  const discovered = await native.discover();
  return discovered.status === 'ok' &&
    discovered.value.kind === 'pywebview' &&
    discovered.value.instanceId === authorization.instance_id
    ? native
    : browser;
}
