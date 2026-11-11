import { useEffect, useRef, useState } from 'react';
import type { BuddyPack, BuddySnapshot } from '../../shell/BuddyControls';
import { useReducedMotion, type BuddyMediaLoader } from '../media';
import { MotionController, type Intensity } from './controller';
import type { GestureName } from './gestures';
import { pagePointer } from './pointer';
import { BuddyRenderer, viewFor, type Framing } from './renderer';
import { buildRig, parseRig, type BuiltRig } from './rig';
import { claimLiveSlot, schedule, wake } from './scheduler';
import { buddyVoiceLevel } from './voice';
import './LivingBuddy.css';

type Loaded = { rig: BuiltRig; art: ImageBitmap; patches: ImageBitmap | null };

const MASK = 128;
const loaded = new Map<string, Promise<Loaded>>();

function maskOf(art: ImageBitmap) {
  const canvas = document.createElement('canvas');
  canvas.width = MASK;
  canvas.height = MASK;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) throw new Error('buddy_mask_unavailable');
  context.drawImage(art, 0, 0, MASK, MASK);
  const data = context.getImageData(0, 0, MASK, MASK).data;
  const alpha = new Uint8Array(MASK * MASK);
  for (let i = 0; i < alpha.length; i++) alpha[i] = data[i * 4 + 3];
  return { width: MASK, height: MASK, alpha };
}

/** A pack's art, patches and built rig; shared by every instance of one revision. */
function load(
  pack: BuddyPack,
  conversation: string | null,
  loadMedia: BuddyMediaLoader,
): Promise<Loaded> {
  const key = `${pack.id}\u0000${pack.revision}`;
  const known = loaded.get(key);
  if (known) return known;
  const has = (id: string) => pack.assets.some((asset) => asset.id === id);
  const read = (id: string) =>
    loadMedia(
      conversation,
      pack.id,
      id,
      pack.revision,
      new AbortController().signal,
    );
  const pending = (async () => {
    const [artBlob, rigBlob, patchBlob] = await Promise.all([
      read('art'),
      read('rig'),
      has('patches') ? read('patches') : Promise.resolve(null),
    ]);
    const bitmap = (blob: Blob) =>
      createImageBitmap(blob, { premultiplyAlpha: 'premultiply' });
    const art = await bitmap(artBlob);
    const patches = patchBlob ? await bitmap(patchBlob) : null;
    const spec = parseRig(JSON.parse(await rigBlob.text()));
    return { rig: buildRig(spec, maskOf(art)), art, patches };
  })();
  loaded.set(key, pending);
  pending.catch(() => loaded.delete(key));
  while (loaded.size > 6) loaded.delete(loaded.keys().next().value!);
  return pending;
}

export function isLivingPack(pack: BuddyPack | null): pack is BuddyPack {
  return (
    !!pack &&
    pack.available &&
    pack.runtime === 'living' &&
    pack.assets.some((asset) => asset.id === 'art') &&
    pack.assets.some((asset) => asset.id === 'rig')
  );
}

function seedOf(text: string) {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i++)
    hash = Math.imul(hash ^ text.charCodeAt(i), 16777619);
  return hash >>> 0;
}

export type LivingBuddyHandle = { trigger(name: GestureName): void };

