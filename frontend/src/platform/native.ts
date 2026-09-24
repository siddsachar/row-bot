import type {
  CapabilityResult,
  ClientPlatform,
  MediaTransport,
  PlatformInfo,
  Selection,
  SelectionIntent,
} from './types';
import {
  protect,
  safeDownloadName,
  safeExternalUrl,
  unavailable,
} from './types';

export interface NativeEndpoint {
  dispatch(
    operation: string,
    payload: Record<string, unknown>,
  ): Promise<unknown>;
}

const object = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value);
const reference = (value: unknown): value is string =>
  typeof value === 'string' && /^[A-Za-z0-9:_-]{1,256}$/.test(value);

// The closure endpoint is installed by trusted shell code. This is not a flag
// check; Python validates instance/window/document proof before every effect.
export function createPyWebViewPlatform(
  endpoint: NativeEndpoint,
  media: MediaTransport,
  attestation: string,
): ClientPlatform {
  async function call<T>(
    operation: string,
    payload: Record<string, unknown>,
    valid: (value: unknown) => value is T,
  ): Promise<CapabilityResult<T>> {
    try {
      const response = await endpoint.dispatch(operation, payload);
      if (!object(response)) return unavailable('invalid_native_response');
      if (response.status === 'cancelled') return { status: 'cancelled' };
      if (response.status === 'unavailable')
        return unavailable('native_operation_unavailable');
      return response.status === 'ok' && valid(response.value)
        ? { status: 'ok', value: response.value }
        : unavailable('invalid_native_response');
    } catch {
      return unavailable('native_operation_failed');
    }
  }
  const nullValue = (value: unknown): value is null => value === null;
  const selection = async (
    kind: 'file' | 'folder',
    signal?: AbortSignal,
    intent?: SelectionIntent,
  ): Promise<CapabilityResult<Selection>> => {
    if (signal?.aborted) return { status: 'cancelled' };
    if (!intent) return unavailable('native_intent_required');
    const result = await call<Selection>(
      kind === 'file' ? 'select_file' : 'select_folder',
      intent,
      (value): value is Selection =>
        object(value) &&
        value.kind === kind &&
        reference(value.reference) &&
        Object.keys(value).length === 2,
    );
    return signal?.aborted ? { status: 'cancelled' } : result;
  };
  return {
    discover: () =>
      call<PlatformInfo>(
        'discover',
        { attestation },
        (value): value is PlatformInfo =>
          object(value) &&
          value.kind === 'pywebview' &&
          ['windows', 'macos', 'linux', 'unknown'].includes(
            String(value.platform),
          ) &&
          Array.isArray(value.capabilities) &&
          value.capabilities.every((item) => typeof item === 'string') &&
          typeof value.instanceId === 'string' &&
          typeof value.windowId === 'string' &&
          typeof value.epoch === 'number',
      ),
    selectFile: (signal, intent) => selection('file', signal, intent),
    selectFolder: (signal, intent) => selection('folder', signal, intent),
    upload: (conversationId, file, signal) =>
      protect(() => media.upload(conversationId, file, signal)),
    readClipboard: () =>
      call<string>(
        'clipboard_read',
        {},
        (value): value is string =>
          typeof value === 'string' && value.length <= 65536,
      ),
    writeClipboard: (text) =>
      text.length > 65536
        ? Promise.resolve(unavailable('payload_too_large'))
        : call('clipboard_write', { text }, nullValue),
    openExternal: (value) => {
      const url = safeExternalUrl(value);
      return url
        ? call('open_external', { url }, nullValue)
        : Promise.resolve(unavailable('invalid_url'));
    },
    managedWindow: (route) =>
      /^\/app-v2\/(?:[A-Za-z0-9_-]+\/?)*$/.test(route)
        ? call('managed_window', { route }, nullValue)
        : Promise.resolve(unavailable('invalid_route')),
    openTerminal: (conversationId) =>
      call<{ terminalId: string }>(
        'terminal_open',
        { conversationId },
        (value): value is { terminalId: string } =>
          object(value) &&
          reference(value.terminalId) &&
          Object.keys(value).length === 1,
      ),
    save: async (ref, name, signal) => {
      if (signal?.aborted) return { status: 'cancelled' };
      if (!reference(ref) || !safeDownloadName(name))
        return unavailable('invalid_request');
      const result = await call('save', { reference: ref, name }, nullValue);
      // Discard late completion; this cannot undo an already performed host save.
      return signal?.aborted ? { status: 'cancelled' } : result;
    },
  };
}
