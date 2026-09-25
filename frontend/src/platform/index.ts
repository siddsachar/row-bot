import type { HandshakeView } from '../api/types';
import { createBrowserPlatform } from './browser';
import { createPyWebViewPlatform, type NativeEndpoint } from './native';
import type { ClientPlatform, MediaTransport } from './types';

export type {
  CapabilityResult,
  ClientPlatform,
  MediaTransport,
  PlatformInfo,
  Selection,
  SelectionIntent,
} from './types';
export { createBrowserPlatform } from './browser';
export { createPyWebViewPlatform } from './native';

declare global {
  interface Window {
    __ROW_BOT_NATIVE_CLIENT__?: NativeEndpoint;
  }
}

export async function selectClientPlatform(
  media: MediaTransport,
  handshake: Pick<HandshakeView, 'native_adapter'> | null | undefined,
  target: Window = window,
): Promise<ClientPlatform> {
  const browser = createBrowserPlatform(media, target);
  const authorization = handshake?.native_adapter;
  let endpoint = target.__ROW_BOT_NATIVE_CLIENT__;
  // pywebview installs its document-bound endpoint on loaded, which can race
  // the React handshake. This bounded wait grants no authority without the
  // server attestation and native discovery.
  if (!endpoint && authorization?.available && 'pywebview' in target) {
    await new Promise<void>((resolve) => {
      const finished = () => {
        target.removeEventListener('row-bot-native-ready', finished);
        target.clearTimeout(timeout);
        resolve();
      };
      const timeout = target.setTimeout(finished, 600);
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
  );
  const discovered = await native.discover();
  return discovered.status === 'ok' &&
    discovered.value.kind === 'pywebview' &&
    discovered.value.instanceId === authorization.instance_id
    ? native
    : browser;
}
