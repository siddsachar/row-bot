import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import ChannelSettings, {
  createChannelSettingsSession,
  type ChannelPage,
} from './ChannelSettings';
import {
  AccountsSnapshotPanel,
  SettingsDraftOwner,
  type SettingsMutationIO,
} from './SettingsSnapshotPanels';

const revision = 'a'.repeat(64);

const telegram: ChannelPage = {
  schema_version: 1,
  total: 1,
  truncated: false,
  items: [
    {
      schema_version: 1,
      channel_id: 'telegram',
      display_name: 'Telegram',
      source: { kind: 'core', label: '' },
      revision,
      configured: true,
      running: true,
      activity: 'none',
      activity_history: [],
      fields: [
        {
          key: 'bot_token',
          label: 'Bot Token',
          field_type: 'password',
          storage: 'env',
          help_text: '',
          configured: true,
          source: 'environment',
          fingerprint: '',
          externally_managed: false,
          writable: true,
        },
        {
          key: 'user_id',
          label: 'Your Telegram User ID',
          field_type: 'text',
          storage: 'env',
          help_text: '',
          configured: false,
          source: '',
          fingerprint: '',
          externally_managed: false,
          writable: true,
        },
      ],
      paired_identities: [],
      capabilities: [],
      link_state: null,
      public_address: null,
      reachability_problem: null,
      can_test: true,
      availability: {
        configuration: 'available',
        lifecycle: 'available',
        pairing: 'unsupported',
        monitor: 'available',
      },
    },
  ],
};

afterEach(() => vi.restoreAllMocks());

it('walks a channel through numbered steps, says what the environment supplies and sends a test message only when asked', async () => {
  const review = vi.fn(async (payload) => ({
    channel_id: payload.channel_id,
    revision: payload.revision,
    operation: payload.operation,
    field_key: payload.field_key,
    identity_id: payload.identity_id,
    action_digest: 'c'.repeat(64),
    review_id: 'review-a',
  }));
  const execute = vi.fn(async (command) => ({
    command_id: command.command_id,
    status: 'completed',
    operation: command.payload.operation,
  }));
  render(
    <MemoryRouter>
      <ChannelSettings
        session={createChannelSettingsSession()}
        load={vi.fn().mockResolvedValue(telegram)}
        review={review}
        execute={execute}
      />
    </MemoryRouter>,
  );
  const panel = await screen.findByLabelText('Telegram channel');
  fireEvent.click(panel.querySelector('summary')!);
  const sheet = within(panel).getByRole('region', { name: 'Connect Telegram' });
  const steps = within(sheet).getAllByRole('listitem');
  // Numbered steps with the link where each one happens.
  expect(steps[0]).toHaveTextContent(/message @BotFather/);
  expect(
    within(steps[0]).getByRole('link', { name: /Open @BotFather/ }),
  ).toHaveAttribute('href', 'https://t.me/BotFather');
  // A field supplied by the environment says so, and can't be cleared here.
  expect(steps[1]).toHaveTextContent('Supplied by the environment');
  expect(
    within(steps[1]).queryByRole('button', { name: 'Clear Bot Token' }),
  ).toBeNull();
  expect(
    within(steps[2]).getByLabelText(/New Your Telegram User ID/),
  ).toBeInTheDocument();
  // Nothing is sent until the person asks, and confirms.
  expect(review).not.toHaveBeenCalled();
  fireEvent.click(
    within(sheet).getByRole('button', { name: 'Send a test message to me' }),
  );
  const confirm = await screen.findByRole('dialog', {
    name: /Send a test message to you on Telegram/,
  });
  expect(review).not.toHaveBeenCalled();
  fireEvent.click(within(confirm).getByRole('button', { name: 'Cancel' }));
  expect(review).not.toHaveBeenCalled();
  fireEvent.click(
    within(sheet).getByRole('button', { name: 'Send a test message to me' }),
  );
  await act(async () =>
    fireEvent.click(
      within(
        await screen.findByRole('dialog', {
          name: /Send a test message to you on Telegram/,
        }),
      ).getByRole('button', { name: 'Send test message' }),
    ),
  );
  expect(review).toHaveBeenCalledOnce();
  expect(review.mock.calls[0][0]).toMatchObject({
    channel_id: 'telegram',
    operation: 'test',
    field_key: null,
    value: null,
  });
  expect(execute).toHaveBeenCalledOnce();
  expect(await screen.findByText(/Test message sent/)).toBeInTheDocument();
});

const account = {
  enabled: null,
  configured: false,
  authentication_state: 'not_configured' as const,
  credential: null,
  operations: [],
  read_operations: [],
  post_operations: [],
  engage_operations: [],
};

it('copies the X callback address from its connect sheet', async () => {
  const writeText = vi.fn(async () => undefined);
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText },
  });
  const mutation = {
    revision: 'settings-a',
    page: 'accounts',
    review: vi.fn(),
    execute: vi.fn(),
    receipt: vi.fn(),
    drafts: new SettingsDraftOwner(),
    onSnapshot: vi.fn(),
  } as unknown as SettingsMutationIO;
  render(
    <MemoryRouter>
      <AccountsSnapshotPanel
        mutation={mutation}
        snapshot={{
          availability: 'available',
          github: { ...account, account_id: 'github' },
          gmail: { ...account, account_id: 'gmail' },
          calendar: { ...account, account_id: 'calendar' },
          x: {
            ...account,
            account_id: 'x',
            callback_url: 'http://127.0.0.1:17638/callback',
          },
        }}
      />
    </MemoryRouter>,
  );
  const sheet = screen.getByRole('region', { name: 'Connect X' });
  expect(sheet).toHaveTextContent('http://127.0.0.1:17638/callback');
  expect(
    within(sheet).getByRole('link', { name: /Open the developer portal/ }),
  ).toHaveAttribute('target', '_blank');
  await act(async () =>
    fireEvent.click(
      within(sheet).getByRole('button', { name: 'Copy X callback address' }),
    ),
  );
  expect(writeText).toHaveBeenCalledWith('http://127.0.0.1:17638/callback');
  expect(within(sheet).getByRole('status')).toHaveTextContent('Copied.');
  expect(mutation.review).not.toHaveBeenCalled();
});
