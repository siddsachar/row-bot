import { expect, it } from 'vitest';
import { canAutoOpenDesign } from './design-auto-open';

it('opens only the visible idle conversation', () => {
  const composer = document.createElement('textarea');
  const panel = document.createElement('div');
  panel.className = 'panel-content';
  const panelButton = document.createElement('button');
  panel.append(panelButton);
  expect(
    canAutoOpenDesign('a', '/conversations/a', 'visible', composer, ''),
  ).toBe(false);
  expect(
    canAutoOpenDesign('a', '/conversations/a', 'visible', document.body, ''),
  ).toBe(true);
  expect(
    canAutoOpenDesign(
      'a',
      '/conversations/a',
      'visible',
      composer,
      'next idea',
    ),
  ).toBe(false);
  expect(canAutoOpenDesign('a', '/conversations/b', 'visible', null, '')).toBe(
    false,
  );
  expect(canAutoOpenDesign('a', '/conversations/a', 'hidden', null, '')).toBe(
    false,
  );
  expect(
    canAutoOpenDesign('a', '/conversations/a', 'visible', panelButton, ''),
  ).toBe(false);
});
