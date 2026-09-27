import type { AttachmentView } from '../api/types';

export type CapabilityResult<T> =
  | { status: 'ok'; value: T }
  | { status: 'cancelled' }
  | { status: 'unavailable'; reason: string };

export type Selection =
  | { kind: 'file'; files: File[] }
  | { kind: 'folder'; files: File[] }
  | { kind: 'file' | 'folder'; reference: string };

export type SelectionIntent = {
  intentId: string;
  intent: string;
  conversationId: string | null;
  destination: string;
};

export interface PlatformInfo {
  kind: 'browser' | 'pywebview' | 'fake';
  platform: 'browser' | 'windows' | 'macos' | 'linux' | 'unknown';
  capabilities: string[];
  instanceId?: string;
  windowId?: string;
  epoch?: number;
}

// Implemented by the authenticated ClientController; never a second transport.
export interface MediaTransport {
  upload(
    conversationId: string,
    file: File,
    signal?: AbortSignal,
  ): Promise<AttachmentView>;
  download(reference: string, signal?: AbortSignal): Promise<Blob>;
}

/**
 * Desktop Buddy placement actions. Main windows tear Buddy off, dock it and
 * read its status; the desktop Buddy itself docks, hides and reports "ready"
 * once its first view is drawn (the host reveals it only then).
 */
export type BuddyPlacementAction =
  'status' | 'tear_off' | 'dock' | 'hide' | 'ready';
export type BuddyPlacement = {
  placement: 'docked' | 'desktop';
  visible: boolean;
};
/** The conversation the desktop Buddy follows, as the native host last heard it. */
export type BuddyTarget = { conversationId: string | null; revision: number };

/** Conversation ids accepted by the native host (its `_SCOPE_VALUE`). */
export const nativeConversationId = (value: unknown): value is string =>
  typeof value === 'string' && /^[A-Za-z0-9:_.-]{1,256}$/.test(value);

export interface ClientPlatform {
  discover(): Promise<CapabilityResult<PlatformInfo>>;
  /**
   * Native windows only: exchange a fresh attestation to renew this
   * document's native lease, which otherwise lapses after 30 minutes.
   */
  renewNative?(attestation: string): Promise<CapabilityResult<PlatformInfo>>;
  buddyPlacement(
    action: BuddyPlacementAction,
    point?: { x: number; y: number },
  ): Promise<CapabilityResult<BuddyPlacement>>;
  /** Main windows: tell the desktop Buddy which conversation is open. */
  publishBuddyTarget(
    conversationId: string,
  ): Promise<CapabilityResult<BuddyTarget>>;
  /** Desktop Buddy: the conversation the main window has open. */
  readBuddyTarget(): Promise<CapabilityResult<BuddyTarget>>;
  /** Desktop Buddy: bring the main window forward on a conversation. */
  showMainWindow(
    conversationId: string | null,
  ): Promise<CapabilityResult<null>>;
  /**
   * Frameless windows: move this window's top-left corner to a screen point
   * (CSS pixels). Returns false where the host cannot move windows.
   */
  moveWindow(x: number, y: number): boolean;
  selectFile(
    signal?: AbortSignal,
    intent?: SelectionIntent,
  ): Promise<CapabilityResult<Selection>>;
  selectFolder(
    signal?: AbortSignal,
    intent?: SelectionIntent,
  ): Promise<CapabilityResult<Selection>>;
  upload(
    conversationId: string,
    file: File,
    signal?: AbortSignal,
  ): Promise<CapabilityResult<AttachmentView>>;
  readClipboard(): Promise<CapabilityResult<string>>;
  writeClipboard(text: string): Promise<CapabilityResult<null>>;
  openExternal(url: string): Promise<CapabilityResult<null>>;
  managedWindow(route: string): Promise<CapabilityResult<null>>;
  openTerminal(
    conversationId: string | null,
  ): Promise<CapabilityResult<{ terminalId: string }>>;
  save(
    reference: string,
    name: string,
    signal?: AbortSignal,
  ): Promise<CapabilityResult<null>>;
}

export const unavailable = (
  reason = 'unsupported',
): CapabilityResult<never> => ({ status: 'unavailable', reason });

export function safeExternalUrl(value: string): string | undefined {
  if (
    value.length > 2048 ||
    /[\s\\]/u.test(value) ||
    [...value].some((character) => character.charCodeAt(0) < 32)
  )
    return undefined;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) &&
      url.hostname &&
      !url.username &&
      !url.password
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}

export const safeDownloadName = (name: string): boolean =>
  /^[a-zA-Z0-9][a-zA-Z0-9 ._-]{0,119}$/.test(name);

export async function protect<T>(
  action: () => Promise<T>,
): Promise<CapabilityResult<T>> {
  try {
    return { status: 'ok', value: await action() };
  } catch (error) {
    return error instanceof DOMException && error.name === 'AbortError'
      ? { status: 'cancelled' }
      : unavailable('operation_failed');
  }
}
