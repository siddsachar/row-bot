import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { renderConsole } from './testing';

afterEach(() => vi.unstubAllGlobals());

test('a lost session sends the person to sign in again, back to this page', async () => {
  renderConsole('/audit', 'populated', (url) =>
    url.pathname === '/api/me'
      ? { status: 401, body: { error: 'sign_in_required' } }
      : undefined,
  );
  expect(
    await screen.findByRole('heading', { name: 'Your session ended' }),
  ).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Sign in again' })).toHaveAttribute(
    'href',
    '/auth/login?next=%2Fconsole%2Faudit',
  );
});

test('a session that ends while working shows the same way out', async () => {
  renderConsole('/devices', 'populated', (url) =>
    url.pathname === '/api/admin/devices'
      ? { status: 401, body: { error: 'sign_in_required' } }
      : undefined,
  );
  expect(
    await screen.findByRole('heading', { name: 'Your session ended' }),
  ).toBeInTheDocument();
});

test('a member who is no longer an admin is told so, not shown a broken console', async () => {
  renderConsole('/people', 'populated', (url) =>
    url.pathname === '/api/admin/people'
      ? { status: 403, body: { error: 'admin_required' } }
      : undefined,
  );
  expect(
    await screen.findByRole('heading', { name: 'You need to be an admin' }),
  ).toBeInTheDocument();
  expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
});

test('a console that cannot open says why and can try again', async () => {
  let fail = true;
  renderConsole('/people', 'populated', (url) =>
    fail && url.pathname === '/api/admin/people'
      ? { status: 503, body: { error: 'database_unavailable' } }
      : undefined,
  );
  const error = await screen.findByRole('alert');
  expect(error).toHaveTextContent("Couldn't open the admin console");
  expect(error).toHaveTextContent('The company server had a problem');
  fail = false;
  await userEvent.click(
    within(error).getByRole('button', { name: 'Try again' }),
  );
  expect(
    await screen.findByRole('heading', { name: 'People' }),
  ).toBeInTheDocument();
  expect(
    within(screen.getByRole('navigation', { name: 'Admin console' }))
      .getAllByRole('link')
      .map((link) => link.textContent),
  ).toEqual(['People', 'Devices', 'Models', 'Review', 'Policy', 'Audit']);
});

test('signing out ends the session and says so', async () => {
  const api = renderConsole('/people');
  await userEvent.click(
    await screen.findByRole('button', { name: 'Sign out' }),
  );
  expect(
    await screen.findByRole('heading', { name: "You're signed out" }),
  ).toBeInTheDocument();
  expect(
    api.calls.some(
      (call) => call.method === 'POST' && call.path === '/auth/logout',
    ),
  ).toBe(true);
});
