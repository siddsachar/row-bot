import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { CompanyView } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import CompanySettings from './CompanySettings';

const COMPANY = 'rowbot.example.test';

function view(extra: Partial<CompanyView> = {}): CompanyView {
  return {
    schema_version: 1,
    state: 'not_joined',
    company: null,
    origin: null,
    name: null,
    email: null,
    joined_at: null,
    computer_name: 'Fixture laptop',
    rejoining: false,
    failure: null,
    notice: null,
    on_this_computer: true,
    ...extra,
  };
}

const joined = (extra: Partial<CompanyView> = {}) =>
  view({
    state: 'joined',
    company: COMPANY,
    origin: `https://${COMPANY}`,
    name: 'Ada Lovelace',
    email: 'ada@example.test',
    joined_at: '2026-11-23T09:00:00+00:00',
    ...extra,
  });

/** A controller whose answers are given per action; it records what was asked. */
function show(answers: Partial<Record<string, CompanyView>>) {
  const company = vi.fn(async (action: string) => {
    const answer = answers[action];
    if (!answer) throw new Error(`unexpected ${action}`);
    return answer;
  });
  render(
    <OverlayProvider>
      <CompanySettings
        controller={{ company } as unknown as ClientController}
      />
    </OverlayProvider>,
  );
  return company;
}

it('not joined: one line on what joining does, the address and Join, and no call to any server', async () => {
  const company = show({ status: view() });

  const field = await screen.findByRole('textbox', {
    name: 'Your company’s Row-Bot server',
  });
  expect(
    screen.getByText(
      'Joining lets Row-Bot use your company’s models and rules. Your own chats and memory stay on this computer.',
    ),
  ).toBeVisible();
  const join = screen.getByRole('button', { name: 'Join' });
  expect(join).toBeDisabled();
  fireEvent.change(field, { target: { value: 'rowbot.example.test' } });
  expect(join).toBeEnabled();
  // The computer's name is behind a disclosure, filled in.
  expect(screen.getByText('This computer’s name')).toBeInTheDocument();
  expect(company.mock.calls.map(([action]) => action)).toEqual(['status']);
});

