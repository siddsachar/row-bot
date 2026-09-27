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

export type ConversationKind = 'designer' | 'code' | 'workflow';
type KindSource = Pick<ConversationView, 'category' | 'resource_bindings'>;

/**
 * What a thread contains. One unified chat can hold a design and a code
 * folder at once, so kinds come from its bindings as well as the server's
 * single category; the order is the display precedence.
 */
export function conversationKinds(row: KindSource): ConversationKind[] {
  const bindings = row.resource_bindings ?? [];
  const kinds: ConversationKind[] = [];
  if (
    row.category === 'designer' ||
    bindings.some((binding) => binding.kind === 'artifact')
  )
    kinds.push('designer');
  if (
    row.category === 'code' ||
    bindings.some((binding) => binding.kind === 'workspace')
  )
    kinds.push('code');
  if (row.category === 'workflow') kinds.push('workflow');
  return kinds;
}

/** A thread matches every type it contains; Chats are threads with none. */
export function matchesType(row: KindSource, type: ConversationType): boolean {
  if (type === 'all') return true;
  const kinds = conversationKinds(row);
  return type === 'chat' ? kinds.length === 0 : kinds.includes(type);
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
 * Adds the open conversation to a list that does not show it, where the
 * list's own order would put it (pinned first, then newest first), so the
 * sidebar's date runs stay whole.
 */
export function withRetainedRow(
  rows: readonly ConversationView[],
  row: ConversationView,
): ConversationView[] {
  const time = (value: ConversationView) =>
    parseTimestamp(value.updated_at)?.getTime() ?? 0;
  const index = rows.findIndex((other) =>
    row.pinned ? !other.pinned : !other.pinned && time(other) < time(row),
  );
  return index < 0
    ? [...rows, row]
    : [...rows.slice(0, index), row, ...rows.slice(index)];
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
