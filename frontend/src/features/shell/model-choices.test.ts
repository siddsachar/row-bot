import { expect, it } from 'vitest';
import { favouritesFirst } from './model-choices';

it('lists favourites first in their order, then the rest, then hidden models (F10)', () => {
  const models = [
    { id: 'a', hidden: true },
    { id: 'b' },
    { id: 'c', favourite_rank: 2 },
    { id: 'd' },
    { id: 'e', favourite_rank: 1 },
    { id: 'f', hidden: true },
  ];
  expect(favouritesFirst(models).map((model) => model.id)).toEqual([
    'e',
    'c',
    'b',
    'd',
    'a',
    'f',
  ]);
  // Nothing chosen: the list is unchanged.
  const plain: { id: string; hidden?: boolean }[] = [{ id: 'x' }, { id: 'y' }];
  expect(favouritesFirst(plain)).toEqual(plain);
});
