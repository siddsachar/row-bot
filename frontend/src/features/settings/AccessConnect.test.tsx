import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type {
  AccessClient,
  AccessDevice,
  AccessInvitation,
  AccessInvitationClient,
  AccessRoute,
  AccessTailscaleClient,
} from '../../api/access';
import { OverlayProvider } from '../../ui/overlays';
import AccessConnect, {
  ARRIVAL_POLL_MS,
  REFRESH_BEFORE_MS,
  installSteps,
} from './AccessConnect';

const TEN_MINUTES = 10 * 60 * 1000;

function route(overrides: Partial<AccessRoute>): AccessRoute {
  return {
    id: 'lan-1',
    kind: 'lan',
    label: 'Local network — 192.168.1.23',
    origin: 'http://192.168.1.23:8080',
    available: true,
    eligible: true,
    detail: '',
    warning: 'LAN HTTP is unencrypted.',
    ...overrides,
  };
}

const phone: AccessDevice = {
  id: 'device-phone',
  display_name: 'Connected browser',
  created_at: '2026-09-29T10:00:00Z',
  last_seen_at: '2026-09-29T10:00:00Z',
  revoked_at: null,
  user_agent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)',
  paired_from: null,
  access_route: 'lan',
  last_address: '192.168.1.40',
  sessions: [],
};

function fixture() {
  const minted: AccessInvitation[] = [];
  const client: AccessInvitationClient = {
    routes: vi.fn(async () => [
      route({}),
      // The loopback address is never offered to another device (U56).
      route({
        id: 'current-1',
        kind: 'current_server',
        label: 'Current server address — 127.0.0.1:8080',
        origin: 'http://127.0.0.1:8080',
        warning: null,
      }),
    ]),
    routeSettings: vi.fn(async () => ({
      listen_mode: 'local_network' as const,
      configured_origins: [],
      managed_externally: false,
      can_manage_routes: true,
    })),
    invitations: vi.fn(async () => minted.map((item) => ({ ...item }))),
    create: vi.fn(async (routeId: string) => {
      const invitation: AccessInvitation = {
        id: `invite-${minted.length + 1}`,
        intended_origin: routeId === 'lan-1' ? 'http://192.168.1.23:8080' : '',
        session_lifetime: 'trusted',
        expires_at: new Date(Date.now() + TEN_MINUTES).toISOString(),
        claimed_at: null,
        cancelled_at: null,
        claimed_device_id: null,
      };
      minted.push(invitation);
      return {
        invitation,
        url: `${invitation.intended_origin}/connect?invitation=code-${minted.length}`,
      };
    }),
    cancel: vi.fn(async () => undefined),
    setListenMode: vi.fn(async () => ({ restart_required: false })),
    changeOrigin: vi.fn(async () => undefined),
  };
  const devices: Pick<AccessClient, 'devices' | 'rename'> = {
    devices: vi.fn(async () => [phone]),
    rename: vi.fn(async (_id: string, name: string) => ({
      ...phone,
      display_name: name,
    })),
  };
  const tailscale: AccessTailscaleClient = {
    status: vi.fn(async () => ({ can_manage: true, status: null })),
    check: vi.fn(),
    action: vi.fn(),
    receipt: vi.fn(),
  };
  return { client, devices, tailscale, minted };
}

afterEach(() => vi.useRealTimers());

