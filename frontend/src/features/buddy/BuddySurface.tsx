import {
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useClientState, useRuntime } from '../../runtime';
import { useShellSettled } from '../../shell-settled';
import { Button, EmptyState, Hint, IconButton } from '../../ui/primitives';
import { PictureInPicture2 } from 'lucide-react';
import { useOverlay } from '../../ui/overlays';
import BuddyPanel, { type BuddyPanelSession } from './BuddyPanel';
import { BuddyAvatar, rememberBuddyMedia } from './BuddyAvatar';
import {
  BuddyDockButton,
  BuddyDragHandle,
  useBuddyPlacement,
} from './BuddyGesture';
import type { BuddyPack, BuddySnapshot } from '../shell/BuddyControls';
import glyph from '../../assets/row_bot_glyph_256.png';

export {
  BuddyAvatar,
  rememberBuddyMedia,
  type BuddyMediaLoader,
} from './BuddyAvatar';

function Avatar(props: Omit<Parameters<typeof BuddyAvatar>[0], 'loadMedia'>) {
  const { controller } = useRuntime();
  const loadMedia = useMemo(
    () =>
      rememberBuddyMedia(
        controller,
        (conversation, pack, asset, revision, signal) =>
          conversation
            ? controller.buddyMedia(conversation, pack, asset, revision, signal)
            : controller.globalBuddyMedia(pack, asset, revision, signal),
      ),
    [controller],
  );
  return <BuddyAvatar {...props} loadMedia={loadMedia} />;
}

function GlobalBuddy() {
  const { controller, platform } = useRuntime();
  const buddyPlacement = useBuddyPlacement(platform);
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
  if (buddyPlacement.placement === 'desktop' && buddyPlacement.visible)
    return <BuddyDockButton dock={buddyPlacement.dock} />;
  if (buddyPlacement.placement === 'desktop') return null;
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
      {/* The sidebar shows no name beside the avatar; it is the tooltip (B225). */}
      <Hint label={snapshot.preferences.display_name || 'Buddy'}>
        <Button
          aria-label="Buddy settings"
          variant="ghost"
          onClick={() => navigate('/settings/buddy')}
        >
          <BuddyDragHandle
            platform={platform}
            onTornOff={() => buddyPlacement.setPlacement('desktop')}
          >
            <Avatar conversation={null} pack={pack} snapshot={snapshot} />
          </BuddyDragHandle>
        </Button>
      </Hint>
      <span className="buddy-companion-text">
        <span className="buddy-companion-name">
          {snapshot.preferences.display_name || 'Buddy'}
        </span>
        {snapshot.preferences.bubble_verbosity !== 'quiet' &&
          !snapshot.preferences.collapsed && (
            <p role="status">
              {snapshot.status.label || 'Ready when you are.'}
            </p>
          )}
      </span>
      {buddyPlacement.supported && (
        <IconButton
          size="sm"
          className="buddy-undock"
          label="Undock Buddy"
          onClick={() => void buddyPlacement.tearOff()}
        >
          <PictureInPicture2 size={15} aria-hidden />
        </IconButton>
      )}
    </aside>
  );
}

/** Row-Bot's glyph in Buddy's frame until Buddy's view is known. */
function PortraitGlyph() {
  return (
    <span className="buddy-avatar-frame" aria-hidden="true">
      <img className="buddy-avatar" src={glyph} alt="" />
    </span>
  );
}

function GlobalPortrait() {
  const { controller } = useRuntime();
  const [view, setView] = useState<{
    snapshot: BuddySnapshot;
    pack: BuddyPack | null;
  } | null>(null);
  useEffect(() => {
    const request = new AbortController();
    void (async () => {
      try {
        const snapshot = await controller.globalBuddy(request.signal);
        const pack = await controller.globalBuddyPack(
          snapshot.preferences.pack_id,
          request.signal,
        );
        if (!request.signal.aborted) setView({ snapshot, pack });
      } catch {
        // The sidebar's Buddy reports and retries; the glyph stays here.
      }
    })();
    return () => request.abort();
  }, [controller]);
  if (!view) return <PortraitGlyph />;
  if (!view.snapshot.preferences.visible) return null;
  return (
    <Avatar conversation={null} pack={view.pack} snapshot={view.snapshot} />
  );
}

