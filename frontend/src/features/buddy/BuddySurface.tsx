import {
  useCallback,
  useEffect,
  useState,
  useSyncExternalStore,
  type CSSProperties,
} from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useClientState, useRuntime } from '../../runtime';
import { Button, EmptyState } from '../../ui/primitives';
import { useOverlay } from '../../ui/overlays';
import BuddyPanel, { type BuddyPanelSession } from './BuddyPanel';
import type { BuddyPack, BuddySnapshot } from '../shell/BuddyControls';
import glyph from '../../assets/row_bot_glyph_256.png';

export type BuddyMediaLoader = (
  conversation: string | null,
  packId: string,
  assetId: string,
  revision: string,
  signal: AbortSignal,
) => Promise<Blob>;

function useReducedMotion() {
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
  useEffect(() => {
    const abort = new AbortController();
    const urls = new Set<string>();
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
      // The old image/video may still reference these URLs until React commits
      // the fallback source. Release them after that paint.
      const revoke = URL.revokeObjectURL.bind(URL);
      requestAnimationFrame(() => urls.forEach((url) => revoke(url)));
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
      data-media={phase}
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
      {motion ? (
        <video
          className="buddy-avatar"
          src={motion}
          poster={still || glyph}
          autoPlay
          muted
          loop
          playsInline
          onError={() => {
            setMotion('');
            setPhase(still ? 'still' : 'fallback');
          }}
        />
      ) : (
        <img
          className="buddy-avatar"
          src={still || glyph}
          alt=""
          onError={
            still
              ? () => {
                  setStill('');
                  setPhase('fallback');
                }
              : undefined
          }
        />
      )}
      {phase === 'unavailable' && (
        <span className="buddy-media-note">Pack unavailable</span>
      )}
    </span>
  );
}

function Avatar(props: Omit<Parameters<typeof BuddyAvatar>[0], 'loadMedia'>) {
  const { controller } = useRuntime();
  const loadMedia = useCallback<BuddyMediaLoader>(
    (conversation, pack, asset, revision, signal) =>
      conversation
        ? controller.buddyMedia(conversation, pack, asset, revision, signal)
        : controller.globalBuddyMedia(pack, asset, revision, signal),
    [controller],
  );
  return <BuddyAvatar {...props} loadMedia={loadMedia} />;
}

function GlobalBuddy() {
  const { controller } = useRuntime();
  const navigate = useNavigate();
  const [snapshot, setSnapshot] = useState<BuddySnapshot | null>(null);
  const [pack, setPack] = useState<BuddyPack | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const request = new AbortController();
    let timer = 0;
    const load = async () => {
      try {
        const next = await controller.globalBuddy(request.signal);
        if (request.signal.aborted) return;
        const selected = await controller.globalBuddyPack(
          next.preferences.pack_id,
          request.signal,
        );
        if (request.signal.aborted) return;
        setSnapshot(next);
        setPack(selected);
        setError(false);
        timer = window.setTimeout(load, 30000);
      } catch {
        if (!request.signal.aborted) {
          setError(true);
          timer = window.setTimeout(load, 15000);
        }
      }
    };
    void load();
    return () => {
      request.abort();
      window.clearTimeout(timer);
    };
  }, [attempt, controller]);
  if (snapshot && !snapshot.preferences.visible) return null;
  if (!snapshot)
    return (
      <aside
        className="buddy-companion buddy-companion-state"
        aria-label="Buddy companion"
        aria-busy={!error}
        data-state={error ? 'unavailable' : 'loading'}
      >
        <img className="buddy-state-glyph" src={glyph} alt="" />
        <p>
          {error ? 'Buddy could not load its saved view.' : 'Buddy is loading…'}
        </p>
        {error && (
          <Button onClick={() => setAttempt((value) => value + 1)}>
            Retry Buddy
          </Button>
        )}
      </aside>
    );
  return (
    <aside className="buddy-companion" aria-label="Buddy companion">
      <Button
        aria-label="Buddy settings"
        variant="ghost"
        onClick={() => navigate('/settings/buddy')}
      >
        <Avatar conversation={null} pack={pack} snapshot={snapshot} />
      </Button>
      {snapshot.preferences.bubble_verbosity !== 'quiet' &&
        !snapshot.preferences.collapsed && (
          <p role="status">{snapshot.status.label || 'Ready when you are.'}</p>
        )}
    </aside>
  );
}

