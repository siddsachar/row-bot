import type { ClientController } from './api';

const STORAGE_KEY = 'row-bot:active-conversation:v1';
const CONVERSATION_ID = /^[A-Za-z0-9:_-]{1,128}$/;
const INSTANCE_ID = /^[A-Za-z0-9:_-]{1,128}$/;

type ActiveConversationRecord = {
  schema_version: 1;
  instance_id: string;
  conversation_id: string;
};

type ActiveConversationController = Pick<
  ClientController,
  'getSnapshot' | 'selectConversation' | 'subscribe'
>;

function validRecord(value: unknown): value is ActiveConversationRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return (
    Object.keys(record).length === 3 &&
    record.schema_version === 1 &&
    typeof record.instance_id === 'string' &&
    INSTANCE_ID.test(record.instance_id) &&
    typeof record.conversation_id === 'string' &&
    CONVERSATION_ID.test(record.conversation_id)
  );
}

function read(storage: Storage): ActiveConversationRecord | null {
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (raw === null) return null;
    const record: unknown = JSON.parse(raw);
    if (validRecord(record)) return record;
  } catch {
    // Treat inaccessible or malformed session state as absent.
  }
  remove(storage);
  return null;
}

function remove(storage: Storage): void {
  try {
    storage.removeItem(STORAGE_KEY);
  } catch {
    // Session storage is optional in restricted browser contexts.
  }
}

function write(
  storage: Storage,
  instanceId: string,
  conversationId: string,
): void {
  if (!INSTANCE_ID.test(instanceId) || !CONVERSATION_ID.test(conversationId)) {
    remove(storage);
    return;
  }
  try {
    storage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        schema_version: 1,
        instance_id: instanceId,
        conversation_id: conversationId,
      } satisfies ActiveConversationRecord),
    );
  } catch {
    // Selection remains memory-only if session storage is unavailable.
  }
}

function hasExplicitConversationRoute(pathname: string): boolean {
  return /^\/conversations\/[^/]+$/.test(pathname);
}

/**
 * Retains only the active opaque conversation ID for this tab and backend.
 * Authentication must succeed before stored state can affect selection.
 */
export function bindActiveConversationSession(
  controller: ActiveConversationController,
  pathname: string,
  storage: Storage | null,
): () => void {
  if (!storage) return () => undefined;
  const explicitRoute = hasExplicitConversationRoute(pathname);
  let observedInstance: string | null = null;

  const synchronize = () => {
    const state = controller.getSnapshot();
    if (state.status === 'unauthorized' || state.status === 'incompatible') {
      observedInstance = null;
      remove(storage);
      return;
    }

    const instanceId = state.handshake?.instance_id;
    if (!instanceId) return;
    if (!INSTANCE_ID.test(instanceId)) {
      observedInstance = null;
      remove(storage);
      return;
    }

    const selected = state.selectedConversationId;
    if (selected !== null) {
      if (CONVERSATION_ID.test(selected)) write(storage, instanceId, selected);
      else remove(storage);
      observedInstance = instanceId;
      return;
    }

    if (observedInstance === instanceId) return;
    observedInstance = instanceId;
    const stored = read(storage);
    if (!stored) return;
    if (stored.instance_id !== instanceId) {
      remove(storage);
      return;
    }
    if (!explicitRoute)
      void controller.selectConversation(stored.conversation_id);
  };

  const unsubscribe = controller.subscribe(synchronize);
  synchronize();
  return unsubscribe;
}

export const activeConversationSessionKey = STORAGE_KEY;

export function browserSessionStorage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}