function SessionPortrait({
  conversation,
  session,
}: {
  conversation: string;
  session: BuddyPanelSession;
}) {
  const view = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const settled = useShellSettled();
  useEffect(() => {
    if (!settled) return;
    let stop: () => void;
    try {
      stop = session.observe();
    } catch {
      // Signed out: the session is revoked and shows nothing new.
      return;
    }
    // A closed phone drawer mounts no other Buddy: read the session here,
    // after a Buddy mounting now has.
    const timer = window.setTimeout(() => {
      const current = session.getSnapshot();
      if (!current.snapshot && !current.busy && !current.revoked)
        void session.load().catch(() => undefined);
    }, 0);
    return () => {
      window.clearTimeout(timer);
      stop();
    };
  }, [session, settled]);
  if (!view.snapshot) return <PortraitGlyph />;
  if (!view.snapshot.preferences.visible) return null;
  return (
    <Avatar
      conversation={conversation}
      pack={view.selectedPack}
      snapshot={view.snapshot}
    />
  );
}

/**
 * Buddy's live avatar on its own, beside Overview's greeting (B269). It is
 * the sidebar's Buddy: the open conversation's shared session (no extra
 * reads) or, with none open, the global Buddy read once.
 */
export function BuddyPortrait() {
  const { buddyOwner } = useRuntime();
  const conversation = useClientState().selectedConversationId;
  if (!conversation) return <GlobalPortrait />;
  let session: BuddyPanelSession | undefined;
  try {
    session = buddyOwner?.get()?.get(conversation);
  } catch {
    session = undefined;
  }
  return session ? (
    <SessionPortrait
      key={conversation}
      conversation={conversation}
      session={session}
    />
  ) : (
    <PortraitGlyph />
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
  const { platform } = useRuntime();
  const buddyPlacement = useBuddyPlacement(platform);
  const view = useSyncExternalStore(session.subscribe, session.getSnapshot);
  useEffect(() => session.observe(), [session]);
  return (
    <>
      {buddyPlacement.placement === 'desktop' && buddyPlacement.visible && (
        <BuddyDockButton dock={buddyPlacement.dock} />
      )}
      <BuddyPanel
        scopeKey={conversation}
        session={session}
        settingsOpen={settings}
        initialPrompt={initialPrompt}
        companionVisible={!settings && buddyPlacement.placement === 'docked'}
        onSettings={() => {
          // Navigate before closing (B17): closing first queues a history
          // step back that would land after the push and undo the route.
          navigate(
            `/settings/buddy?conversation=${encodeURIComponent(conversation)}`,
          );
          overlay.close();
        }}
        onUndock={
          buddyPlacement.supported && !settings
            ? () => void buddyPlacement.tearOff()
            : undefined
        }
        renderAvatar={(snapshot) => (
          <BuddyDragHandle
            platform={platform}
            onTornOff={() => buddyPlacement.setPlacement('desktop')}
          >
            <Avatar
              conversation={conversation}
              pack={view.selectedPack}
              snapshot={snapshot}
            />
          </BuddyDragHandle>
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
    </>
  );
}

export default function BuddySurface({
  settings = false,
  initialPrompt,
  conversationId,
}: {
  settings?: boolean;
  initialPrompt?: string;
  /** Settings: the conversation whose profile and approvals Buddy uses. */
  conversationId?: string | null;
}) {
  const { buddyOwner } = useRuntime();
  const state = useClientState();
  // Buddy's reads wait until the open conversation is confirmed (B29).
  const shellSettled = useShellSettled();
  const [search] = useSearchParams();
  const conversation =
    (settings ? (search.get('conversation') ?? conversationId) : null) ||
    state.selectedConversationId;
  const [, refresh] = useState(0);
  if (!conversation)
    return settings ? (
      <EmptyState title="Open a conversation for Buddy">
        Buddy uses that conversation’s current profile and approvals.
      </EmptyState>
    ) : (
      <GlobalBuddy />
    );
  if (!settings && buddyOwner?.get() && !shellSettled)
    return (
      <aside
        className="buddy-companion buddy-companion-state"
        aria-label="Buddy companion"
        aria-busy="true"
        data-state="loading"
      >
        <img className="buddy-state-glyph" src={glyph} alt="" />
        <p>Buddy is loading…</p>
      </aside>
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
