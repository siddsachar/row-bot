import { describe, expect, it } from 'vitest';
import { fuzzyMatch, fuzzyScore } from './fuzzy';

describe('fuzzy matching', () => {
  it('matches characters in order and rejects missing ones', () => {
    expect(fuzzyMatch('nwc', 'New chat')?.indices).toEqual([0, 2, 4]);
    expect(fuzzyMatch('xyz', 'New chat')).toBeNull();
    expect(fuzzyMatch('tahc', 'New chat')).toBeNull();
  });

  it('ranks literal prefixes over word starts over scattered letters', () => {
    const prefix = fuzzyMatch('set', 'Settings')!.score;
    const word = fuzzyMatch('set', 'Open settings')!.score;
    const scattered = fuzzyMatch('set', 'Sample text')!.score;
    expect(prefix).toBeGreaterThan(word);
    expect(word).toBeGreaterThan(scattered);
  });

  it('treats an empty query as a neutral match', () => {
    expect(fuzzyMatch('  ', 'Anything')).toEqual({ score: 0, indices: [] });
  });

  it('scores the best field, weighting later fields lower', () => {
    const label = fuzzyScore('prov', 'Providers', 'models keys')!;
    const keyword = fuzzyScore('prov', 'Models', 'providers keys')!;
    expect(label).toBeGreaterThan(keyword);
    expect(fuzzyScore('zzz', 'Models', 'keys')).toBeNull();
  });
});
