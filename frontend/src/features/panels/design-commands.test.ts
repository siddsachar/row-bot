import { expect, it, vi } from 'vitest';
import { designCommandSets, registerDesignCommands } from './design-commands';

it('lists the commands of shown designs until they are hidden', () => {
  const present = vi.fn();
  const stop = registerDesignCommands({
    resourceId: 'deck-a',
    title: 'Tides',
    commands: [{ id: 'present', label: 'Present Tides', run: present }],
  });
  const later = registerDesignCommands({
    resourceId: 'deck-a',
    title: 'Tides deck',
    commands: [],
  });
  expect(designCommandSets()).toEqual([
    { resourceId: 'deck-a', title: 'Tides deck', commands: [] },
  ]);
  later();
  designCommandSets()[0].commands[0].run();
  expect(present).toHaveBeenCalledOnce();
  stop();
  expect(designCommandSets()).toEqual([]);
});
