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

it('goes back once on a swipe in from the left edge', () => {
  const back = vi.fn();
  uninstall = installEdgeSwipe(back);
  swipe(6, 140);
  expect(back).toHaveBeenCalledTimes(1);
});

it('leaves a swipe that starts away from the edge, or is too short, alone', () => {
  const back = vi.fn();
  uninstall = installEdgeSwipe(back);
  swipe(80, 260);
  swipe(6, 40);
  expect(back).not.toHaveBeenCalled();
});

it('leaves a mostly vertical drag alone, so scrolling near the edge still scrolls', () => {
  const back = vi.fn();
  uninstall = installEdgeSwipe(back);
  swipe(6, 70, 200);
  expect(back).not.toHaveBeenCalled();
});

it('never fires over a sheet, dialog or menu already open', () => {
  const back = vi.fn();
  uninstall = installEdgeSwipe(back);
  document.body.innerHTML = '<div role="dialog"></div>';
  swipe(6, 140);
  expect(back).not.toHaveBeenCalled();
});

it('stops listening once uninstalled', () => {
  const back = vi.fn();
  installEdgeSwipe(back)();
  swipe(6, 140);
  expect(back).not.toHaveBeenCalled();
});