it('Join sends the address and this computer’s name, then waits for the browser with Cancel', async () => {
  const company = show({
    status: view(),
    join: view({ state: 'waiting', company: COMPANY }),
    cancel: view({ failure: 'cancelled' }),
  });

  const field = await screen.findByRole('textbox', {
    name: 'Your company’s Row-Bot server',
  });
  fireEvent.change(field, { target: { value: ' rowbot.example.test ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Join' }));

  expect(
    await screen.findByText('Finish signing in in your browser'),
  ).toBeVisible();
  expect(company).toHaveBeenCalledWith('join', {
    address: 'rowbot.example.test',
    device_name: 'Fixture laptop',
  });
  expect(
    screen.getByText(
      `Your browser opened ${COMPANY}’s sign-in. Press Connect there.`,
    ),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(await screen.findByText('Sign-in cancelled.')).toBeVisible();
  expect(company).toHaveBeenCalledWith('cancel', undefined);
});

it('joined: the company and the person, when it joined, one light check, and Leave behind a confirm', async () => {
  const company = show({
    status: joined(),
    check: joined(),
    leave: view({ notice: 'left', company: COMPANY }),
  });

  expect(await screen.findByText(COMPANY)).toBeVisible();
  expect(screen.getByText('Ada Lovelace · ada@example.test')).toBeVisible();
  expect(screen.getByText(/^Joined .*2026/)).toBeVisible();
  await waitFor(() => expect(company).toHaveBeenCalledWith('check'));

  fireEvent.click(screen.getByRole('button', { name: `Leave ${COMPANY}` }));
  const dialog = await screen.findByRole('alertdialog', {
    name: `Leave ${COMPANY}?`,
  });
  expect(dialog).toHaveTextContent(
    `This Row-Bot stops using ${COMPANY}’s models and rules. Your own chats and memory stay on this computer.`,
  );
  expect(company).not.toHaveBeenCalledWith('leave', undefined);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Leave' }));

  expect(
    await screen.findByText(`This Row-Bot left ${COMPANY}.`),
  ).toBeVisible();
  expect(company).toHaveBeenCalledWith('leave', undefined);
});

it('a Leave the server couldn’t hear says the company may still list it', async () => {
  show({ status: view({ notice: 'left_offline', company: COMPANY }) });

  expect(
    await screen.findByText(
      'This Row-Bot left. Your company may still list it until an admin removes it.',
    ),
  ).toBeVisible();
});

it('disconnected: a calm explanation and Sign in again, with no check', async () => {
  const company = show({
    status: joined({ state: 'disconnected' }),
    sign_in_again: joined({ state: 'waiting', rejoining: true }),
  });

  expect(
    await screen.findByText('Your company disconnected this Row-Bot'),
  ).toBeVisible();
  expect(
    screen.getByText(/Your own chats and memory are still here\./),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Sign in again' }));

  expect(
    await screen.findByText(/If it says the link can’t connect Row-Bot/),
  ).toBeVisible();
  expect(company.mock.calls.map(([action]) => action)).toEqual([
    'status',
    'sign_in_again',
  ]);
});

it('unreachable: a quiet note, still joined, and Try again', async () => {
  const company = show({
    status: joined({ state: 'unreachable' }),
    check: joined({ state: 'unreachable' }),
  });

  expect(
    await screen.findByText(
      `Can’t reach ${COMPANY} right now. Row-Bot stays joined.`,
    ),
  ).toBeVisible();
  await waitFor(() => expect(company).toHaveBeenCalledTimes(2));
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  await waitFor(() => expect(company).toHaveBeenCalledTimes(3));
  expect(
    screen.getByRole('button', { name: `Leave ${COMPANY}` }),
  ).toBeVisible();
});

it.each([
  [
    'not_https',
    'Use an https:// address. Your company’s server needs a secure connection.',
  ],
  [
    'not_company_server',
    'That doesn’t look like a Row-Bot company server. Check the address with your IT team.',
  ],
  [
    'unreachable',
    'Row-Bot couldn’t reach that address. Check it and your connection, then try again.',
  ],
  ['denied', 'The sign-in was cancelled on your company’s page.'],
  ['timed_out', 'The sign-in timed out. Try again when you’re ready.'],
  ['storage_unavailable', /this computer’s keychain is locked or unavailable/],
] as const)('says why Join didn’t join: %s', async (failure, words) => {
  show({ status: view({ failure }) });

  expect(await screen.findByText(words)).toBeVisible();
});

it('on a phone or another computer, Join is off and says where to join from', async () => {
  show({ status: view({ on_this_computer: false }) });

  expect(
    await screen.findByText(
      'Join from Row-Bot on Fixture laptop: the sign-in opens in that computer’s browser.',
    ),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Join' })).toBeDisabled();
  expect(
    screen.getByRole('textbox', { name: 'Your company’s Row-Bot server' }),
  ).toBeDisabled();
});

it('joined: one line says how many models the company offers, linking to Models', async () => {
  const company = vi.fn(async () => joined({ model_count: 3 }));
  render(
    <MemoryRouter>
      <OverlayProvider>
        <CompanySettings
          controller={{ company } as unknown as ClientController}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );

  expect(await screen.findByText(`3 models from ${COMPANY}`)).toBeVisible();
  expect(
    screen.getByRole('link', { name: `See ${COMPANY}’s models in Models` }),
  ).toHaveAttribute('href', '/settings/models#company-models');
});

it('joined with no models offered yet, the line says so', async () => {
  const company = vi.fn(async () => joined({ model_count: 0 }));
  render(
    <MemoryRouter>
      <OverlayProvider>
        <CompanySettings
          controller={{ company } as unknown as ClientController}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );

  expect(
    await screen.findByText(`No models from ${COMPANY} yet`),
  ).toBeVisible();
});

const rules: NonNullable<CompanyView['rules']> = [
  {
    setting: 'models.personal_api_keys',
    title: 'Allow personal API keys',
    help: 'Whether people may add their own API keys and custom endpoints.',
    value: 'Off',
  },
  {
    setting: 'offline.max_days',
    title: 'Offline days',
    help: 'Days a Row-Bot may run on its cached policy.',
    value: '7 days',
  },
];

it('joined: the company’s rules, in plain words, behind a disclosure', async () => {
  show({
    status: joined({ policy_state: 'current', rules }),
    check: joined({ policy_state: 'current', rules }),
  });

  const summary = await screen.findByText('Your company’s rules');
  expect(screen.getByText('Allow personal API keys')).not.toBeVisible();
  fireEvent.click(summary);
  expect(screen.getByText('Allow personal API keys')).toBeVisible();
  expect(screen.getByText('Off')).toBeVisible();
  expect(screen.getByText('Offline days')).toBeVisible();
  expect(screen.getByText('7 days')).toBeVisible();
  expect(screen.queryByText('Locked down')).toBeNull();
});

it('locked down: one calm line in place of the joined date, a Retry, and the safe rules in force', async () => {
  const locked = joined({
    state: 'unreachable',
    policy_state: 'locked_down',
    policy_reason: 'lease_expired',
    rules,
  });
  const company = show({ status: locked, check: locked });

  expect(
    await screen.findByText(
      `Row-Bot can’t reach ${COMPANY}, so it’s using only what’s safe on this computer until it can.`,
    ),
  ).toBeVisible();
  expect(screen.queryByText(/Row-Bot stays joined/)).toBeNull();
  expect(screen.queryByRole('alertdialog')).toBeNull(); // No modal.
  expect(screen.getByText('Locked down')).toBeVisible();
  await waitFor(() => expect(company).toHaveBeenCalledTimes(2));
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(company).toHaveBeenCalledTimes(3));
  expect(company).toHaveBeenLastCalledWith('check', undefined);
});

it('joined: one quiet line on what Row-Bot tells the company, with each field in plain words behind it', async () => {
  show({ status: joined(), check: joined() });

  const line = await screen.findByText(
    `Row-Bot tells ${COMPANY} which risky actions it takes, never what’s in your chats.`,
  );
  expect(line).toBeVisible();
  expect(screen.getByText('Actions that needed approval')).not.toBeVisible();
  fireEvent.click(line);
  expect(screen.getByText('Actions that needed approval')).toBeVisible();
  expect(screen.getByText('Exports')).toBeVisible();
  expect(screen.getByText('Lockdowns')).toBeVisible();
  expect(screen.getByText('Never')).toBeVisible();
  expect(
    screen.getByText(
      'What you or Row-Bot wrote, your files, what a tool was asked to do or what it found.',
    ),
  ).toBeVisible();
});
