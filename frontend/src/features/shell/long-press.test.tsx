import { act, render } from '@testing-library/react';
import { useRef } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { LONG_PRESS_MS, useLongPress } from './long-press';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

function Pressable({ onPress }: { onPress: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useLongPress(ref, onPress);
  return <div ref={ref} data-testid="target" />;
}

function touch(target: Element, type: string, x = 50, y = 50, fingers = 1) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'touches', {
    value: Array.from({ length: type === 'touchend' ? 0 : fingers }, () => ({
      clientX: x,
      clientY: y,
    })),
  });
  target.dispatchEvent(event);
}

it('calls back once a finger has rested for the long-press time', () => {
  const onPress = vi.fn();
  const { getByTestId } = render(<Pressable onPress={onPress} />);
  touch(getByTestId('target'), 'touchstart');
  act(() => vi.advanceTimersByTime(LONG_PRESS_MS - 1));
  expect(onPress).not.toHaveBeenCalled();
  act(() => vi.advanceTimersByTime(1));
  expect(onPress).toHaveBeenCalledTimes(1);
});

it('never fires for a tap, a scroll or a two-finger touch', () => {
  const onPress = vi.fn();
  const { getByTestId } = render(<Pressable onPress={onPress} />);
  const target = getByTestId('target');
  touch(target, 'touchstart');
  touch(target, 'touchend');
  touch(target, 'touchstart');
  touch(target, 'touchmove', 50, 80);
  touch(target, 'touchstart', 50, 50, 2);
  act(() => vi.advanceTimersByTime(LONG_PRESS_MS * 2));
  expect(onPress).not.toHaveBeenCalled();
});
