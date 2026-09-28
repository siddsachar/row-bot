import { useState } from 'react';
import {
  BookOpen,
  Brain,
  CalendarDays,
  FileText,
  Globe,
  MessageSquare,
  Palette,
  Zap,
  type LucideIcon,
} from 'lucide-react';
import type { ConversationView } from '../../api/types';
import { relativeTime } from '../../ui/format';
import { EXAMPLE_LABELS, EXAMPLE_PROMPTS } from './welcome-prompts';

const ICONS: LucideIcon[] = [
  FileText,
  Zap,
  Palette,
  Brain,
  Globe,
  CalendarDays,
];

function greeting(now = new Date()) {
  const hour = now.getHours();
  if (hour < 5) return 'Working late';
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

function Prompt({
  index,
  disabled,
  onChoose,
}: {
  index: number;
  disabled: boolean;
  onChoose: (prompt: string) => void;
}) {
  const Icon = ICONS[index] ?? BookOpen;
  return (
    <button
      type="button"
      className="chat-empty-prompt"
      disabled={disabled}
      aria-label={EXAMPLE_LABELS[index]}
      onClick={() => onChoose(EXAMPLE_PROMPTS[index])}
    >
      <Icon className="chat-empty-prompt-icon" aria-hidden />
      <span className="chat-empty-prompt-label" aria-hidden>
        {EXAMPLE_LABELS[index]}
      </span>
      <span className="chat-empty-prompt-text" aria-hidden>
        {EXAMPLE_PROMPTS[index]}
      </span>
    </button>
  );
}

/**
 * A new chat: a greeting, four prompt suggestions (two more on request) and
 * the most recent threads. A prompt fills the composer to edit and send
 * (U17); recent threads open.
 */
export default function ChatEmpty({
  conversationId,
  disabled,
  onChoose,
  recent,
  onOpen,
}: {
  conversationId: string | null;
  disabled: boolean;
  onChoose: (prompt: string) => void;
  recent: ConversationView[];
  onOpen: (id: string) => void;
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
  const threads = recent
    .filter((item) => item.id !== conversationId && item.title)
    .slice(0, 3);
  return (
    <div className="chat-empty">
      <p className="chat-empty-eyebrow">{greeting()}</p>
      <h2 className="chat-empty-title">What would you like to work on?</h2>
      <div
        className="chat-empty-prompts"
        role="group"
        aria-label="Example prompts"
      >
        {[0, 1, 2, 3].map((index) => (
          <Prompt
            key={index}
            index={index}
            disabled={disabled}
            onChoose={onChoose}
          />
        ))}
        {more &&
          [4, 5].map((index) => (
            <Prompt
              key={index}
              index={index}
              disabled={disabled}
              onChoose={onChoose}
            />
          ))}
      </div>
      {!more && EXAMPLE_PROMPTS.length > 4 && (
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
