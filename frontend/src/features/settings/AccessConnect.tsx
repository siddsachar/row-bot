import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from 'react';
import {
  Check,
  Globe,
  Lock,
  QrCode as QrIcon,
  Smartphone,
  Wifi,
  X,
} from 'lucide-react';
import {
  accessClient,
  accessInvitationClient,
  accessTailscaleClient,
  type AccessClient,
  type AccessDevice,
  type AccessInvitationClient,
  type AccessRoute,
  type AccessRouteSettings,
  type AccessTailscaleClient,
} from '../../api/access';
import { clientError } from '../../api/errors';
import type { SettingsSnapshot } from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { writeClipboardText } from '../../platform/clipboard';
import { AppLink } from '../../ui/app-link';
import { useOverlay } from '../../ui/overlays';
import {
  Button,
  CopyGlyph,
  Input,
  Segmented,
  Select,
  Skeleton,
  useCopyFeedback,
} from '../../ui/primitives';
import { QrCode } from '../../ui/QrCode';
import AccessTailscale from './AccessTailscale';

/** Mint the next code this long before the current one expires. */
export const REFRESH_BEFORE_MS = 30_000;
/** Codes one open flow mints before it asks for a new one (about 40 min). */
export const MAX_CODES = 4;
/** How often the flow looks for the device while a code is open. */
export const ARRIVAL_POLL_MS = 2_000;

type Choice = 'tailscale' | 'lan' | 'internet' | 'other';
type Lifetime = 'trusted' | 'temporary';
type Code = { id: string; url: string; expiresAt: string; origin: string };
type Tunnel = SettingsSnapshot['system']['tunnel'];

