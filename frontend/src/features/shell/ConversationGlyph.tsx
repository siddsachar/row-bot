import { Code2, MessageSquare, Palette, Workflow } from 'lucide-react';
import type { ConversationView } from '../../api/types';

/** A monochrome glyph for the conversation's type; the title keeps any emoji. */
export function ConversationGlyph({
  category,
  size = 16,
}: {
  category: ConversationView['category'];
  size?: number;
}) {
  const Icon =
    category === 'designer'
      ? Palette
      : category === 'code'
        ? Code2
        : category === 'workflow'
          ? Workflow
          : MessageSquare;
  return <Icon size={size} aria-hidden />;
}
