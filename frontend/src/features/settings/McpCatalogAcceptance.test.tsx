import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import McpCatalogAcceptance, {
  createMcpCatalogSession,
  type McpTestedCatalogPage,
} from './McpCatalogAcceptance';
import type { McpConfigurationReceipt } from './CapabilitySettings';

const serverId = 'a'.repeat(64);
const testCommandId = '00000000-0000-0000-0000-000000000001';
const page: McpTestedCatalogPage = {
  schema_version: 1,
  configuration_revision: 'b'.repeat(64),
  server_id: serverId,
  test_command_id: testCommandId,
  availability: 'available',
  manual_selection_required: false,
  total: 2,
  next_cursor: null,
  items: [
    {
      tool_id: 'c'.repeat(64),
      name: 'get_record',
      enabled_after_accept: true,
      requires_approval: false,
      destructive: false,
    },
    {
      tool_id: 'd'.repeat(64),
      name: 'delete_record',
      enabled_after_accept: false,
      requires_approval: true,
      destructive: true,
    },
  ],
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { resolve, promise };
}
function options() {
  return {
    session: createMcpCatalogSession(serverId, testCommandId),
    load: vi.fn().mockResolvedValue(page),
    review: vi.fn().mockImplementation(async (payload) => ({
      ...payload,
      action_digest: 'e'.repeat(64),
      nonce: 'synthetic',
      tool_count: 2,
      manual_selection_required: false,
    })),
    execute: vi.fn().mockImplementation(async (command) => ({
      command_id: command.command_id,
      status: 'completed',
      mcp_configuration: {
        status: 'saved',
        revision: 'f'.repeat(64),
        saved_disabled: null,
        runtime_cleanup: 'not_requested',
      },
    })),
  };
}
async function accept() {
  const button = await screen.findByRole('button', {
    name: 'Accept tools',
  });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
}

it('reads only the exact Test and displays mandatory approval', async () => {
  const props = options();
  render(<McpCatalogAcceptance {...props} />);
  await screen.findByText('2 matching tested tools.');
  expect(screen.getByText(/Approval required/)).toBeVisible();
  expect(props.load).toHaveBeenCalledWith(
    {
      server_id: serverId,
      test_command_id: testCommandId,
      query: '',
      cursor: undefined,
    },
    expect.any(AbortSignal),
  );
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
});

it('one-click acceptance preserves the original exact source', async () => {
  const props = options();
  render(<McpCatalogAcceptance {...props} />);
  await accept();
  await screen.findByText(/Tested tools accepted/);
  expect(props.execute.mock.calls[0][0].payload).toEqual({
    configuration_revision: page.configuration_revision,
    server_id: serverId,
    test_command_id: testCommandId,
  });
  expect(screen.getByRole('button', { name: 'Accept tools' })).toBeDisabled();
});

it('pages replace bounded rows and First uses the changed filter', async () => {
  const props = options();
  props.load
    .mockResolvedValueOnce({ ...page, total: 60, next_cursor: 'next' })
    .mockResolvedValueOnce({
      ...page,
      items: [{ ...page.items[0], name: 'last tool' }],
      total: 60,
    });
  render(<McpCatalogAcceptance {...props} />);
  await screen.findByText('60 matching tested tools.');
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  await screen.findByText('last tool');
  expect(screen.queryByText('get_record')).not.toBeInTheDocument();
  expect(props.load.mock.calls[1][0].cursor).toBe('next');
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Filter tested tools' }),
    { target: { value: 'last' } },
  );
  fireEvent.click(screen.getByRole('button', { name: 'First page' }));
  await waitFor(() => expect(props.load).toHaveBeenCalledTimes(3));
  expect(props.load.mock.calls[2][0]).toMatchObject({
    query: 'last',
    cursor: undefined,
  });
});

