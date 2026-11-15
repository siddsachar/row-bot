/**
 * Swipe down to close a phone sheet: a menu, popover or sheet dialog. Every
 * sheet keeps its own visible way out (the scrim, Close, Escape); the swipe is
 * a shortcut for the thumb. It starts on a downward pull from the handle
 * strip, or from anywhere while nothing under the finger is scrolled down, so
 * scrolling a long list back up never closes it. It closes the sheet the way
 * Escape would.
 */

/** Where menus and popovers are drawn as bottom sheets (responsive.css). */
export const PHONE_SHEETS = '(max-width: 767px) and (pointer: coarse)';

const SHEET =
  '.dialog.sheet, [data-radix-popper-content-wrapper] > :is(.menu, .popover, .model-picker, .combobox-popover)';
/** A pull this far, or a quick flick, closes the sheet. */
const CLOSE_DISTANCE = 120;
const FLICK_DISTANCE = 32;
const FLICK_SPEED = 0.3; // px per ms, averaged over the pull
const SLOP = 8;
/** The strip with the grab handle drags the sheet even when its list is scrolled. */
const HANDLE_STRIP = 28;

type Drag = {
  sheet: HTMLElement;
  target: Element;
  x: number;
  y: number;
  at: number;
  dy: number;
  onHandle: boolean;
  pulling: boolean;
};

function scrolledDown(from: Element, sheet: HTMLElement): boolean {
  for (let node: Element | null = from; node; node = node.parentElement) {
    if (node instanceof HTMLElement && node.scrollTop > 0) return true;
    if (node === sheet) return false;
  }
  return false;
}

export function installSheetSwipe(doc: Document = document): () => void {
  const view = doc.defaultView;
  const phone = () => view?.matchMedia?.(PHONE_SHEETS).matches === true;
  let drag: Drag | null = null;

  const release = (sheet: HTMLElement) => {
    sheet.style.removeProperty('transition');
    sheet.style.removeProperty('transform');
  };

  const start = (event: TouchEvent) => {
    drag = null;
    if (event.touches.length !== 1 || !phone()) return;
    const target = event.target instanceof Element ? event.target : null;
    const sheet = target?.closest<HTMLElement>(SHEET);
    if (!target || !sheet || target.closest('input[type="range"], textarea'))
      return;
    const touch = event.touches[0];
    drag = {
      sheet,
      target,
      x: touch.clientX,
      y: touch.clientY,
      at: event.timeStamp,
      dy: 0,
      onHandle:
        touch.clientY - sheet.getBoundingClientRect().top < HANDLE_STRIP,
      pulling: false,
    };
  };

  const move = (event: TouchEvent) => {
    if (!drag) return;
    const touch = event.touches[0];
    const dx = touch.clientX - drag.x;
    const dy = touch.clientY - drag.y;
    if (!drag.pulling) {
      if (Math.abs(dx) < SLOP && Math.abs(dy) < SLOP) return;
      if (
        dy <= Math.abs(dx) ||
        (!drag.onHandle && scrolledDown(drag.target, drag.sheet))
      ) {
        drag = null;
        return;
      }
      drag.pulling = true;
      drag.sheet.style.transition = 'none';
    }
    if (event.cancelable) event.preventDefault();
    drag.dy = Math.max(0, dy);
    drag.sheet.style.transform = `translateY(${drag.dy}px)`;
  };

  const end = (event: TouchEvent) => {
    const finished = drag;
    drag = null;
    if (!finished?.pulling) return;
    const { sheet, dy } = finished;
    const speed = dy / Math.max(1, event.timeStamp - finished.at);
    const closes =
      dy >=
        Math.min(CLOSE_DISTANCE, sheet.offsetHeight / 3 || CLOSE_DISTANCE) ||
      (dy >= FLICK_DISTANCE && speed >= FLICK_SPEED);
    if (closes) {
      sheet.dispatchEvent(
        new KeyboardEvent('keydown', {
          key: 'Escape',
          code: 'Escape',
          bubbles: true,
          cancelable: true,
        }),
      );
      // A sheet that stays open (a form that refuses to close) settles back.
      if (sheet.isConnected) release(sheet);
      return;
    }
    sheet.style.transition = 'transform var(--motion-panel) var(--ease-out)';
    sheet.style.transform = '';
    sheet.addEventListener('transitionend', () => release(sheet), {
      once: true,
    });
  };

  const cancel = () => {
    if (drag?.pulling) release(drag.sheet);
    drag = null;
  };

  doc.addEventListener('touchstart', start, { passive: true });
  doc.addEventListener('touchmove', move, { passive: false });
  doc.addEventListener('touchend', end);
  doc.addEventListener('touchcancel', cancel);
  return () => {
    doc.removeEventListener('touchstart', start);
    doc.removeEventListener('touchmove', move);
    doc.removeEventListener('touchend', end);
    doc.removeEventListener('touchcancel', cancel);
  };
}
