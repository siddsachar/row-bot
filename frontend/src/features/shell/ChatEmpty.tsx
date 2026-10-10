import { useState } from 'react';
import {
  Brain,
  CalendarDays,
  FileText,
  Globe,
  LayoutTemplate,
  ListChecks,
  MessageSquare,
  Palette,
  PenLine,
  Zap,
  type LucideIcon,
} from 'lucide-react';
import type { ConversationView } from '../../api/types';
import { relativeTime } from '../../ui/format';
import { neverUsed } from './new-chat';
import {
  DESIGN_LABELS,
  DESIGN_PROMPTS,
  EXAMPLE_LABELS,
  EXAMPLE_PROMPTS,
} from './welcome-prompts';

type Suggestion = { label: string; prompt: string; Icon: LucideIcon };

const GENERAL: Suggestion[] = EXAMPLE_PROMPTS.map((prompt, index) => ({
  prompt,
  label: EXAMPLE_LABELS[index],
  Icon: [FileText, Zap, Palette, Brain, Globe, CalendarDays][index],
}));
/** A chat working on a design suggests work on that design. */
const DESIGN: Suggestion[] = DESIGN_PROMPTS.map((prompt, index) => ({
  prompt,
  label: DESIGN_LABELS[index],
  Icon: [PenLine, LayoutTemplate, ListChecks, Palette][index],
}));

function greeting(now = new Date()) {
  const hour = now.getHours();
  if (hour < 5) return 'Working late';
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

function Prompt({
  suggestion: { label, prompt, Icon },
  disabled,
  onChoose,
}: {
  suggestion: Suggestion;
  disabled: boolean;
  onChoose: (prompt: string) => void;
}) {
  return (
    <button
      type="button"
      className="chat-empty-prompt"
      disabled={disabled}
      aria-label={label}
      onClick={() => onChoose(prompt)}
    >
      <Icon className="chat-empty-prompt-icon" aria-hidden />
      <span className="chat-empty-prompt-label" aria-hidden>
        {label}
      </span>
      <span className="chat-empty-prompt-text" aria-hidden>
        {prompt}
      </span>
    </button>
  );
}

/**
 * A new chat: a greeting, four prompt suggestions (two more on request) and
 * the most recent threads. A prompt fills the composer to edit and send
 * (U17); recent threads open. A chat working on a design suggests work on
 * the design instead.
 */
export default function ChatEmpty({
  conversationId,
  disabled,
  onChoose,
  recent,
  onOpen,
  design = false,
}: {
  conversationId: string | null;
  disabled: boolean;
  onChoose: (prompt: string) => void;
  recent: ConversationView[];
  onOpen: (id: string) => void;
  /** The chat has a design in Working on. */
  design?: boolean;
}) {
  const [more, setMore] = useState(false);
  if (!conversationId)
    return (
      <div className="chat-empty">
        <h2 className="chat-empty-title">A place for your ideas</h2>
        <p className="chat-empty-lede">
          <span className="desktop-empty-hint">
            Chat, reason, browse, use tools, and work with your local knowledge,
            workflows, and designs. Settings can be finished anytime.
          </span>
          <span className="mobile-empty-hint">
            Start with a message. Add a code folder or design anytime.
          </span>
        </p>
      </div>
    );
  // Never-used chats stay out, as in the sidebar and on Home.
  const threads = recent
    .filter(
      (item) => item.id !== conversationId && item.title && !neverUsed(item),
    )
    .slice(0, 3);
  const suggestions = design ? DESIGN : GENERAL;
  return (
    <div className="chat-empty">
      <p className="chat-empty-eyebrow">{greeting()}</p>
      <h2 className="chat-empty-title">What would you like to work on?</h2>
      <div
        className="chat-empty-prompts"
        role="group"
        aria-label="Example prompts"
      >
        {suggestions.slice(0, more ? undefined : 4).map((suggestion) => (
          <Prompt
            key={suggestion.label}
            suggestion={suggestion}
            disabled={disabled}
            onChoose={onChoose}
          />
        ))}
      </div>
      {!more && suggestions.length > 4 && (
        <button
          type="button"
          className="chat-empty-more"
          onClick={() => setMore(true)}
        >
          More ideas
        </button>
      )}
      {!!threads.length && (
        <nav className="chat-empty-recent" aria-label="Recent conversations">
          <h3>Recent</h3>
          <ul>
            {threads.map((item) => (
              <li key={item.id}>
                <a
                  href={`/app-v2/conversations/${encodeURIComponent(item.id)}`}
                  onClick={(event) => {
                    if (
                      event.button !== 0 ||
                      event.metaKey ||
                      event.ctrlKey ||
                      event.shiftKey
                    )
                      return;
                    event.preventDefault();
                    onOpen(item.id);
                  }}
                >
                  <MessageSquare aria-hidden />
                  <span className="chat-empty-recent-title">{item.title}</span>
                  {item.updated_at && (
                    <time dateTime={item.updated_at}>
                      {relativeTime(item.updated_at)}
                    </time>
                  )}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      )}
    </div>
  );
}
