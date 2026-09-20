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
  const endpoint = target.__ROW_BOT_NATIVE_CLIENT__;
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
