import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { LocalAddressGrantView, LocalAddressPage } from '../../api/types';
import { LocalAddresses, timeLeft } from './LocalAddresses';

afterEach(cleanup);

const inAnHour = new Date(Date.now() + 42 * 60_000).toISOString();

function grant(
  patch: Partial<LocalAddressGrantView> = {},
): LocalAddressGrantView {
  return {
    grant_id: 'a'.repeat(32),
    address: 'localhost:5173',
    origin: 'http://localhost:5173',
    source: 'you',
    granted_at: new Date().toISOString(),
    expires_at: inAnHour,
    ...patch,
  };
}

function page(items: LocalAddressGrantView[]): LocalAddressPage {
  return { conversation_id: 'conversation-a', items };
}

it('lists what this chat may open, who allowed it and when it ends', async () => {
  const load = vi.fn(async () =>
    page([
      grant(),
      grant({
        grant_id: 'b'.repeat(32),
        address: '127.0.0.1:8000',
        source: 'row_bot_server',
      }),
      grant({
        grant_id: 'c'.repeat(32),
        address: 'localhost:3000',
        source: 'reviewer',
      }),
    ]),
  );
  render(
    <LocalAddresses
      conversationId="conversation-a"
      load={load}
      remove={vi.fn()}
    />,
  );

  const list = await screen.findByRole('list', {
    name: 'Local addresses allowed in this chat',
  });
  const rows = within(list).getAllByRole('listitem');
  expect(rows).toHaveLength(3);
  expect(rows[0]).toHaveTextContent('localhost:5173');
  expect(rows[0]).toHaveTextContent('Allowed by you · 42 min left');
  expect(rows[1]).toHaveTextContent('Row-Bot started this server');
  expect(rows[2]).toHaveTextContent('Allowed by the reviewer');
  expect(load).toHaveBeenCalledWith('conversation-a', expect.anything());
  expect(
    screen.getByText(/Other computers and devices on your network never open/),
  ).toBeInTheDocument();
});

it('removes one and says what that means', async () => {
  const remove = vi.fn(async () => page([]));
  render(
    <LocalAddresses
      conversationId="conversation-a"
      load={vi.fn(async () => page([grant()]))}
      remove={remove}
    />,
  );

  fireEvent.click(
    await screen.findByRole('button', { name: 'Remove localhost:5173' }),
  );

  expect(
    await screen.findByText(
      'localhost:5173 removed. Row-Bot asks again before opening it.',
    ),
  ).toBeInTheDocument();
  expect(remove).toHaveBeenCalledWith('conversation-a', 'a'.repeat(32));
  expect(
    screen.getByText(/No addresses on this computer are open/),
  ).toBeInTheDocument();
});

it('words the time left plainly', () => {
  const now = Date.parse('2026-10-14T10:00:00Z');
  expect(timeLeft('2026-10-14T10:42:00+00:00', now)).toBe('42 min left');
  expect(timeLeft('2026-10-14T11:00:00+00:00', now)).toBe('1 hour left');
  expect(timeLeft('2026-10-14T10:00:20+00:00', now)).toBe('1 min left');
});

it('says when nothing is allowed, and offers Try again when it cannot check', async () => {
  const load = vi
    .fn<() => Promise<LocalAddressPage>>()
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValue(page([]));
  render(
    <LocalAddresses
      conversationId="conversation-a"
      load={load}
      remove={vi.fn()}
    />,
  );

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent(
    "Local addresses couldn't be checked just now.",
  );
  fireEvent.click(within(alert).getByRole('button', { name: 'Try again' }));

  expect(
    await screen.findByText(
      /It asks before opening one, like localhost:5173\./,
    ),
  ).toBeInTheDocument();
});
