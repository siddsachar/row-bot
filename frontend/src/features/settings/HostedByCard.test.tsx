import { render, screen, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import HostedByCard from './HostedByCard';

const GB = 2 ** 30;
const pikapods = {
  id: 'pikapods',
  name: 'PikaPods',
  home_url: 'https://www.pikapods.com/',
  support_url: 'https://docs.pikapods.com/',
  status_url:
    'https://docs.pikapods.com/faq/account#business-use-and-service-level',
  manage_url: 'https://www.pikapods.com/pods',
  unit: 'pod',
  runs: 'the server, billing and backups',
  applies_updates: true,
  updates_help:
    'They test each release first, so new versions usually arrive within a week.',
};
const railway = {
  id: 'railway',
  name: 'Railway',
  home_url: 'https://railway.com/',
  support_url: 'https://station.railway.com/',
  status_url: 'https://status.railway.com/',
  manage_url: 'https://railway.com/dashboard',
  unit: 'service',
  runs: 'the server and billing',
  applies_updates: false,
  updates_help:
    "When a new version is out, Railway offers it in your service's settings. Turn on auto updates there to get each one by itself.",
};

it('says who runs Row-Bot, its version, the memory in use and the host’s own pages (U5)', () => {
  render(
    <HostedByCard
      host={pikapods}
      memory={{ limit_bytes: 4 * GB, used_bytes: 1.4 * GB }}
      version="5.3.2"
    />,
  );
  const card = screen.getByRole('region', { name: 'Hosted by PikaPods' });
  expect(within(card).getByText('Hosted by PikaPods')).toBeVisible();
  expect(
    within(card).getByText('Row-Bot 5.3.2 · updated by PikaPods'),
  ).toBeVisible();
  expect(within(card).getByText('Memory 1.4 GB of 4 GB')).toBeVisible();
  const bar = within(card).getByRole('progressbar', { name: 'Memory in use' });
  expect(bar).toHaveAttribute('max', String(4 * GB));
  expect(bar).toHaveAttribute('value', String(1.4 * GB));
  // Icon buttons, named by their tooltips, open the host's pages in a new tab.
  const links = [
    ['Manage your pod in PikaPods', pikapods.manage_url],
    ['PikaPods support', pikapods.support_url],
    ['PikaPods status', pikapods.status_url],
  ];
  for (const [name, url] of links) {
    const link = within(card).getByRole('link', { name });
    expect(link).toHaveAttribute('href', url);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(link.textContent).toBe('');
  }
  expect(card).toHaveTextContent(
    'PikaPods runs the server, billing and backups. For anything inside Row-Bot, use Row-Bot’s help.',
  );
  expect(
    within(card).getByRole('link', { name: 'Row-Bot’s help' }),
  ).toHaveAttribute('href', 'https://row-bot.ai/docs/');
});

it('shows only the links the host confirmed, only web pages, and no memory row without a limit', () => {
  render(
    <HostedByCard
      host={{
        ...pikapods,
        support_url: null,
        status_url: 'javascript:alert(1)',
      }}
      memory={null}
      version="5.3.2"
    />,
  );
  const card = screen.getByRole('region', { name: 'Hosted by PikaPods' });
  expect(
    within(card).getByRole('link', { name: 'Manage your pod in PikaPods' }),
  ).toBeVisible();
  expect(within(card).queryByRole('link', { name: /support|status/ })).toBe(
    null,
  );
  expect(within(card).queryByText(/Memory/)).toBeNull();
  expect(within(card).queryByRole('progressbar')).toBeNull();
  expect(
    within(card).getByText('Row-Bot 5.3.2 · updated by PikaPods'),
  ).toBeVisible();
});

it('speaks in the host’s own words: Railway’s service, which its owner updates (R1)', () => {
  render(
    <HostedByCard
      host={railway}
      memory={{ limit_bytes: 4 * GB, used_bytes: 0.9 * GB }}
      version="5.3.2"
    />,
  );
  const card = screen.getByRole('region', { name: 'Hosted by Railway' });
  expect(
    within(card).getByText('Row-Bot 5.3.2 · you update it in Railway'),
  ).toBeVisible();
  expect(
    within(card).getByRole('link', { name: 'Manage your service in Railway' }),
  ).toHaveAttribute('href', 'https://railway.com/dashboard');
  expect(
    within(card).getByRole('link', { name: 'Railway support' }),
  ).toHaveAttribute('href', 'https://station.railway.com/');
  expect(card).toHaveTextContent(
    'Railway runs the server and billing. For anything inside Row-Bot, use Row-Bot’s help.',
  );
  expect(card.textContent).not.toMatch(/pod|backups/);
});
