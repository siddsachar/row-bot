import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import type { AccessPushClient, AccessPushStatus } from '../../api/access';
import DeviceNotifications, { type PushBrowser } from './DeviceNotifications';

const SUBSCRIPTION = {
  endpoint: 'https://fcm.googleapis.com/fcm/send/device-token',
  keys: { p256dh: 'BKey', auth: 'auth' },
};

function client(status: AccessPushStatus) {
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

const AVAILABLE: AccessPushStatus = {
  available: true,
  publicKey: 'BServerKey',
  subscribed: false,
  showDetails: true,
};

const deviceSwitch = () =>
  screen.findByRole('switch', { name: 'Notifications on this device' });
const detailsSwitch = () =>
  screen.getByRole('switch', { name: 'Show details in notifications' });

it('turns notifications on for this device with the server’s key, then off again', async () => {
  const api = client(AVAILABLE);
  const push = browser();
  render(<DeviceNotifications client={api} browser={push} />);
  const toggle = await deviceSwitch();
  expect(toggle).not.toBeChecked();
  expect(screen.getByText(/only this device can read it/)).toBeInTheDocument();

  await userEvent.click(toggle);
  await waitFor(() => expect(toggle).toBeChecked());
  expect(push.subscribe).toHaveBeenCalledWith('BServerKey');
  expect(api.subscribe).toHaveBeenCalledWith(SUBSCRIPTION);

  await userEvent.click(toggle);
  await waitFor(() => expect(toggle).not.toBeChecked());
  expect(push.unsubscribe).toHaveBeenCalled();
  expect(api.unsubscribe).toHaveBeenCalled();
});

it('shows details by default and turns them off, saying what notices say then', async () => {
  const api = client(AVAILABLE);
  render(<DeviceNotifications client={api} browser={browser()} />);
  await deviceSwitch();
  const details = detailsSwitch();
  await waitFor(() => expect(details).toBeEnabled());
  expect(details).toBeChecked();
  expect(
    screen.getByText(
      'Notifications show what they’re about. Your phone’s lock-screen preview setting decides whether they show while it’s locked.',
    ),
  ).toBeInTheDocument();

  await userEvent.click(details);
  await waitFor(() => expect(details).not.toBeChecked());
  expect(api.setShowDetails).toHaveBeenCalledWith(false);
  expect(
    screen.getByText('Notifications say only “Row-Bot is waiting for you”.'),
  ).toBeInTheDocument();
});

it('turns nothing on when the browser’s permission is refused', async () => {
  const api = client(AVAILABLE);
  render(
    <DeviceNotifications
      client={api}
      browser={browser({ subscribe: vi.fn().mockResolvedValue(null) })}
    />,
  );
  const toggle = await deviceSwitch();
  await userEvent.click(toggle);
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Notifications weren’t allowed',
  );
  expect(toggle).not.toBeChecked();
  expect(api.subscribe).not.toHaveBeenCalled();
});

it('says why it can’t be turned on: plain HTTP, an iPhone tab, or a blocked site', async () => {
  const { unmount } = render(
    <DeviceNotifications
      client={client({
        available: false,
        reason: 'needs_https',
        showDetails: true,
      })}
      browser={browser()}
    />,
  );
  expect(await deviceSwitch()).toBeDisabled();
  expect(screen.getByText(/reach Row-Bot over HTTPS/)).toBeInTheDocument();
  unmount();

  const second = render(
    <DeviceNotifications
      client={client(AVAILABLE)}
      browser={browser({ appleNotInstalled: true })}
    />,
  );
  expect(await deviceSwitch()).toBeDisabled();
  expect(screen.getByText(/add Row-Bot to your Home Screen/)).toBeVisible();
  second.unmount();

  render(
    <DeviceNotifications
      client={client(AVAILABLE)}
      browser={browser({ permission: () => 'denied' })}
    />,
  );
  expect(await deviceSwitch()).toBeDisabled();
  expect(screen.getByText(/blocked for Row-Bot/)).toBeVisible();
});

it('offers only the details switch on the computer running Row-Bot', async () => {
  const api = client({
    available: false,
    reason: 'this_computer',
    showDetails: false,
  });
  render(<DeviceNotifications client={api} browser={browser()} />);
  await waitFor(() => expect(detailsSwitch()).toBeEnabled());
  expect(detailsSwitch()).not.toBeChecked();
  expect(
    screen.queryByRole('switch', { name: 'Notifications on this device' }),
  ).not.toBeInTheDocument();

  await userEvent.click(detailsSwitch());
  await waitFor(() => expect(detailsSwitch()).toBeChecked());
  expect(api.setShowDetails).toHaveBeenCalledWith(true);
});
