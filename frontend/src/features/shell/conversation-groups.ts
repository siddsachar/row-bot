import type { ConversationView } from '../../api/types';
import { parseTimestamp } from '../../ui/format';

/** The sidebar type filter; "all" shows every top-level conversation. */
export type ConversationType =
  'all' | 'chat' | 'designer' | 'code' | 'workflow';

export const CONVERSATION_TYPES: readonly {
  value: ConversationType;
  label: string;
}[] = [
  { value: 'all', label: 'All' },
  { value: 'chat', label: 'Chats' },
  { value: 'designer', label: 'Designs' },
  { value: 'code', label: 'Code' },
  { value: 'workflow', label: 'Workflows' },
];

export type ConversationGroupId =
  'pinned' | 'today' | 'yesterday' | 'week' | 'older';

export type ConversationGroup = {
  id: ConversationGroupId;
  label: string;
  rows: ConversationView[];
};

const LABELS: Record<ConversationGroupId, string> = {
  pinned: 'Pinned',
  today: 'Today',
  yesterday: 'Yesterday',
  week: 'This week',
  older: 'Older',
};

export function conversationType(
  row: Pick<ConversationView, 'category'>,
): Exclude<ConversationType, 'all'> {
  return row.category ?? 'chat';
}

export function matchesType(
  row: Pick<ConversationView, 'category'>,
  type: ConversationType,
): boolean {
  return type === 'all' || conversationType(row) === type;
}

function startOfDay(value: Date): number {
  return new Date(
    value.getFullYear(),
    value.getMonth(),
    value.getDate(),
  ).getTime();
}

/** Which recency bucket an unpinned conversation falls in, in local time. */
export function recencyGroup(
  updatedAt: string | undefined,
  now: Date = new Date(),
): Exclude<ConversationGroupId, 'pinned'> {
  const date = parseTimestamp(updatedAt);
  if (!date) return 'older';
  const today = startOfDay(now);
  const day = startOfDay(date);
  if (day >= today) return 'today';
  const days = Math.round((today - day) / 86_400_000);
  if (days === 1) return 'yesterday';
  if (days < 7) return 'week';
  return 'older';
}

/**
 * Pinned first, then Today / Yesterday / This week / Older. Rows keep the
 * server's order inside a group; empty groups are omitted.
 */
export function groupConversations(
  rows: readonly ConversationView[],
  now: Date = new Date(),
): ConversationGroup[] {
  const buckets = new Map<ConversationGroupId, ConversationView[]>();
  for (const row of rows) {
    const id = row.pinned ? 'pinned' : recencyGroup(row.updated_at, now);
    const bucket = buckets.get(id);
    if (bucket) bucket.push(row);
    else buckets.set(id, [row]);
  }
  return (Object.keys(LABELS) as ConversationGroupId[])
    .filter((id) => buckets.has(id))
    .map((id) => ({ id, label: LABELS[id], rows: buckets.get(id)! }));
}