function hostOf(origin: string) {
  try {
    return new URL(origin).hostname.replace(/^\[|\]$/g, '');
  } catch {
    return '';
  }
}
function isLoopback(origin: string) {
  const host = hostOf(origin);
  return host === 'localhost' || host === '::1' || host.startsWith('127.');
}
/** Same Wi-Fi addresses, most likely first: home ranges, then the rest. */
function lanRank(route: AccessRoute) {
  const host = hostOf(route.origin);
  const [a, b] = host.split('.').map(Number);
  if (host.includes(':')) return 8;
  if (a === 192 && b === 168) return 0;
  if (a === 10) return 1;
  if (a === 172 && b >= 16 && b <= 31) return 2;
  // 100.64.0.0/10 is Tailscale's own range, not the Wi-Fi.
  if (a === 100 && b >= 64 && b <= 127) return 7;
  return 5;
}
function withoutScheme(origin: string) {
  return origin.replace(/^https?:\/\//, '');
}

/** Install steps for the device that just connected (U60). */
export function installSteps(
  device: Pick<AccessDevice, 'user_agent'>,
  origin: string,
): string[] {
  if (!origin.startsWith('https://'))
    return [
      'Row-Bot opens in its browser. Installing it as an app needs a secure address (Tailscale or the internet link), so add a bookmark or a home-screen shortcut instead.',
    ];
  const agent = device.user_agent ?? '';
  if (/iPhone|iPad|iPod/.test(agent))
    return ['In Safari, tap Share.', 'Tap Add to Home Screen, then Add.'];
  if (/Android/.test(agent))
    return [
      'In Chrome, tap ⋮ at the top right.',
      'Tap Add to Home screen, or Install app.',
    ];
  return [
    'In Chrome or Edge, choose Install Row-Bot in the address bar or the ⋯ menu.',
    'Safari on a Mac: File › Add to Dock.',
  ];
}

/**
 * Settings › Devices & remote access › Connect a phone or computer (U56–U58,
 * parity rows 46, 47, 50). One guided flow: 1) how the device reaches this
 * computer, from what is detected here — Tailscale (private, HTTPS), Same
 * Wi-Fi (HTTP, browser only) or Internet (the saved public link); 2) one QR
 * code with Copy link that renews itself before its invitation expires (every
 * code is a one-time, 10-minute invitation; the previous one is cancelled);
 * 3) it waits for the device, then shows "Connected: <name> · Rename" and how
 * to install Row-Bot on it. Nothing becomes reachable without an explicit,
 * confirmed step, and closing the flow cancels the open code.
 */
export default function AccessConnect({
  client = accessInvitationClient,
  devices = accessClient,
  tailscale = accessTailscaleClient,
  tunnel,
  startPublic,
  writeClipboard,
  onConnected,
}: {
  client?: AccessInvitationClient;
  devices?: Pick<AccessClient, 'devices' | 'rename'>;
  tailscale?: AccessTailscaleClient;
  /** The public link's state, from the settings snapshot. */
  tunnel?: Tunnel;
  /** Starts the saved public link; rendered for the owner on this computer. */
  startPublic?: ReactNode;
  writeClipboard?: ClientPlatform['writeClipboard'];
  /** A device connected or was renamed: read the device list again. */
  onConnected?: () => void;
}) {
  const overlay = useOverlay();
  const lanSelectId = useId();
  const otherSelectId = useId();
  const [open, setOpen] = useState(false);
  const [routes, setRoutes] = useState<AccessRoute[] | null>(null);
  const [network, setNetwork] = useState<AccessRouteSettings | null>(null);
  const [reloadRoutes, setReloadRoutes] = useState(0);
  const [choice, setChoice] = useState<Choice | ''>('');
  const [lanId, setLanId] = useState('');
  const [otherId, setOtherId] = useState('');
  const [lifetime, setLifetime] = useState<Lifetime>('trusted');
  const [code, setCode] = useState<Code | null>(null);
  const [minted, setMinted] = useState(0);
  const [generation, setGeneration] = useState(0);
  const [expired, setExpired] = useState(false);
  const [connected, setConnected] = useState<AccessDevice | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [copied, setCopied] = useCopyFeedback();
  const live = useRef<string | null>(null);

  const cancelLive = useCallback(() => {
    const id = live.current;
    live.current = null;
    if (id) void client.cancel(id).catch(() => undefined);
  }, [client]);
  // Leaving the flow cancels the open code; so does leaving the page
  // (closing the tab or reloading runs no React cleanup).
  useEffect(() => cancelLive, [cancelLive]);
  useEffect(() => {
    const leave = () => {
      const id = live.current;
      if (id) void client.cancel(id, undefined, true).catch(() => undefined);
    };
    window.addEventListener('pagehide', leave);
    return () => window.removeEventListener('pagehide', leave);
  }, [client]);

  useEffect(() => {
    if (!open) return;
    const abort = new AbortController();
    void Promise.all([
      client.routes(abort.signal),
      client.routeSettings(abort.signal),
    ]).then(
      ([nextRoutes, nextNetwork]) => {
        if (abort.signal.aborted) return;
        setRoutes(nextRoutes);
        setNetwork(nextNetwork);
      },
      (cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [client, open, reloadRoutes, tunnel?.main_app_url]);

  const usable = (route: AccessRoute) =>
    route.available && route.eligible && Boolean(route.origin);
  const tailscaleRoute = routes?.find(
    (route) => route.kind === 'tailscale' && usable(route),
  );
  const lanRoutes = (routes ?? [])
    .filter((route) => route.kind === 'lan')
    .sort((a, b) => lanRank(a) - lanRank(b));
  const lanUsable = lanRoutes.filter(usable);
  const internetRoute = routes?.find(
    (route) => route.kind === 'ngrok' && usable(route),
  );
  // A loopback address never reaches another device (U56).
  const otherRoutes = (routes ?? []).filter(
    (route) =>
      (route.kind === 'reverse_proxy' || route.kind === 'current_server') &&
      usable(route) &&
      !isLoopback(route.origin) &&
      route.origin !== tailscaleRoute?.origin,
  );

  // Detection over questions: start on the first way that works now.
  useEffect(() => {
    if (!routes || choice) return;
    setChoice(
      tailscaleRoute
        ? 'tailscale'
        : lanUsable.length
          ? 'lan'
          : internetRoute
            ? 'internet'
            : otherRoutes.length
              ? 'other'
              : 'tailscale',
    );
  }, [
    routes,
    choice,
    tailscaleRoute,
    lanUsable.length,
    internetRoute,
    otherRoutes.length,
  ]);

  const lanRoute =
    lanUsable.find((route) => route.id === lanId) ?? lanUsable[0] ?? null;
  const otherRoute =
    otherRoutes.find((route) => route.id === otherId) ?? otherRoutes[0] ?? null;
  const route =
    choice === 'tailscale'
      ? tailscaleRoute
      : choice === 'lan'
        ? lanRoute
        : choice === 'internet'
          ? internetRoute
          : choice === 'other'
            ? otherRoute
            : null;
  const routeId = open && !connected && !expired ? (route?.id ?? '') : '';

  // Step 2: one code at a time for the chosen way; a newer one replaces it.
  useEffect(() => {
    if (!routeId) {
      cancelLive();
      setCode(null);
      return;
    }
    let current = true;
    setError('');
    void client.create(routeId, lifetime).then(
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
        setError(failure.message);
        if (failure.code === 'route_changed')
          setReloadRoutes((value) => value + 1);
      },
    );
    return () => {
      current = false;
    };
  }, [client, routeId, lifetime, generation, cancelLive]);

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

  // Step 3: wait for the device.
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
          setName(device.display_name);
          onConnected?.();
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
  }, [client, devices, code, connected, onConnected]);

  function reset() {
    cancelLive();
    setCode(null);
    setMinted(0);
    setExpired(false);
    setConnected(null);
    setRenaming(false);
    setNotice('');
    setError('');
  }

  function close() {
    reset();
    setOpen(false);
    setChoice('');
  }

  async function rename() {
    if (!connected || !name.trim() || busy) return;
    setBusy(true);
    setError('');
    try {
      const device = await devices.rename(connected.id, name.trim());
      setConnected(device);
      setRenaming(false);
      onConnected?.();
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  function allowNetwork(event: MouseEvent<HTMLButtonElement>) {
    if (!network) return;
    overlay.open({
      kind: 'alert',
      returnFocusTo: event.currentTarget,
      title: 'Let devices on your network reach Row-Bot?',
      description:
        'Row-Bot restarts and listens on your network. Anyone there can reach its sign-in page; only a device with a code from this page gets in. Change it back any time under Advanced.',
      confirmLabel: 'Restart and allow',
      onConfirm: () => {
        overlay.close();
        setBusy(true);
        void client
          .setListenMode(network.listen_mode, 'local_network')
          .then(
            (result) =>
              setNotice(
                result.restart_required
                  ? 'Saved. Restart Row-Bot, then come back here.'
                  : 'Row-Bot is restarting. This page reconnects by itself.',
              ),
            (cause) => setError(clientError(cause).message),
          )
          .finally(() => {
            setBusy(false);
            setReloadRoutes((value) => value + 1);
          });
      },
    });
  }

  const tunnelWords = !tunnel
    ? ''
    : tunnel.runtime_state === 'failed'
      ? `The public link didn’t start: ${tunnel.last_error ?? 'no reason given'}`
      : tunnel.runtime_state === 'not_configured' ||
          !tunnel.credential.configured
        ? 'Needs an ngrok token first.'
        : 'Set up, not running.';

  const option = (
    value: Choice,
    title: string,
    tagline: string,
    Icon: typeof Lock,
    ready: boolean,
  ) => (
    <label className="access-option" data-ready={ready || undefined}>
      <input
        type="radio"
        name="access-way"
        value={value}
        checked={choice === value}
        onChange={() => {
          reset();
          setChoice(value);
        }}
      />
      <span className="access-option-icon" aria-hidden>
        <Icon size={18} aria-hidden />
      </span>
      <span className="access-option-text">
        <span className="access-option-title">{title}</span>
        <span className="access-option-tagline">{tagline}</span>
      </span>
      {ready && (
        <span className="access-option-ready">
          <Check size={14} aria-hidden /> Ready
        </span>
      )}
    </label>
  );

  return (
    <section
      className="settings-section-flat access-connect"
      aria-labelledby="access-connect-title"
      data-setting-anchor="connect"
    >
      <header className="settings-section-head">
        <span className="settings-section-icon" aria-hidden>
          <Smartphone size={16} aria-hidden />
        </span>
        <div className="settings-section-text">
          <h3 id="access-connect-title">Connect a phone or computer</h3>
          <p>
            Scan a code with the other device’s camera. It signs in once and
            stays signed in.
          </p>
        </div>
        {open && (
          <div className="settings-section-actions">
            <Button
              iconOnly
              variant="ghost"
              aria-label="Close connect a phone or computer"
              title="Close"
              onClick={close}
            >
              <X size={16} aria-hidden />
            </Button>
          </div>
        )}
      </header>
      {!open ? (
        <div className="access-connect-start">
          <Button variant="primary" onClick={() => setOpen(true)}>
            <QrIcon size={16} aria-hidden /> Connect a phone or computer
          </Button>
        </div>
      ) : !routes ? (
        error ? (
          <p role="alert">{error}</p>
        ) : (
          <Skeleton label="Looking for ways to reach this computer" />
        )
      ) : (
        <ol className="access-steps">
          <li className="access-step">
            <h4>How will it reach this computer?</h4>
            <div
              role="radiogroup"
              aria-label="How it reaches this computer"
              className="access-options"
            >
              {option(
                'tailscale',
                'Tailscale',
                'Private and encrypted (HTTPS). Recommended; installs as an app.',
                Lock,
                Boolean(tailscaleRoute),
              )}
              {option(
                'lan',
                'Same Wi-Fi',
                'At home or the office. Opens in the browser (HTTP).',
                Wifi,
                lanUsable.length > 0,
              )}
              {option(
                'internet',
                'Internet',
                'From anywhere, through your public link (ngrok).',
                Globe,
                Boolean(internetRoute),
              )}
              {otherRoutes.length > 0 &&
                option(
                  'other',
                  'Another address',
                  'An address you set up yourself.',
                  Globe,
                  true,
                )}
            </div>
            <div className="access-option-panel">
              {choice === 'tailscale' && (
                <AccessTailscale
                  client={tailscale}
                  onChanged={() => setReloadRoutes((value) => value + 1)}
                  writeClipboard={writeClipboard}
                />
              )}
              {choice === 'lan' &&
                (network?.listen_mode === 'local_only' ? (
                  <div className="access-option-detail">
                    <p className="settings-help">
                      Row-Bot only listens on this computer now.
                    </p>
                    {network.can_manage_routes ? (
                      <div className="button-row">
                        <Button
                          variant="primary"
                          disabled={busy}
                          onClick={allowNetwork}
                        >
                          Allow on my network…
                        </Button>
                      </div>
                    ) : (
                      <p className="settings-help">
                        The owner can allow it on the computer running Row-Bot.
                      </p>
                    )}
                  </div>
                ) : network?.listening_on_network === false ? (
                  // B184: the saved mode says network, the launch says no.
                  <p className="settings-help">
                    Row-Bot was started to listen on this computer only, so Same
                    Wi-Fi can’t reach it now. Restart Row-Bot normally to use
                    Same Wi-Fi.
                  </p>
                ) : lanRoute ? (
                  <div className="access-option-detail">
                    <ol className="access-next-steps">
                      <li>
                        Connect the other device to the same Wi-Fi as this
                        computer.
                      </li>
                      <li>
                        Scan the code. Row-Bot opens in its browser; the
                        connection isn’t encrypted (HTTP).
                      </li>
                    </ol>
                    {lanUsable.length > 1 && (
                      <div className="access-inline-field">
                        <label htmlFor={lanSelectId}>Address</label>
                        <Select
                          id={lanSelectId}
                          value={lanRoute.id}
                          onChange={(event) => setLanId(event.target.value)}
                        >
                          {lanUsable.map((item) => (
                            <option key={item.id} value={item.id}>
                              {withoutScheme(item.origin)}
                            </option>
                          ))}
                        </Select>
                      </div>
                    )}
                    {lanRoute.warning && lanRank(lanRoute) >= 5 && (
                      <p className="settings-help" data-tone="warning">
                        {lanRoute.warning}
                      </p>
                    )}
                  </div>
                ) : (
                  <p className="settings-help">
                    This computer has no network address right now. Check that
                    it is connected to Wi-Fi.
                  </p>
                ))}
              {choice === 'internet' &&
                (internetRoute ? (
                  <p className="settings-help">
                    Public at {withoutScheme(internetRoute.origin)}. Anyone can
                    reach the sign-in page; only a device with this code gets
                    in. Stop the link under Advanced.
                  </p>
                ) : (
                  <div className="access-option-detail">
                    <p className="settings-help">{tunnelWords}</p>
                    {tunnel?.credential.configured &&
                    tunnel.local_owner_control_available
                      ? startPublic
                      : tunnel && !tunnel.credential.configured
                        ? tunnel.local_owner_control_available && (
                            <p className="settings-help">
                              <AppLink to="/settings/access#tunnel">
                                Add your ngrok token under Advanced
                              </AppLink>
                              , then come back here.
                            </p>
                          )
                        : null}
                    {tunnel && !tunnel.local_owner_control_available && (
                      <p className="settings-help">
                        The owner can start it on the computer running Row-Bot.
                      </p>
                    )}
                  </div>
                ))}
              {choice === 'other' && otherRoute && (
                <div className="access-option-detail">
                  {otherRoutes.length > 1 ? (
                    <div className="access-inline-field">
                      <label htmlFor={otherSelectId}>Address</label>
                      <Select
                        id={otherSelectId}
                        value={otherRoute.id}
                        onChange={(event) => setOtherId(event.target.value)}
                      >
                        {otherRoutes.map((item) => (
                          <option key={item.id} value={item.id}>
                            {withoutScheme(item.origin)}
                          </option>
                        ))}
                      </Select>
                    </div>
                  ) : (
                    <p className="settings-help">
                      Opens {withoutScheme(otherRoute.origin)}.
                    </p>
                  )}
                  {otherRoute.warning && (
                    <p className="settings-help" data-tone="warning">
                      {otherRoute.warning}
                    </p>
                  )}
                </div>
              )}
            </div>
          </li>
          <li className="access-step">
            <h4>Scan the code</h4>
            {connected ? (
              <p className="settings-help">Used. Codes work only once.</p>
            ) : !route ? (
              <p className="settings-help">
                The code appears here once the way above is ready.
              </p>
            ) : expired ? (
              <div className="access-option-detail">
                <p className="settings-help">This code expired.</p>
                <div className="button-row">
                  <Button onClick={() => reset()}>Show a new code</Button>
                </div>
              </div>
            ) : code ? (
              <div className="access-code">
                <QrCode
                  value={code.url}
                  label="QR code to connect a phone or computer"
                  size={184}
                />
                <div className="access-code-text">
                  <p>
                    Point the other device’s camera at the code, open the link
                    and choose Connect. It opens{' '}
                    <strong className="settings-break-word">
                      {withoutScheme(code.origin)}
                    </strong>
                    .
                  </p>
                  <p className="settings-help">
                    Works once. It renews by itself while this is open.
                  </p>
                  <div className="button-row">
                    <Button
                      onClick={() =>
                        void writeClipboardText(code.url, writeClipboard).then(
                          (done) =>
                            done
                              ? setCopied(true)
                              : setError(
                                  'Row-Bot couldn’t copy the link. Scan the code instead.',
                                ),
                        )
                      }
                    >
                      <CopyGlyph copied={copied} />{' '}
                      {copied ? 'Copied' : 'Copy link'}
                    </Button>
                  </div>
                  <Segmented
                    label="Stay signed in"
                    size="sm"
                    value={lifetime}
                    onChange={(value) => {
                      setMinted(0);
                      setLifetime(value);
                    }}
                    options={[
                      { value: 'trusted', label: '30 days' },
                      { value: 'temporary', label: '12 hours' },
                    ]}
                  />
                </div>
              </div>
            ) : (
              <Skeleton label="Making a code" />
            )}
          </li>
          <li className="access-step">
            <h4>Connected</h4>
            {connected ? (
              <div className="access-connected">
                {renaming ? (
                  <form
                    className="access-rename"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void rename();
                    }}
                  >
                    <label
                      className="visually-hidden"
                      htmlFor={`${lanSelectId}-name`}
                    >
                      Device name
                    </label>
                    <Input
                      id={`${lanSelectId}-name`}
                      value={name}
                      maxLength={80}
                      autoFocus
                      onChange={(event) => setName(event.target.value)}
                    />
                    <Button
                      type="submit"
                      variant="primary"
                      disabled={busy || !name.trim()}
                    >
                      Save
                    </Button>
                    <Button variant="ghost" onClick={() => setRenaming(false)}>
                      Cancel
                    </Button>
                  </form>
                ) : (
                  <p role="status" className="access-connected-line">
                    <Check size={16} aria-hidden /> Connected:{' '}
                    <strong>{connected.display_name}</strong>
                    <Button
                      variant="ghost"
                      className="small"
                      onClick={() => setRenaming(true)}
                    >
                      Rename
                    </Button>
                  </p>
                )}
                <p className="settings-help">
                  To keep Row-Bot on its home screen:
                </p>
                <ol className="access-next-steps">
                  {installSteps(
                    connected,
                    code?.origin ?? route?.origin ?? '',
                  ).map((step) => (
                    <li key={step}>{step}</li>
                  ))}
                </ol>
                <div className="button-row">
                  <Button variant="primary" onClick={close}>
                    Done
                  </Button>
                  <Button onClick={reset}>Connect another</Button>
                </div>
              </div>
            ) : code ? (
              <p role="status" className="settings-help access-waiting">
                Waiting for the device…
              </p>
            ) : (
              <p className="settings-help">
                The device shows up here as soon as it connects.
              </p>
            )}
          </li>
        </ol>
      )}
      {notice && <p role="status">{notice}</p>}
      {open && routes && error && <p role="alert">{error}</p>}
    </section>
  );
}
