import { useEffect, useId, useState } from 'react';
import { Bell, BellRing } from 'lucide-react';
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

/** What a notification on this device is for and what it sends. */
export const DEVICE_NOTICE_WORDS =
  'When Row-Bot needs you, such as for an approval or a question, this device shows a notice. It travels encrypted: only this device can read it.';

/**
 * Turn notifications on for this device: the browser asks the person first
 * (call it from their tap, so the browser shows its prompt), then Row-Bot
 * keeps the device's encrypted address. False when they didn't allow it.
 */
export async function turnOnDeviceNotifications(
  client: AccessPushClient,
  browser: PushBrowser,
  publicKey: string,
): Promise<boolean> {
  const subscription = await browser.subscribe(publicKey);
  if (!subscription) return false;
  await client.subscribe(subscription);
  return true;
}

/** Said when the browser's permission was refused. */
export const NOT_ALLOWED =
  'Notifications weren’t allowed, so nothing was turned on.';

/**
 * Notifications on this device (D8). When Row-Bot needs the person (an
 * approval or a question), a device with this on shows a notice saying what
 * it's about, end-to-end encrypted for that device. Offered only to a device
 * that reaches Row-Bot over HTTPS; elsewhere it says why not.
 *
 * Show details in notifications is one switch for every device, so it shows
 * on this computer too: off, every notice says only "Row-Bot is waiting for
 * you".
 *
 * `row` is the phone's Settings list (C3, D13): the same switch and flow as
 * one row of This phone, saying in a line why it can't be on. The details
 * switch stays on Devices & remote access.
 */
export default function DeviceNotifications({
  client = accessPushClient,
  browser = browserPush,
  variant = 'page',
}: {
  client?: AccessPushClient;
  browser?: PushBrowser;
  variant?: 'page' | 'row';
}) {
  const id = useId();
  const detailsId = useId();
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

  // The details switch is there from the start, so a search for it lands on
  // it; it can be changed once the saved value has been read.
  const showDetails = status?.showDetails ?? true;
  const details = (
    <div data-setting-anchor="notification-details">
      <SettingRow
        label="Show details in notifications"
        htmlFor={detailsId}
        description={
          showDetails
            ? 'Notifications show what they’re about. Your phone’s lock-screen preview setting decides whether they show while it’s locked.'
            : 'Notifications say only “Row-Bot is waiting for you”.'
        }
        control={
          <Toggle
            id={detailsId}
            label="Show details in notifications"
            checked={showDetails}
            disabled={!status || busy}
            onChange={(event) =>
              void changeDetails(event.currentTarget.checked)
            }
          />
        }
      />
    </div>
  );

  async function changeDetails(next: boolean) {
    if (!status || busy) return;
    setBusy(true);
    setError('');
    try {
      const saved = await client.setShowDetails(next);
      setStatus({ ...status, showDetails: saved });
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  const failure = error && (
    <p role="alert" className="setting-row-help">
      {error}
    </p>
  );

  /** This phone's row: the switch, and one line saying what it's for or why not. */
  const row = (line: string, on: boolean, disabled: boolean) => (
    <div data-setting-anchor="notifications">
      <div className="phone-settings-notify">
        <span className="phone-row-icon" aria-hidden>
          {on ? <BellRing size={18} /> : <Bell size={18} />}
        </span>
        <span className="phone-row-text">
          <label className="phone-row-title" htmlFor={id}>
            Notifications
          </label>
          <span className="phone-row-meta" id={`${id}-line`}>
            {line}
          </span>
        </span>
        <Toggle
          id={id}
          label="Notifications"
          aria-describedby={`${id}-line`}
          checked={on}
          disabled={disabled}
          onChange={(event) => void change(event.currentTarget.checked)}
        />
      </div>
      {failure}
    </div>
  );

  if (variant === 'row' && !status)
    return row(error ? 'Can’t be read now.' : 'Checking…', false, true);
  if (variant === 'row' && status && !status.available)
    return row(
      status.reason === 'this_computer'
        ? 'Row-Bot’s own window shows them on this computer.'
        : 'Needs Row-Bot over HTTPS, such as through Tailscale.',
      false,
      true,
    );

  // The computer running Row-Bot has its own window: only the details switch.
  if (!status || (!status.available && status.reason === 'this_computer'))
    return (
      <div className="device-notifications" data-setting-anchor="notifications">
        {details}
        {failure}
      </div>
    );

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
        if (
          !(await turnOnDeviceNotifications(client, browser, status.publicKey))
        ) {
          setError(NOT_ALLOWED);
          return;
        }
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

  if (variant === 'row')
    return row(
      browser.appleNotInstalled
        ? 'Add Row-Bot to your Home Screen first.'
        : !browser.supported
          ? 'This browser can’t show them.'
          : blocked
            ? 'Blocked in this browser’s settings.'
            : 'When Row-Bot needs you',
      on,
      Boolean(why) || busy,
    );

  return (
    <div className="device-notifications" data-setting-anchor="notifications">
      <SettingRow
        label="Notifications on this device"
        htmlFor={id}
        description={why || DEVICE_NOTICE_WORDS}
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
      {details}
      {failure}
    </div>
  );
}
