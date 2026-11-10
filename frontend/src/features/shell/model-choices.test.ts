import { expect, it } from 'vitest';
import { favouritesFirst } from './model-choices';

it('lists your starred models first in their order, then the rest', () => {
  const models = [
    { id: 'a' },
    { id: 'b' },
    { id: 'c', favourite_rank: 2 },
    { id: 'd' },
    { id: 'e', favourite_rank: 1 },
  ];
  expect(favouritesFirst(models).map((model) => model.id)).toEqual([
    'e',
    'c',
    'a',
    'b',
    'd',
  ]);
  // Nothing starred: the list is unchanged.
  const plain: { id: string; favourite_rank?: number }[] = [
    { id: 'x' },
    { id: 'y' },
  ];
  expect(favouritesFirst(plain)).toEqual(plain);
});
