import { afterEach, expect, it } from 'vitest';
import { installInputModality } from './input-modality';

let dispose = () => {};
afterEach(() => dispose());

it('marks keyboard and pointer interaction so programmatic focus draws a ring only for keyboard users', () => {
  dispose = installInputModality(document);
  const root = document.documentElement;
  expect(root.dataset.inputModality).toBeUndefined();
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Shift' }));
  expect(root.dataset.inputModality).toBeUndefined();
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }));
  expect(root.dataset.inputModality).toBe('keyboard');
  document.dispatchEvent(
    new MouseEvent('pointerdown', { clientX: 20, clientY: 30 }),
  );
  expect(root.dataset.inputModality).toBe('pointer');
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
  expect(root.dataset.inputModality).toBe('keyboard');
  dispose();
  expect(root.dataset.inputModality).toBeUndefined();
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }));
  expect(root.dataset.inputModality).toBeUndefined();
});
