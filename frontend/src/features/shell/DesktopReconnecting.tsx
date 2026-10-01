import { useEffect, useState, useSyncExternalStore } from 'react';
import type { ClientPlatform } from '../../platform';
import './DesktopReconnecting.css';

const alwaysReady = {
  get: () => 'ready' as const,
  subscribe: () => () => {},
};

/**
 * How long a window may take to bind again before it says so: a binding a
 * background check (the lease renewal, Buddy's status) finds lost usually
 * comes back well within it.
 */
export const RECONNECTING_NOTICE_DELAY_MS = 2000;

/**
 * Says so while a desktop window binds its native features again (B231,
 * B238): attaching, saving and Buddy never fail silently meanwhile. The live
 * region stays mounted so the change is announced.
 */
export default function DesktopReconnecting({
  platform,
}: {
  platform: ClientPlatform;
}) {
  const source = platform.nativeConnection ?? alwaysReady;
  const connection = useSyncExternalStore(
    source.subscribe,
    source.get,
    source.get,
  );
  const [overdue, setOverdue] = useState(false);
  useEffect(() => {
    if (connection !== 'reconnecting') return;
    const timer = window.setTimeout(
      () => setOverdue(true),
      RECONNECTING_NOTICE_DELAY_MS,
    );
    return () => {
      window.clearTimeout(timer);
      setOverdue(false);
    };
  }, [connection]);
  return (
    <p className="desktop-reconnecting" role="status">
      {connection === 'reconnecting' && overdue
        ? 'Desktop features are reconnecting…'
        : ''}
    </p>
  );
}
