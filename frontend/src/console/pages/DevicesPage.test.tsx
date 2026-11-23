import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { renderConsole } from '../testing';

afterEach(() => vi.unstubAllGlobals());

test('devices list who, platform, version and last seen, and filter by person and revoked', async () => {
  const api = renderConsole('/devices');
  const list = await screen.findByRole('list', { name: 'Devices' });
  expect(within(list).getAllByRole('listitem')).toHaveLength(3);
  expect(within(list).getAllByRole('listitem')[0]).toHaveTextContent(
    'Ada Lovelace · Windows · Row-Bot 5.4.0 · Last seen',
  );

  await userEvent.click(screen.getByRole('radio', { name: 'Revoked' }));
  expect(await screen.findByText('Old laptop')).toBeInTheDocument();
  await userEvent.selectOptions(
    screen.getByRole('combobox', { name: 'Person' }),
    'Ada Lovelace',
  );
  expect(
    await screen.findByText('No devices match these filters.'),
  ).toBeInTheDocument();
  expect(api.calls.at(-1)?.path).toBe(
    '/api/admin/devices?person=22222222-2222-4222-8222-222222222222&revoked=true',
  );
});

test('revoking asks first and says what happens', async () => {
  const api = renderConsole('/devices');
  await userEvent.click(
    await screen.findByRole('button', { name: 'Revoke Build box' }),
  );
  const confirm = screen.getByRole('alertdialog');
  expect(confirm).toHaveTextContent("Revoke Grace Hopper's Build box?");
  expect(confirm).toHaveTextContent(
    'Row-Bot on this device can no longer reach the company server.',
  );
  await userEvent.click(
    within(confirm).getByRole('button', { name: 'Revoke' }),
  );
  expect(await screen.findByText('Build box is revoked')).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Revoke Build box' }),
  ).not.toBeInTheDocument();
  expect(
    api.calls.some(
      (call) => call.method === 'POST' && call.path.endsWith('/revoke'),
    ),
  ).toBe(true);
});

test('no devices yet says so', async () => {
  renderConsole('/devices', 'empty');
  expect(
    await screen.findByRole('heading', { name: 'No active devices' }),
  ).toBeInTheDocument();
});

test('a list that cannot load offers to try again', async () => {
  let fail = true;
  renderConsole('/devices', 'populated', (url) =>
    fail && url.pathname === '/api/admin/devices'
      ? { status: 500, body: { error: 'internal' } }
      : undefined,
  );
  const error = await screen.findByRole('alert');
  expect(error).toHaveTextContent("Couldn't load devices");
  fail = false;
  await userEvent.click(
    within(error).getByRole('button', { name: 'Try again' }),
  );
  expect(
    await screen.findByRole('list', { name: 'Devices' }),
  ).toBeInTheDocument();
});
