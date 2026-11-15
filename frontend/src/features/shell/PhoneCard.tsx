import { useEffect, useState, useSyncExternalStore } from 'react';
import { Check, QrCode as QrIcon, Smartphone, X } from 'lucide-react';
import {
  accessClient,
  accessInvitationClient,
  type AccessClient,
  type AccessInvitationClient,
  type AccessRoute,
} from '../../api/access';
import type { OnboardingSnapshot } from '../../api/types';
import { useRuntime } from '../../runtime';
import { appPwaClient, type PwaClient } from '../../pwa/client';
import { AppLink } from '../../ui/app-link';
import { Button, IconButton, Skeleton } from '../../ui/primitives';
import { QrCode } from '../../ui/QrCode';
import { installSteps } from '../settings/AccessConnect';
import { browserRoute, useConnectCode } from '../settings/connect-code';

export const PHONE_CARD_TITLE = 'Add Row-Bot to your phone';
const DISMISS =
  'Hide this. You can add your phone later in Settings › Devices & remote access.';

type Controller = ReturnType<typeof useRuntime>['controller'];
/** One read of the setup state per connection: the card shows in many chats. */
const reads = new WeakMap<Controller, Promise<OnboardingSnapshot | null>>();
function readSetup(controller: Controller, fresh = false) {
  let read = fresh ? undefined : reads.get(controller);
  if (!read) {
    read = Promise.resolve(controller.onboarding())
      .then((value) => value ?? null)
      .catch(() => null);
    reads.set(controller, read);
  }
  return read;
}

/** A phone's browser, by its own name (an iPad asks for a QR code too). */
function onPhone(agent: string) {
  return /iPhone|iPod|Android/.test(agent);
}

/** Already on the home screen: nothing to add. */
function installed() {
  try {
    return (
      window.matchMedia?.('(display-mode: standalone)').matches === true ||
      (navigator as { standalone?: boolean }).standalone === true
    );
  } catch {
    return false;
  }
}

/**
 * The chat the card belongs to: the first that got an answer, kept on the
 * server. The first chat to ask claims it; any later one reads who has it.
 */
async function claimFor(
  controller: Controller,
  conversationId: string,
): Promise<OnboardingSnapshot | null> {
  const setup = await readSetup(controller);
  if (
    !setup?.host_name ||
    setup.dismissed_phone_card ||
    setup.phone_card_conversation
  )
    return setup;
  try {
    const receipt = await controller.onboardingCommand({
      command_id: crypto.randomUUID(),
      expected_revision: setup.revision,
      action: 'show_phone_card',
      profile: [],
      step: '',
      conversation_id: conversationId,
    });
    reads.set(controller, Promise.resolve(receipt.snapshot));
    return receipt.snapshot;
  } catch {
    // Another chat (or this one, twice) claimed it meanwhile: read who has it.
    return readSetup(controller, true);
  }
}

/**
 * U10: after the first answer on a hosted Row-Bot, one small card in that
 * chat, "Add Row-Bot to your phone". A phone gets the home-screen steps; a
 * computer shows a QR code (Devices' own one-time code) once asked. It stays
 * with the first answered chat and never follows into a new one; it never
 * blocks the chat, and once dismissed it never comes back. Settings › Devices
 * & remote access keeps it.
 */
export default function PhoneCard({
  conversationId,
  client = accessInvitationClient,
  devices = accessClient,
  pwa = appPwaClient,
  agent = navigator.userAgent,
  origin = window.location.origin,
}: {
  /** The chat with an answer this card would show in. */
  conversationId: string;
  client?: AccessInvitationClient;
  devices?: Pick<AccessClient, 'devices'>;
  pwa?: PwaClient;
  agent?: string;
  /** This browser's address: a phone installs only from a secure (HTTPS) one. */
  origin?: string;
}) {
  const { controller } = useRuntime();
  const [setup, setSetup] = useState<OnboardingSnapshot | null>(null);
  const [hidden, setHidden] = useState(false);
  useEffect(() => {
    let current = true;
    void claimFor(controller, conversationId).then((value) => {
      if (current) setSetup(value);
    });
    return () => {
      current = false;
    };
  }, [controller, conversationId]);

  const phone = onPhone(agent);
  if (
    hidden ||
    !setup ||
    setup.dismissed_phone_card ||
    setup.phone_card_conversation !== conversationId ||
    (phone && installed())
  )
    return null;

  async function dismiss() {
    setHidden(true);
    const send = (known: OnboardingSnapshot) =>
      controller.onboardingCommand({
        command_id: crypto.randomUUID(),
        expected_revision: known.revision,
        action: 'dismiss_phone_card',
        profile: [],
        step: '',
      });
    // Other chats on this page know at once.
    if (setup)
      reads.set(
        controller,
        Promise.resolve({ ...setup, dismissed_phone_card: true }),
      );
    try {
      // At once, with the state this card read: one request, so a reload
      // right after rarely beats it. Changed meanwhile: read it and send again.
      await send(setup!).catch(async () => {
        const latest = await readSetup(controller, true);
        if (latest && !latest.dismissed_phone_card) await send(latest);
      });
    } catch {
      /* Hidden here; it may show once more after a reload. */
    }
  }

  return (
    <section
      className="phone-card"
      aria-labelledby="phone-card-title"
      data-testid="phone-card"
    >
      <span className="phone-card-icon" aria-hidden>
        <Smartphone size={18} />
      </span>
      <div className="phone-card-text">
        <h3 id="phone-card-title">{PHONE_CARD_TITLE}</h3>
        {phone ? (
          <HomeScreen agent={agent} origin={origin} pwa={pwa} />
        ) : (
          <ScanCode client={client} devices={devices} onDone={dismiss} />
        )}
      </div>
      <IconButton
        size="sm"
        className="phone-card-dismiss"
        label={DISMISS}
        onClick={() => void dismiss()}
      >
        <X size={16} aria-hidden />
      </IconButton>
    </section>
  );
}

