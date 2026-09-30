import type { HandshakeView } from '../api/types';
import { createBrowserPlatform } from './browser';
import { createDesktopPlatform } from './desktop';
import type { NativeEndpoint } from './native';
import type { ClientPlatform, MediaTransport } from './types';

export type {
  BuddyPlacement,
  BuddyPlacementAction,
  BuddyTarget,
  CapabilityResult,
  ClientPlatform,
  MediaTransport,
  NativeConnection,
  PlatformInfo,
  SavedFile,
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
  // A fresh attestation from this document's session, used when the server
  // refuses the one it holds (B102) and to bind the window again (B231).
  reattest: () => Promise<string | null> = async () => null,
): Promise<ClientPlatform> {
  const browser = createBrowserPlatform(media, target);
  const authorization = handshake?.native_adapter;
  if (
    !authorization?.available ||
    !authorization.attestation ||
    !authorization.instance_id ||
    (!target.__ROW_BOT_NATIVE_CLIENT__ && !pywebviewHost(target))
  )
    return browser;
  // A desktop window runs its native versions, or says they are reconnecting
  // while it binds (again); it never silently runs the browser versions,
  // which it cannot save with (B238). Waiting grants no authority without the
  // server attestation and native discovery.
  const desktop = createDesktopPlatform(
    media,
    browser,
    target,
    authorization.instance_id,
    reattest,
  );
  await desktop.connect(authorization.attestation, NATIVE_READY_WAIT_MS);
  return desktop;
}
