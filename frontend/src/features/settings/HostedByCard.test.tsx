import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { OnboardingSnapshot } from '../../api/types';
import { RuntimeContext } from '../../runtime';
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
    'New versions install by themselves each night when automatic updates are on in Railway.',
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
    within(card).getByText('Row-Bot 5.3.2 · updates come from Railway'),
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

it('says memory in the unit the host set: Railway’s 8 GB limit is 8 GB, not 7.5 (F1)', () => {
  render(
    <HostedByCard
      host={railway}
      memory={{ limit_bytes: 8_000_000_000, used_bytes: 1_100_000_000 }}
      version="5.3.3"
    />,
  );
  const card = screen.getByRole('region', { name: 'Hosted by Railway' });
  expect(within(card).getByText('Memory 1.1 GB of 8 GB')).toBeVisible();
});

const railwayService = {
  ...railway,
  manage_url:
    'https://railway.com/project/0f6e3c1a-5b2d-4c8e-9a71-2d4b6f8e1c30/service/7a1c9e42-3f5b-4d6a-8e20-b1c3d5e7f901?environmentId=c2e4a6b8-1d3f-4a5c-9e7b-0f2d4c6e8a10',
  settings_url:
    'https://railway.com/project/0f6e3c1a-5b2d-4c8e-9a71-2d4b6f8e1c30/service/7a1c9e42-3f5b-4d6a-8e20-b1c3d5e7f901/settings?environmentId=c2e4a6b8-1d3f-4a5c-9e7b-0f2d4c6e8a10',
  usage_url: 'https://railway.com/workspace/usage',
};

it('on Railway links the service itself and its usage, as icon buttons, and no backups it doesn’t make (A2)', () => {
  render(
    <HostedByCard
      host={railwayService}
      memory={{ limit_bytes: 8_000_000_000, used_bytes: 1_100_000_000 }}
      version="5.3.3"
    />,
  );
  const card = screen.getByRole('region', { name: 'Hosted by Railway' });
  const links = within(card)
    .getAllByRole('link')
    .filter((link) => link.textContent === '');
  expect(
    links.map((link) => [
      link.getAttribute('aria-label'),
      link.getAttribute('href'),
    ]),
  ).toEqual([
    ['Manage your service in Railway', railwayService.manage_url],
    ['Usage in Railway', 'https://railway.com/workspace/usage'],
    ['Railway support', 'https://station.railway.com/'],
    ['Railway status', 'https://status.railway.com/'],
  ]);
  for (const link of links) expect(link).toHaveAttribute('target', '_blank');
  expect(within(card).queryByText(/trial/)).toBeNull();
});

it('on Railway’s trial says what 1 GB runs, with the way to upgrade (A4)', () => {
  render(
    <HostedByCard
      host={{
        ...railwayService,
        trial: {
          note: "Railway's trial gives Row-Bot 1 GB: chats, memory and schedules work. Browser tasks and local speech-to-text need Railway's Hobby plan.",
          upgrade_url: 'https://railway.com/workspace/plans',
        },
      }}
      memory={{ limit_bytes: 999_997_440, used_bytes: 300_000_000 }}
      version="5.3.3"
    />,
  );
  const card = screen.getByRole('region', { name: 'Hosted by Railway' });
  expect(within(card).getByText('Memory 0.3 GB of 1 GB')).toBeVisible();
  const line = within(card).getByText(/Railway’s trial|Railway's trial/);
  expect(line.closest('p')).toHaveTextContent(
    "Railway's trial gives Row-Bot 1 GB: chats, memory and schedules work. Browser tasks and local speech-to-text need Railway's Hobby plan. Upgrade in Railway",
  );
  expect(
    within(card).getByRole('link', { name: 'Upgrade in Railway' }),
  ).toHaveAttribute('href', 'https://railway.com/workspace/plans');
});

const setup: OnboardingSnapshot = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  setup_complete: true,
  needs_model: false,
  default_model: 'model:codex:gpt-5.6-sol',
  live_done: [],
  import_sources: [],
  starter_workflows_missing: 0,
  profile: [],
  completed_steps: [],
  skipped_steps: [],
  dismissed_home_card: false,
  dismissed_phone_card: false,
  // The first chat with an answer, as the phone card claimed it (U10).
  phone_card_conversation: 'conversation-1',
  host_name: 'Railway',
  steps: [],
  intents: [],
};

function withRuntime(onboarding: Partial<OnboardingSnapshot>) {
  const controller = {
    onboarding: vi.fn(async () => ({ ...setup, ...onboarding })),
    onboardingCommand: vi.fn(async () => ({
      schema_version: 1,
      command_id: crypto.randomUUID(),
      status: 'completed',
      snapshot: { ...setup, ...onboarding, dismissed_spending_limit: true },
    })),
  };
  render(
    <RuntimeContext.Provider value={{ controller } as never}>
      <HostedByCard
        host={{
          ...railwayService,
          spending_limit_step: 'Set a spending limit in Railway',
        }}
        memory={{ limit_bytes: 8_000_000_000, used_bytes: 1_100_000_000 }}
        version="5.3.3"
      />
    </RuntimeContext.Provider>,
  );
  return controller;
}

it('after the first answer, offers Railway’s one step, a spending limit, and hides it for good (A3)', async () => {
  const controller = withRuntime({});
  const card = screen.getByRole('region', { name: 'Hosted by Railway' });
  const step = await within(card).findByRole('link', {
    name: 'Set a spending limit in Railway',
  });
  expect(step).toHaveAttribute('href', 'https://railway.com/workspace/usage');
  expect(step).toHaveAttribute('target', '_blank');

  fireEvent.click(
    within(card).getByRole('button', { name: 'Done. Don’t show this again' }),
  );
  expect(
    within(card).queryByRole('link', {
      name: 'Set a spending limit in Railway',
    }),
  ).toBeNull();
  await waitFor(() =>
    expect(controller.onboardingCommand).toHaveBeenCalledWith(
      expect.objectContaining({
        action: 'dismiss_spending_limit',
        expected_revision: setup.revision,
      }),
    ),
  );
});

it.each([
  ['before the first answer', { phone_card_conversation: null }],
  ['once hidden', { dismissed_spending_limit: true }],
])('says nothing of a spending limit %s (A3)', async (_when, onboarding) => {
  const controller = withRuntime(onboarding);
  await waitFor(() => expect(controller.onboarding).toHaveBeenCalled());
  await Promise.resolve();
  expect(screen.queryByRole('link', { name: /spending limit/ })).toBeNull();
  expect(screen.queryByRole('button', { name: /Don’t show/ })).toBeNull();
});

it('a host without the step, or without its usage page, never asks (A3)', () => {
  render(
    <HostedByCard
      host={{
        ...railwayService,
        spending_limit_step: 'Set a spending limit in Railway',
        usage_url: null,
      }}
      memory={null}
      version="5.3.3"
    />,
  );
  expect(screen.queryByRole('link', { name: /spending limit/ })).toBeNull();
});
