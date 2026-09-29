import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { OverlayProvider } from '../../ui/overlays';
import WebhookAddress, { type WebhookAddressProps } from './WebhookAddress';

const SECRET = 's3cret-value-not-for-the-page';

function props(
  overrides: Partial<WebhookAddressProps> = {},
): WebhookAddressProps {
  return {
    taskId: 'task-a',
    localBase: 'http://127.0.0.1:8080',
    readAddress: vi.fn(async () => ({
      path: '/api/webhook/task-a',
      pathWithSecret: `/api/webhook/task-a?secret=${SECRET}`,
      header: 'X-Row-Bot-Webhook-Secret',
      secret: SECRET,
    })),
    writeClipboard: vi.fn(async () => true),
    loadTunnel: vi.fn(async () => ({
      publicBase: null,
      canControl: true,
    })),
    setPublic: vi.fn(async (on: boolean) => ({
      tunnel: {
        publicBase: on ? 'https://public.example.invalid' : null,
        canControl: true,
      },
      message: on ? 'Tunnel started.' : 'Tunnel stopped.',
    })),
    ...overrides,
  };
}

function show(overrides: Partial<WebhookAddressProps> = {}) {
  const callbacks = props(overrides);
  render(
    <OverlayProvider>
      <WebhookAddress {...callbacks} />
    </OverlayProvider>,
  );
  return callbacks;
}

it('shows the webhook address without its secret and copies it on request (parity row 20)', async () => {
  const callbacks = show();
  const group = await screen.findByRole('group', { name: 'Webhook address' });
  // The secret goes in a header, not in the address (B132).
  expect(group).toHaveTextContent(
    'POST http://127.0.0.1:8080/api/webhook/task-a',
  );
  expect(group).toHaveTextContent('X-Row-Bot-Webhook-Secret: ••••');
  expect(group).not.toHaveTextContent('?secret=');
  expect(document.body.innerHTML).not.toContain(SECRET);
  expect(callbacks.readAddress).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(
      within(group).getByRole('button', { name: 'Copy address' }),
    ),
  );
  expect(callbacks.writeClipboard).toHaveBeenCalledWith(
    'http://127.0.0.1:8080/api/webhook/task-a',
  );
  expect(within(group).getByRole('status')).toHaveTextContent('Copied.');
  await act(async () =>
    fireEvent.click(within(group).getByRole('button', { name: 'Copy secret' })),
  );
  expect(callbacks.writeClipboard).toHaveBeenLastCalledWith(SECRET);
  // A service that can only take an address gets the older form by choice.
  fireEvent.click(
    within(group).getByRole('checkbox', {
      name: /Put the secret in the address/,
    }),
  );
  expect(group).toHaveTextContent(
    'POST http://127.0.0.1:8080/api/webhook/task-a?secret=••••',
  );
  await act(async () =>
    fireEvent.click(
      within(group).getByRole('button', { name: 'Copy address' }),
    ),
  );
  expect(callbacks.writeClipboard).toHaveBeenLastCalledWith(
    `http://127.0.0.1:8080/api/webhook/task-a?secret=${SECRET}`,
  );
  expect(document.body.innerHTML).not.toContain(SECRET);
});

it('makes it reachable from the internet only after an explicit confirmation (parity row 52)', async () => {
  const callbacks = show();
  expect(
    await screen.findByText('Not reachable from the internet.'),
  ).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Make reachable from the internet' }),
  );
  const confirm = await screen.findByRole('alertdialog', {
    name: 'Make Row-Bot reachable from the internet?',
  });
  expect(confirm).toHaveTextContent(/anyone with this webhook address/i);
  expect(confirm).toHaveTextContent(/sign-in/i);
  fireEvent.click(within(confirm).getByRole('button', { name: 'Cancel' }));
  expect(callbacks.setPublic).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Make reachable from the internet' }),
  );
  await act(async () =>
    fireEvent.click(
      within(
        await screen.findByRole('alertdialog', {
          name: 'Make Row-Bot reachable from the internet?',
        }),
      ).getByRole('button', { name: 'Make reachable' }),
    ),
  );
  expect(callbacks.setPublic).toHaveBeenCalledWith(true);
  expect(
    screen.getByText(/Reachable from the internet at public\.example\.invalid/),
  ).toBeInTheDocument();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Copy public address' }),
    ),
  );
  expect(callbacks.writeClipboard).toHaveBeenLastCalledWith(
    'https://public.example.invalid/api/webhook/task-a',
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Stop public access' })),
  );
  expect(callbacks.setPublic).toHaveBeenLastCalledWith(false);
  expect(
    screen.getByText('Not reachable from the internet.'),
  ).toBeInTheDocument();
});

it('says who can change reachability and shows start failures in place', async () => {
  show({
    loadTunnel: vi.fn(async () => ({ publicBase: null, canControl: false })),
  });
  expect(
    await screen.findByText(
      /Only Row-Bot's owner on this computer can make it reachable/,
    ),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Make reachable from the internet' }),
  ).toBeNull();
});

it('keeps the address private when the tunnel cannot start', async () => {
  show({
    setPublic: vi.fn(async () => {
      throw { code: 'tunnel_unavailable' };
    }),
  });
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Make reachable from the internet',
    }),
  );
  await act(async () =>
    fireEvent.click(
      within(
        await screen.findByRole('alertdialog', {
          name: 'Make Row-Bot reachable from the internet?',
        }),
      ).getByRole('button', { name: 'Make reachable' }),
    ),
  );
  expect(screen.getByRole('alert')).toBeInTheDocument();
  expect(
    screen.getByText('Not reachable from the internet.'),
  ).toBeInTheDocument();
});
