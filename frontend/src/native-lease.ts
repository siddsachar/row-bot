import type { ClientController } from './api/controller';
import type { ClientPlatform } from './platform';

/** Renew well inside the bridge's 30-minute document lease. */
export const NATIVE_RENEW_MS = 20 * 60 * 1000;
const CHECK_MS = 60 * 1000;

/**
 * Keep a native window's bridge alive (B99). Its lease lapses 30 minutes
 * after the last attestation it exchanged, so every 20 minutes (checked each
 * minute, on focus and when shown) the window handshakes again on its
 * session for a fresh attestation and exchanges it. Browsers do nothing.
 */
export function keepNativeLease(
  controller: Pick<ClientController, 'nativeAttestation'>,
  platform: Pick<ClientPlatform, 'renewNative'>,
  target: Window = window,
  now: () => number = () => Date.now(),
): () => void {
  const renewNative = platform.renewNative;
  if (!renewNative) return () => {};
  let last = now();
  let running = false;
  let active = true;
  const renew = async () => {
    running = true;
    try {
      const attestation = await controller.nativeAttestation();
      if (!active || !attestation) return;
      const result = await renewNative(attestation);
      if (result.status === 'ok') last = now();
    } catch {
      /* Try again at the next check; the current lease is untouched. */
    } finally {
      running = false;
    }
  };
  const due = () => {
    if (active && !running && now() - last >= NATIVE_RENEW_MS) void renew();
  };
  const timer = target.setInterval(due, CHECK_MS);
  target.addEventListener('focus', due);
  target.document.addEventListener('visibilitychange', due);
  return () => {
    active = false;
    target.clearInterval(timer);
    target.removeEventListener('focus', due);
    target.document.removeEventListener('visibilitychange', due);
  };
}

/**
 * The desktop Buddy holds no unsaved state (drafts save as they are typed),
 * so when its grant stays refused after a fresh attestation it simply loads
 * again. At most once a minute. (A lapsed lease never reaches here: the
 * desktop platform binds the window again by itself, B231.)
 */
export function reloadWhenLeaseLapses<T extends ClientPlatform>(
  platform: T,
  reload: () => void,
  now: () => number = () => Date.now(),
): T {
  let reloadedAt = -Infinity;
  const watch =
    <A extends unknown[], R>(operation: (...args: A) => Promise<R>) =>
    async (...args: A): Promise<R> => {
      const result = await operation(...args);
      const value = result as { status?: string; reason?: string };
      if (
        value?.status === 'unavailable' &&
        value.reason === 'native_authentication_required' &&
        now() - reloadedAt > CHECK_MS
      ) {
        reloadedAt = now();
        reload();
      }
      return result;
    };
  return {
    ...platform,
    buddyPlacement: watch(platform.buddyPlacement),
    readBuddyTarget: watch(platform.readBuddyTarget),
    showMainWindow: watch(platform.showMainWindow),
  };
}
