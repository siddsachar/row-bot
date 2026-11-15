import { expect, it } from 'vitest';
import {
  absoluteTime,
  apiKeyLabel,
  ariaKeyShortcut,
  credentialSourceLabel,
  humanizeToken,
  maskedTail,
  parseTimestamp,
  relativeTime,
  setServerUtcOffset,
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

it('reads offset-less times as the server clock on a device in another timezone (B341)', () => {
  // The server is an hour ahead of UTC; this device's own timezone doesn't matter.
  setServerUtcOffset(60);
  try {
    expect(parseTimestamp('2026-10-14T14:00:00.123456')?.toISOString()).toBe(
      '2026-10-14T13:00:00.123Z',
    );
    expect(parseTimestamp('2026-10-14T14:00')?.toISOString()).toBe(
      '2026-10-14T13:00:00.000Z',
    );
    // A time that says its offset is read as written.
    expect(parseTimestamp('2026-10-14T14:00:00Z')?.toISOString()).toBe(
      '2026-10-14T14:00:00.000Z',
    );
    expect(parseTimestamp('2026-10-14T14:00:00+05:30')?.toISOString()).toBe(
      '2026-10-14T08:30:00.000Z',
    );
    // A chat the server saved a moment ago is "just now", never "in 60 minutes".
    expect(
      relativeTime('2026-10-14T14:00:00', new Date('2026-10-14T13:00:20Z')),
    ).toBe('just now');
  } finally {
    setServerUtcOffset(null);
  }
  // Without the server's offset, an offset-less time is this device's local time.
  expect(parseTimestamp('2026-10-14T14:00:00')?.getTime()).toBe(
    new Date(2026, 9, 14, 14, 0, 0).getTime(),
  );
});

it.each([
  [new Date(2026, 8, 25, 11, 59, 40), 'just now'],
  [new Date(2026, 8, 25, 11, 55, 0), '5 minutes ago'],
  [new Date(2026, 8, 25, 9, 0, 0), '3 hours ago'],
  [new Date(2026, 8, 24, 12, 0, 0), 'yesterday'],
  [new Date(2026, 8, 4, 12, 0, 0), '3 weeks ago'],
  [new Date(2026, 7, 3, 19, 12, 17), '2 months ago'],
  [new Date(2026, 8, 25, 14, 0, 0), 'in 2 hours'],
  [new Date(2026, 8, 25, 12, 0, 30), 'in under a minute'],
  [new Date(2026, 8, 25, 12, 0, 3), 'just now'],
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

it('shows only the masked tail of a saved secret and where it lives', () => {
  expect(maskedTail('****4c99')).toBe('····4c99');
  expect(maskedTail('****')).toBe('····');
  expect(maskedTail('')).toBe('');
  expect(credentialSourceLabel('keyring')).toBe('in keychain');
  expect(credentialSourceLabel('environment')).toBe('from environment');
  expect(credentialSourceLabel('')).toBe('');
  expect(humanizeToken('cloud_provider')).toBe('Cloud');
  expect(humanizeToken('third_party_router')).toBe('Router');
});

it('names a provider key without doubling "API" (B116)', () => {
  expect(apiKeyLabel('OpenAI API')).toBe('OpenAI API key');
  expect(apiKeyLabel('Anthropic')).toBe('Anthropic API key');
  expect(apiKeyLabel('')).toBe('Provider API key');
  expect(apiKeyLabel(undefined)).toBe('Provider API key');
});
