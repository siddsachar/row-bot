/**
 * Swipe in from a phone's left edge to open the conversation list (D10). The
 * header's navigation button opens the same list; the swipe is a shortcut for
 * the thumb. It starts only within the edge strip, never while a sheet or a
 * dialog is open, and opens once a mostly sideways pull passes the distance.
 */

/** The strip along the left edge that starts the swipe, in px. */
const EDGE = 24;
/** A pull this far to the right opens the list. */
const OPEN_DISTANCE = 56;

export function installEdgeSwipe(
  open: () => void,
  doc: Document = document,
): () => void {
  let start: { x: number; y: number } | null = null;

  const begin = (event: TouchEvent) => {
    start = null;
    if (event.touches.length !== 1) return;
    const touch = event.touches[0];
    if (touch.clientX > EDGE) return;
    if (
      doc.querySelector('[role="dialog"], [role="alertdialog"], [role="menu"]')
    )
      return;
    start = { x: touch.clientX, y: touch.clientY };
  };

  const move = (event: TouchEvent) => {
    if (!start) return;
    const touch = event.touches[0];
    const dx = touch.clientX - start.x;
    const dy = Math.abs(touch.clientY - start.y);
    if (dy > Math.max(24, dx)) {
      start = null;
      return;
    }
    if (dx >= OPEN_DISTANCE) {
      start = null;
      open();
    }
  };

  const end = () => {
    start = null;
  };

  doc.addEventListener('touchstart', begin, { passive: true });
  doc.addEventListener('touchmove', move, { passive: true });
  doc.addEventListener('touchend', end);
  doc.addEventListener('touchcancel', end);
  return () => {
    doc.removeEventListener('touchstart', begin);
    doc.removeEventListener('touchmove', move);
    doc.removeEventListener('touchend', end);
    doc.removeEventListener('touchcancel', end);
  };
}
