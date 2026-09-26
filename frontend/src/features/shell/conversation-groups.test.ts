import { describe, expect, it } from 'vitest';
import type { ConversationView } from '../../api/types';
import {
  groupConversations,
  matchesType,
  recencyGroup,
} from './conversation-groups';

const now = new Date(2026, 8, 26, 15, 30);
const at = (days: number, hour = 10) =>
  new Date(2026, 8, 26 - days, hour, 0).toISOString();
const row = (
  id: string,
  updated_at: string | undefined,
  extra: Partial<ConversationView> = {},
): ConversationView => ({
  id,
  revision: '1',
  title: id,
  pinned: false,
  updated_at,
  ...extra,
});

describe('conversation groups', () => {
  it('buckets by local day: today, yesterday, this week, older', () => {
    expect(recencyGroup(at(0, 0), now)).toBe('today');
    expect(recencyGroup(at(1, 23), now)).toBe('yesterday');
    expect(recencyGroup(at(2), now)).toBe('week');
    expect(recencyGroup(at(6), now)).toBe('week');
    expect(recencyGroup(at(7), now)).toBe('older');
    expect(recencyGroup(undefined, now)).toBe('older');
    expect(recencyGroup('not a date', now)).toBe('older');
  });

  it('puts pinned rows first and omits empty groups, keeping server order', () => {
    const groups = groupConversations(
      [
        row('a', at(0)),
        row('b', at(9), { pinned: true }),
        row('c', at(3)),
        row('d', at(0, 8)),
        row('e', at(20)),
      ],
      now,
    );
    expect(groups.map((group) => group.label)).toEqual([
      'Pinned',
      'Today',
      'This week',
      'Older',
    ]);
    expect(groups[1].rows.map((item) => item.id)).toEqual(['a', 'd']);
    expect(groups[0].rows.map((item) => item.id)).toEqual(['b']);
  });

  it('filters by the server category, treating a missing one as a chat', () => {
    expect(matchesType({ category: 'designer' }, 'designer')).toBe(true);
    expect(matchesType({ category: 'designer' }, 'code')).toBe(false);
    expect(matchesType({}, 'chat')).toBe(true);
    expect(matchesType({ category: 'workflow' }, 'all')).toBe(true);
  });
});
