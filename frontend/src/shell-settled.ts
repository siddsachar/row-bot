import { useEffect, useState } from 'react';
import { useClientState, useRuntime } from './runtime';

/** Shell reads start anyway if a snapshot has not been confirmed by then. */
const SETTLE_FALLBACK_MS = 3000;

/**
 * True once this handshake has a confirmed snapshot for the selected
 * conversation (or none is selected). Shell reads that do not render the
 * conversation (Buddy, voice capability, profile counts) wait for it so they
 * never compete with open and subscribe (B29). A hidden tab or a failed open
 * settles after a short fallback instead of waiting forever.
 */
export function useShellSettled(): boolean {
  const state = useClientState();
  const handshake = state.handshake;
  const ready = state.status === 'ready';
  const confirmed =
    ready && (!state.selectedConversationId || state.connection !== 'none');
  const [settled, setSettled] = useState<object | null>(null);
  useEffect(() => {
    if (!handshake || !ready) return;
    if (confirmed) {
      setSettled(handshake);
      return;
    }
    const timer = window.setTimeout(
      () => setSettled(handshake),
      SETTLE_FALLBACK_MS,
    );
    return () => window.clearTimeout(timer);
  }, [handshake, ready, confirmed]);
  return Boolean(handshake) && settled === handshake;
}

/**
 * True once the window's first screen is up (`controller.afterFirstScreen`):
 * the list and the open conversation are read, and a moment has passed. Reads
 * for what the first screen doesn't show (the sidebar's Monitor counts, Buddy,
 * Proactive, voice, profiles) wait for it, so the first screen's own reads
 * never queue behind them; a screen showing them reads at once.
 */
export function useBackgroundReady(): boolean {
  const { controller } = useRuntime();
  const [ready, setReady] = useState(() => controller.firstScreenSettled);
  useEffect(() => {
    if (ready) return;
    let alive = true;
    void controller.afterFirstScreen().then(() => {
      if (alive) setReady(true);
    });
    return () => {
      alive = false;
    };
  }, [controller, ready]);
  return ready;
}

/**
 * The settled server identity. A reconnect that resumes the same instance,
 * epoch and client session keeps the value, so identity-scoped reads are not
 * repeated; a different identity is published only after it settles.
 */
export function useSettledIdentity(): string {
  const handshake = useClientState().handshake;
  const identity = handshake
    ? JSON.stringify([
        handshake.instance_id,
        handshake.server_epoch,
        handshake.client_session_id,
      ])
    : '';
  const settled = useShellSettled();
  const [value, setValue] = useState('');
  useEffect(() => {
    if (settled && identity) setValue(identity);
  }, [settled, identity]);
  return value;
}
