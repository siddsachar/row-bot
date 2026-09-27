import { useEffect, useState } from 'react';
import type { ConversationView } from '../api/types';
import type { ClientPlatform } from '../platform';
import { nativeConversationId } from '../platform';

/** How often the desktop Buddy re-reads its target when no hint arrives. */
export const TARGET_POLL_MS = 5000;

/**
 * The conversation the desktop Buddy follows. The main window publishes its
 * selection to the native host; the host hints this window
 * (`row-bot-native-changed`) and the overlay re-reads the target through its
 * own attested bridge, so the hint itself carries nothing. An explicit
 * `?conversation=` (browser, tests) wins; with neither, the latest
 * conversation is used.
 */
export function useBuddyTarget(
  platform: ClientPlatform,
  explicit: string | null,
  conversations: readonly ConversationView[],
  target: Window = window,
): { conversationId: string | null; source: 'explicit' | 'host' | 'recent' } {
  const [hosted, setHosted] = useState<string | null>(null);
  useEffect(() => {
    if (explicit) return;
    let active = true;
    let latest = -1;
    const read = () =>
      void platform.readBuddyTarget().then((result) => {
        if (!active || result.status !== 'ok') return;
        const { conversationId, revision } = result.value;
        if (revision < latest) return;
        latest = revision;
        // A null target keeps the last conversation the main window showed.
        if (conversationId) setHosted(conversationId);
      });
    read();
    const timer = target.setInterval(read, TARGET_POLL_MS);
    target.addEventListener('row-bot-native-changed', read);
    target.addEventListener('focus', read);
    return () => {
      active = false;
      target.clearInterval(timer);
      target.removeEventListener('row-bot-native-changed', read);
      target.removeEventListener('focus', read);
    };
  }, [platform, explicit, target]);
  if (explicit && nativeConversationId(explicit))
    return { conversationId: explicit, source: 'explicit' };
  if (hosted) return { conversationId: hosted, source: 'host' };
  return { conversationId: mostRecent(conversations), source: 'recent' };
}

/** The most recently updated top-level conversation, if any. */
export function mostRecent(
  conversations: readonly ConversationView[],
): string | null {
  let best: ConversationView | null = null;
  for (const row of conversations) {
    if (row.parent_conversation_id) continue;
    if (!best || (row.updated_at ?? '') > (best.updated_at ?? '')) best = row;
  }
  return best?.id ?? null;
}
