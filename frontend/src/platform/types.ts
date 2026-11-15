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
  /** Desktop app: the server writes a conversation export into Exports. */
  saveToExports(
    reference: string,
    signal?: AbortSignal,
  ): Promise<{ file_name: string; folder: string }>;
  revealExport(fileName: string): Promise<{ status: string }>;
}

/**
 * Where a save went (B238): the file chosen in the Save dialog, a browser
 * download (a page cannot tell whether it finished), or, while a desktop
 * window reconnects, a copy the server wrote into the Exports folder.
 */
export type SavedFile =
  | {
      kind: 'file';
      /** The chosen file's own name (never its folder), when the host says. */
      fileName?: string;
      /** Show the file chosen in the Save dialog in the file manager. */
      reveal?(): Promise<boolean>;
    }
  | { kind: 'download' }
  | {
      kind: 'exports';
      fileName: string;
      folder: string;
      /** Show the copy in the file manager; false if it could not. */
      reveal(): Promise<boolean>;
    };

/**
 * 'reconnecting' while something the person asked for waits on a desktop
 * window's native features to bind again; 'ready' otherwise (B406).
 */
export type NativeConnection = 'ready' | 'reconnecting';

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
/**
 * The desktop pet's own window (D5–D9). The page reports its layout (bubble or
 * menu open) and which pixels take clicks (Buddy's silhouette as 24×24 cells,
 * base64, plus the open bubble or menu), says when a drag starts and ends,
 * and reads where the cursor is and whether Buddy just landed.
 */
export type BuddyPetRequest =
  | { action: 'layout'; expanded: boolean }
  | {
      action: 'hit';
      pet: [number, number, number, number];
      cells: string;
      bubble?: [number, number, number, number] | null;
      menu?: [number, number, number, number] | null;
    }
  | { action: 'drag'; phase: 'start' | 'end' }
  | { action: 'state' };
export type BuddyPetState = {
  side: 'left' | 'right';
  expanded: boolean;
  width: number;
  height: number;
  /** Buddy's box inside the window, in CSS pixels. */
  petX: number;
  petY: number;
  /** The cursor relative to Buddy's box, and seconds since it last moved. */
  cursor: { x: number; y: number; age: number } | null;
  landed: number;
  perched: boolean;
  following: boolean;
};
export type BuddyPetAnswer = Partial<BuddyPetState> & { accepted?: boolean };

/** The conversation the desktop Buddy follows, as the native host last heard it. */
export type BuddyTarget = { conversationId: string | null; revision: number };

/** Conversation ids accepted by the native host (its `_SCOPE_VALUE`). */
export const nativeConversationId = (value: unknown): value is string =>
  typeof value === 'string' && /^[A-Za-z0-9:_.-]{1,256}$/.test(value);

/** A Proactive place the desktop Buddy may open in the main window (the host's `MAIN_TARGET`). */
export type MainWindowTarget =
  'for-you' | 'drafts' | `draft:${string}` | `why:${string}`;

export const mainWindowTarget = (value: unknown): value is MainWindowTarget =>
  typeof value === 'string' &&
  /^(?:for-you|drafts|draft:[0-9a-f]{16}|why:[0-9a-f]{16})$/.test(value);

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
  /** Desktop Buddy: its own pet window (layout, hit mask, drags, state). */
  buddyPet(request: BuddyPetRequest): Promise<CapabilityResult<BuddyPetAnswer>>;
  /**
   * Desktop Buddy: bring the main window forward on a conversation, or on a
   * Proactive place (`target`: For you, Drafts, a draft or a moment's Why this).
   */
  showMainWindow(
    conversationId: string | null,
    target?: MainWindowTarget,
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
  /**
   * Desktop app only: open the person's own terminal app at the
   * conversation's code folder (else home). The host picks the folder.
   */
  openExternalTerminal(
    conversationId: string | null,
  ): Promise<CapabilityResult<null>>;
  save(
    reference: string,
    name: string,
    signal?: AbortSignal,
  ): Promise<CapabilityResult<SavedFile>>;
  /**
   * Desktop windows: whether something the person asked for is waiting on
   * native features that are binding again (the window binds its bridge
   * again by itself and silently; B231, B406).
   */
  nativeConnection?: {
    get(): NativeConnection;
    subscribe(listener: () => void): () => void;
  };
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