function OwnedBuddy({
  conversation,
  session,
  settings,
  initialPrompt,
}: {
  conversation: string;
  session: BuddyPanelSession;
  settings: boolean;
  initialPrompt?: string;
}) {
  const navigate = useNavigate();
  const overlay = useOverlay();
  const view = useSyncExternalStore(session.subscribe, session.getSnapshot);
  useEffect(() => session.observe(), [session]);
  return (
    <BuddyPanel
      scopeKey={conversation}
      session={session}
      settingsOpen={settings}
      initialPrompt={initialPrompt}
      companionVisible={!settings}
      onSettings={() => {
        overlay.close();
        navigate(
          `/settings/buddy?conversation=${encodeURIComponent(conversation)}`,
        );
      }}
      renderAvatar={(snapshot) => (
        <Avatar
          conversation={conversation}
          pack={view.selectedPack}
          snapshot={snapshot}
        />
      )}
      renderPackPreview={(pack) =>
        view.snapshot && (
          <Avatar
            conversation={conversation}
            pack={pack}
            snapshot={view.snapshot}
            preview
          />
        )
      }
    />
  );
}

export default function BuddySurface({
  settings = false,
  initialPrompt,
}: {
  settings?: boolean;
  initialPrompt?: string;
}) {
  const { buddyOwner } = useRuntime();
  const state = useClientState();
  const [search, setSearch] = useSearchParams();
  const conversation =
    (settings ? search.get('conversation') : null) ||
    state.selectedConversationId;
  useEffect(() => {
    if (settings && conversation && !search.has('conversation')) {
      const next = new URLSearchParams(search);
      next.set('conversation', conversation);
      setSearch(next, { replace: true });
    }
  }, [settings, conversation, search, setSearch]);
  const [, refresh] = useState(0);
  if (!conversation)
    return settings ? (
      <EmptyState title="Open a conversation for Buddy">
        Buddy uses that conversation’s current profile and approvals.
      </EmptyState>
    ) : (
      <GlobalBuddy />
    );
  if (!buddyOwner?.get())
    return settings ? (
      <EmptyState title="Buddy is reconnecting">
        The authenticated Buddy session is not available yet.
        <Button onClick={() => refresh((value) => value + 1)}>
          Check Buddy connection
        </Button>
      </EmptyState>
    ) : (
      <aside
        className="buddy-companion buddy-companion-state"
        aria-label="Buddy companion"
        aria-busy="true"
        data-state="unavailable"
      >
        <img className="buddy-state-glyph" src={glyph} alt="" />
        <p>Buddy is reconnecting…</p>
      </aside>
    );
  let session: BuddyPanelSession;
  try {
    session = buddyOwner.get()!.get(conversation);
  } catch {
    return settings ? (
      <EmptyState title="Buddy sessions need attention">
        Finish or discard retained Buddy edits before opening another session.
        <Button onClick={() => refresh((value) => value + 1)}>
          Check Buddy sessions
        </Button>
      </EmptyState>
    ) : (
      <aside
        className="buddy-companion buddy-companion-state"
        aria-label="Buddy companion"
        data-state="unavailable"
      >
        <img className="buddy-state-glyph" src={glyph} alt="" />
        <p>Buddy needs attention.</p>
        <Button onClick={() => refresh((value) => value + 1)}>
          Retry Buddy
        </Button>
      </aside>
    );
  }
  return (
    <OwnedBuddy
      key={conversation}
      conversation={conversation}
      session={session}
      settings={settings}
      initialPrompt={initialPrompt}
    />
  );
}
