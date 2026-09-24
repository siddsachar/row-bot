import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { AccessTailscaleClient, TailscaleStatus } from '../../api/access';
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
afterEach(() => sessionStorage.clear());

it('does not probe on mount and enables Serve with one explicit click', async () => {
  const client = fixture();
  render(<AccessTailscale client={client} />);
  expect(
    await screen.findByText(
      'Tailscale has not been checked in this app session.',
    ),
  ).toBeInTheDocument();
  expect(client.check).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Check status' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Enable Serve' }));
  await waitFor(() =>
    expect(client.action).toHaveBeenCalledWith('enable', expect.any(String)),
  );
  expect(
    await screen.findByText(
      'Tailscale changed. Restart Row-Bot to refresh access routes.',
    ),
  ).toBeInTheDocument();
});

it('recovers an interrupted original command without resending it', async () => {
  sessionStorage.setItem('row-bot-tailscale-command', 'original-command');
  const client = fixture();
  render(<AccessTailscale client={client} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Check original result' }),
  );
  await waitFor(() =>
    expect(client.receipt).toHaveBeenCalledWith('original-command'),
  );
  expect(client.action).not.toHaveBeenCalled();
  expect(sessionStorage.getItem('row-bot-tailscale-command')).toBeNull();
});

it('denies remote owner mutations in the view', async () => {
  const client = fixture();
  vi.mocked(client.status).mockResolvedValue({
    can_manage: false,
    status: active,
  });
  render(<AccessTailscale client={client} />);
  expect(
    await screen.findByText(
      'Serve changes are available in the local owner session.',
    ),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Disable owned Serve' }),
  ).not.toBeInTheDocument();
});
