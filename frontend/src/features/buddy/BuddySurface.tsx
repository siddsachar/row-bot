import { useEffect, useState, useSyncExternalStore } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useClientState, useRuntime } from '../../runtime';
import { Button, EmptyState } from '../../ui/primitives';
import { useOverlay } from '../../ui/overlays';
import BuddyPanel, { type BuddyPanelSession } from './BuddyPanel';
import type { BuddyPack, BuddySnapshot } from '../shell/BuddyControls';
import glyph from '../../assets/row_bot_glyph_256.png';

function Avatar({
  conversation,
  pack,
  snapshot,
  preview = false,
}: {
  conversation: string;
  pack: BuddyPack | null;
  snapshot: BuddySnapshot;
  preview?: boolean;
}) {
  const { controller } = useRuntime();
  const [still, setStill] = useState('');
  const [motion, setMotion] = useState('');
  const [reduced, setReduced] = useState(
    () => matchMedia('(prefers-reduced-motion: reduce)').matches,
  );
  useEffect(() => {
    const media = matchMedia('(prefers-reduced-motion: reduce)');
    const changed = () => setReduced(media.matches);
    media.addEventListener('change', changed);
    return () => media.removeEventListener('change', changed);
  }, []);
  const clip =
    pack?.animation_map[snapshot.status.animation] ?? snapshot.status.animation;
  useEffect(() => {
    const abort = new AbortController(),
      urls: string[] = [];
    setStill('');
    setMotion('');
    if (pack?.available) {
      const load = async (asset: string, apply: (url: string) => void) => {
        try {
          const blob = await controller.buddyMedia(
            conversation,
            pack.id,
            asset,
            pack.revision,
            abort.signal,
          );
          if (abort.signal.aborted) return;
          const url = URL.createObjectURL(blob);
          urls.push(url);
          apply(url);
        } catch {
          /* The saved still/familiar remains available when a clip fails. */
        }
      };
      if (pack.assets.some((asset) => asset.id === 'preview'))
        void load('preview', setStill);
      const active = pack.assets.find(
        (asset) => asset.id === clip && asset.content_type === 'video/mp4',
      );
      if (
        active &&
        !preview &&
        !reduced &&
        snapshot.preferences.animation_intensity !== 'quiet'
      )
        void load(active.id, setMotion);
    }
    return () => {
      abort.abort();
      urls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [
    controller,
    conversation,
    pack,
    clip,
    reduced,
    preview,
    snapshot.preferences.animation_intensity,
  ]);
  return motion ? (
    <video
      className="buddy-avatar"
      src={motion}
      poster={still || glyph}
      autoPlay
      muted
      loop
      playsInline
      aria-hidden
      onError={() => setMotion('')}
    />
  ) : (
    <img className="buddy-avatar" src={still || glyph} alt="" />
  );
}

function OwnedBuddy({
  conversation,
  session,
  settings,
}: {
  conversation: string;
  session: BuddyPanelSession;
  settings: boolean;
}) {
  const { controller } = useRuntime();
  const navigate = useNavigate();
  const overlay = useOverlay();
  const state = useClientState();
  const view = useSyncExternalStore(session.subscribe, session.getSnapshot);
  useEffect(() => session.observe(), [session]);
  const run =
    state.conversation?.id === conversation
      ? state.conversation.generation_state?.[0]
      : undefined;
  return (
    <BuddyPanel
      scopeKey={conversation}
      session={session}
      settingsOpen={settings}
      companionVisible={!settings}
      onSettings={() => {
        overlay.close();
        navigate(
          `/settings/buddy?conversation=${encodeURIComponent(conversation)}`,
        );
      }}
      currentRunId={run?.generation_id ?? null}
      stop={async (id) => {
        const current = controller.getSnapshot();
        if (
          current.selectedConversationId !== conversation ||
          !current.conversation?.generation_state?.some(
            (value) => value.generation_id === id,
          )
        )
          throw new Error('generation_changed');
        await controller.intent(
          conversation,
          'conversation.stop',
          { generation_id: id },
          current.conversation!.revision,
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
}: {
  settings?: boolean;
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
  if (!buddyOwner?.get() || !conversation)
    return settings ? (
      <EmptyState title="Open a conversation for Buddy">
        Buddy uses that conversation’s current profile and approvals.
      </EmptyState>
    ) : null;
  let session: BuddyPanelSession;
  try {
    session = buddyOwner.get()!.get(conversation);
  } catch {
    return (
      <EmptyState title="Buddy sessions need attention">
        Finish or discard retained Buddy edits before opening another session.
        <Button onClick={() => refresh((value) => value + 1)}>
          Check Buddy sessions
        </Button>
      </EmptyState>
    );
  }
  return (
    <OwnedBuddy
      key={conversation}
      conversation={conversation}
      session={session}
      settings={settings}
    />
  );
}
