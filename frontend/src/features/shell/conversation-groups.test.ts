import { describe, expect, it } from 'vitest';
import type { ConversationView } from '../../api/types';
import {
  conversationKinds,
  groupConversations,
  matchesType,
  recencyGroup,
  withRetainedRow,
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

  it('matches a unified thread under every type it contains', () => {
    const binding = (kind: 'artifact' | 'workspace') => ({
      binding_id: `binding-${kind}`,
      kind,
      resource_id: `resource-${kind}`,
      role: 'primary' as const,
      revision: '1',
    });
    const both = {
      category: 'designer' as const,
      resource_bindings: [binding('artifact'), binding('workspace')],
    };
    expect(conversationKinds(both)).toEqual(['designer', 'code']);
    expect(matchesType(both, 'designer')).toBe(true);
    expect(matchesType(both, 'code')).toBe(true);
    expect(matchesType(both, 'chat')).toBe(false);
    // A chat that later gained a code folder is Code, not a plain chat.
    const grown = {
      category: 'chat' as const,
      resource_bindings: [binding('workspace')],
    };
    expect(matchesType(grown, 'code')).toBe(true);
    expect(matchesType(grown, 'chat')).toBe(false);
    expect(conversationKinds({ category: 'workflow' })).toEqual(['workflow']);
  });

  it('filters by the server category, treating a missing one as a chat', () => {
    expect(matchesType({ category: 'designer' }, 'designer')).toBe(true);
    expect(matchesType({ category: 'designer' }, 'code')).toBe(false);
    expect(matchesType({}, 'chat')).toBe(true);
    expect(matchesType({ category: 'workflow' }, 'all')).toBe(true);
  });

  it('keeps a retained open conversation in the list order', () => {
    const list = [
      row('pinned', at(9), { pinned: true }),
      row('today', at(0, 9)),
      row('week', at(3)),
    ];
    const ids = (rows: ConversationView[]) => rows.map(({ id }) => id);
    // Newer than the listed rows: first after the pinned block.
    expect(ids(withRetainedRow(list, row('newest', at(0, 12))))).toEqual([
      'pinned',
      'newest',
      'today',
      'week',
    ]);
    // Between two runs: the date labels stay in one run each.
    expect(ids(withRetainedRow(list, row('yesterday', at(1))))).toEqual([
      'pinned',
      'today',
      'yesterday',
      'week',
    ]);
    // Older than everything: last, as before.
    expect(ids(withRetainedRow(list, row('old', at(30))))).toEqual([
      'pinned',
      'today',
      'week',
      'old',
    ]);
    expect(
      ids(withRetainedRow(list, row('pin2', at(0), { pinned: true }))),
    ).toEqual(['pinned', 'pin2', 'today', 'week']);
  });
});
