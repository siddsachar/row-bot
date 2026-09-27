import type {
  BuddyPlacement,
  BuddyTarget,
  CapabilityResult,
  ClientPlatform,
  MediaTransport,
  PlatformInfo,
  Selection,
  SelectionIntent,
} from './types';
import {
  nativeConversationId,
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

/** pywebview's own page bridge; only its window-move channel is used here. */
type PyWebViewHost = {
  pywebview?: {
    _jsApiCallback?(name: string, params: unknown, id: string): unknown;
  };
};

const object = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value);
const reference = (value: unknown): value is string =>
  typeof value === 'string' && /^[A-Za-z0-9:_-]{1,256}$/.test(value);
const placementValue = (value: unknown): value is BuddyPlacement =>
  object(value) &&
  ['docked', 'desktop'].includes(String(value.placement)) &&
  typeof value.visible === 'boolean' &&
  Object.keys(value).length === 2;
const targetValue = (value: unknown): value is BuddyTarget =>
  object(value) &&
  (value.conversationId === null ||
    nativeConversationId(value.conversationId)) &&
  Number.isSafeInteger(value.revision) &&
  (value.revision as number) >= 0 &&
  Object.keys(value).length === 2;

// The closure endpoint is installed by trusted shell code. This is not a flag
// check; Python validates instance/window/document proof before every effect.
export function createPyWebViewPlatform(
  endpoint: NativeEndpoint,
  media: MediaTransport,
  initialAttestation: string,
  host: PyWebViewHost = window as PyWebViewHost,
): ClientPlatform {
  // The latest attestation this document exchanged; renewNative replaces it.
  let attestation = initialAttestation;
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
        // A lapsed lease is told apart: its window can only recover by
        // loading again (B99). Other reasons stay generic.
        return unavailable(
          response.reason === 'native_proof_required'
            ? 'native_proof_required'
            : 'native_operation_unavailable',
        );
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
  const platformInfo = (value: unknown): value is PlatformInfo =>
    object(value) &&
    value.kind === 'pywebview' &&
    ['windows', 'macos', 'linux', 'unknown'].includes(String(value.platform)) &&
    Array.isArray(value.capabilities) &&
    value.capabilities.every((item) => typeof item === 'string') &&
    typeof value.instanceId === 'string' &&
    typeof value.windowId === 'string' &&
    typeof value.epoch === 'number';
  return {
    // A fresh attestation (from a new handshake on this session) renews the
    // document's lease before it lapses.
    renewNative: async (fresh) => {
      if (!reference(fresh)) return unavailable('invalid_attestation');
      const result = await call(
        'discover',
        { attestation: fresh },
        platformInfo,
      );
      if (result.status === 'ok') attestation = fresh;
      return result;
    },
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
    buddyPlacement: async (action, point) => {
      if (
        action === 'tear_off' &&
        (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y))
      )
        return unavailable('invalid_drop_position');
      const discovered = await call<PlatformInfo>(
        'discover',
        { attestation },
        (value): value is PlatformInfo =>
          object(value) &&
          value.kind === 'pywebview' &&
          Array.isArray(value.capabilities) &&
          typeof value.instanceId === 'string' &&
          typeof value.windowId === 'string' &&
          typeof value.epoch === 'number',
      );
      if (
        discovered.status !== 'ok' ||
        !discovered.value.capabilities.includes('buddy_placement')
      )
        return unavailable('buddy_placement_requires_native');
      return call(
        'buddy_placement',
        action === 'tear_off'
          ? { action, x: point!.x, y: point!.y }
          : { action },
        placementValue,
      );
    },
    // The host refuses these outside the window role that owns them: main
    // windows publish, only the desktop Buddy reads and shows the main window.
    publishBuddyTarget: (conversationId) =>
      nativeConversationId(conversationId)
        ? call('buddy_follow', { conversationId }, targetValue)
        : Promise.resolve(unavailable('invalid_conversation')),
    readBuddyTarget: () => call('buddy_follow', {}, targetValue),
    showMainWindow: (conversationId) =>
      conversationId === null || nativeConversationId(conversationId)
        ? call('main_window', { conversationId }, nullValue)
        : Promise.resolve(unavailable('invalid_conversation')),
    // pywebview binds `.pywebview-drag-region` once, when the page loads,
    // before this client renders; the same move channel serves our header.
    moveWindow: (x, y) => {
      const bridge = host.pywebview?._jsApiCallback;
      if (typeof bridge !== 'function' || !Number.isFinite(x + y)) return false;
      bridge.call(
        host.pywebview,
        'pywebviewMoveWindow',
        [Math.round(x), Math.round(y)],
        'move',
      );
      return true;
    },
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
