import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { AccessClient, AccessDevice } from '../../api/access';
import { OverlayProvider } from '../../ui/overlays';
import AccessDevices, { addressInWords } from './AccessDevices';

const device: AccessDevice = {
  id: 'device-a',
  display_name: 'Phone',
  created_at: '2026-09-20T00:00:00Z',
  last_seen_at: '2026-09-20T01:00:00Z',
  revoked_at: null,
  user_agent: null,
  paired_from: null,
  access_route: null,
  last_address: '192.168.1.40',
  current: true,
  sessions: [
    {
      id: 'session-a',
      device_id: 'device-a',
      created_at: '2026-09-20T00:00:00Z',
      last_seen_at: null,
      expires_at: '2026-10-20T00:00:00Z',
      revoked_at: null,
      lifetime: 'trusted' as const,
      current: true,
    },
  ],
};
const laptop: AccessDevice = {
  ...device,
  id: 'device-b',
  display_name: 'Laptop',
  last_address: '203.0.113.9',
  current: false,
  sessions: [],
};

function fixture(): Pick<AccessClient, 'devices' | 'revokeDevice' | 'rename'> {
  return {
    devices: vi.fn().mockResolvedValue([device, laptop]),
    revokeDevice: vi.fn().mockResolvedValue(undefined),
    rename: vi.fn(async (_id: string, name: string) => ({
      ...laptop,
      display_name: name,
    })),
  };
}

function show(client = fixture()) {
  render(
    <OverlayProvider>
      <AccessDevices client={client} />
    </OverlayProvider>,
  );
  return client;
}

it('marks the device the server says is making the request (B141)', async () => {
  show();
  const list = await screen.findByRole('list', { name: 'Your devices' });
  const rows = within(list).getAllByRole('listitem');
  expect(rows[0]).toHaveTextContent('Phone');
  expect(rows[0]).toHaveTextContent('This device');
  expect(rows[1]).toHaveTextContent('Laptop');
  expect(rows[1]).not.toHaveTextContent('This device');
});

it('signs a device out only after a confirmation', async () => {
  const client = show();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Sign out Laptop' }),
  );
  expect(client.revokeDevice).not.toHaveBeenCalled();
  const confirm = await screen.findByRole('alertdialog', {
    name: 'Sign out Laptop?',
  });
  fireEvent.click(within(confirm).getByRole('button', { name: 'Sign out' }));
  await waitFor(() =>
    expect(client.revokeDevice).toHaveBeenCalledWith('device-b'),
  );
});

it('keeps a sign-out failure visible and retryable', async () => {
  const client = fixture();
  vi.mocked(client.revokeDevice).mockRejectedValue({ code: 'origin_rejected' });
  show(client);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Sign out Laptop' }),
  );
  fireEvent.click(
    within(
      await screen.findByRole('alertdialog', { name: 'Sign out Laptop?' }),
    ).getByRole('button', { name: 'Sign out' }),
  );
  expect(await screen.findByRole('alert')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
});

it('says when and where a device was last seen in words, never raw timestamps (U59)', async () => {
  show();
  await screen.findByText('Phone');
  expect(document.body.textContent).not.toMatch(/\d{4}-\d{2}-\d{2}T\d{2}:/);
  expect(
    document.querySelector('time[datetime="2026-09-20T01:00:00.000Z"]'),
  ).not.toBeNull();
  expect(screen.getByText(/on your network \(192\.168\.1\.40\)/)).toBeVisible();
  expect(addressInWords('100.101.2.3')).toBe('over Tailscale (100.101.2.3)');
  expect(addressInWords('203.0.113.9')).toBe('from the internet (203.0.113.9)');
  expect(addressInWords('127.0.0.1')).toBe('via this computer');
  expect(addressInWords(null)).toBe('');
});