export function LivingBuddy({
  conversation,
  pack,
  snapshot,
  loadMedia,
  framing = 'portrait',
  workSide = 1,
  cursor = null,
  shadow = 0.22,
  still,
  handle,
}: {
  conversation: string | null;
  pack: BuddyPack;
  snapshot: BuddySnapshot;
  loadMedia: BuddyMediaLoader;
  framing?: Framing;
  workSide?: 1 | -1;
  /** The cursor relative to Buddy (pet window), in head-heights; else the page's pointer. */
  cursor?: { x: number; y: number; age: number } | null;
  shadow?: number;
  /** Shown until the living Buddy draws, and instead of it when it can't. */
  still: React.ReactNode;
  handle?: React.RefObject<LivingBuddyHandle | null>;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const reduced = useReducedMotion();
  const [state, setState] = useState<'loading' | 'live' | 'still'>('loading');
  const controllerRef = useRef<MotionController | null>(null);
  // Read every frame without re-creating the renderer.
  const live = useRef({ snapshot, reduced, cursor, workSide, shadow, framing });
  live.current = { snapshot, reduced, cursor, workSide, shadow, framing };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const release = claimLiveSlot();
    if (!release || typeof createImageBitmap !== 'function') {
      setState('still');
      release?.();
      return;
    }
    let cancelled = false;
    let renderer: BuddyRenderer | null = null;
    let unschedule = () => {};
    let observer: IntersectionObserver | null = null;
    setState('loading');
    load(pack, conversation, loadMedia)
      .then(({ rig, art, patches }) => {
        if (cancelled) return;
        renderer = new BuddyRenderer(canvas, rig, art, patches);
        const controller = new MotionController(rig.spec, seedOf(pack.id));
        controllerRef.current = controller;
        if (handle)
          handle.current = { trigger: (name) => controller.trigger(name) };
        let last = -1;
        let shown = false;
        let wanted = 60;
        const animated = {
          visible: true,
          fps: () => wanted,
          tick(now: number) {
            if (!renderer || renderer.lost) return;
            const dt = last < 0 ? 1 / 60 : (now - last) / 1000;
            last = now;
            const current = live.current;
            const status = current.snapshot.status;
            let pointer: { x: number; y: number } | null = null;
            let pointerAge = Infinity;
            if (current.cursor) {
              pointer = { x: current.cursor.x, y: current.cursor.y };
              pointerAge = current.cursor.age;
            } else {
              const page = pagePointer();
              if (page && page.age < 3) {
                const box = canvas.getBoundingClientRect();
                const view = viewFor(
                  rig,
                  current.framing,
                  box.width,
                  box.height,
                );
                const scale = box.height / (view[3] - view[1]);
                const headX = box.left + (rig.head.center[0] - view[0]) * scale;
                const headY = box.top + (rig.head.center[1] - view[1]) * scale;
                const reach = Math.max(
                  24,
                  rig.head.radius[1] * 2 * scale * 2.5,
                );
                pointer = {
                  x: (page.x - headX) / reach,
                  y: (page.y - headY) / reach,
                };
                pointerAge = page.age;
              }
            }
            const frame = controller.update(dt, {
              activity: current.snapshot.activity ?? 'idle',
              animation: status.animation,
              eventId: status.event_id,
              mood: status.mood,
              energy: status.energy,
              focus: status.focus,
              alert: status.alert,
              intensity: current.snapshot.preferences
                .animation_intensity as Intensity,
              reducedMotion: current.reduced,
              pointer,
              pointerAge,
              voice: buddyVoiceLevel(),
              workSide: current.workSide,
            });
            wanted = frame.fps;
            renderer.draw(frame.pose, {
              framing: current.framing,
              shadow: current.shadow,
              sparkles: frame.sparkles,
              time: frame.time,
            });
            if (!shown) {
              shown = true;
              setState('live');
            }
          },
        };
        unschedule = schedule(animated);
        if (typeof IntersectionObserver === 'function') {
          observer = new IntersectionObserver((entries) => {
            animated.visible = entries.some((entry) => entry.isIntersecting);
            if (animated.visible) wake();
          });
          observer.observe(canvas);
        }
        canvas.addEventListener('webglcontextlost', () => setState('still'));
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        // The still stays; say why for anyone looking at the console.
        console.warn('Buddy is showing its still image:', error);
        setState('still');
      });
    return () => {
      cancelled = true;
      unschedule();
      observer?.disconnect();
      renderer?.dispose();
      controllerRef.current = null;
      if (handle) handle.current = null;
      release();
    };
    // The renderer is rebuilt only for a different pack revision.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pack.id, pack.revision, conversation, loadMedia]);

  return (
    <span
      className="living-buddy"
      data-runtime={state}
      data-framing={framing}
      data-state={snapshot.activity ?? 'idle'}
      data-buddy-mood={snapshot.status.mood}
      data-buddy-animation={snapshot.status.animation}
      data-animation-intensity={snapshot.preferences.animation_intensity}
      data-reduced-motion={reduced ? 'true' : 'false'}
      aria-hidden="true"
    >
      <canvas
        ref={canvasRef}
        className="living-buddy-canvas"
        onPointerDown={() => controllerRef.current?.trigger('boop')}
      />
      {state !== 'live' && <span className="living-buddy-still">{still}</span>}
    </span>
  );
}
