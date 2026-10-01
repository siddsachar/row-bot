import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { useRuntime } from '../../runtime';
import { useShellSettled } from '../../shell-settled';
import { rememberBuddyMedia, revokeLater } from './BuddyAvatar';
import type { BuddyPanelSession } from './BuddyPanel';

const noSession = {
  subscribe: () => () => {},
  getSnapshot: () => null,
};

/**
 * The selected Buddy pack's still image in a conversation, as an object URL
 * ('' until it is known; B271). It shares the conversation's Buddy session
 * and downloaded media with the sidebar's Buddy, so it follows a change of
 * pack and spends no extra reads while that Buddy is on screen.
 */
export function useBuddyStill(conversation: string | null): string {
  const { controller, buddyOwner } = useRuntime();
  // Buddy's reads wait until the open conversation is confirmed (B29).
  const settled = useShellSettled();
  let session: BuddyPanelSession | null;
  try {
    session = conversation
      ? (buddyOwner?.get()?.get(conversation) ?? null)
      : null;
  } catch {
    // Signed out or a full session list: Row-Bot's own glyph stands in.
    session = null;
  }
  const view = useSyncExternalStore(
    session?.subscribe ?? noSession.subscribe,
    session?.getSnapshot ?? noSession.getSnapshot,
  );
  useEffect(() => {
    if (!settled || !session) return;
    // Phones keep the sidebar's Buddy in a closed drawer, so nothing else
    // reads the session: read it here, after a Buddy mounting now has.
    const timer = window.setTimeout(() => {
      const current = session.getSnapshot();
      if (!current.snapshot && !current.busy && !current.revoked)
        void session.load().catch(() => undefined);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [session, settled]);
  const pack = view?.selectedPack;
  const packId =
    pack?.available && pack.assets.some((asset) => asset.id === 'preview')
      ? pack.id
      : '';
  const revision = pack?.revision ?? '';
  const loadMedia = useMemo(
    () =>
      rememberBuddyMedia(controller, (owner, packRef, asset, version, signal) =>
        controller.buddyMedia(owner!, packRef, asset, version, signal),
      ),
    [controller],
  );
  const [still, setStill] = useState({ key: '', url: '' });
  const key = `${conversation}\u0000${packId}\u0000${revision}`;
  useEffect(() => {
    if (!conversation || !packId) return;
    const abort = new AbortController();
    let url = '';
    void loadMedia(conversation, packId, 'preview', revision, abort.signal)
      .then((blob) => {
        if (abort.signal.aborted) return;
        url = URL.createObjectURL(blob);
        setStill({ key, url });
      })
      .catch(() => undefined);
    return () => {
      abort.abort();
      // The still may show it until the next one commits.
      if (url) revokeLater(url);
    };
  }, [conversation, key, loadMedia, packId, revision]);
  return still.key === key ? still.url : '';
}
