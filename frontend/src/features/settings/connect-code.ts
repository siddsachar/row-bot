import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  AccessClient,
  AccessDevice,
  AccessInvitationClient,
} from '../../api/access';
import { clientError } from '../../api/errors';

/** Mint the next code this long before the current one expires. */
export const REFRESH_BEFORE_MS = 30_000;
/** Codes one open flow mints before it asks for a new one (about 40 min). */
export const MAX_CODES = 4;
/** How often the flow looks for the device while a code is open. */
export const ARRIVAL_POLL_MS = 2_000;

export type ConnectCode = {
  id: string;
  url: string;
  expiresAt: string;
  origin: string;
};
export type Lifetime = 'trusted' | 'temporary';

/**
 * One code to connect a phone or computer, for one way in (U56–U58, U10).
 * While `routeId` is set it makes a one-time, 10-minute invitation, renews it
 * shortly before it expires (a few times at most, then "expired"), cancels the
 * one it replaces, and cancels the open one when the way changes, the owner
 * unmounts or the page is left. It looks for the device until one uses the
 * code, then reports it.
 */
export function useConnectCode({
  client,
  devices,
  routeId,
  lifetime,
  onConnected,
  onError,
  onRouteChanged,
}: {
  client: AccessInvitationClient;
  devices: Pick<AccessClient, 'devices'>;
  /** The way the code is for; empty while nothing should be open. */
  routeId: string;
  lifetime: Lifetime;
  onConnected?: (device: AccessDevice) => void;
  onError: (message: string) => void;
  /** The way changed under the code: read the ways again. */
  onRouteChanged?: () => void;
}) {
  const [code, setCode] = useState<ConnectCode | null>(null);
  const [minted, setMinted] = useState(0);
  const [generation, setGeneration] = useState(0);
  const [expired, setExpired] = useState(false);
  const [connected, setConnected] = useState<AccessDevice | null>(null);
  const live = useRef<string | null>(null);
  const report = useRef(onError);
  report.current = onError;
  const routeChanged = useRef(onRouteChanged);
  routeChanged.current = onRouteChanged;

  const cancelLive = useCallback(() => {
    const id = live.current;
    live.current = null;
    if (id) void client.cancel(id).catch(() => undefined);
  }, [client]);
  // Leaving cancels the open code; so does leaving the page (closing the tab
  // or reloading runs no React cleanup).
  useEffect(() => cancelLive, [cancelLive]);
  useEffect(() => {
    const leave = () => {
      const id = live.current;
      if (id) void client.cancel(id, undefined, true).catch(() => undefined);
    };
    window.addEventListener('pagehide', leave);
    return () => window.removeEventListener('pagehide', leave);
  }, [client]);

  const wanted = !connected && !expired ? routeId : '';

  // One code at a time for the chosen way; a newer one replaces it.
  useEffect(() => {
    if (!wanted) {
      cancelLive();
      setCode(null);
      return;
    }
    let current = true;
    report.current('');
    void client.create(wanted, lifetime).then(
      (result) => {
        if (!current) {
          void client.cancel(result.invitation.id).catch(() => undefined);
          return;
        }
        const previous = live.current;
        live.current = result.invitation.id;
        setCode({
          id: result.invitation.id,
          url: result.url,
          expiresAt: result.invitation.expires_at,
          origin: result.invitation.intended_origin,
        });
        setMinted((count) => count + 1);
        // The code on screen is the only one that works.
        if (previous) void client.cancel(previous).catch(() => undefined);
      },
      (cause) => {
        if (!current) return;
        const failure = clientError(cause);
        report.current(failure.message);
        if (failure.code === 'route_changed') routeChanged.current?.();
      },
    );
    return () => {
      current = false;
    };
  }, [client, wanted, lifetime, generation, cancelLive]);

  // The code renews itself shortly before it expires, a few times at most.
  useEffect(() => {
    if (!code || connected) return;
    const wait = Math.max(
      0,
      Date.parse(code.expiresAt) - REFRESH_BEFORE_MS - Date.now(),
    );
    const timer = window.setTimeout(() => {
      if (minted >= MAX_CODES) {
        cancelLive();
        setCode(null);
        setExpired(true);
      } else setGeneration((value) => value + 1);
    }, wait);
    return () => window.clearTimeout(timer);
  }, [code, connected, minted, cancelLive]);

  // Wait for the device.
  const arrived = useRef(onConnected);
  arrived.current = onConnected;
  useEffect(() => {
    if (!code || connected) return;
    let looking = false;
    let active = true;
    const timer = window.setInterval(async () => {
      if (looking) return;
      looking = true;
      try {
        const mine = (await client.invitations()).find(
          (item) => item.id === code.id,
        );
        if (!active || !mine?.claimed_at) return;
        live.current = null;
        const list = await devices.devices();
        if (!active) return;
        const device =
          list.find((item) => item.id === mine.claimed_device_id) ??
          [...list]
            .filter((item) => !item.revoked_at)
            .sort(
              (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
            )[0];
        if (device) {
          setConnected(device);
          arrived.current?.(device);
        }
      } catch {
        /* Look again on the next tick. */
      } finally {
        looking = false;
      }
    }, ARRIVAL_POLL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [client, devices, code, connected]);

  /** Back to no code: cancels the open one; a set way makes a new one. */
  const reset = useCallback(() => {
    cancelLive();
    setCode(null);
    setMinted(0);
    setExpired(false);
    setConnected(null);
  }, [cancelLive]);

  return {
    code,
    expired,
    connected,
    setConnected,
    reset,
    /** A new lifetime starts the renewal count again. */
    restartCount: () => setMinted(0),
  };
}
