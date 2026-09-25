import { expect, it } from 'vitest';
import {
  absoluteTime,
  ariaKeyShortcut,
  humanizeToken,
  parseTimestamp,
  relativeTime,
  shortcutKeys,
} from './format';

const now = new Date(2026, 8, 25, 12, 0, 0);

it('parses local backend timestamps with microseconds and epoch seconds', () => {
  expect(parseTimestamp('2026-08-03T19:12:17.428064')?.getTime()).toBe(
    new Date(2026, 7, 3, 19, 12, 17, 428).getTime(),
  );
  expect(parseTimestamp('2026-08-03T19:12:17Z')?.toISOString()).toBe(
    '2026-08-03T19:12:17.000Z',
  );
  expect(parseTimestamp(0)?.toISOString()).toBe('1970-01-01T00:00:00.000Z');
  expect(parseTimestamp('not a date')).toBeNull();
  expect(parseTimestamp(undefined)).toBeNull();
});

it.each([
  [new Date(2026, 8, 25, 11, 59, 40), 'just now'],
  [new Date(2026, 8, 25, 11, 55, 0), '5 minutes ago'],
  [new Date(2026, 8, 25, 9, 0, 0), '3 hours ago'],
  [new Date(2026, 8, 24, 12, 0, 0), 'yesterday'],
  [new Date(2026, 8, 4, 12, 0, 0), '3 weeks ago'],
  [new Date(2026, 7, 3, 19, 12, 17), '2 months ago'],
  [new Date(2026, 8, 25, 14, 0, 0), 'in 2 hours'],
])('describes %s relative to a fixed clock as %s', (value, expected) => {
  expect(relativeTime(value, now, 'en-GB')).toBe(expected);
});

it('never shows a raw timestamp when a value is missing or invalid', () => {
  expect(relativeTime('garbage', now, 'en-GB')).toBe('Unknown');
  expect(relativeTime(null, now, 'en-GB')).toBe('Unknown');
  expect(absoluteTime('garbage')).toBe('');
  expect(absoluteTime(new Date(2026, 7, 3, 19, 12), 'en-GB')).toMatch(
    /3 Aug 2026/,
  );
});

it.each([
  ['third_party_router', 'Router'],
  ['streamable_http', 'HTTP'],
  ['local_private', 'Private · on device'],
  ['api_key', 'API key'],
  ['consolidate_skills', 'Consolidate skills'],
  ['skill_insight', 'Skill insight'],
  ['usageSummary', 'Usage summary'],
  ['', ''],
])('humanizes %s as %s', (value, expected) => {
  expect(humanizeToken(value)).toBe(expected);
});

it('formats shortcuts per platform for display and aria-keyshortcuts', () => {
  expect(shortcutKeys('Mod+K', 'mac')).toEqual(['⌘', 'K']);
  expect(shortcutKeys('Mod+K', 'other')).toEqual(['Ctrl', 'K']);
  expect(shortcutKeys('Mod+Shift+Enter', 'mac')).toEqual(['⌘', '⇧', '↵']);
  expect(shortcutKeys('Escape', 'other')).toEqual(['Esc']);
  expect(ariaKeyShortcut('Mod+K', 'mac')).toBe('Meta+K');
  expect(ariaKeyShortcut('Mod+Period', 'other')).toBe('Control+Period');
  expect(ariaKeyShortcut('Esc', 'other')).toBe('Escape');
});
