import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { AccessTailscaleClient, TailscaleStatus } from '../../api/access';
import { OverlayProvider } from '../../ui/overlays';
import AccessTailscale from './AccessTailscale';

const ready: TailscaleStatus = {
  state: 'ready',
  installed: true,
  signed_in: true,
  serve_url: '',
  consent_url: '',
  owned: false,
  detail: 'Ready',
};
const active: TailscaleStatus = {
  ...ready,
  state: 'active_owned',
  owned: true,
  serve_url: 'https://machine.tail.test',
  detail: 'Active',
};
function fixture(): AccessTailscaleClient {
  return {
    status: vi.fn().mockResolvedValue({ can_manage: true, status: null }),
    check: vi.fn().mockResolvedValue(ready),
    action: vi.fn().mockResolvedValue({
      success: true,
      status: active,
      error: '',
      restart_required: true,
    }),
    receipt: vi.fn().mockResolvedValue({
      success: true,
      status: active,
      error: '',
      restart_required: false,
    }),
  };
}
function show(
  client: AccessTailscaleClient,
  variant: 'option' | 'line' = 'option',
) {
  return render(
    <OverlayProvider>
      <AccessTailscale client={client} variant={variant} />
    </OverlayProvider>,
  );
}
afterEach(() => sessionStorage.clear());

it('looks once when the connect flow opens and shares privately after one confirmation', async () => {
  const client = fixture();
  show(client);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Share privately…' }),
  );
  expect(client.check).toHaveBeenCalledTimes(1);
  expect(client.action).not.toHaveBeenCalled();
  const confirm = await screen.findByRole('alertdialog', {
    name: 'Share Row-Bot privately on your tailnet?',
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Share privately' }),
  );
  await waitFor(() =>
    expect(client.action).toHaveBeenCalledWith('enable', expect.any(String)),
  );
  expect(
    await screen.findByText('Done. Restart Row-Bot to use the new address.'),
  ).toBeInTheDocument();
});

it('never probes from Advanced and stops a share Row-Bot made', async () => {
  const client = fixture();
  vi.mocked(client.status).mockResolvedValue({
    can_manage: true,
    status: active,
  });
  show(client, 'line');
  fireEvent.click(await screen.findByRole('button', { name: /Stop sharing/ }));
  await waitFor(() =>
    expect(client.action).toHaveBeenCalledWith('disable', expect.any(String)),
  );
  expect(client.check).not.toHaveBeenCalled();
});

it('shows Tailscale’s consent page and next steps for a route conflict (B138)', async () => {
  const client = fixture();
  vi.mocked(client.status).mockResolvedValue({
    can_manage: true,
    status: {
      ...ready,
      state: 'consent_required',
      consent_url: 'https://login.tailscale.com/f/serve?node=example',
    },
  });
  const view = show(client);
  expect(
    await screen.findByRole('link', { name: /Open Tailscale’s consent page/ }),
  ).toHaveAttribute('href', 'https://login.tailscale.com/f/serve?node=example');
  view.unmount();
  vi.mocked(client.status).mockResolvedValue({
    can_manage: true,
    status: { ...ready, state: 'route_conflict' },
  });
  show(client);
  expect(
    await screen.findByText(/already shares something else/),
  ).toBeInTheDocument();
  expect(screen.getByText('tailscale serve status')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Check again/ })).toBeVisible();
});

it('recovers an interrupted original change without resending it', async () => {
  sessionStorage.setItem('row-bot-tailscale-command', 'original-command');
  const client = fixture();
  vi.mocked(client.status).mockResolvedValue({
    can_manage: true,
    status: ready,
  });
  show(client);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Check the earlier change' }),
  );
  await waitFor(() =>
    expect(client.receipt).toHaveBeenCalledWith('original-command'),
  );
  expect(client.action).not.toHaveBeenCalled();
  expect(sessionStorage.getItem('row-bot-tailscale-command')).toBeNull();
});

it('denies remote owner changes in the view', async () => {
  const client = fixture();
  vi.mocked(client.status).mockResolvedValue({
    can_manage: false,
    status: active,
  });
  show(client);
  expect(
    await screen.findByText('Shared privately at machine.tail.test.'),
  ).toBeInTheDocument();
  expect(client.check).not.toHaveBeenCalled();
  expect(
    screen.queryByRole('button', { name: /Stop sharing/ }),
  ).not.toBeInTheDocument();
});
