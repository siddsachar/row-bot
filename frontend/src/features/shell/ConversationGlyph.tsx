import {
  Code2,
  HatGlasses,
  MessageSquare,
  MessageSquareShare,
  Palette,
  Workflow,
  type LucideIcon,
} from 'lucide-react';
import type { ConversationView } from '../../api/types';
import {
  conversationKinds,
  type ConversationKind,
} from './conversation-groups';

const ICONS: Record<ConversationKind, LucideIcon> = {
  designer: Palette,
  code: Code2,
  workflow: Workflow,
};

const NAMES: Record<ConversationKind, string> = {
  designer: 'design',
  code: 'code',
  workflow: 'workflow',
};

/**
 * A monochrome glyph for what the thread contains; the title keeps any
 * emoji. A thread with a design and a code folder shows the first as the
 * glyph and the second as a small badge. An incognito chat (N11) shows the
 * incognito hat and glasses wherever it is listed; a chat another one started
 * (5.3.1 C) shows a shared-message glyph.
 */
export function ConversationGlyph({
  row,
  size = 16,
}: {
  row: Pick<
    ConversationView,
    'category' | 'resource_bindings' | 'incognito' | 'started_from'
  >;
  size?: number;
}) {
  if (row.incognito)
    return (
      <span className="conversation-glyph" title="Incognito" aria-hidden>
        <HatGlasses size={size} />
      </span>
    );
  const kinds = conversationKinds(row);
  if (row.started_from && !kinds.length)
    return (
      <span
        className="conversation-glyph"
        title="Started by another conversation"
        aria-hidden
      >
        <MessageSquareShare size={size} />
      </span>
    );
  const Primary = kinds.length ? ICONS[kinds[0]] : MessageSquare;
  if (kinds.length < 2) return <Primary size={size} aria-hidden />;
  const Secondary = ICONS[kinds[1]];
  return (
    <span
      className="conversation-glyph"
      title={`Contains ${kinds.map((kind) => NAMES[kind]).join(' and ')}`}
      aria-hidden
    >
      <Primary size={size} />
      <span className="conversation-glyph-badge">
        <Secondary size={Math.round(size * 0.62)} strokeWidth={2.5} />
      </span>
    </span>
  );
}