it('renews the QR code before it expires, copies its link, and names the device on arrival', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const { client, devices, tailscale, minted } = fixture();
  const writeClipboard = vi.fn(async (_text: string) => ({
    status: 'ok' as const,
    value: null,
  }));
  const onConnected = vi.fn();
  render(
    <OverlayProvider>
      <AccessConnect
        client={client}
        devices={devices}
        tailscale={tailscale}
        writeClipboard={writeClipboard}
        onConnected={onConnected}
      />
    </OverlayProvider>,
  );
  // Nothing is created until the person asks.
  expect(client.create).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Connect a phone or computer' }),
  );

  // 1. Detected: Same Wi-Fi works now, so it is chosen; loopback is not offered.
  const ways = await screen.findByRole('radiogroup', {
    name: 'How it reaches this computer',
  });
  expect(
    await within(ways).findByRole('radio', {
      name: /Same Wi-Fi/,
      checked: true,
    }),
  ).toBeInTheDocument();
  expect(screen.queryByText(/127\.0\.0\.1/)).toBeNull();

  // 2. One code for that address, with Copy link.
  expect(
    await screen.findByRole('img', {
      name: 'QR code to connect a phone or computer',
    }),
  ).toBeVisible();
  expect(client.create).toHaveBeenCalledTimes(1);
  expect(client.create).toHaveBeenLastCalledWith('lan-1', 'trusted');
  expect(screen.getByText('192.168.1.23:8080')).toBeVisible();
  // The one-time secret is never written on the page, only in the code.
  expect(document.body.textContent).not.toContain('code-1');
  fireEvent.click(screen.getByRole('button', { name: 'Copy link' }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(writeClipboard).toHaveBeenCalledWith(
    'http://192.168.1.23:8080/connect?invitation=code-1',
  );
  expect(await screen.findByRole('button', { name: 'Copied' })).toBeVisible();

  // The code renews itself before the invitation expires, and the old one
  // stops working.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(TEN_MINUTES - REFRESH_BEFORE_MS + 50);
  });
  expect(client.create).toHaveBeenCalledTimes(2);
  expect(client.cancel).toHaveBeenCalledWith('invite-1');
  expect(Date.parse(minted[1].expires_at)).toBeGreaterThan(
    Date.parse(minted[0].expires_at),
  );
  expect(screen.getByRole('status')).toHaveTextContent(
    'Waiting for the device…',
  );

  // 3. The phone claims the current code: "Connected: <name> · Rename".
  minted[1].claimed_at = new Date().toISOString();
  minted[1].claimed_device_id = phone.id;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ARRIVAL_POLL_MS + 50);
  });
  expect(await screen.findByText(/^Connected:/)).toHaveTextContent(
    'Connected: Connected browser',
  );
  expect(screen.getByRole('button', { name: 'Rename' })).toBeVisible();
  expect(onConnected).toHaveBeenCalled();
  // Over plain HTTP it can't install as an app; it says so (U60).
  expect(screen.getByText(/needs a secure address/)).toBeVisible();
  // A claimed code is not cancelled, and no more codes are made.
  expect(client.cancel).not.toHaveBeenCalledWith('invite-2');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(TEN_MINUTES);
  });
  expect(client.create).toHaveBeenCalledTimes(2);

  fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
  fireEvent.change(screen.getByLabelText('Device name'), {
    target: { value: 'Kitchen iPhone' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  expect(await screen.findByText('Kitchen iPhone')).toBeVisible();
  expect(devices.rename).toHaveBeenCalledWith(phone.id, 'Kitchen iPhone');
});

it('cancels the open code when the flow closes', async () => {
  const { client, devices, tailscale } = fixture();
  render(
    <OverlayProvider>
      <AccessConnect client={client} devices={devices} tailscale={tailscale} />
    </OverlayProvider>,
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Connect a phone or computer' }),
  );
  await screen.findByRole('img', {
    name: 'QR code to connect a phone or computer',
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Close connect a phone or computer' }),
  );
  expect(client.cancel).toHaveBeenCalledWith('invite-1');
  expect(
    screen.getByRole('button', { name: 'Connect a phone or computer' }),
  ).toBeVisible();
});

it('says how to install on the phone that connected, and why not over plain HTTP', () => {
  expect(
    installSteps(
      { user_agent: 'Mozilla/5.0 (Linux; Android 15)' },
      'https://pc.tail.ts.net',
    ),
  ).toEqual([
    'In Chrome, tap ⋮ at the top right.',
    'Tap Add to Home screen, or Install app.',
  ]);
  expect(installSteps(phone, 'http://192.168.1.23:8080')[0]).toMatch(
    /needs a secure address/,
  );
});
