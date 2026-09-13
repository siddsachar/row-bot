import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ChannelSettings, {
  createChannelSettingsSession,
  type ChannelPage,
  type ChannelReceipt,
} from './ChannelSettings';

const revision = 'a'.repeat(64);
const page: ChannelPage = {
  schema_version: 1,
  total: 1,
  truncated: false,
  items: [
    {
      schema_version: 1,
      channel_id: 'slack',
      display_name: 'Synthetic Slack',
      source: { kind: 'core', label: '' },
      revision,
      configured: true,
      running: false,
      activity: 'within_hour',
      activity_history: [{ kind: 'last_inbound', recency: 'within_hour' }],
      fields: [
        {
          key: 'bot_token',
          label: 'Bot token',
          field_type: 'password',
          storage: 'env',
          help_text: 'Saved securely',
          configured: true,
          source: 'channel keyring',
          fingerprint: 'fp:synthetic',
          externally_managed: false,
          writable: true,
        },
      ],
      paired_identities: [
        {
          identity_id: 'b'.repeat(64),
          display_name: 'Synthetic person',
          hint: '…3456',
        },
      ],
      capabilities: ['streaming', 'buttons'],
      availability: {
        configuration: 'available',
        lifecycle: 'available',
        pairing: 'available',
        monitor: 'available',
      },
    },
  ],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function options() {
  return {
    session: createChannelSettingsSession(),
    load: vi.fn().mockResolvedValue(page),
    review: vi.fn().mockImplementation(async (payload) => ({
      channel_id: payload.channel_id,
      revision: payload.revision,
      operation: payload.operation,
      field_key: payload.field_key,
      identity_id: payload.identity_id,
      action_digest: 'c'.repeat(64),
    })),
    execute: vi.fn().mockImplementation(async (command) => ({
      command_id: command.command_id,
      status: 'completed',
      operation: command.payload.operation,
      channel: page.items[0],
    })),
  };
}

it('loads only redacted passive status and does not review or execute', async () => {
  const props = options();
  render(<ChannelSettings {...props} />);
  await screen.findByRole('heading', { name: 'Synthetic Slack' });
  expect(
    screen.getByText(/Inbound activity within the last hour/),
  ).toBeVisible();
  expect(
    screen.getByText(/Saved via channel keyring \(fp:synthetic\)/),
  ).toBeVisible();
  expect(screen.getAllByText(/Synthetic person/)[0]).toHaveTextContent('…3456');
  expect(screen.getByLabelText(/New Bot token/)).toHaveValue('');
  expect(props.load).toHaveBeenCalledTimes(1);
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
});

it('keeps credential input write-only through review and clears it on completion', async () => {
  const props = options();
  render(<ChannelSettings {...props} />);
  const input = await screen.findByLabelText(/New Bot token/);
  fireEvent.change(input, { target: { value: 'private-replacement-token' } });
  fireEvent.click(
    screen.getByRole('button', { name: 'Review save Bot token' }),
  );
  await screen.findByRole('button', { name: 'Confirm channel action' });
  expect(props.review).toHaveBeenCalledWith(
    expect.objectContaining({
      operation: 'configure',
      field_key: 'bot_token',
      value: 'private-replacement-token',
    }),
    expect.any(AbortSignal),
  );
  expect(
    screen.queryByText('private-replacement-token'),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm channel action' }),
  );
  await screen.findByText('Channel action completed.');
  expect(props.execute).toHaveBeenCalledTimes(1);
  expect(input).toHaveValue('');
  expect(props.session.hasRetained()).toBe(false);
});

it('retains an uncertain original and checks it without another review', async () => {
  const props = options();
  props.execute
    .mockRejectedValueOnce(Error('synthetic response loss'))
    .mockImplementationOnce(async (command) => ({
      command_id: command.command_id,
      status: 'partial',
      code: 'channel_operation_unconfirmed',
    }));
  render(<ChannelSettings {...props} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Review start Synthetic Slack' }),
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Confirm channel action' }),
  );
  const retry = await screen.findByRole('button', {
    name: 'Check original channel action',
  });
  const original = props.execute.mock.calls[0];
  fireEvent.click(retry);
  await screen.findByText(/outcome is uncertain/);
  expect(props.execute.mock.calls[1]).toEqual(original);
  expect(props.review).toHaveBeenCalledTimes(1);
});

it('shows a pairing code only after explicit review and confirmation', async () => {
  const props = options();
  props.execute.mockImplementationOnce(async (command) => ({
    command_id: command.command_id,
    status: 'completed',
    operation: 'pair',
    pairing_code: 'PAIR1234',
  }));
  render(<ChannelSettings {...props} />);
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Review pairing code for Synthetic Slack',
    }),
  );
  expect(screen.queryByText(/PAIR1234/)).not.toBeInTheDocument();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Confirm channel action' }),
  );
  expect(await screen.findByText(/PAIR1234/)).toBeVisible();
});

it('uses the opaque identity when reviewing revocation', async () => {
  const props = options();
  render(<ChannelSettings {...props} />);
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Review revoke Synthetic person',
    }),
  );
  await waitFor(() => expect(props.review).toHaveBeenCalledTimes(1));
  expect(props.review.mock.calls[0][0]).toMatchObject({
    operation: 'revoke',
    identity_id: 'b'.repeat(64),
    value: null,
  });
  expect(JSON.stringify(props.review.mock.calls[0][0])).not.toContain('3456');
});

it('reports unavailable pairing without inventing an account flow', async () => {
  const props = options();
  props.load.mockResolvedValue({
    ...page,
    items: [
      {
        ...page.items[0],
        source: { kind: 'plugin', label: 'Synthetic plugin' },
        availability: { ...page.items[0].availability, pairing: 'unavailable' },
      },
    ],
  });
  render(<ChannelSettings {...props} />);
  expect(
    await screen.findByText(/Pairing controls are unavailable/),
  ).toBeVisible();
  expect(
    screen.getByRole('button', {
      name: 'Review pairing code for Synthetic Slack',
    }),
  ).toBeDisabled();
});

it('tombstones private drafts and ignores a late execution after auth loss', async () => {
  const props = options();
  const pending = deferred<ChannelReceipt>();
  props.execute.mockReturnValue(pending.promise);
  render(<ChannelSettings {...props} />);
  fireEvent.change(await screen.findByLabelText(/New Bot token/), {
    target: { value: 'private-late-token' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Review save Bot token' }),
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Confirm channel action' }),
  );
  const command = props.execute.mock.calls[0][0];
  act(() => props.session.dispose());
  await act(async () =>
    pending.resolve({
      command_id: command.command_id,
      status: 'completed',
      operation: 'configure',
    }),
  );
  expect(JSON.stringify(props.session.getSnapshot())).not.toContain(
    'private-late-token',
  );
  expect(screen.getByText(/Sign in again/)).toBeVisible();
});

it('rejects malformed oversized pages without retaining rows', async () => {
  const props = options();
  props.load.mockResolvedValue({
    ...page,
    items: Array.from({ length: 51 }, () => page.items[0]),
  });
  render(<ChannelSettings {...props} />);
  await screen.findByText(/Channel status is unavailable/);
  expect(props.session.getSnapshot().page).toBeNull();
  expect(
    screen.queryByRole('heading', { name: 'Synthetic Slack' }),
  ).not.toBeInTheDocument();
});
