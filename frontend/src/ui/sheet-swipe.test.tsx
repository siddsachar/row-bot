import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useEffect } from 'react';
import { Menu } from './primitives';
import { installSheetSwipe, PHONE_SHEETS } from './sheet-swipe';

let phone = true;
beforeEach(() => {
  phone = true;
  vi.mocked(window.matchMedia).mockImplementation(
    (query: string) =>
      ({
        matches: phone && query === PHONE_SHEETS,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }) as unknown as MediaQueryList,
  );
});
afterEach(() => vi.mocked(window.matchMedia).mockReset());

function Swipeable() {
  useEffect(() => installSheetSwipe(), []);
  return (
    <Menu
      label="Chat actions"
      actions={[
        { label: 'Rename', onSelect: () => {} },
        { label: 'Pin', onSelect: () => {} },
      ]}
    />
  );
}

function touch(target: Element, type: string, y: number, timeStamp: number) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  const touches = type === 'touchend' ? [] : [{ clientX: 100, clientY: y }];
  Object.defineProperties(event, {
    touches: { value: touches },
    changedTouches: { value: [{ clientX: 100, clientY: y }] },
    timeStamp: { value: timeStamp },
  });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
}

/** A slow pull of `distance` px, so only the distance decides. */
function pull(target: Element, distance: number) {
  touch(target, 'touchstart', 300, 0);
  const moved = touch(target, 'touchmove', 300 + distance, 1000);
  touch(target, 'touchend', 300 + distance, 2000);
  return moved;
}

async function openMenu() {
  await userEvent.click(screen.getByRole('button', { name: 'Chat actions' }));
  return screen.getByRole('menu');
}

it('closes a phone menu sheet when it is pulled down', async () => {
  render(<Swipeable />);
  const menu = await openMenu();

  const moved = pull(screen.getByRole('menuitem', { name: 'Pin' }), 160);

  expect(moved.defaultPrevented).toBe(true);
  expect(screen.queryByRole('menu')).toBeNull();
  expect(menu.isConnected).toBe(false);
});

it('closes on a quick flick even when it is short', async () => {
  render(<Swipeable />);
  const menu = await openMenu();

  touch(menu, 'touchstart', 300, 0);
  touch(menu, 'touchmove', 340, 20);
  touch(menu, 'touchend', 340, 40);

  expect(screen.queryByRole('menu')).toBeNull();
});

it('keeps the sheet open after a short pull and settles it back', async () => {
  render(<Swipeable />);
  const menu = await openMenu();

  pull(menu, 40);

  expect(screen.getByRole('menu')).toBe(menu);
  expect(menu.style.transform).toBe('');
});

it('lets a scrolled list scroll back up instead of closing', async () => {
  render(<Swipeable />);
  const menu = await openMenu();
  menu.scrollTop = 80;

  const moved = pull(menu, 200);

  expect(moved.defaultPrevented).toBe(false);
  expect(screen.getByRole('menu')).toBe(menu);
});

it('drags a scrolled sheet from its handle strip', async () => {
  render(<Swipeable />);
  const menu = await openMenu();
  menu.scrollTop = 80;
  menu.getBoundingClientRect = () => ({ top: 290 }) as DOMRect;

  pull(menu, 200);

  expect(screen.queryByRole('menu')).toBeNull();
});

it('leaves upward and sideways swipes alone', async () => {
  render(<Swipeable />);
  const menu = await openMenu();

  pull(menu, -200);

  expect(screen.getByRole('menu')).toBe(menu);
});

it('does nothing off a touch phone, where menus stay popovers', async () => {
  phone = false;
  render(<Swipeable />);
  const menu = await openMenu();

  const moved = pull(menu, 200);

  expect(moved.defaultPrevented).toBe(false);
  expect(screen.getByRole('menu')).toBe(menu);
});

it('a phone menu opens on the tap, so the press that opened it chooses nothing', async () => {
  const chosen = vi.fn();
  render(
    <Menu
      label="Long sample menu"
      actions={[{ label: 'First option', onSelect: chosen }]}
    />,
  );
  const trigger = screen.getByRole('button', { name: 'Long sample menu' });
  // The press alone no longer opens it (the sheet would rise under the finger)…
  fireEvent.pointerDown(trigger, { button: 0, pointerType: 'touch' });
  expect(screen.queryByRole('menu')).toBeNull();
  // …the tap's click does, and chooses nothing.
  fireEvent.click(trigger);
  expect(await screen.findByRole('menu')).toBeVisible();
  expect(chosen).not.toHaveBeenCalled();
});

it('away from phone sheets a menu still opens on the press', async () => {
  phone = false;
  render(
    <Menu
      label="Long sample menu"
      actions={[{ label: 'First option', onSelect: vi.fn() }]}
    />,
  );
  fireEvent.pointerDown(
    screen.getByRole('button', { name: 'Long sample menu' }),
    { button: 0, pointerType: 'mouse' },
  );
  expect(await screen.findByRole('menu')).toBeVisible();
});
