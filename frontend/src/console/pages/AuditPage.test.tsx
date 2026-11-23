import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { renderConsole } from '../testing';

afterEach(() => vi.unstubAllGlobals());

test('events read as sentences, never as action codes', async () => {
  renderConsole('/audit');
  const list = await screen.findByRole('list', { name: 'Audit events' });
  expect(
    within(list)
      .getAllByRole('listitem')
      .map((row) => row.querySelector('.entity-row-title')?.textContent),
  ).toEqual([
    'Sam Rivera signed in',
    'Ada Lovelace used company-chat (1,540 tokens)',
    'Sam Rivera changed the company policy',
    'Ada Lovelace signed in',
    'Sam Rivera suspended Lin Okafor',
    'Sam Rivera signed Lin Okafor out everywhere',
  ]);
  expect(list).not.toHaveTextContent('session.signed_in');

  await userEvent.click(
    screen.getByRole('button', { name: 'Show older events' }),
  );
  expect(
    await within(list).findByText(
      "The server's operator made Sam Rivera an admin",
    ),
  ).toBeInTheDocument();
  expect(
    within(list).getByText('Lin Okafor was refused sign-in'),
  ).toBeInTheDocument();
  expect(within(list).getByText('Refused')).toBeInTheDocument();
  expect(
    within(list).getByText('Sam Rivera added a model endpoint'),
  ).toBeInTheDocument();
  expect(
    within(list).getByText('Ada Lovelace joined a Windows device'),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Show older events' }),
  ).not.toBeInTheDocument();
});

test('the integrity check says plainly where the log breaks', async () => {
  renderConsole('/audit', 'populated', (url) =>
    url.pathname === '/api/admin/audit/verify'
      ? {
          status: 200,
          body: {
            ok: false,
            checked: 311,
            head: 'f'.repeat(64),
            broken_at: 312,
            reason: 'hash',
          },
        }
      : undefined,
  );
  await userEvent.click(
    await screen.findByRole('button', { name: 'Check integrity' }),
  );
  expect(
    await screen.findByText('The audit log is broken at event 312'),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      /an event there was changed\. The 311 events before it check out/,
    ),
  ).toBeInTheDocument();
});

test('filters go to the server, and the export link carries them', async () => {
  const api = renderConsole('/audit');
  await screen.findByRole('list', { name: 'Audit events' });
  await userEvent.selectOptions(
    screen.getByRole('combobox', { name: 'Who' }),
    'Ada Lovelace',
  );
  await userEvent.selectOptions(
    screen.getByRole('combobox', { name: 'What' }),
    'Signed in',
  );
  expect(await screen.findByText('Ada Lovelace signed in')).toBeInTheDocument();
  expect(api.calls.at(-1)?.path).toBe(
    '/api/admin/audit?action=session.signed_in&actor=22222222-2222-4222-8222-222222222222&limit=50',
  );
  expect(
    screen.getByRole('link', { name: 'Export as JSON Lines' }),
  ).toHaveAttribute(
    'href',
    '/api/admin/audit/export?action=session.signed_in&actor=22222222-2222-4222-8222-222222222222',
  );
  await userEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
  expect(screen.getByRole('combobox', { name: 'Who' })).toHaveValue('');
});

test('an empty log says so', async () => {
  renderConsole('/audit', 'empty');
  expect(
    await screen.findByRole('heading', { name: 'Nothing recorded yet' }),
  ).toBeInTheDocument();
});
