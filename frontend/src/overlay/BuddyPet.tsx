import {
  useEffect,
  useEffectEvent,
  useRef,
  useState,
  type PointerEvent,
  type ReactNode,
} from 'react';
import { EyeOff, PanelLeft, SquareArrowOutUpRight } from 'lucide-react';
import type { ClientPlatform } from '../platform';
import type { BuddyPetState } from '../platform/types';
import {
  BuddyAvatar,
  type BuddyMediaLoader,
} from '../features/buddy/BuddyAvatar';
import type { LivingBuddyHandle } from '../features/buddy/living/LivingBuddy';
import type { BuddyPack, BuddySnapshot } from '../features/shell/BuddyControls';

/** Buddy's box in the pet window (the host's PET_WIDTH × PET_HEIGHT). */
export const PET_BOX = { width: 168, height: 176 };
const STATE_EVERY_MS = 80;
const MASK_EVERY_MS = 250;
const DRAG_SLOP = 4;
/** Every cell set: the whole box takes clicks until Buddy's silhouette is read. */
const FULL_MASK = btoa(String.fromCharCode(...new Uint8Array(72).fill(255)));

type Geometry = Pick<BuddyPetState, 'side' | 'petX' | 'petY'>;

const rect = (
  element: Element | null,
): [number, number, number, number] | null => {
  if (!element) return null;
  const box = element.getBoundingClientRect();
  return box.width && box.height
    ? [
        Math.round(box.left),
        Math.round(box.top),
        Math.ceil(box.width),
        Math.ceil(box.height),
      ]
    : null;
};

/**
 * The desktop pet (D5–D9): Buddy alone on the desktop, its bubble (the card's
 * content) beside it on the side with room, a small menu on right-click. The
 * host learns the layout and which pixels take clicks; the page reads where
 * the cursor is (Buddy's gaze) and when Buddy lands.
 */
