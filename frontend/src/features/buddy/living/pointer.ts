/** The page's last cursor position, shared by every living Buddy. */
let x = 0;
let y = 0;
let moved = -Infinity;
let listening = false;

function listen() {
  if (listening || typeof window === 'undefined') return;
  listening = true;
  window.addEventListener(
    'pointermove',
    (event) => {
      x = event.clientX;
      y = event.clientY;
      moved = performance.now();
    },
    { passive: true },
  );
}

export function pagePointer(): { x: number; y: number; age: number } | null {
  listen();
  if (moved === -Infinity) return null;
  return { x, y, age: (performance.now() - moved) / 1000 };
}
