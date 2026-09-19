import type { ConversationView } from '../../api/types';
import { Field, Select } from '../../ui/primitives';

const MAX_SETTINGS_CONVERSATIONS = 50;

export function settingsConversationChoices(
  conversations: readonly ConversationView[],
  preferredId?: string | null,
) {
  const choices = conversations.slice(0, MAX_SETTINGS_CONVERSATIONS);
  if (
    preferredId &&
    !choices.some((conversation) => conversation.id === preferredId)
  ) {
    const preferred = conversations.find(
      (conversation) => conversation.id === preferredId,
    );
    if (preferred) choices.unshift(preferred);
  }
  return choices.slice(0, MAX_SETTINGS_CONVERSATIONS);
}

export function resolveSettingsConversation(
  conversations: readonly ConversationView[],
  requestedId?: string | null,
  selectedId?: string | null,
) {
  const requested = conversations.find(
    (conversation) => conversation.id === requestedId,
  );
  if (requested) return requested.id;
  const selected = conversations.find(
    (conversation) => conversation.id === selectedId,
  );
  return selected?.id ?? conversations[0]?.id ?? null;
}

export default function SettingsConversationPicker({
  conversations,
  conversationId,
  onChange,
}: {
  conversations: readonly ConversationView[];
  conversationId: string;
  onChange: (conversationId: string) => void;
}) {
  const choices = settingsConversationChoices(conversations, conversationId);
  return (
    <section
      className="settings-conversation-context"
      aria-label="Settings conversation context"
    >
      <Field
        label="Conversation context"
        hint="This choice applies only to this Settings page."
      >
        <Select
          aria-label="Conversation context"
          value={conversationId}
          onChange={(event) => onChange(event.target.value)}
        >
          {choices.map((conversation) => (
            <option key={conversation.id} value={conversation.id}>
              {conversation.title.trim() || 'Untitled conversation'}
            </option>
          ))}
        </Select>
      </Field>
    </section>
  );
}
