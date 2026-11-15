import {
  type RefObject,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import type {
  BrowserInputEvent,
  BrowserLiveSize,
  BrowserPaneState,
} from '../../api/types';
import { useRuntime } from '../../runtime';

/**
 * Row-Bot's browser, live (N12-A). The server streams the conversation's
 * page as JPEG frames plus the pane's state while something watches; a
 * hidden pane or page doesn't watch at all, so nothing streams.
 */
export type LiveFrame = {
  seq: number;
  src: string;
  cssWidth: number;
  cssHeight: number;
};
export type LiveConnection = 'connecting' | 'live' | 'offline';

function pageVisible() {
  return (
    typeof document === 'undefined' || document.visibilityState !== 'hidden'
  );
}

/**
 * The page follows the pane: its page area in whole CSS pixels and the
 * screen's pixel ratio (up to 2×). A pane that isn't laid out (hidden, or
 * no layout at all) has no size to give, so the page stays a desktop window.
 */
export function paneSize(
  rect: { width: number; height: number },
  ratio = 1,
): BrowserLiveSize {
  const width = Math.round(rect.width);
  const height = Math.round(rect.height);
  if (width < 1 || height < 1) return { width: 1280 };
  const scale = Math.min(2, Math.max(1, Math.round((ratio || 1) * 100) / 100));
  return { width, height, scale };
}

const sameSize = (a: BrowserLiveSize | null, b: BrowserLiveSize) =>
  a?.width === b.width && a.height === b.height && a.scale === b.scale;

/**
 * The pane's size, measured before it first connects and again after each
 * resize settles (docked, phone or its own window), so the page re-lays out
 * once per resize rather than on every frame of a drag.
 */
export function usePaneSize(
  element: RefObject<HTMLElement | null>,
  settle = 250,
) {
  const [size, setSize] = useState<BrowserLiveSize | null>(null);
  useLayoutEffect(() => {
    const node = element.current;
    if (!node) return;
    const measure = () => {
      const next = paneSize(
        node.getBoundingClientRect(),
        window.devicePixelRatio,
      );
      setSize((current) => (sameSize(current, next) ? current : next));
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const observer = new ResizeObserver(() => {
      clearTimeout(timer);
      timer = setTimeout(measure, settle);
    });
    observer.observe(node);
    return () => {
      clearTimeout(timer);
      observer.disconnect();
    };
  }, [element, settle]);
  return size;
}

export function useBrowserLive({
  conversationId,
  view,
  width,
  height,
  scale,
  enabled,
}: {
  conversationId: string;
  view: 'pane' | 'thumb';
  /** A thumbnail's width; a pane's page area in CSS pixels, with its height and pixel ratio. */
  width: number;
  height?: number;
  scale?: number;
  enabled: boolean;
}) {
  const { controller } = useRuntime();
  const [state, setState] = useState<BrowserPaneState | null>(null);
  const [frame, setFrame] = useState<LiveFrame | null>(null);
  const [connection, setConnection] = useState<LiveConnection>('connecting');
  const [visible, setVisible] = useState(pageVisible);
  useEffect(() => {
    const update = () => setVisible(pageVisible());
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);
  const watching = enabled && visible && Boolean(conversationId);
  useEffect(() => {
    if (!watching) return;
    const abort = new AbortController();
    let attempt = 0;
    void (async () => {
      while (!abort.signal.aborted) {
        setConnection('connecting');
        try {
          for await (const item of controller.browserLive(
            conversationId,
            view,
            { width, height, scale },
            abort.signal,
          )) {
            attempt = 0;
            setConnection('live');
            if ('state' in item) setState(item.state);
            else
              setFrame({
                seq: item.frame.seq,
                src: `data:image/jpeg;base64,${item.frame.image_base64}`,
                cssWidth: item.frame.css_width,
                cssHeight: item.frame.css_height,
              });
          }
        } catch {
          if (abort.signal.aborted) return;
        }
        if (abort.signal.aborted) return;
        setConnection('offline');
        // Back off while the server is away; a returning server picks up at once.
        await new Promise((resolve) => {
          const timer = setTimeout(
            resolve,
            Math.min(10_000, 500 * 2 ** attempt),
          );
          abort.signal.addEventListener('abort', () => {
            clearTimeout(timer);
            resolve(undefined);
          });
        });
        attempt += 1;
      }
    })();
    return () => abort.abort();
  }, [controller, conversationId, view, width, height, scale, watching]);
  // A different conversation never shows the last one's page.
  const shown = useRef(conversationId);
  useEffect(() => {
    if (shown.current === conversationId) return;
    shown.current = conversationId;
    setState(null);
    setFrame(null);
  }, [conversationId]);
  const current = state?.conversation_id === conversationId;
  return {
    state: current ? state : null,
    frame: current ? frame : null,
    connection,
    watching,
  };
}

/** A point on the shown picture in the page's CSS pixels. */
export function pagePoint(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
  frame: { cssWidth: number; cssHeight: number },
) {
  const x = ((clientX - rect.left) / Math.max(1, rect.width)) * frame.cssWidth;
  const y = ((clientY - rect.top) / Math.max(1, rect.height)) * frame.cssHeight;
  return {
    x: Math.round(Math.min(Math.max(x, 0), frame.cssWidth) * 10) / 10,
    y: Math.round(Math.min(Math.max(y, 0), frame.cssHeight) * 10) / 10,
  };
}

export const BUTTONS: Record<number, BrowserInputEvent['button']> = {
  0: 'left',
  1: 'middle',
  2: 'right',
};

/** Keys a page understands as one key: printable characters and DOM key names. */
export function pageKey(key: string) {
  if (!key || key === 'Unidentified' || key === 'Process' || key === 'Dead')
    return '';
  return key.length === 1 || /^[A-Z][A-Za-z0-9]{1,30}$/.test(key) ? key : '';
}

/**
 * Sends the person's input in order, a batch at a time: moves coalesce, so a
 * slow connection never queues up stale pointer moves. A batch that fails is
 * sent once more before anything after it, so a busy moment drops no keys.
 */
export function createInputQueue(
  send: (events: BrowserInputEvent[]) => Promise<unknown>,
) {
  let queue: BrowserInputEvent[] = [];
  let sending = false;
  async function flush() {
    if (sending || !queue.length) return;
    sending = true;
    const batch = queue.slice(0, 64);
    queue = queue.slice(64);
    try {
      await send(batch).catch(() => send(batch));
    } catch {
      // Twice refused: the pane shows the connection; stale input isn't replayed later.
      queue = [];
    } finally {
      sending = false;
    }
    if (queue.length) void flush();
  }
  return {
    push(event: BrowserInputEvent) {
      const last = queue[queue.length - 1];
      if (
        event.type === 'mouse' &&
        event.action === 'move' &&
        last?.type === 'mouse' &&
        last.action === 'move'
      )
        queue[queue.length - 1] = event;
      else queue.push(event);
      void flush();
    },
    get pending() {
      return queue.length;
    },
  };
}
