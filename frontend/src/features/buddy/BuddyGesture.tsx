import {
  useCallback,
  useEffect,
  useEffectEvent,
  useRef,
  useState,
  type PointerEvent,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import type { ClientPlatform } from '../../platform';
import { TONED_NOTICE_MS } from '../../ui/overlays';
import { Button } from '../../ui/primitives';

export function useBuddyPlacement(platform: ClientPlatform) {
  const [placement, setPlacement] = useState<'docked' | 'desktop'>('docked');
  const [visible, setVisible] = useState(true);
  const [supported, setSupported] = useState(false);
  const apply = useCallback(
    (result: Awaited<ReturnType<ClientPlatform['buddyPlacement']>>) => {
      if (result.status !== 'ok') return;
      setSupported(true);
      setPlacement(result.value.placement);
      setVisible(result.value.visible);
    },
    [],
  );
  useEffect(() => {
    let active = true;
    void platform.buddyPlacement('status').then((result) => {
      if (active) apply(result);
    });
    return () => {
      active = false;
    };
  }, [platform, apply]);
  useEffect(() => {
    if (placement !== 'desktop') return;
    let active = true;
    const read = () =>
      void platform.buddyPlacement('status').then((result) => {
        if (active) apply(result);
      });
    const timer = window.setInterval(read, 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [platform, placement, apply]);
  const dock = async () => {
    const result = await platform.buddyPlacement('dock');
    if (result.status === 'ok') {
      setPlacement(result.value.placement);
      setVisible(result.value.visible);
    }
    return result.status === 'ok';
  };
  const tearOff = async () => {
    const result = await platform.buddyPlacement('tear_off', {
      x: window.screenX + window.innerWidth / 2,
      y: window.screenY + window.innerHeight / 2,
    });
    if (result.status === 'ok') setPlacement(result.value.placement);
    return result.status === 'ok';
  };
  return { placement, visible, supported, setPlacement, dock, tearOff };
}

/** Plain words for a tear-off that did not happen (B224). */
function tearOffProblem(reason: string | null): string {
  switch (reason) {
    case 'buddy_placement_requires_native':
      return 'Buddy can leave the window only in the Row-Bot desktop app.';
    case 'native_reconnecting':
      return 'Desktop features are reconnecting. Try again in a moment.';
    case 'native_authentication_required':
      return 'Row-Bot couldn’t confirm this window. Try again in a moment.';
    case 'invalid_drop_position':
      return 'Drop Buddy somewhere on your screen.';
    default:
      return 'Buddy couldn’t open its own window. Try again, or restart Row-Bot if it keeps happening.';
  }
}

type Release = Pick<
  PointerEvent,
  'pointerId' | 'clientX' | 'clientY' | 'screenX' | 'screenY'
>;

export function BuddyDragHandle({
  children,
  platform,
  onTornOff,
}: {
  children: ReactNode;
  platform: ClientPlatform;
  onTornOff(): void;
}) {
  const gesture = useRef<{
    id: number;
    x: number;
    y: number;
    dock: DOMRect;
    dragging: boolean;
  } | null>(null);
  const suppressClick = useRef(false);
  const [point, setPoint] = useState<{ x: number; y: number } | null>(null);
  // Beside the avatar, never inside it; the next drag clears it.
  const [notice, setNotice] = useState<{
    text: string;
    left: number;
    top: number;
  } | null>(null);
  const cancel = (event?: Pick<PointerEvent, 'pointerId'>) => {
    if (event && gesture.current?.id !== event.pointerId) return;
    gesture.current = null;
    setPoint(null);
  };
  const end = (event: Release) => {
    const current = gesture.current;
    if (!current || current.id !== event.pointerId) return;
    cancel(event);
    if (!current.dragging) return;
    suppressClick.current = true;
    if (
      event.clientX >= current.dock.left &&
      event.clientX <= current.dock.right &&
      event.clientY >= current.dock.top &&
      event.clientY <= current.dock.bottom
    )
      return;
    void platform
      .buddyPlacement('tear_off', { x: event.screenX, y: event.screenY })
      .then((result) => {
        if (result.status === 'ok' && result.value.placement === 'desktop') {
          onTornOff();
          return;
        }
        setNotice({
          text: tearOffProblem(
            result.status === 'unavailable' ? result.reason : null,
          ),
          left: Math.max(
            8,
            Math.min(current.dock.right + 8, window.innerWidth - 296),
          ),
          top: current.dock.top + current.dock.height / 2,
        });
      });
  };
  const release = useEffectEvent(end);
  const lose = useEffectEvent(cancel);
  const dragging = point !== null;
  // A drag always ends, wherever the pointer is let go, and puts the avatar
  // back even if the handle lost its pointer capture (B224).
  useEffect(() => {
    if (!dragging) return;
    const up = (event: globalThis.PointerEvent) => release(event);
    const lost = (event: globalThis.PointerEvent) => lose(event);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', lost);
    return () => {
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', lost);
    };
  }, [dragging]);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), TONED_NOTICE_MS);
    return () => window.clearTimeout(timer);
  }, [notice]);
  return (
    <>
      <span
        className="buddy-drag-handle"
        data-dragging={point ? 'true' : 'false'}
        style={
          point
            ? { transform: `translate(${point.x}px, ${point.y}px)` }
            : undefined
        }
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          suppressClick.current = false;
          setNotice(null);
          const dock = event.currentTarget.getBoundingClientRect();
          gesture.current = {
            id: event.pointerId,
            x: event.clientX,
            y: event.clientY,
            dock,
            dragging: false,
          };
          event.currentTarget.setPointerCapture?.(event.pointerId);
        }}
        onPointerMove={(event) => {
          const current = gesture.current;
          if (!current || current.id !== event.pointerId) return;
          const x = event.clientX - current.x;
          const y = event.clientY - current.y;
          if (!current.dragging && Math.hypot(x, y) <= 6) return;
          current.dragging = true;
          setPoint({ x, y });
        }}
        onPointerUp={end}
        onPointerCancel={cancel}
        onLostPointerCapture={cancel}
        // The avatar is an image: the browser's own image drag would start on
        // the first move and cancel this gesture, so Buddy never tore off (B101).
        onDragStart={(event) => event.preventDefault()}
        onClickCapture={(event) => {
          if (!suppressClick.current) return;
          suppressClick.current = false;
          event.stopPropagation();
          event.preventDefault();
        }}
      >
        {children}
      </span>
      {notice &&
        createPortal(
          <span
            className="buddy-drag-notice"
            role="status"
            style={{ left: notice.left, top: notice.top }}
          >
            {notice.text}
          </span>,
          document.body,
        )}
    </>
  );
}

export function BuddyDockButton({ dock }: { dock(): Promise<boolean> }) {
  const [error, setError] = useState(false);
  return (
    <div className="buddy-dock-placeholder">
      <Button onClick={() => void dock().then((ok) => setError(!ok))}>
        Dock Buddy
      </Button>
      {error && <span role="status">Buddy could not dock.</span>}
    </div>
  );
}
