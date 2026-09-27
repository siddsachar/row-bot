import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from 'react';
import type { BuddyPack, BuddySnapshot } from '../shell/BuddyControls';
import glyph from '../../assets/row_bot_glyph_256.png';
import { drawBuddyMedia } from './buddy-media';
import './BuddyAvatar.css';

// A replaced image or video may still start its fetch in a later task, so a
// retired media URL outlives the commit that dropped it by this long.
const REVOKE_DELAY_MS = 250;

function revokeLater(url: string) {
  window.setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
}

export type BuddyMediaLoader = (
  conversation: string | null,
  packId: string,
  assetId: string,
  revision: string,
  signal: AbortSignal,
) => Promise<Blob>;

const MEDIA_LIMIT = 16;
const rememberedMedia = new WeakMap<object, Map<string, Blob>>();

/**
 * Pack media are the same bytes in every conversation for one pack revision,
 * so switching conversations reuses them instead of downloading them again
 * (each download spends the session's view budget). Bounded per owner.
 */
export function rememberBuddyMedia(
  owner: object,
  load: BuddyMediaLoader,
): BuddyMediaLoader {
  return async (conversation, pack, asset, revision, signal) => {
    let media = rememberedMedia.get(owner);
    if (!media) rememberedMedia.set(owner, (media = new Map()));
    const key = `${pack}\u0000${asset}\u0000${revision}`;
    const known = media.get(key);
    if (known) {
      media.delete(key);
      media.set(key, known);
      return known;
    }
    const blob = await load(conversation, pack, asset, revision, signal);
    media.set(key, blob);
    for (const oldest of media.keys()) {
      if (media.size <= MEDIA_LIMIT) break;
      media.delete(oldest);
    }
    return blob;
  };
}

export function useReducedMotion() {
  const query = '(prefers-reduced-motion: reduce)';
  const read = () =>
    typeof matchMedia === 'function' ? matchMedia(query).matches : false;
  const [reduced, setReduced] = useState(read);
  useEffect(() => {
    if (typeof matchMedia !== 'function') return;
    const media = matchMedia(query);
    const changed = () => setReduced(media.matches);
    media.addEventListener('change', changed);
    changed();
    return () => media.removeEventListener('change', changed);
  }, []);
  return reduced;
}

const percentage = (value: number) => Math.max(0, Math.min(100, value));

