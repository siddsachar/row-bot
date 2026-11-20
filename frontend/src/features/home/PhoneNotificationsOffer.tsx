import { useEffect, useState, type CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { BellRing, Share } from 'lucide-react';
import {
  accessPushClient,
  type AccessPushClient,
  type AccessPushStatus,
} from '../../api/access';
import { clientError } from '../../api/errors';
import { Button } from '../../ui/primitives';
import { PHONE_LAYOUT, useMedia } from '../../ui/use-media';
import {
  browserPush,
  DEVICE_NOTICE_WORDS,
  NOT_ALLOWED,
  turnOnDeviceNotifications,
  type PushBrowser,
} from '../settings/DeviceNotifications';

/** Where this Home is open; injected in tests. */
export type OfferDevice = {
  /** A touch screen under 768px. */
  phone: boolean;
  /** HTTPS (or this computer): the only place a browser offers push. */
  secure: boolean;
  /** Opened from the Home Screen rather than a browser tab. */
  homeScreen: boolean;
};

const ANSWERED = 'row-bot:phone-notifications-offer';

function answeredKey(device: OfferDevice) {
  // The Home Screen app asks again after a browser tab's "Not now": that is
  // where an iPhone can turn them on.
  return device.homeScreen ? `${ANSWERED}:home-screen` : ANSWERED;
}

function answered(device: OfferDevice) {
  try {
    return localStorage.getItem(answeredKey(device)) === 'not-now';
  } catch {
    return false;
  }
}

function useDevice(): OfferDevice {
  const narrow = useMedia(PHONE_LAYOUT);
  const touch = useMedia('(pointer: coarse)');
  const homeScreen = useMedia('(display-mode: standalone)');
  return {
    phone: narrow && touch,
    secure: typeof window !== 'undefined' && window.isSecureContext,
    homeScreen,
  };
}

/**
 * The first time Row-Bot opens on a phone, one card on Home asks whether
 * this phone should get a notification when Row-Bot needs the person. Turn
 * on is the Settings switch's own flow; Not now is remembered on this phone,
 * and the switch stays in Settings › Devices & remote access. Never offered
 * where push can't work, or once it is on.
 */
export default function PhoneNotificationsOffer({
  client = accessPushClient,
  browser = browserPush,
  device: given,
}: {
  client?: AccessPushClient;
  browser?: PushBrowser;
  device?: OfferDevice;
}) {
  const detected = useDevice();
  const device = given ?? detected;
  const offered = device.phone && device.secure && !answered(device);
  const [status, setStatus] = useState<AccessPushStatus | null>(null);
  const [state, setState] = useState<'asking' | 'busy' | 'on' | 'not-now'>(
    'asking',
  );
  const [error, setError] = useState('');

  useEffect(() => {
    if (!offered) return;
    const abort = new AbortController();
    void client.status(abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) setStatus(value);
      },
      () => undefined,
    );
    return () => abort.abort();
  }, [client, offered]);

  if (!offered || state === 'not-now' || !status?.available) return null;
  const iphoneTab = browser.appleNotInstalled;
  if (
    state !== 'on' &&
    !error &&
    !iphoneTab &&
    (!browser.supported ||
      status.subscribed ||
      browser.permission() === 'denied')
  )
    return null;

  function notNow() {
    try {
      localStorage.setItem(answeredKey(device), 'not-now');
    } catch {
      // Private browsing: it asks again next time.
    }
    setState('not-now');
  }

  async function turnOn() {
    if (!status?.available || state === 'busy') return;
    setState('busy');
    setError('');
    try {
      // Straight from the tap, so the browser shows its permission prompt.
      if (await turnOnDeviceNotifications(client, browser, status.publicKey))
        setState('on');
      else {
        setError(NOT_ALLOWED);
        setState('asking');
      }
    } catch (cause) {
      setError(clientError(cause).message);
      setState('asking');
    }
  }

  const title =
    state === 'on'
      ? 'Notifications are on for this phone'
      : iphoneTab
        ? 'Add Row-Bot to your Home Screen first'
        : 'Get a notification when Row-Bot needs you?';
  const words =
    state === 'on' ? (
      <>
        Change it in{' '}
        <Link to="/settings/access#notifications">
          Devices &amp; remote access
        </Link>
        .
      </>
    ) : iphoneTab ? (
      'On iPhone, notifications need Row-Bot on your Home Screen: tap Share, then Add to Home Screen. Row-Bot asks again when you open it from there.'
    ) : (
      DEVICE_NOTICE_WORDS
    );
  return (
    <div
      className="overview-card overview-cell overview-notify"
      role="region"
      aria-label="Phone notifications"
      style={{ '--overview-order': 1 } as CSSProperties}
    >
      <span className="overview-tile" data-tone="accent" aria-hidden>
        {iphoneTab && state !== 'on' ? (
          <Share size={16} />
        ) : (
          <BellRing size={16} />
        )}
      </span>
      <span className="overview-row-text">
        <strong className="overview-row-title">{title}</strong>
        <span className="overview-row-meta">{words}</span>
      </span>
      {state !== 'on' && (
        <div className="overview-notify-actions">
          <Button variant="ghost" className="small" onClick={notNow}>
            Not now
          </Button>
          {!iphoneTab && (
            <Button
              variant="primary"
              className="small"
              disabled={state === 'busy'}
              onClick={() => void turnOn()}
            >
              Turn on
            </Button>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="overview-setup-error">
          {error}
        </p>
      )}
    </div>
  );
}
