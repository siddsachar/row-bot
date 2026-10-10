import type { ConversationView } from '../../api/types';

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
