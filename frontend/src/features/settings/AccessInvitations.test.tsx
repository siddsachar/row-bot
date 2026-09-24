import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { AccessInvitationClient } from '../../api/access';
import AccessInvitations from './AccessInvitations';

const route = {
  id: 'current_server-1234',
  kind: 'current_server',
  label: 'Current server',
  origin: 'http://localhost:8080',
  available: true,
  eligible: true,
  detail: 'Verified address',
  warning: null,
};
const invitation = {
  id: 'invite-a',
  intended_origin: route.origin,
  session_lifetime: 'temporary' as const,
  expires_at: '2099-01-01T00:00:00Z',
  claimed_at: null,
  cancelled_at: null,
};
function fixture(): AccessInvitationClient {
  return {
    routes: vi.fn().mockResolvedValue([route]),
    routeSettings: vi.fn().mockResolvedValue({
      listen_mode: 'local_only',
      configured_origins: [],
      managed_externally: false,
      can_manage_routes: true,
    }),
    invitations: vi.fn().mockResolvedValue([]),
    create: vi.fn().mockResolvedValue({
      invitation,
      url: `${route.origin}/connect?invitation=secret`,
    }),
    cancel: vi.fn().mockResolvedValue(undefined),
    setListenMode: vi.fn().mockResolvedValue({ restart_required: true }),
    changeOrigin: vi.fn().mockResolvedValue(undefined),
  };
}

it('creates the selected route with one click and shows its one-time link', async () => {
  const client = fixture();
  render(<AccessInvitations client={client} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Create connection link' }),
  );
  await waitFor(() =>
    expect(client.create).toHaveBeenCalledWith(route.id, 'trusted'),
  );
  expect(
    await screen.findByLabelText('One-time link · expires in 10 minutes'),
  ).toHaveValue(`${route.origin}/connect?invitation=secret`);
});

it('cancels an open invitation and hides its link', async () => {
  const client = fixture();
  vi.mocked(client.invitations).mockResolvedValue([invitation]);
  render(<AccessInvitations client={client} />);
  fireEvent.click(
    await screen.findByRole('button', {
      name: `Cancel invitation for ${route.origin}`,
    }),
  );
  await waitFor(() =>
    expect(client.cancel).toHaveBeenCalledWith(invitation.id),
  );
});

it('surfaces a stale route error and offers retry', async () => {
  const client = fixture();
  vi.mocked(client.create).mockRejectedValue({ code: 'route_changed' });
  render(<AccessInvitations client={client} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Create connection link' }),
  );
  expect(await screen.findByRole('alert')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
});

it('changes LAN mode with one toggle action and reports restart need', async () => {
  const client = fixture();
  render(<AccessInvitations client={client} />);
  fireEvent.click(
    await screen.findByRole('switch', {
      name: 'Allow local network connections',
    }),
  );
  await waitFor(() =>
    expect(client.setListenMode).toHaveBeenCalledWith(
      'local_only',
      'local_network',
    ),
  );
  expect(
    await screen.findByText(
      'Saved. Restart Row-Bot to apply the listen change.',
    ),
  ).toBeInTheDocument();
});

it('adds one trusted address and does not expose mutation to a remote owner', async () => {
  const client = fixture();
  const view = render(<AccessInvitations client={client} />);
  fireEvent.change(await screen.findByLabelText('Additional trusted address'), {
    target: { value: 'https://example.test' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Add trusted address' }));
  await waitFor(() =>
    expect(client.changeOrigin).toHaveBeenCalledWith(
      'add',
      'https://example.test',
      [],
    ),
  );
  vi.mocked(client.routeSettings).mockResolvedValue({
    listen_mode: 'local_only',
    configured_origins: [],
    managed_externally: false,
    can_manage_routes: false,
  });
  view.unmount();
  render(<AccessInvitations client={client} />);
  await screen.findByRole('button', { name: 'Create connection link' });
  expect(
    screen.queryByRole('switch', { name: 'Allow local network connections' }),
  ).not.toBeInTheDocument();
});
