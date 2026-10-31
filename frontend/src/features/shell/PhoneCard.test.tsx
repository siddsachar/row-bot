import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type {
  AccessClient,
  AccessDevice,
  AccessInvitation,
  AccessInvitationClient,
  AccessRoute,
} from '../../api/access';
import type { OnboardingSnapshot } from '../../api/types';
import { PwaClient, type PwaEnvironment } from '../../pwa/client';
import { ARRIVAL_POLL_MS } from '../settings/connect-code';
import PhoneCard, { PHONE_CARD_TITLE } from './PhoneCard';

// U10: after the first answer on a hosted Row-Bot, "Add Row-Bot to your
// phone" in that chat only: home-screen steps on a phone, Devices' QR code on
// a computer. The server keeps which chat it belongs to and its dismissal.

const mock = vi.hoisted(() => ({ controller: null as unknown }));
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: mock.controller }),
}));

const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)';
const ANDROID = 'Mozilla/5.0 (Linux; Android 15; Pixel 9)';
const COMPUTER = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/141.0';
const POD = 'https://my-row-bot.pikapod.net';

const setup: OnboardingSnapshot = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  setup_complete: true,
  needs_model: false,
  default_model: 'model:codex:gpt-5.6-sol',
  live_done: ['models'],
  import_sources: [],
  starter_workflows_missing: 0,
  profile: [],
  completed_steps: ['models'],
  skipped_steps: [],
  dismissed_home_card: false,
  dismissed_phone_card: false,
  phone_card_conversation: null,
  host_name: 'PikaPods',
  steps: [],
  intents: [],
};

/** The server's side: the first chat to ask keeps the card; dismissal is for good. */
function controller(snapshot: Partial<OnboardingSnapshot> = {}) {
  let saved: OnboardingSnapshot = { ...setup, ...snapshot };
  return {
    onboarding: vi.fn(async () => saved),
    onboardingCommand: vi.fn(
      async (command: {
        command_id: string;
        action: string;
        conversation_id?: string;
      }) => {
        if (command.action === 'dismiss_phone_card')
          saved = { ...saved, dismissed_phone_card: true };
        if (command.action === 'show_phone_card')
          saved = {
            ...saved,
            phone_card_conversation:
              saved.phone_card_conversation ?? command.conversation_id ?? null,
          };
        return {
          schema_version: 1,
          command_id: command.command_id,
          status: 'completed',
          snapshot: saved,
        };
      },
    ),
  };
}

const phone: AccessDevice = {
  id: 'device-phone',
  display_name: 'iPhone',
  created_at: '2026-10-31T10:00:00Z',
  last_seen_at: '2026-10-31T10:00:00Z',
  revoked_at: null,
  user_agent: IPHONE,
  paired_from: null,
  access_route: 'current_server',
  last_address: '203.0.113.9',
  sessions: [],
};

function access() {
  const minted: AccessInvitation[] = [];
  const route = (overrides: Partial<AccessRoute>): AccessRoute => ({
    id: 'lan-1',
    kind: 'lan',
    label: 'Local network',
    origin: 'http://10.0.0.7:8080',
    available: true,
    eligible: true,
    detail: '',
    warning: null,
    ...overrides,
  });
  const client = {
    routes: vi.fn(async () => [
      route({}),
      route({
        id: 'current-1',
        kind: 'current_server',
        origin: POD,
      }),
    ]),
    invitations: vi.fn(async () => minted.map((item) => ({ ...item }))),
    create: vi.fn(async (routeId: string) => {
      const invitation: AccessInvitation = {
        id: `invite-${minted.length + 1}`,
        intended_origin: routeId === 'current-1' ? POD : 'elsewhere',
        session_lifetime: 'trusted',
        expires_at: new Date(Date.now() + 600_000).toISOString(),
        claimed_at: null,
        cancelled_at: null,
        claimed_device_id: null,
      };
      minted.push(invitation);
      return { invitation, url: `${invitation.intended_origin}/connect#x` };
    }),
    cancel: vi.fn(async () => undefined),
  } as unknown as AccessInvitationClient;
  const devices: Pick<AccessClient, 'devices'> = {
    devices: vi.fn(async () => [phone]),
  };
  return { client, devices, minted };
}

function pwa(installAvailable = false) {
  const environment: PwaEnvironment = {
    origin: window.location.origin,
    online: () => true,
    userActivation: () => true,
    reload: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  };
  const client = new PwaClient(environment);
  if (installAvailable)
    vi.spyOn(client, 'getSnapshot').mockReturnValue({
      ...client.getSnapshot(),
      installAvailable: true,
    });
  vi.spyOn(client, 'requestInstall').mockResolvedValue('accepted');
  return client;
}

