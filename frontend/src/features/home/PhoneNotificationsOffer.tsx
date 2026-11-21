import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { BellRing, Share, X } from 'lucide-react';
import {
  accessPushClient,
  type AccessPushClient,
  type AccessPushStatus,
} from '../../api/access';
import { clientError } from '../../api/errors';
import { Button, IconButton } from '../../ui/primitives';
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
const WAITED = 'row-bot:phone-notifications-waited';

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

/**
 * Whether something has waited for the person on this phone (D13): the offer
 * comes in context, the first time Home shows anything that needs them, and
 * stays until it is answered.
 */
function useWaited(waiting: number): boolean {
  const [waited, setWaited] = useState(() => {
    try {
      return localStorage.getItem(WAITED) === 'yes';
    } catch {
      return false;
    }
  });
  if (waiting > 0 && !waited) setWaited(true);
  useEffect(() => {
    if (!waited) return;
    try {
      localStorage.setItem(WAITED, 'yes');
    } catch {
      // Private browsing: it waits for the next thing that needs the person.
    }
  }, [waited]);
  return waited;
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
 * Once something has waited for the person on this phone (D13), a one-line
 * card on Home offers a notification for next time. Turn on is the Settings
 * switch's own flow; Not now is remembered on this phone, and the switch stays
 * at the top of Settings, in This phone (C3). Push works only over HTTPS, so it is never offered elsewhere;
 * an iPhone browser tab is told to add Row-Bot to the Home Screen first. Never
 * offered once notifications are on.
 */
export default function PhoneNotificationsOffer({
  waiting,
  client = accessPushClient,
  browser = browserPush,
  device: given,
}: {
  /** How many things need the person now (Home's Needs you count). */
  waiting: number;
  client?: AccessPushClient;
  browser?: PushBrowser;
  device?: OfferDevice;
}) {
  const detected = useDevice();
  const device = given ?? detected;
  // Only what waited on this phone counts.
  const waited = useWaited(device.phone ? waiting : 0);
  const offered = waited && device.phone && device.secure && !answered(device);
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
        : 'Notify me when needed';
  // One line, except where there is something to do first or to change later.
  const words =
    state === 'on' ? (
      <>
        Change it in <Link to="/settings#notifications">Settings</Link>.
      </>
    ) : iphoneTab ? (
      'On iPhone, tap Share, then Add to Home Screen, and open Row-Bot from there.'
    ) : null;
  return (
    <div
      className="phone-notify"
      role="region"
      aria-label="Phone notifications"
      aria-description={words ? undefined : DEVICE_NOTICE_WORDS}
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
        {words && <span className="overview-row-meta">{words}</span>}
      </span>
      {state !== 'on' && (
        <div className="phone-notify-actions">
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
          <IconButton label="Not now" onClick={notNow}>
            <X size={16} aria-hidden />
          </IconButton>
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
