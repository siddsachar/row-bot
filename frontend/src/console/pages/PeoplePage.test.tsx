import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { renderConsole } from '../testing';

afterEach(() => vi.unstubAllGlobals());

const ADA = '22222222-2222-4222-8222-222222222222';

test('people are listed with their email, groups, admin and suspended state', async () => {
  renderConsole('/people');
  const list = await screen.findByRole('list', { name: 'People' });
  const rows = within(list).getAllByRole('listitem');
  expect(
    rows.map((row) => row.querySelector('.entity-row-title')?.textContent),
  ).toEqual(['Ada Lovelace', 'Grace Hopper', 'Lin Okafor', 'Sam Rivera']);
  expect(rows[0]).toHaveTextContent(
    'ada@example.com · Engineering, Sales · Signed in',
  );
  expect(rows[1]).toHaveTextContent('Admin');
  expect(rows[2]).toHaveTextContent('Suspended');

  await userEvent.type(
    screen.getByRole('searchbox', { name: 'Search people' }),
    'sales',
  );
  expect(within(list).getAllByRole('listitem')).toHaveLength(2);
  await userEvent.clear(
    screen.getByRole('searchbox', { name: 'Search people' }),
  );
  await userEvent.type(
    screen.getByRole('searchbox', { name: 'Search people' }),
    'nobody',
  );
  expect(screen.getByText('No one matches “nobody”.')).toBeInTheDocument();
});

test('you cannot act on yourself, and the reason is the action’s name', async () => {
  const api = renderConsole('/people');
  const self = await screen.findByRole('button', {
    name: "You can't suspend yourself",
  });
  expect(self).toHaveAttribute('aria-disabled', 'true');
  expect(
    screen.getByRole('button', {
      name: "You can't change your own admin role",
    }),
  ).toHaveAttribute('aria-disabled', 'true');
  // An admin through a provider group is changed in the provider, not here.
  expect(
    screen.getByRole('button', {
      name: 'Admin through a provider group: change it in your identity provider',
    }),
  ).toHaveAttribute('aria-disabled', 'true');
  await userEvent.click(self);
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  expect(api.calls.filter((call) => call.method !== 'GET')).toEqual([]);
});

test('suspending asks first, says what happens, and shows the result', async () => {
  const api = renderConsole('/people');
  await userEvent.click(
    await screen.findByRole('button', { name: 'Suspend Ada Lovelace' }),
  );
  const confirm = screen.getByRole('alertdialog');
  expect(confirm).toHaveTextContent('Suspend Ada Lovelace?');
  expect(confirm).toHaveTextContent(
    'Suspending signs Ada Lovelace out everywhere.',
  );

  await userEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel' }),
  );
  expect(api.calls.some((call) => call.method === 'POST')).toBe(false);

  await userEvent.click(
    screen.getByRole('button', { name: 'Suspend Ada Lovelace' }),
  );
  await userEvent.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'Suspend',
    }),
  );
  expect(
    await screen.findByRole('button', { name: 'Resume Ada Lovelace' }),
  ).toBeInTheDocument();
  expect(api.calls.map((call) => `${call.method} ${call.path}`)).toContain(
    `POST /api/admin/people/${ADA}/suspend`,
  );
});

test('making an admin asks first and sends the role', async () => {
  const api = renderConsole('/people');
  await userEvent.click(
    await screen.findByRole('button', { name: 'Make Ada Lovelace an admin' }),
  );
  const confirm = screen.getByRole('alertdialog');
  expect(confirm).toHaveTextContent('Make Ada Lovelace an admin?');
  await userEvent.click(
    within(confirm).getByRole('button', { name: 'Make admin' }),
  );
  expect(
    await screen.findByRole('button', {
      name: "Remove Ada Lovelace's admin role",
    }),
  ).toBeInTheDocument();
  expect(
    api.calls.find((call) => call.path === `/api/admin/people/${ADA}/role`)
      ?.body,
  ).toEqual({
    role: 'admin',
  });
});

test('a refused change says why in plain words', async () => {
  renderConsole('/people', 'populated', (url, method) =>
    method === 'POST'
      ? { status: 404, body: { error: 'person_not_found' } }
      : undefined,
  );
  await userEvent.click(
    await screen.findByRole('button', { name: 'Suspend Ada Lovelace' }),
  );
  await userEvent.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'Suspend',
    }),
  );
  expect(
    await screen.findByText('That person is no longer on the server.'),
  ).toBeInTheDocument();
});
