import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import type { AccessPushClient, AccessPushStatus } from '../../api/access';
import type { PushBrowser } from '../settings/DeviceNotifications';
import PhoneNotificationsOffer, {
  type OfferDevice,
} from './PhoneNotificationsOffer';

const SUBSCRIPTION = {
  endpoint: 'https://fcm.googleapis.com/fcm/send/device-token',
  keys: { p256dh: 'BKey', auth: 'auth' },
};
const AVAILABLE: AccessPushStatus = {
  available: true,
  publicKey: 'BServerKey',
  subscribed: false,
  showDetails: true,
};
const PHONE: OfferDevice = { phone: true, secure: true, homeScreen: false };
const QUESTION = 'Get a notification when Row-Bot needs you?';

function client(status: AccessPushStatus = AVAILABLE) {
  return {
    status: vi.fn().mockResolvedValue(status),
    subscribe: vi.fn().mockResolvedValue(undefined),
    unsubscribe: vi.fn().mockResolvedValue(undefined),
    setShowDetails: vi.fn((on: boolean) => Promise.resolve(on)),
  } satisfies AccessPushClient;
}

function browser(overrides: Partial<PushBrowser> = {}): PushBrowser {
  return {
    supported: true,
    appleNotInstalled: false,
    permission: () => 'default',
    subscribe: vi.fn().mockResolvedValue(SUBSCRIPTION),
    unsubscribe: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

function offer({
  api = client(),
  push = browser(),
  device = PHONE,
}: {
  api?: AccessPushClient;
  push?: PushBrowser;
  device?: OfferDevice;
} = {}) {
  return render(
    <MemoryRouter>
      <PhoneNotificationsOffer client={api} browser={push} device={device} />
    </MemoryRouter>,
  );
}

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

it('asks on a phone and Turn on runs the device switch’s own flow', async () => {
  const api = client();
  const push = browser();
  offer({ api, push });

  expect(await screen.findByText(QUESTION)).toBeVisible();
  expect(screen.getByText(/only this device can read it/)).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Turn on' }));

  expect(push.subscribe).toHaveBeenCalledWith('BServerKey');
  await waitFor(() => expect(api.subscribe).toHaveBeenCalledWith(SUBSCRIPTION));
  expect(
    await screen.findByText('Notifications are on for this phone'),
  ).toBeVisible();
  expect(
    screen.getByRole('link', { name: 'Devices & remote access' }),
  ).toHaveAttribute('href', '/settings/access#notifications');
  expect(screen.queryByRole('button', { name: 'Turn on' })).toBeNull();
});

it('keeps asking, and says so, when the browser’s permission is refused', async () => {
  const api = client();
  offer({ api, push: browser({ subscribe: vi.fn().mockResolvedValue(null) }) });

  await userEvent.click(await screen.findByRole('button', { name: 'Turn on' }));

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Notifications weren’t allowed',
  );
  expect(api.subscribe).not.toHaveBeenCalled();
  expect(screen.getByText(QUESTION)).toBeVisible();
});

it('remembers Not now on this phone, and the Home Screen app asks again', async () => {
  const first = offer();
  await userEvent.click(await screen.findByRole('button', { name: 'Not now' }));
  expect(
    screen.queryByRole('region', { name: 'Phone notifications' }),
  ).toBeNull();
  first.unmount();

  const api = client();
  const again = offer({ api });
  await Promise.resolve();
  expect(screen.queryByText(QUESTION)).toBeNull();
  expect(api.status).not.toHaveBeenCalled();
  again.unmount();

  offer({ device: { ...PHONE, homeScreen: true } });
  expect(await screen.findByText(QUESTION)).toBeVisible();
});

it('still answers Not now when this browser keeps nothing', async () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  offer();
  await userEvent.click(await screen.findByRole('button', { name: 'Not now' }));
  expect(screen.queryByText(QUESTION)).toBeNull();
});

it('on an iPhone browser tab, says to add Row-Bot to the Home Screen first', async () => {
  offer({
    push: browser({ supported: false, appleNotInstalled: true }),
  });

  expect(
    await screen.findByText('Add Row-Bot to your Home Screen first'),
  ).toBeVisible();
  expect(screen.getByText(/tap Share, then Add to Home Screen/)).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Turn on' })).toBeNull();
  await userEvent.click(screen.getByRole('button', { name: 'Not now' }));
  expect(
    screen.queryByText('Add Row-Bot to your Home Screen first'),
  ).toBeNull();
});

it.each<[string, { api?: ReturnType<typeof client>; push?: PushBrowser }]>([
  [
    'plain HTTP',
    {
      api: client({
        available: false,
        reason: 'needs_https',
        showDetails: true,
      }),
    },
  ],
  ['no Push API', { push: browser({ supported: false }) }],
  ['already on', { api: client({ ...AVAILABLE, subscribed: true }) }],
  [
    'blocked in this browser',
    { push: browser({ permission: () => 'denied' }) },
  ],
])('is hidden where push can’t work or is on: %s', async (_case, given) => {
  const api = given.api ?? client();
  offer({ ...given, api });
  await waitFor(() => expect(api.status).toHaveBeenCalled());
  // The device's status has been read and shown before looking.
  await act(async () => {
    await api.status.mock.results[0]?.value;
  });
  expect(
    screen.queryByRole('region', { name: 'Phone notifications' }),
  ).toBeNull();
});

it.each<[string, OfferDevice]>([
  ['on a desktop', { phone: false, secure: true, homeScreen: false }],
  ['outside a secure context', { ...PHONE, secure: false }],
])(
  'never asks %s, nor reads the device’s notifications',
  async (_case, device) => {
    const api = client();
    offer({ api, device });
    await Promise.resolve();
    expect(screen.queryByText(QUESTION)).toBeNull();
    expect(api.status).not.toHaveBeenCalled();
  },
);
