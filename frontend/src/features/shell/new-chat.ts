import type { ConversationView } from '../../api/types';

// What the person is typing in Home's box while it shows. A New chat that opens as they type (a slow
// one, over a remote link) takes it into the new chat's composer, so nothing typed is lost (M12).
let homeText = '';

export function setHomeDraft(text: string): void {
  homeText = text;
}

/** Home's text, once: the chat that opens now has it. */
export function takeHomeDraft(): string {
  const text = homeText;
  homeText = '';
  return text;
}

/** How voice starts in a chat opened from Home's mic or Talk (D3). */
export type HomeVoice = 'dictate' | 'talk';

// Home's mic or Talk opened this chat: its composer starts that voice once, when it can.
let voiceOnOpen: { conversationId: string; voice: HomeVoice } | null = null;

export function startVoiceOnOpen(conversationId: string, voice: HomeVoice) {
  voiceOnOpen = { conversationId, voice };
}

/** Whether this chat should start this voice now; true once. */
export function takeVoiceOnOpen(
  conversationId: string,
  voice: HomeVoice,
): boolean {
  if (
    voiceOnOpen?.conversationId !== conversationId ||
    voiceOnOpen.voice !== voice
  )
    return false;
  voiceOnOpen = null;
  return true;
}

/** The name the server gives a new chat; its first message renames it (B230). */
const NEW_CHAT_TITLE = 'New conversation';

/**
 * A chat that was started and never used: it still has the new-chat name,
 * nothing runs or waits in it, and no folder, design or agent belongs to it.
 */
export function neverUsed(row: ConversationView): boolean {
  return (
    row.title === NEW_CHAT_TITLE &&
    !row.parent_conversation_id &&
    !row.activity_state &&
    !row.generation_state?.length &&
    !row.resource_bindings?.length &&
    (row.category ?? 'chat') === 'chat'
  );
}

/**
 * The most recent never-used chat without a draft, which New chat opens
 * instead of making another empty one.
 */
export function unusedChat(
  rows: readonly ConversationView[],
  draftOf: (id: string) => { text: string; attachments: readonly unknown[] },
): string | null {
  const candidates = rows.filter((row) => {
    if (!neverUsed(row)) return false;
    const draft = draftOf(row.id);
    return !draft.text.trim() && !draft.attachments.length;
  });
  candidates.sort((a, b) =>
    (b.updated_at ?? '').localeCompare(a.updated_at ?? ''),
  );
  return candidates[0]?.id ?? null;
}
