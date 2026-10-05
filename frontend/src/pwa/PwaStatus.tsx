import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { Button } from '../ui/primitives';
import { appPwaClient, PwaClient } from './client';

export default function PwaStatus({ client }: { client?: PwaClient }) {
  const owned = client ?? appPwaClient;
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
    state.updateAvailable ||
    state.installAvailable ||
    Boolean(actionError);
  // The composer keeps clear of it (styles.css), so it needs the height,
  // which grows when the text wraps on a narrow screen.
  const status = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    const element = status.current;
    if (!visible || !element) return;
    const root = document.documentElement;
    const publish = () =>
      root.style.setProperty(
        '--pwa-status-height',
        `${element.offsetHeight}px`,
      );
    publish();
    const observer =
      typeof ResizeObserver === 'function'
        ? new ResizeObserver(publish)
        : undefined;
    observer?.observe(element);
    return () => {
      observer?.disconnect();
      root.style.removeProperty('--pwa-status-height');
    };
  }, [visible]);
  return (
    <aside
      className="pwa-status"
      ref={status}
      aria-label="App install and connection status"
      hidden={!visible}
      data-testid="pwa-status"
      data-pwa-state={state.phase}
      data-pwa-online={state.online ? 'true' : 'false'}
    >
      <span role="status" aria-live="polite">
        {state.phase === 'offline' &&
          'Row-Bot is offline. Your unsent draft stays on this device.'}
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