export function BuddyPet({
  platform,
  pack,
  snapshot,
  loadMedia,
  open,
  onOpenChange,
  onOpenMain,
  onDock,
  onHide,
  glance = false,
  moment,
  children,
}: {
  platform: ClientPlatform;
  pack: BuddyPack | null;
  snapshot: BuddySnapshot | null;
  loadMedia: BuddyMediaLoader;
  open: boolean;
  onOpenChange(open: boolean): void;
  onOpenMain(): void;
  onDock(): void;
  onHide(): void;
  /** Proactive: something waits for the person (Buddy's glance). */
  glance?: boolean;
  /**
   * Proactive: a moment Buddy delivers, in its own smaller bubble. It never
   * takes over the chat bubble: while that is open, Buddy only glances.
   */
  moment?: ReactNode;
  /** The bubble's content: the Buddy card. */
  children: ReactNode;
}) {
  const handle = useRef<LivingBuddyHandle | null>(null);
  const bubble = useRef<HTMLElement>(null);
  const momentBubble = useRef<HTMLElement>(null);
  const showMoment = !open && Boolean(moment);
  const menu = useRef<HTMLElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [geometry, setGeometry] = useState<Geometry>({
    side: 'right',
    petX: 0,
    petY: 0,
  });
  const [cursor, setCursor] = useState<BuddyPetState['cursor']>(null);
  const expanded = open || menuOpen || showMoment;

  // The window grows for the bubble or menu (toward the side with room) and shrinks back.
  useEffect(() => {
    let active = true;
    void platform.buddyPet({ action: 'layout', expanded }).then((result) => {
      if (!active || result.status !== 'ok') return;
      const { side, petX, petY } = result.value;
      if (side && petX !== undefined && petY !== undefined)
        setGeometry({ side, petX, petY });
    });
    return () => {
      active = false;
    };
  }, [platform, expanded]);

  // Where the cursor is (Buddy's gaze) and whether Buddy just landed.
  const landed = useRef<number | null>(null);
  const read = useEffectEvent(async () => {
    const result = await platform.buddyPet({ action: 'state' });
    if (result.status !== 'ok') return;
    const value = result.value;
    setCursor(value.cursor ?? null);
    if (typeof value.landed === 'number') {
      if (landed.current !== null && value.landed > landed.current)
        handle.current?.trigger('land');
      landed.current = value.landed;
    }
  });
  useEffect(() => {
    const timer = window.setInterval(() => void read(), STATE_EVERY_MS);
    return () => window.clearInterval(timer);
  }, []);

  // Which pixels take clicks: Buddy's silhouette, the open bubble, the menu.
  const lastMask = useRef('');
  const sendMask = useEffectEvent(() => {
    handle.current?.requestMask();
    const payload = {
      action: 'hit' as const,
      pet: [geometry.petX, geometry.petY, PET_BOX.width, PET_BOX.height] as [
        number,
        number,
        number,
        number,
      ],
      cells: handle.current?.mask() ?? FULL_MASK,
      bubble: open
        ? rect(bubble.current)
        : showMoment
          ? rect(momentBubble.current)
          : null,
      menu: menuOpen ? rect(menu.current) : null,
    };
    const key = JSON.stringify(payload);
    if (key === lastMask.current) return;
    lastMask.current = key;
    void platform.buddyPet(payload);
  });
  useEffect(() => {
    const timer = window.setInterval(sendMask, MASK_EVERY_MS);
    sendMask();
    return () => window.clearInterval(timer);
  }, []);

  // Esc tucks the bubble and menu away.
  useEffect(() => {
    if (!expanded) return;
    const key = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setMenuOpen(false);
      onOpenChange(false);
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [expanded, onOpenChange]);

  // A press on Buddy is a click (the bubble) or a drag (Buddy moves, then lands).
  const press = useRef<{
    pointer: number;
    offsetX: number;
    offsetY: number;
    startX: number;
    startY: number;
    dragging: boolean;
    frame: number;
    x: number;
    y: number;
  } | null>(null);
  function down(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    press.current = {
      pointer: event.pointerId,
      offsetX: event.clientX,
      offsetY: event.clientY,
      startX: event.screenX,
      startY: event.screenY,
      dragging: false,
      frame: 0,
      x: 0,
      y: 0,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }
  function move(event: PointerEvent<HTMLDivElement>) {
    const current = press.current;
    if (!current || current.pointer !== event.pointerId) return;
    if (
      !current.dragging &&
      Math.hypot(
        event.screenX - current.startX,
        event.screenY - current.startY,
      ) < DRAG_SLOP
    )
      return;
    if (!current.dragging) {
      current.dragging = true;
      setMenuOpen(false);
      void platform.buddyPet({ action: 'drag', phase: 'start' });
    }
    current.x = event.screenX - current.offsetX;
    current.y = event.screenY - current.offsetY;
    if (current.frame) return;
    current.frame = requestAnimationFrame(() => {
      current.frame = 0;
      platform.moveWindow(current.x, current.y);
    });
  }
  function up(event: PointerEvent<HTMLDivElement>) {
    const current = press.current;
    if (!current || current.pointer !== event.pointerId) return;
    press.current = null;
    if (current.frame) {
      cancelAnimationFrame(current.frame);
      platform.moveWindow(current.x, current.y);
    }
    if (current.dragging) {
      void platform.buddyPet({ action: 'drag', phase: 'end' });
      return;
    }
    setMenuOpen(false);
    onOpenChange(!open);
  }

  const bubbleLeft =
    geometry.side === 'right' ? geometry.petX + PET_BOX.width + 8 : 0;
  return (
    <main
      className="buddy-overlay buddy-pet-window"
      aria-label="Buddy"
      data-mode="pet"
      data-side={geometry.side}
      data-open={open ? 'true' : 'false'}
    >
      <div
        className="buddy-pet"
        style={{ left: geometry.petX, top: geometry.petY }}
        role="button"
        tabIndex={0}
        aria-label={open ? 'Hide Buddy’s bubble' : 'Show Buddy’s bubble'}
        aria-expanded={open}
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={up}
        onPointerCancel={up}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onOpenChange(!open);
          }
        }}
        onContextMenu={(event) => {
          event.preventDefault();
          setMenuOpen(true);
        }}
      >
        {snapshot && (
          <BuddyAvatar
            conversation={null}
            pack={pack}
            snapshot={snapshot}
            loadMedia={loadMedia}
            framing="pet"
            cursor={cursor}
            handle={handle}
            glance={glance}
          />
        )}
      </div>
      {showMoment && (
        <section
          ref={momentBubble}
          className="buddy-bubble"
          data-variant="moment"
          aria-label="Something for you"
          data-side={geometry.side}
          style={{
            left:
              geometry.side === 'right'
                ? bubbleLeft
                : Math.max(0, geometry.petX - 308),
            top: Math.max(0, geometry.petY + 24),
          }}
        >
          {moment}
        </section>
      )}
      {open && (
        <section
          ref={bubble}
          className="buddy-bubble"
          aria-label="Buddy’s bubble"
          data-side={geometry.side}
          style={{ left: bubbleLeft }}
        >
          {children}
        </section>
      )}
      {menuOpen && (
        <nav
          ref={menu}
          className="buddy-pet-menu"
          aria-label="Buddy menu"
          style={{
            left:
              geometry.side === 'right'
                ? geometry.petX + PET_BOX.width - 24
                : Math.max(0, geometry.petX - 176),
            top: Math.max(0, geometry.petY + 24),
          }}
        >
          {(
            [
              ['Open Row-Bot', SquareArrowOutUpRight, onOpenMain],
              ['Dock Buddy', PanelLeft, onDock],
              ['Hide Buddy', EyeOff, onHide],
            ] as const
          ).map(([label, Icon, action]) => (
            <button
              key={label}
              type="button"
              onClick={() => {
                setMenuOpen(false);
                action();
              }}
            >
              <Icon size={14} aria-hidden />
              {label}
            </button>
          ))}
        </nav>
      )}
    </main>
  );
}
