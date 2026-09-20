import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { AccessClient } from '../../api/access';
import AccessSessions from './AccessSessions';

const device = {
  id: 'device-a',
  display_name: 'Phone',
  created_at: '2026-09-20T00:00:00Z',
  last_seen_at: '2026-09-20T01:00:00Z',
  revoked_at: null,
  user_agent: null,
  paired_from: null,
  access_route: null,
  sessions: [
    {
      id: 'session-a',
      device_id: 'device-a',
      created_at: '2026-09-20T00:00:00Z',
      last_seen_at: null,
      expires_at: '2026-10-20T00:00:00Z',
      revoked_at: null,
      lifetime: 'trusted' as const,
    },
  ],
};

function fixture(): AccessClient {
  return {
    devices: vi.fn().mockResolvedValue([device]),
    refresh: vi.fn().mockResolvedValue({
      renewed: true,
      expires_at: device.sessions[0].expires_at,
    }),
    revokeSession: vi.fn().mockResolvedValue(undefined),
    revokeDevice: vi.fn().mockResolvedValue(undefined),
    logout: vi.fn().mockResolvedValue(undefined),
  };
}

it('lists public device/session metadata and revokes the exact session', async () => {
  const client = fixture();
  render(<AccessSessions currentSessionId="session-a" client={client} />);
  expect(await screen.findByText('Phone')).toBeInTheDocument();
  expect(screen.getByText(/trusted session/)).toHaveTextContent('current');
  fireEvent.click(screen.getByRole('button', { name: 'Revoke session' }));
  await waitFor(() =>
    expect(client.revokeSession).toHaveBeenCalledWith(
      'session-a',
      expect.any(AbortSignal),
    ),
  );
  expect(screen.queryByText(/user_agent/i)).not.toBeInTheDocument();
});

it('keeps an exact revocation failure visible and retryable', async () => {
  const client = fixture();
  vi.mocked(client.revokeDevice).mockRejectedValue({ code: 'origin_rejected' });
  render(<AccessSessions client={client} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Revoke device' }));
  expect(await screen.findByRole('alert')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
});
