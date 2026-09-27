/**
 * Lets a side panel hand a short request to the open conversation's composer,
 * which sends it through its own path (model, write targets, queue while a
 * turn runs, receipts). Only the mounted conversation registers a sender, so
 * nothing is sent for a conversation that is not on screen.
 */
type Sender = (text: string) => boolean;

const senders = new Map<string, Sender>();

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
export function sendPrompt(conversationId: string, text: string): boolean {
  const sender = senders.get(conversationId);
  if (!sender || !text.trim()) return false;
  try {
    return sender(text);
  } catch {
    return false;
  }
}
