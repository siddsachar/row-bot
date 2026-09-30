import { useSyncExternalStore } from 'react';
import type { ClientPlatform } from '../../platform';
import './DesktopReconnecting.css';

const alwaysReady = {
  get: () => 'ready' as const,
  subscribe: () => () => {},
};

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
  return (
    <p className="desktop-reconnecting" role="status">
      {connection === 'reconnecting'
        ? 'Desktop features are reconnecting…'
        : ''}
    </p>
  );
}
