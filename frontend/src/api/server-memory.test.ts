import { expect, it } from 'vitest';
import { sizeWords } from './server-memory';

const GIB = 2 ** 30;

it.each([
  // Railway's 8 GB, and its trial's 1 GB as the kernel keeps it (in whole pages).
  [8_000_000_000, '8 GB'],
  [999_997_440, '1 GB'],
  [1_000_000_000, '1 GB'],
  // Docker's --memory 4g and 2g.
  [4 * GIB, '4 GB'],
  [2 * GIB, '2 GB'],
  // Anything else: decimal GB to one place.
  [2_500_000_000, '2.5 GB'],
  [1.5 * GIB, '1.6 GB'],
  [GIB / 4, '0.3 GB'],
])('a limit of %d bytes is %s', (bytes, words) => {
  expect(sizeWords(bytes)).toBe(words);
});

it.each([
  // In use, to one place, in the limit's own unit.
  [1_100_000_000, 8_000_000_000, '1.1 GB'],
  [2_000_000_000, 8_000_000_000, '2.0 GB'],
  [1.4 * GIB, 4 * GIB, '1.4 GB'],
  [1.4 * GIB, 2_500_000_000, '1.5 GB'],
  [10_000_000, 8_000_000_000, '0.1 GB'],
])('%d bytes in use of %d is %s', (bytes, limit, words) => {
  expect(sizeWords(bytes, limit)).toBe(words);
});
