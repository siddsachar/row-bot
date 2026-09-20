import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { Button } from '../ui/primitives';
import { PwaClient } from './client';

export default function PwaStatus({ client }: { client?: PwaClient }) {
  const owned = useMemo(() => client ?? new PwaClient(), [client]);
  const state = useSyncExternalStore(
    owned.subscribe,
    owned.getSnapshot,
    owned.getSnapshot,
  );
  const [actionError, setActionError] = useState('');
  useEffect(() => {
    void owned.start();
    return () => owned.dispose();
  }, [owned]);
  const visible =
    state.phase === 'offline' ||
    state.phase === 'error' ||
    state.updateAvailable ||
    state.installAvailable ||
    Boolean(actionError);
  return (
    <aside
      className="pwa-status"
      aria-label="App install and connection status"
      hidden={!visible}
      data-testid="pwa-status"
      data-pwa-state={state.phase}
      data-pwa-online={state.online ? 'true' : 'false'}
    >
      <span role="status" aria-live="polite">
        {state.phase === 'offline' &&
          'Row-Bot is offline. Your unsent draft stays on this device.'}
        {state.phase === 'error' &&
          'Install and offline support are unavailable in this browser.'}
        {actionError}
      </span>
      {state.installAvailable && (
        <Button
          data-testid="pwa-install"
          onClick={() => {
            void owned.requestInstall().then((result) => {
              setActionError(
                result === 'user_activation_required'
                  ? 'Choose Install again from a direct click.'
                  : '',
              );
            });
          }}
        >
          Install app
        </Button>
      )}
      {state.updateAvailable && (
        <Button
          data-testid="pwa-apply-update"
          onClick={() => {
            const result = owned.applyUpdate();
            setActionError(
              result === 'user_activation_required'
                ? 'Choose Update again from a direct click.'
                : result === 'unavailable'
                  ? 'The update is no longer waiting.'
                  : '',
            );
          }}
        >
          Update and reload
        </Button>
      )}
    </aside>
  );
}