/** On the phone itself: keep Row-Bot on the home screen, like an app. */
function HomeScreen({
  agent,
  origin,
  pwa,
}: {
  agent: string;
  origin: string;
  pwa: PwaClient;
}) {
  const state = useSyncExternalStore(
    pwa.subscribe,
    pwa.getSnapshot,
    pwa.getSnapshot,
  );
  const [note, setNote] = useState('');
  return (
    <>
      <p>Keep Row-Bot on your home screen and open it like an app.</p>
      {state.installAvailable ? (
        <div className="phone-card-actions">
          <Button
            variant="primary"
            onClick={() =>
              void pwa
                .requestInstall()
                .then((result) =>
                  setNote(
                    result === 'user_activation_required'
                      ? 'Tap Install again.'
                      : '',
                  ),
                )
            }
          >
            Install
          </Button>
        </div>
      ) : (
        <ol className="phone-card-steps">
          {installSteps({ user_agent: agent }, origin).map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      )}
      {note && <p role="status">{note}</p>}
    </>
  );
}

/** On a computer: a one-time QR code the phone's camera opens. */
function ScanCode({
  client,
  devices,
  onDone,
}: {
  client: AccessInvitationClient;
  devices: Pick<AccessClient, 'devices'>;
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [route, setRoute] = useState<AccessRoute | null | undefined>(undefined);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!open) return;
    const abort = new AbortController();
    void client.routes(abort.signal).then(
      (routes) => {
        if (!abort.signal.aborted) setRoute(browserRoute(routes));
      },
      () => {
        if (!abort.signal.aborted) setRoute(null);
      },
    );
    return () => abort.abort();
  }, [client, open]);
  const { code, expired, connected, reset } = useConnectCode({
    client,
    devices,
    routeId: open ? (route?.id ?? '') : '',
    lifetime: 'trusted',
    onError: setError,
  });

  if (!open)
    return (
      <>
        <p>
          Use Row-Bot from your phone too. Scan a code with its camera; it signs
          in once and stays signed in.
        </p>
        <div className="phone-card-actions">
          <Button onClick={() => setOpen(true)}>
            <QrIcon size={16} aria-hidden /> Show the code
          </Button>
        </div>
      </>
    );
  if (connected)
    return (
      <>
        <p role="status" className="phone-card-connected">
          <Check size={16} aria-hidden /> Connected:{' '}
          <strong>{connected.display_name}</strong>
        </p>
        <p>To keep Row-Bot on its home screen:</p>
        <ol className="phone-card-steps">
          {installSteps(connected, code?.origin ?? route?.origin ?? '').map(
            (step) => (
              <li key={step}>{step}</li>
            ),
          )}
        </ol>
        <div className="phone-card-actions">
          <Button variant="primary" onClick={onDone}>
            Done
          </Button>
        </div>
      </>
    );
  if (route === null)
    return (
      <p>
        {error || 'Your phone can’t reach the address this browser uses.'}{' '}
        Connect it from{' '}
        <AppLink to="/settings/access#connect">
          Settings › Devices &amp; remote access
        </AppLink>
        .
      </p>
    );
  if (expired)
    return (
      <>
        <p>This code expired.</p>
        <div className="phone-card-actions">
          <Button onClick={reset}>Show a new code</Button>
        </div>
      </>
    );
  if (!code)
    return error ? (
      <p role="alert">{error}</p>
    ) : (
      <Skeleton label="Making a code" />
    );
  return (
    <div className="phone-card-code">
      <QrCode
        value={code.url}
        label="QR code to open Row-Bot on your phone"
        size={148}
      />
      <p>
        Point your phone’s camera at the code and open the link. It works once,
        and renews by itself while this shows.
      </p>
    </div>
  );
}
