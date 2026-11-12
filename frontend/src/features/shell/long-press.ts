import { useEffect, useRef, type RefObject } from 'react';

/** How long a finger rests before it counts as a long-press, in ms. */
export const LONG_PRESS_MS = 500;
/** A finger that moves this far is scrolling, not pressing. */
const SLOP = 10;

/**
 * Call `onPress` when a finger rests on `target` (D10). Touch only: a mouse
 * keeps its right-click and the visible buttons. Moving, lifting early or a
 * second finger cancels it, so scrolling never opens anything.
 */
export function useLongPress(
  target: RefObject<HTMLElement | null>,
  onPress: () => void,
  enabled = true,
) {
  const latest = useRef(onPress);
  latest.current = onPress;
  useEffect(() => {
    const element = target.current;
    if (!enabled || !element) return;
    let timer = 0;
    let start: { x: number; y: number } | null = null;
    const cancel = () => {
      window.clearTimeout(timer);
      start = null;
    };
    const begin = (event: TouchEvent) => {
      cancel();
      if (event.touches.length !== 1) return;
      const touch = event.touches[0];
      start = { x: touch.clientX, y: touch.clientY };
      timer = window.setTimeout(() => {
        start = null;
        latest.current();
      }, LONG_PRESS_MS);
    };
    const move = (event: TouchEvent) => {
      if (!start) return;
      const touch = event.touches[0];
      if (
        Math.abs(touch.clientX - start.x) > SLOP ||
        Math.abs(touch.clientY - start.y) > SLOP
      )
        cancel();
    };
    element.addEventListener('touchstart', begin, { passive: true });
    element.addEventListener('touchmove', move, { passive: true });
    element.addEventListener('touchend', cancel);
    element.addEventListener('touchcancel', cancel);
    return () => {
      cancel();
      element.removeEventListener('touchstart', begin);
      element.removeEventListener('touchmove', move);
      element.removeEventListener('touchend', cancel);
      element.removeEventListener('touchcancel', cancel);
    };
  }, [target, enabled]);
}
