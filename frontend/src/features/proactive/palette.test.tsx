import { expect, it, vi } from 'vitest';
import { DRAFTS_PATH, proactivePaletteCommands } from './palette';
import { FOR_YOU_PATH } from './moment-actions';
import { proactiveStore } from './proactive-state';
import { fakeIO } from './test-data';

it('offers For you, Drafts and Show what Buddy has, with no shortcut of their own', () => {
  const go = vi.fn();
  const close = vi.fn();
  const commands = proactivePaletteCommands(
    proactiveStore(fakeIO()),
    go,
    close,
  );
  expect(commands.map((command) => command.label)).toEqual([
    'For you',
    'Drafts',
    'Show what Buddy has for you',
  ]);
  expect(commands.every((command) => !command.shortcut)).toBe(true);
  commands[0].run();
  commands[1].run();
  // Nothing Buddy brought up is open: For you instead.
  commands[2].run();
  expect(go.mock.calls).toEqual([
    [FOR_YOU_PATH],
    [DRAFTS_PATH],
    [FOR_YOU_PATH],
  ]);
  expect(close).toHaveBeenCalled();
  expect(proactivePaletteCommands(null, go, close)).toEqual([]);
});
