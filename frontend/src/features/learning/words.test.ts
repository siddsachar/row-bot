import { expect, it } from 'vitest';
import { nextLookWords } from './words';

it('says tonight only for a look at night, and today or tomorrow in the day (FL10)', () => {
  const morning = new Date(2026, 10, 27, 9, 0);
  expect(
    nextLookWords(new Date(2026, 10, 28, 1, 0).toISOString(), morning),
  ).toMatch(/^Next look tonight at /);
  expect(
    nextLookWords(new Date(2026, 10, 27, 14, 0).toISOString(), morning),
  ).toMatch(/^Next look today at /);
  expect(
    nextLookWords(new Date(2026, 10, 28, 8, 0).toISOString(), morning),
  ).toMatch(/^Next look tomorrow at /);
  expect(
    nextLookWords(new Date(2026, 10, 29, 14, 0).toISOString(), morning),
  ).not.toMatch(/tonight|today|tomorrow/);
});
