import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent,
  type ReactNode,
} from 'react';
import type { ClientPlatform } from '../../platform';
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
  const [error, setError] = useState('');
  const cancel = (event?: PointerEvent<HTMLSpanElement>) => {
    if (event && gesture.current?.id !== event.pointerId) return;
    gesture.current = null;
    setPoint(null);
  };
  const end = (event: PointerEvent<HTMLSpanElement>) => {
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
          setError('');
          onTornOff();
        } else setError('Buddy tear-off is unavailable here.');
      });
  };
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
      {error && (
        <span className="buddy-drag-error" role="status">
          {error}
        </span>
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
