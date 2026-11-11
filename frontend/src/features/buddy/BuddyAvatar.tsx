import { useEffect, useState } from 'react';
import type { BuddyPack, BuddySnapshot } from '../shell/BuddyControls';
import { revokeLater, type BuddyMediaLoader } from './media';
import glyph from '../../assets/row_bot_glyph_256.png';
import {
  isLivingPack,
  LivingBuddy,
  type LivingBuddyHandle,
} from './living/LivingBuddy';
import type { Framing } from './living/renderer';
import './BuddyAvatar.css';

export {
  rememberBuddyMedia,
  revokeLater,
  useReducedMotion,
  type BuddyMediaLoader,
} from './media';

/** The pack's still: its cut-out art (shared with the living renderer), else its preview. */
function stillAsset(pack: BuddyPack | null) {
  if (!pack?.available) return '';
  if (pack.assets.some((asset) => asset.id === 'art')) return 'art';
  return pack.assets.some((asset) => asset.id === 'preview') ? 'preview' : '';
}

/**
 * Buddy, everywhere it appears: alive (the living renderer) when its pack
 * can be, its still otherwise and in pack previews, Row-Bot's glyph while
 * nothing else is known.
 */
export function BuddyAvatar({
  conversation,
  pack,
  snapshot,
  loadMedia,
  preview = false,
  framing = 'portrait',
  cursor,
  handle,
}: {
  conversation: string | null;
  pack: BuddyPack | null;
  snapshot: BuddySnapshot;
  loadMedia: BuddyMediaLoader;
  preview?: boolean;
  framing?: Framing;
  /** The desktop pet: the cursor relative to Buddy's box. */
  cursor?: { x: number; y: number; age: number } | null;
  handle?: React.RefObject<LivingBuddyHandle | null>;
}) {
  const [still, setStill] = useState('');
  const asset = stillAsset(pack);
  const packId = pack?.id ?? '';
  const revision = pack?.revision ?? '';
  useEffect(() => {
    if (!asset) {
      setStill('');
      return;
    }
    const abort = new AbortController();
    let url = '';
    void loadMedia(conversation, packId, asset, revision, abort.signal)
      .then((blob) => {
        if (abort.signal.aborted) return;
        url = URL.createObjectURL(blob);
        setStill(url);
      })
      .catch(() => {
        if (!abort.signal.aborted) setStill('');
      });
    return () => {
      abort.abort();
      setStill('');
      if (url) revokeLater(url);
    };
  }, [loadMedia, conversation, packId, asset, revision]);
  const living = !preview && isLivingPack(pack);
  const media = !pack
    ? 'fallback'
    : !pack.available
      ? 'unavailable'
      : living
        ? 'living'
        : still
          ? 'still'
          : 'fallback';
  const image = (
    <img
      className="buddy-avatar"
      src={still || glyph}
      alt=""
      aria-hidden="true"
      data-glyph={still ? undefined : 'true'}
      onError={still ? () => setStill('') : undefined}
    />
  );
  return (
    <span
      className="buddy-avatar-frame"
      data-state={snapshot.activity ?? 'idle'}
      data-buddy-mood={snapshot.status.mood}
      data-buddy-animation={snapshot.status.animation}
      data-media={media}
      data-framing={framing}
      data-collapsed={snapshot.preferences.collapsed ? 'true' : 'false'}
      data-animation-intensity={snapshot.preferences.animation_intensity}
      data-preview={preview ? 'true' : 'false'}
      aria-hidden="true"
    >
      {living ? (
        <LivingBuddy
          conversation={conversation}
          pack={pack}
          snapshot={snapshot}
          loadMedia={loadMedia}
          framing={framing}
          shadow={framing === 'head' ? 0 : 0.22}
          cursor={cursor}
          handle={handle}
          still={image}
        />
      ) : (
        image
      )}
      {media === 'unavailable' && (
        <span className="buddy-media-note">Pack unavailable</span>
      )}
    </span>
  );
}
