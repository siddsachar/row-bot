import { expect, it } from 'vitest';
import { validateWire } from '../../../contracts/client-platform/v1/typescript/client';

it('validates every animation-map value instead of accepting arbitrary nested objects', () => {
  const pack = {
    id: 'glyph',
    name: 'Glyph',
    revision: 'saved',
    runtime: 'generated_still',
    available: true,
    generated: false,
    assets: [],
    animation_map: { idle: 'idle' },
  };
  expect(validateWire('BuddyPack', pack)).toEqual(pack);
  expect(() =>
    validateWire('BuddyPack', {
      ...pack,
      animation_map: { idle: { url: 'untrusted' } },
    }),
  ).toThrow();
  expect(() =>
    validateWire('BuddyPack', { ...pack, animation_map: { idle: 3 } }),
  ).toThrow();
});
