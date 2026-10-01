import { forwardRef, lazy, Suspense, useState } from 'react';
import { Skeleton } from '../../ui/primitives';

// xterm.js and the terminal load when the dock first opens, never at startup.
const NativeTerminal = lazy(() => import('./NativeTerminal'));

const HEIGHT_KEY = 'row-bot:terminal-dock:v1';
export const TERMINAL_MIN_HEIGHT = 120;
const TERMINAL_DEFAULT_HEIGHT = 280;

function savedHeight(): number {
  try {
    const saved = JSON.parse(localStorage.getItem(HEIGHT_KEY) ?? 'null') as {
      height?: unknown;
    } | null;
    if (typeof saved?.height === 'number' && Number.isFinite(saved.height))
      return Math.max(TERMINAL_MIN_HEIGHT, Math.round(saved.height));
  } catch {
    /* A blocked or damaged store starts at the default height. */
  }
  return TERMINAL_DEFAULT_HEIGHT;
}

/**
 * The terminal dock under the conversation (B249). It opens only when the
 * person asks, for this page; its height is remembered on this device.
 */
export function useTerminalDock() {
  const [open, setOpen] = useState(false);
  const [height, setHeight] = useState(savedHeight);
  // Each opening moves the keyboard focus into the terminal.
  const [focusKey, setFocusKey] = useState(0);
  return {
    open,
    height,
    focusKey,
    show() {
      setOpen(true);
      setFocusKey((value) => value + 1);
    },
    hide() {
      setOpen(false);
    },
    resize(pixels: number) {
      const next = Math.max(TERMINAL_MIN_HEIGHT, Math.round(pixels));
      setHeight(next);
      try {
        localStorage.setItem(HEIGHT_KEY, JSON.stringify({ height: next }));
      } catch {
        /* The height still applies for this page. */
      }
    },
  };
}

/**
 * Where the terminal shows. Focus given to the slot (a closing palette
 * returning focus) goes on into the terminal, wherever it is on screen.
 */
export const TerminalSlot = forwardRef<
  HTMLDivElement,
  { open: boolean; focusKey: number; onClose: () => void }
>(function TerminalSlot({ open, focusKey, onClose }, ref) {
  return (
    <div
      ref={ref}
      className="terminal-slot"
      tabIndex={-1}
      onFocus={(event) => {
        if (event.target === event.currentTarget)
          document
            .querySelector<HTMLElement>(
              '.native-terminal .xterm-helper-textarea',
            )
            ?.focus();
      }}
    >
      {open && (
        <Suspense fallback={<Skeleton label="Opening terminal" />}>
          <NativeTerminal onClose={onClose} focusKey={focusKey} />
        </Suspense>
      )}
    </div>
  );
});