function show(
  agent: string,
  extra: Partial<Parameters<typeof PhoneCard>[0]> = {},
) {
  return render(
    <MemoryRouter>
      <PhoneCard
        conversationId="first-chat"
        agent={agent}
        origin={POD}
        pwa={pwa()}
        {...extra}
      />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mock.controller = controller();
});
afterEach(() => vi.useRealTimers());

it('on a computer, a QR code for this address, then the phone that used it', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const { client, devices, minted } = access();
  const owner = mock.controller as ReturnType<typeof controller>;
  show(COMPUTER, { client, devices });
  expect(
    await screen.findByRole('heading', { name: PHONE_CARD_TITLE }),
  ).toBeVisible();
  // Nothing is made until it's asked for.
  expect(client.create).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Show the code' }));
  expect(
    await screen.findByRole('img', {
      name: 'QR code to open Row-Bot on your phone',
    }),
  ).toBeVisible();
  // The pod's own HTTPS address, which the phone reaches too (not the pod's LAN).
  expect(client.create).toHaveBeenCalledWith('current-1', 'trusted');

  minted[0].claimed_at = '2026-10-31T10:01:00Z';
  minted[0].claimed_device_id = 'device-phone';
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ARRIVAL_POLL_MS + 50);
  });
  expect(await screen.findByText('iPhone')).toBeVisible();
  expect(screen.getByText('Tap Add to Home Screen, then Add.')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Done' }));
  await vi.waitFor(() =>
    expect(owner.onboardingCommand).toHaveBeenCalledWith(
      expect.objectContaining({ action: 'dismiss_phone_card' }),
    ),
  );
  expect(screen.queryByRole('heading', { name: PHONE_CARD_TITLE })).toBeNull();
});

it('on an iPhone, the home-screen steps and no code', async () => {
  const { client, devices } = access();
  show(IPHONE, { client, devices });
  expect(await screen.findByText('In Safari, tap Share.')).toBeVisible();
  expect(screen.getByText('Tap Add to Home Screen, then Add.')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Show the code' })).toBeNull();
  expect(client.routes).not.toHaveBeenCalled();
});

it('on Android with the install prompt ready, Install', async () => {
  const installable = pwa(true);
  render(
    <MemoryRouter>
      <PhoneCard
        conversationId="first-chat"
        agent={ANDROID}
        origin={POD}
        pwa={installable}
      />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Install' }));
  expect(installable.requestInstall).toHaveBeenCalledOnce();
});

it('once dismissed it never comes back', async () => {
  const owner = mock.controller as ReturnType<typeof controller>;
  const { unmount } = show(COMPUTER);
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Hide this. You can add your phone later in Settings › Devices & remote access.',
    }),
  );
  expect(screen.queryByRole('heading', { name: PHONE_CARD_TITLE })).toBeNull();
  await vi.waitFor(() =>
    expect(owner.onboardingCommand).toHaveBeenCalledWith(
      expect.objectContaining({
        action: 'dismiss_phone_card',
        expected_revision: setup.revision,
      }),
    ),
  );
  unmount();
  owner.onboarding.mockResolvedValue({ ...setup, dismissed_phone_card: true });
  show(COMPUTER);
  await vi.waitFor(() => expect(owner.onboarding).toHaveBeenCalledTimes(3));
  expect(screen.queryByRole('heading', { name: PHONE_CARD_TITLE })).toBeNull();
});

it('stays with the first chat that got an answer and never follows into a new one', async () => {
  const owner = mock.controller as ReturnType<typeof controller>;
  const { unmount } = show(COMPUTER);
  expect(
    await screen.findByRole('heading', { name: PHONE_CARD_TITLE }),
  ).toBeVisible();
  expect(owner.onboardingCommand).toHaveBeenCalledWith(
    expect.objectContaining({
      action: 'show_phone_card',
      conversation_id: 'first-chat',
    }),
  );
  unmount();
  // A later chat gets its answer: no card there, and the first keeps it.
  show(COMPUTER, { conversationId: 'later-chat' });
  await act(async () => {
    await Promise.resolve();
  });
  expect(screen.queryByRole('heading', { name: PHONE_CARD_TITLE })).toBeNull();
  expect(owner.onboardingCommand).toHaveBeenCalledOnce();
  cleanup();
  show(COMPUTER);
  expect(
    await screen.findByRole('heading', { name: PHONE_CARD_TITLE }),
  ).toBeVisible();
});
