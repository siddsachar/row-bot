import { useSyncExternalStore } from 'react';

/**
 * Lets a side panel hand a short request to the open conversation's composer,
 * which sends it through its own path (model, write targets, queue while a
 * turn runs, receipts). Only the mounted conversation registers a sender, so
 * nothing is sent for a conversation that is not on screen.
 */
/** A reply button's tap: the reply that offered it, and the button's label. */
export type TappedChoice = { message_id: string; label?: string };
/** A press on a surface's button: the reply, the surface, the action and its context. */
export type SurfacePress = {
  message_id: string;
  surface_id: string;
  name: string;
  source_component_id: string;
  context: Record<string, unknown>;
};
/** What sent a prompt from the transcript: a reply button, or a surface's button. */
export type Tapped = TappedChoice | { surface: SurfacePress };
type Sender = (text: string, tapped?: Tapped) => boolean;

const senders = new Map<string, Sender>();
const ready = new Map<string, boolean>();
const listeners = new Set<() => void>();

export function registerPromptSender(
  conversationId: string,
  sender: Sender,
): () => void {
  senders.set(conversationId, sender);
  return () => {
    if (senders.get(conversationId) === sender) senders.delete(conversationId);
  };
}

/** True when the conversation's composer accepted the request. */
export function sendPrompt(
  conversationId: string,
  text: string,
  tapped?: Tapped,
): boolean {
  const sender = senders.get(conversationId);
  if (!sender || !text.trim()) return false;
  try {
    return sender(text, tapped);
  } catch {
    return false;
  }
}

/** The composer says whether it can take a request now (no turn or send in flight). */
export function setPromptReady(conversationId: string, value: boolean) {
  if (ready.get(conversationId) === value) return;
  ready.set(conversationId, value);
  for (const listener of listeners) listener();
}

/** Whether that conversation's composer can take a request now; false until it says so. */
export function usePromptReady(conversationId: string | undefined): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => (conversationId ? (ready.get(conversationId) ?? false) : false),
  );
}
