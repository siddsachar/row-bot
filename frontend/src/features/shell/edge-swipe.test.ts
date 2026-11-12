import { afterEach, expect, it, vi } from 'vitest';
import { installEdgeSwipe } from './edge-swipe';

let uninstall = () => {};
afterEach(() => {
  uninstall();
  document.body.innerHTML = '';
});

function touch(type: string, x: number, y: number) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'touches', {
    value: type === 'touchend' ? [] : [{ clientX: x, clientY: y }],
  });
  document.body.dispatchEvent(event);
}

function swipe(fromX: number, toX: number, dy = 0) {
  touch('touchstart', fromX, 300);
  touch('touchmove', (fromX + toX) / 2, 300 + dy / 2);
  touch('touchmove', toX, 300 + dy);
  touch('touchend', toX, 300 + dy);
}

it('opens the conversation list once on a swipe in from the left edge', () => {
  const open = vi.fn();
  uninstall = installEdgeSwipe(open);
  swipe(6, 140);
  expect(open).toHaveBeenCalledTimes(1);
});

it('leaves a swipe that starts away from the edge, or is too short, alone', () => {
  const open = vi.fn();
  uninstall = installEdgeSwipe(open);
  swipe(80, 260);
  swipe(6, 40);
  expect(open).not.toHaveBeenCalled();
});

it('leaves a mostly vertical drag alone, so scrolling near the edge still scrolls', () => {
  const open = vi.fn();
  uninstall = installEdgeSwipe(open);
  swipe(6, 70, 200);
  expect(open).not.toHaveBeenCalled();
});

it('never opens over a sheet, dialog or menu already open', () => {
  const open = vi.fn();
  uninstall = installEdgeSwipe(open);
  document.body.innerHTML = '<div role="dialog"></div>';
  swipe(6, 140);
  expect(open).not.toHaveBeenCalled();
});

it('stops listening once uninstalled', () => {
  const open = vi.fn();
  installEdgeSwipe(open)();
  swipe(6, 140);
  expect(open).not.toHaveBeenCalled();
});