it('rejects oversized pages and a different Test source', async () => {
  const props = options();
  props.load
    .mockResolvedValueOnce({ ...page, test_command_id: 'other' })
    .mockResolvedValueOnce({
      ...page,
      items: Array.from({ length: 51 }, () => page.items[0]),
    });
  render(<McpCatalogAcceptance {...props} />);
  await screen.findByText(/Tested tools are unavailable/);
  expect(screen.queryByText('get_record')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'First page' }));
  await screen.findByText(/Return to the first page/);
  expect(screen.getByRole('button', { name: 'Accept tools' })).toBeDisabled();
});

it('requires manual selection when the canonical overlap owner says so', async () => {
  const props = options();
  props.load.mockResolvedValue({
    ...page,
    manual_selection_required: true,
    items: [],
  });
  render(<McpCatalogAcceptance {...props} />);
  await screen.findByText(
    /New tools remain disabled until individually enabled/,
  );
});

it.each(['unavailable', 'stale', 'recovery_required'])(
  'does not permit acceptance from %s',
  async (availability) => {
    const props = options();
    props.load.mockResolvedValue({ ...page, availability });
    render(<McpCatalogAcceptance {...props} />);
    await screen.findByText(/Catalog:/);
    expect(screen.getByRole('button', { name: 'Accept tools' })).toBeDisabled();
    expect(props.execute).not.toHaveBeenCalled();
  },
);

it('retains an uncertain original across remount and never creates another acceptance', async () => {
  const props = options();
  props.execute.mockRejectedValueOnce(Error('response lost'));
  const rendered = render(<McpCatalogAcceptance {...props} />);
  await accept();
  await screen.findByText(/original acceptance is unconfirmed/);
  const original = props.execute.mock.calls[0];
  rendered.unmount();
  render(<McpCatalogAcceptance {...props} />);
  expect(screen.getByRole('button', { name: 'Accept tools' })).toBeDisabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original acceptance' }),
  );
  await screen.findByText(/Tested tools accepted/);
  expect(props.execute.mock.calls[1]).toEqual(original);
});

it('keeps the exact in-flight acceptance through remount and tombstones after auth loss', async () => {
  const props = options();
  const response = deferred<McpConfigurationReceipt>();
  props.execute.mockReturnValue(response.promise);
  const rendered = render(<McpCatalogAcceptance {...props} />);
  await accept();
  await waitFor(() => expect(props.execute).toHaveBeenCalledOnce());
  rendered.unmount();
  render(<McpCatalogAcceptance {...props} />);
  expect(
    screen.getByRole('button', { name: 'Check original acceptance' }),
  ).toBeDisabled();
  act(() => props.session.dispose());
  await act(async () =>
    response.resolve({
      command_id: props.execute.mock.calls[0][0].command_id,
      status: 'partial',
    }),
  );
  expect(props.session.hasRetained()).toBe(false);
  expect(props.session.getSnapshot().page).toBeNull();
  expect(
    screen.queryByRole('button', { name: 'Check original acceptance' }),
  ).not.toBeInTheDocument();
  expect(props.execute).toHaveBeenCalledTimes(1);
});

it('does not accept a review for another Test', async () => {
  const props = options();
  props.review.mockImplementation(async (payload) => ({
    ...payload,
    test_command_id: 'other',
    action_digest: 'e'.repeat(64),
    nonce: 'synthetic',
    tool_count: 2,
    manual_selection_required: false,
  }));
  render(<McpCatalogAcceptance {...props} />);
  const button = await screen.findByRole('button', {
    name: 'Accept tools',
  });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  await screen.findByText(/could not be validated/);
  expect(props.execute).not.toHaveBeenCalled();
});

it('does not offer an original retry after explicit rejection', async () => {
  const props = options();
  props.execute.mockImplementation(async (command) => ({
    command_id: command.command_id,
    status: 'rejected',
  }));
  render(<McpCatalogAcceptance {...props} />);
  await accept();
  await screen.findByText(/Acceptance was rejected/);
  expect(
    screen.queryByRole('button', { name: 'Check original acceptance' }),
  ).not.toBeInTheDocument();
});
