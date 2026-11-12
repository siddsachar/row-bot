import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import PhoneTabBar, { phoneTabFor } from './PhoneTabBar';

const pendingApprovals = vi.fn();
const attention = vi.fn();
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: { pendingApprovals, attention } }),
  useClientState: () => ({ conversations: [] }),
}));

it('puts each place under its tab, and nothing else under one', () => {
  expect(phoneTabFor('/', '')).toBe('home');
  expect(phoneTabFor('/', '?tab=overview')).toBe('home');
  expect(phoneTabFor('/', '?tab=workflows&workflow=w1')).toBe('workflows');
  expect(phoneTabFor('/library', '?kind=pages')).toBe('chats');
  expect(phoneTabFor('/settings/system', '')).toBe('settings');
  expect(phoneTabFor('/conversations/c1', '')).toBeNull();
});

it('reaches the four places in one tap and counts what needs you on Home', async () => {
  pendingApprovals.mockResolvedValue({
    items: [
      { id: 'a1', kind: 'approval', title: 'Commit changes' },
      { id: 'a2', kind: 'approval', title: 'Send the email' },
    ],
    total: 2,
  });
  attention.mockResolvedValue({
    problems: [{ id: 'p1', title: 'Ollama is not running' }],
  });
  render(
    <MemoryRouter>
      <PhoneTabBar current="chats" />
    </MemoryRouter>,
  );
  const main = screen.getByRole('navigation', { name: 'Main' });
  expect(
    ['Home', 'Chats', 'Workflows', 'Settings'].map((name) =>
      screen.getByRole('link', { name }).getAttribute('href'),
    ),
  ).toEqual(['/', '/library', '/?tab=workflows', '/settings']);
  expect(screen.getByRole('link', { name: 'Chats' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  await waitFor(() =>
    expect(
      screen.getByRole('link', { name: 'Home' }),
    ).toHaveAccessibleDescription('3 things need you'),
  );
  expect(main).toHaveTextContent('3');
});