export function BuddyAvatar({
  conversation,
  pack,
  snapshot,
  loadMedia,
  preview = false,
}: {
  conversation: string | null;
  pack: BuddyPack | null;
  snapshot: BuddySnapshot;
  loadMedia: BuddyMediaLoader;
  preview?: boolean;
}) {
  const [still, setStill] = useState('');
  const [motion, setMotion] = useState('');
  // Media URLs from a finished load, revoked once no committed source uses them.
  const retiredUrls = useRef(new Set<string>());
  const [retiredVersion, setRetiredVersion] = useState(0);
  const [motionPasses, setMotionPasses] = useState(0);
  const [videoReady, setVideoReady] = useState(false);
  const [canvasReady, setCanvasReady] = useState(false);
  const canvasReadyRef = useRef(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  // A detached video keeps loading byte ranges from its source, so release it
  // before that source is revoked.
  const attachVideo = useCallback((node: HTMLVideoElement | null) => {
    const previous = videoRef.current;
    if (previous && previous !== node) {
      previous.pause();
      previous.removeAttribute('src');
      previous.removeAttribute('poster');
      previous.load();
    }
    videoRef.current = node;
  }, []);
  const scratchRef = useRef<HTMLCanvasElement | null>(null);
  const [phase, setPhase] = useState<
    'loading' | 'motion' | 'still' | 'fallback' | 'unavailable'
  >('loading');
  const reduced = useReducedMotion();
  const activity = snapshot.activity ?? 'idle';
  const activityAnimation = {
    idle: snapshot.status.animation,
    thinking: 'thinking',
    streaming: 'talking',
    tool: 'working',
    approval: 'alert',
    stopping: 'alert',
    completed: 'celebrate',
    stopped: 'idle',
    error: 'alert',
    disconnected: 'alert',
  }[activity];
  const clip =
    pack?.animation_map[activityAnimation] ??
    pack?.animation_map[snapshot.status.animation] ??
    activityAnimation;
  const packId = pack?.id ?? '';
  const packRevision = pack?.revision ?? '';
  const packAvailable = pack?.available ?? false;
  const stillAssetId = pack?.assets.some((asset) => asset.id === 'preview')
    ? 'preview'
    : '';
  const motionAssetId =
    pack?.assets.find(
      (asset) => asset.id === clip && asset.content_type === 'video/mp4',
    )?.id ?? '';
  const motionEvent = `${activity}:${clip}:${snapshot.status.event_id}`;
  useEffect(() => setMotionPasses(0), [motionEvent]);
  // Idle and finished states play their clip twice and then rest on the
  // still; only live work loops. A clip looping forever keeps a video and a
  // per-frame canvas pass running on every open page (B29).
  const settles =
    activity === 'idle' || activity === 'completed' || activity === 'stopped';
  const motionActive = !!motion && (!settles || motionPasses < 2);
  const draw = useCallback((source: HTMLImageElement | HTMLVideoElement) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    scratchRef.current ??= document.createElement('canvas');
    if (
      drawBuddyMedia(canvas, source, scratchRef.current) &&
      !canvasReadyRef.current
    ) {
      canvasReadyRef.current = true;
      setCanvasReady(true);
    }
  }, []);
  useEffect(() => {
    if (!motionActive || !videoReady) {
      if (imageRef.current?.complete) draw(imageRef.current);
      return;
    }
    let frame = 0;
    const tick = () => {
      if (videoRef.current) draw(videoRef.current);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [draw, motionActive, videoReady, still]);
  useEffect(() => {
    const abort = new AbortController();
    const urls = new Set<string>();
    const retired = retiredUrls.current;
    canvasReadyRef.current = false;
    setCanvasReady(false);
    setVideoReady(false);
    setStill('');
    setMotion('');
    if (!packId) setPhase('fallback');
    else if (!packAvailable) setPhase('unavailable');
    else {
      const motionAllowed =
        !!motionAssetId &&
        !preview &&
        !reduced &&
        snapshot.preferences.animation_intensity !== 'quiet';
      setPhase(stillAssetId || motionAllowed ? 'loading' : 'fallback');
      const load = async (
        asset: string,
        apply: (url: string) => void,
        loadedPhase: 'motion' | 'still',
      ) => {
        try {
          const blob = await loadMedia(
            conversation,
            packId,
            asset,
            packRevision,
            abort.signal,
          );
          if (abort.signal.aborted) return;
          const url = URL.createObjectURL(blob);
          urls.add(url);
          apply(url);
          setPhase((current) =>
            current === 'motion' && loadedPhase === 'still'
              ? current
              : loadedPhase,
          );
        } catch {
          if (!abort.signal.aborted)
            setPhase((current) =>
              current === 'motion' || current === 'still'
                ? current
                : 'fallback',
            );
        }
      };
      if (stillAssetId) void load(stillAssetId, setStill, 'still');
      if (motionAllowed) void load(motionAssetId, setMotion, 'motion');
    }
    return () => {
      abort.abort();
      // The old image/video keeps these URLs until React commits the cleared
      // sources. A frame is not a guaranteed commit, so revoke after it.
      urls.forEach((url) => retired.add(url));
      setRetiredVersion((value) => value + 1);
    };
  }, [
    loadMedia,
    conversation,
    packId,
    packRevision,
    packAvailable,
    stillAssetId,
    motionAssetId,
    reduced,
    preview,
    snapshot.preferences.animation_intensity,
  ]);
  useEffect(() => {
    for (const url of retiredUrls.current)
      if (url !== still && url !== motion) {
        revokeLater(url);
        retiredUrls.current.delete(url);
      }
  }, [still, motion, retiredVersion]);
  useEffect(() => {
    const retired = retiredUrls.current;
    return () => {
      retired.forEach(revokeLater);
      retired.clear();
    };
  }, []);
  const style = {
    '--buddy-energy': `${percentage(snapshot.status.energy)}%`,
    '--buddy-focus': `${percentage(snapshot.status.focus)}%`,
    '--buddy-alert': `${percentage(snapshot.status.alert)}%`,
  } as CSSProperties;
  return (
    <span
      className="buddy-avatar-frame"
      data-state={activity}
      data-buddy-mood={snapshot.status.mood}
      data-buddy-animation={snapshot.status.animation}
      data-media={
        motion && settles && motionPasses >= 2
          ? still
            ? 'still'
            : 'fallback'
          : phase
      }
      data-canvas-ready={canvasReady ? 'true' : 'false'}
      data-collapsed={snapshot.preferences.collapsed ? 'true' : 'false'}
      data-animation-intensity={snapshot.preferences.animation_intensity}
      data-reduced-motion={reduced ? 'true' : 'false'}
      data-energy={percentage(snapshot.status.energy)}
      data-focus={percentage(snapshot.status.focus)}
      data-alert={percentage(snapshot.status.alert)}
      data-preview={preview ? 'true' : 'false'}
      style={style}
      aria-hidden="true"
    >
      <img
        ref={imageRef}
        className="buddy-avatar buddy-avatar-source"
        src={still || glyph}
        alt=""
        aria-hidden="true"
        onLoad={(event) => {
          if (!motionActive) draw(event.currentTarget);
        }}
        onError={
          still
            ? () => {
                setStill('');
                setPhase('fallback');
              }
            : undefined
        }
      />
      {motionActive && (
        <video
          ref={attachVideo}
          key={motionEvent}
          className="buddy-avatar buddy-avatar-source"
          aria-hidden="true"
          src={motion}
          poster={still || glyph}
          autoPlay
          muted
          playsInline
          onLoadedData={() => setVideoReady(true)}
          onEnded={(event) => {
            if (!settles) {
              event.currentTarget.currentTime = 0;
              void event.currentTarget.play().catch(() => setMotionPasses(2));
            } else if (motionPasses === 0) {
              setMotionPasses(1);
              event.currentTarget.currentTime = 0;
              void event.currentTarget.play().catch(() => setMotionPasses(2));
            } else setMotionPasses(2);
          }}
          onError={() => {
            setVideoReady(false);
            setMotion('');
            setPhase(still ? 'still' : 'fallback');
          }}
        />
      )}
      <canvas
        ref={canvasRef}
        className="buddy-avatar-canvas"
        aria-hidden="true"
      />
      {phase === 'unavailable' && (
        <span className="buddy-media-note">Pack unavailable</span>
      )}
    </span>
  );
}
