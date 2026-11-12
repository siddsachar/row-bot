import { useEffect, useId, useState } from 'react';
import {
  accessPushClient,
  type AccessPushClient,
  type AccessPushStatus,
} from '../../api/access';
import { clientError } from '../../api/errors';
import { SettingRow, Toggle } from '../../ui/primitives';

/** What this browser offers for notifications; injected in tests. */
export type PushBrowser = {
  supported: boolean;
  /** An iPhone or iPad browser tab: push needs Row-Bot on the Home Screen. */
  appleNotInstalled: boolean;
  permission(): NotificationPermission;
  subscribe(publicKey: string): Promise<PushSubscriptionJSON | null>;
  unsubscribe(): Promise<void>;
};

function keyBytes(publicKey: string): Uint8Array<ArrayBuffer> {
  const base64 = publicKey.replace(/-/g, '+').replace(/_/g, '/');
  const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
  return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
}

export const browserPush: PushBrowser = {
  supported:
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window,
  appleNotInstalled:
    typeof navigator !== 'undefined' &&
    /iPhone|iPad/.test(navigator.userAgent) &&
    !window.matchMedia?.('(display-mode: standalone)').matches,
  permission: () => Notification.permission,
  async subscribe(publicKey) {
    if ((await Notification.requestPermission()) !== 'granted') return null;
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: keyBytes(publicKey),
    });
    return subscription.toJSON();
  },
  async unsubscribe() {
    const registration = await navigator.serviceWorker.ready;
    await (await registration.pushManager.getSubscription())?.unsubscribe();
  },
};

/**
 * Notifications on this device (D8). When Row-Bot waits for the person (an
 * approval or a question), a device with this on shows "Row-Bot is waiting
 * for you". The wake-up carries no words from any chat. Offered only to a
 * device that reaches Row-Bot over HTTPS; elsewhere it says why not.
 */
export default function DeviceNotifications({
  client = accessPushClient,
  browser = browserPush,
}: {
  client?: AccessPushClient;
  browser?: PushBrowser;
}) {
  const id = useId();
  const [status, setStatus] = useState<AccessPushStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const abort = new AbortController();
    void client.status(abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) setStatus(value);
      },
      (cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [client]);

  // The computer running Row-Bot has its own window: nothing to offer here.
  if (!status || (!status.available && status.reason === 'this_computer'))
    return error ? <p role="alert">{error}</p> : null;

  const blocked = browser.supported && browser.permission() === 'denied';
  const why = !status.available
    ? 'Notifications need this device to reach Row-Bot over HTTPS, such as through Tailscale or a hosted Row-Bot.'
    : browser.appleNotInstalled
      ? 'On iPhone and iPad, add Row-Bot to your Home Screen first, then turn this on there.'
      : !browser.supported
        ? 'This browser can’t show notifications from Row-Bot.'
        : blocked
          ? 'Notifications are blocked for Row-Bot in this browser’s settings.'
          : '';
  const on = status.available && status.subscribed;

  async function change(next: boolean) {
    if (!status?.available || busy) return;
    setBusy(true);
    setError('');
    try {
      if (next) {
        const subscription = await browser.subscribe(status.publicKey);
        if (!subscription) {
          setError('Notifications weren’t allowed, so nothing was turned on.');
          return;
        }
        await client.subscribe(subscription);
      } else {
        await browser.unsubscribe();
        await client.unsubscribe();
      }
      setStatus({ ...status, subscribed: next });
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="device-notifications" data-setting-anchor="notifications">
      <SettingRow
        label="Notifications on this device"
        htmlFor={id}
        description={
          why ||
          'When Row-Bot waits for you, this device shows “Row-Bot is waiting for you”. The notice carries no words from your chats.'
        }
        control={
          <Toggle
            id={id}
            label="Notifications on this device"
            checked={on}
            disabled={Boolean(why) || busy}
            onChange={(event) => void change(event.currentTarget.checked)}
          />
        }
      />
      {error && (
        <p role="alert" className="setting-row-help">
          {error}
        </p>
      )}
    </div>
  );
}
