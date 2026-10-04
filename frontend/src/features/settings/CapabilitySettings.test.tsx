import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import CapabilitySettings, {
  createCapabilitySettingsSession,
  type CapabilitySettingsProps,
  type McpConfigurationPage,
  type McpConfigurationReceipt,
} from './CapabilitySettings';

/** Details, Edit, Rename and Remove sit in a row's ⋯. */
async function rowMenu(server: string) {
  return screen.findByRole('button', { name: `More actions for ${server}` });
}
async function chooseRow(server: string, item: string) {
  const more = await rowMenu(server);
  await act(async () => fireEvent.keyDown(more, { key: 'Enter' }));
  await act(async () =>
    fireEvent.click(screen.getByRole('menuitem', { name: item })),
  );
}
/** Refresh sits in the page's ⋯. */
async function choosePage(item: string | RegExp) {
  const more = await screen.findByRole('button', { name: 'More MCP actions' });
  await act(async () => fireEvent.keyDown(more, { key: 'Enter' }));
  await act(async () =>
    fireEvent.click(screen.getByRole('menuitem', { name: item })),
  );
}

const serverId = 'b'.repeat(64);
const page: McpConfigurationPage = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  availability: 'available',
  enabled: true,
  total: 1,
  next_cursor: null,
  items: [
    {
      server_id: serverId,
      name: 'Synthetic',
      enabled: true,
      transport: 'stdio',
      runtime_status: null,
      tool_count: null,
      configured_fields: ['command', 'env'],
      connection_present: null,
    },
  ],
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (value: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function options() {
  return {
    session: createCapabilitySettingsSession(),
    load: vi.fn().mockResolvedValue(page),
    review: vi.fn().mockImplementation(async (payload) => ({
      configuration_revision: payload.configuration_revision,
      action_digest: 'c'.repeat(64),
      nonce: 'synthetic',
    })),
    execute: vi.fn().mockImplementation(async (command) => ({
      command_id: command.command_id,
      status: 'completed',
      mcp_configuration: { status: 'saved', revision: 'd'.repeat(64) },
    })),
  };
}
async function openAdd() {
  await rowMenu('Synthetic');
  fireEvent.click(screen.getByRole('button', { name: 'Add server' }));
  return screen.findByRole('dialog', { name: 'Add a server' });
}
async function enterDraft() {
  await openAdd();
  fireEvent.change(screen.getByLabelText('Server name'), {
    target: { value: 'New synthetic' },
  });
  fireEvent.change(screen.getByLabelText('Command'), {
    target: { value: 'synthetic-executable' },
  });
  fireEvent.change(screen.getByLabelText('Arguments (one per line)'), {
    target: { value: 'one two\n--exact' },
  });
}

it('does passive reads only and keeps saved launch values write-only', async () => {
  const props = options();
  render(<CapabilitySettings {...props} />);
  await rowMenu('Synthetic');
  expect(props.execute).not.toHaveBeenCalled();
  expect(props.review).not.toHaveBeenCalled();
  await chooseRow('Synthetic', 'Edit settings…');
  const dialog = screen.getByRole('dialog', { name: 'Edit Synthetic' });
  expect(within(dialog).getByLabelText('New command')).toHaveValue('');
  expect(
    within(dialog).getByLabelText('Additional settings (JSON)'),
  ).toHaveValue('');
});

it('says in the row when a runtime it needs is missing, without sending anything', async () => {
  const props = options();
  props.load.mockResolvedValue({
    ...page,
    items: [
      {
        ...page.items[0],
        requirements: [
          {
            id: 'node',
            label: 'Node.js LTS',
            available: false,
            managed: true,
            installable: true,
            source: 'missing',
          },
        ],
      },
    ],
  });
  render(<CapabilitySettings {...props} />);
  expect(
    await screen.findByText('Needs Node.js LTS; install it under Runtimes'),
  ).toBeVisible();
  expect(screen.getByText('On this computer')).toBeVisible();
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
});

function directory(
  entries: Partial<
    Awaited<
      ReturnType<NonNullable<CapabilitySettingsProps['searchDirectory']>>
    >['items'][number]
  >[],
) {
  return vi.fn(async () => ({
    schema_version: 1 as const,
    mode: 'curated' as const,
    items: entries.map((entry, index) => ({
      id: `entry-${index}`,
      name: `Entry ${index}`,
      description: 'Synthetic entry',
      source: 'curated',
      publisher: 'Synthetic',
      transport: 'streamable_http',
      risk_level: 'low',
      requires_auth: false,
      sign_in_required: false,
      recommended: true,
      import_json: `{"mcpServers":{"entry-${index}":{"url":"https://example.invalid/${index}"}}}`,
      ...entry,
    })),
  }));
}
async function browse() {
  const dialog = await openAdd();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Search' }));
  return dialog;
}

it('searches the MCP directory only on request, adds the entry turned off and opens its details', async () => {
  const props = options();
  const onConnection = vi.fn();
  const newServer = 'e'.repeat(64);
  props.execute.mockImplementation(async (command) => ({
    command_id: command.command_id,
    status: 'completed',
    mcp_configuration: {
      status: 'saved',
      revision: 'd'.repeat(64),
      server_ids: [newServer],
    },
  }));
  const searchDirectory = directory([
    { name: 'Fixture', description: '<img onerror=sentinel()>' },
  ]);
  render(
    <CapabilitySettings
      {...props}
      searchDirectory={searchDirectory}
      onConnection={onConnection}
    />,
  );
  const dialog = await openAdd();
  // Browse is the first way to add; nothing is searched until asked.
  expect(within(dialog).getByRole('radio', { name: 'Browse' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
  expect(searchDirectory).not.toHaveBeenCalled();
  fireEvent.change(
    within(dialog).getByRole('searchbox', { name: 'Search the MCP directory' }),
    { target: { value: 'fixture' } },
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Search' }));
  expect(await screen.findByText('<img onerror=sentinel()>')).toBeVisible();
  expect(dialog.querySelector('img')).toBeNull();
  props.load.mockResolvedValue({
    ...page,
    total: 2,
    items: [
      ...page.items,
      { ...page.items[0], server_id: newServer, name: 'fixture' },
    ],
  });
  fireEvent.click(screen.getByRole('button', { name: 'Add Fixture' }));
  await waitFor(() => expect(props.execute).toHaveBeenCalledTimes(1));
  expect(props.review.mock.calls[0][0].intent).toEqual({
    operation: 'import',
    import_json:
      '{"mcpServers":{"entry-0":{"url":"https://example.invalid/0"}}}',
  });
  // Adding ends by opening the new server's details.
  await waitFor(() =>
    expect(onConnection).toHaveBeenCalledWith(newServer, 'fixture'),
  );
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(
    screen.getByText('Added. It stays turned off until you connect it.'),
  ).toBeVisible();
});

it('reads a changed revision again and retries the add once', async () => {
  const props = options();
  const searchDirectory = directory([{ name: 'Docs' }]);
  props.review.mockRejectedValueOnce({
    code: 'revision_conflict',
    status: 409,
  });
  render(<CapabilitySettings {...props} searchDirectory={searchDirectory} />);
  await browse();
  props.load.mockResolvedValue({ ...page, revision: 'e'.repeat(64) });
  fireEvent.click(await screen.findByRole('button', { name: 'Add Docs' }));
  await waitFor(() => expect(props.execute).toHaveBeenCalledOnce());
  expect(
    props.review.mock.calls.map((call) => call[0].configuration_revision),
  ).toEqual(['a'.repeat(64), 'e'.repeat(64)]);
});

it('says a failed add next to the entry that was clicked', async () => {
  const props = options();
  const searchDirectory = directory([{ name: 'Docs' }, { name: 'Other' }]);
  props.review.mockResolvedValueOnce({
    configuration_revision: 'different',
    action_digest: 'c'.repeat(64),
  });
  render(<CapabilitySettings {...props} searchDirectory={searchDirectory} />);
  await browse();
  fireEvent.click(await screen.findByRole('button', { name: 'Add Docs' }));
  const entry = screen.getByRole('article', { name: 'Docs' });
  expect(
    await within(entry).findByText(/could not be validated/),
  ).toBeVisible();
  expect(
    within(screen.getByRole('article', { name: 'Other' })).queryByRole(
      'status',
    ),
  ).toBeNull();
  expect(props.execute).not.toHaveBeenCalled();
});

it('marks servers that sign in through the browser and offers no Add for them', async () => {
  const props = options();
  const searchDirectory = directory([
    { name: 'Notion MCP', requires_auth: true, sign_in_required: true },
    { name: 'Xquik MCP', requires_auth: true },
  ]);
  render(<CapabilitySettings {...props} searchDirectory={searchDirectory} />);
  await browse();
  const notion = await screen.findByRole('article', { name: 'Notion MCP' });
  expect(
    within(notion).getByText('Needs sign-in (not supported yet)'),
  ).toBeVisible();
  expect(within(notion).queryByRole('button', { name: /Add/ })).toBeNull();
  const xquik = screen.getByRole('article', { name: 'Xquik MCP' });
  expect(within(xquik).getByText(/needs a key/)).toBeVisible();
  expect(
    within(xquik).getByRole('button', { name: 'Add Xquik MCP' }),
  ).toBeEnabled();
});

it('opens Add server on the way chosen, with its first field focused', async () => {
  const props = options();
  render(<CapabilitySettings {...props} />);
  const dialog = await openAdd();
  // Without a directory, Manual is first.
  await waitFor(() =>
    expect(within(dialog).getByLabelText('Server name')).toHaveFocus(),
  );
  fireEvent.click(within(dialog).getByRole('radio', { name: 'Paste JSON' }));
  expect(
    within(dialog).getByLabelText('Server configuration (JSON)'),
  ).toBeVisible();
  expect(within(dialog).queryByLabelText('Server name')).toBeNull();
  expect(
    screen.getByText('Adding opens the server’s details next.'),
  ).toBeVisible();
});

it('confirms removing a server, then drops its row once the original command succeeds', async () => {
  const props = options();
  const onRemoved = vi.fn();
  render(<CapabilitySettings {...props} onRemoved={onRemoved} />);
  await chooseRow('Synthetic', 'Remove server…');
  expect(props.review).not.toHaveBeenCalled();
  expect(
    screen.getByRole('dialog', { name: 'Remove Synthetic?' }),
  ).toBeVisible();
  // After the removal the list is read again, now without the server.
  props.load.mockResolvedValue({ ...page, items: [], total: 0 });
  fireEvent.click(screen.getByRole('button', { name: 'Remove server' }));
  await waitFor(() => expect(props.execute).toHaveBeenCalledTimes(1));
  expect(props.review.mock.calls[0][0].intent).toEqual({
    operation: 'delete',
    server_id: serverId,
  });
  await waitFor(() => expect(onRemoved).toHaveBeenCalledWith(serverId));
  expect(
    screen.queryByRole('button', { name: 'More actions for Synthetic' }),
  ).not.toBeInTheDocument();
  expect(screen.getByText('No servers yet')).toBeVisible();
  expect(
    screen.getByText('Add a server to give Row-Bot new tools.'),
  ).toBeVisible();
});

it('opens the same Remove confirmation when the server details ask for it', async () => {
  const props = options();
  render(<CapabilitySettings {...props} />);
  await rowMenu('Synthetic');
  act(() => props.session.confirmRemove(serverId, 'Synthetic'));
  expect(
    screen.getByRole('dialog', { name: 'Remove Synthetic?' }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(props.review).not.toHaveBeenCalled();
});

it('keeps path-free MCP diagnostics under Advanced', async () => {
  const props = options();
  render(<CapabilitySettings {...props} />);
  await rowMenu('Synthetic');
  fireEvent.click(screen.getByText('Advanced'));
  expect(
    screen.getByRole('region', { name: 'MCP diagnostics' }),
  ).toHaveTextContent(
    'Synthetic: not started; connection unknown; unknown tools.',
  );
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
});

it('shows one status line and keeps the Add dialog closed at rest', async () => {
  const props = options();
  render(<CapabilitySettings {...props} />);
  await rowMenu('Synthetic');
  expect(screen.getByText('0 of 1 server connected')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Add server' })).toBeVisible();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText('On')).toBeVisible();
});

it('offers a search icon for a short list and a search field for a long one', async () => {
  const props = options();
  const first = render(<CapabilitySettings {...props} />);
  await rowMenu('Synthetic');
  expect(
    screen.queryByRole('searchbox', { name: 'Search servers' }),
  ).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Search servers' }));
  expect(
    screen.getByRole('searchbox', { name: 'Search servers' }),
  ).toHaveFocus();
  first.unmount();
  const many = options();
  many.load.mockResolvedValue({
    ...page,
    total: 7,
    items: Array.from({ length: 7 }, (_, index) => ({
      ...page.items[0],
      server_id: String(index).repeat(64),
      name: `Server ${index}`,
    })),
  });
  render(<CapabilitySettings {...many} />);
  expect(
    await screen.findByRole('searchbox', { name: 'Search servers' }),
  ).toBeVisible();
});

it('preserves the exact unsent draft across full unmount', async () => {
  const props = options();
  const first = render(<CapabilitySettings {...props} />);
  await enterDraft();
  first.unmount();
  render(<CapabilitySettings {...props} />);
  // The draft waits in the list until it is continued or discarded.
  expect(await screen.findByText(/You have an unsaved server/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  expect(screen.getByLabelText('Arguments (one per line)')).toHaveValue(
    'one two\n--exact',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));
  await screen.findByText(/^Added\. It stays turned off/);
  await waitFor(() => expect(props.execute).toHaveBeenCalledTimes(1));
  expect(props.review).toHaveBeenCalledOnce();
  expect(props.execute.mock.calls[0][0].payload.intent.fields.args).toEqual([
    'one two',
    '--exact',
  ]);
  expect(props.session.hasRetained()).toBe(false);
  // The saved list is read again and the dialog closes on an empty draft.
  await waitFor(() => expect(props.load).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByText(/You have an unsaved server/)).toBeNull();
});

it('retains uncertain originals and only reconciles their exact request after refresh', async () => {
  const props = options();
  props.execute.mockRejectedValueOnce(Error('synthetic transport loss'));
  render(<CapabilitySettings {...props} />);
  await enterDraft();
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));
  await screen.findByRole('button', { name: 'Check original save' });
  expect(screen.getByLabelText('Command')).toBeDisabled();
  const original = props.execute.mock.calls[0];
  // Closed, the dialog's message moves above the list.
  fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
  await screen.findByRole('button', { name: 'Check original save' });
  props.load.mockResolvedValue({
    ...page,
    revision: 'e'.repeat(64),
    availability: 'recovery_required',
  });
  await choosePage('Refresh');
  await screen.findByText(/interrupted save requires recovery/);
  fireEvent.click(screen.getByRole('button', { name: 'Check original save' }));
  await screen.findByText(/^Added\. It stays turned off/);
  expect(props.execute.mock.calls[1]).toEqual(original);
  expect(props.review).toHaveBeenCalledTimes(1);
});

it('never replays a rejected original and preserves its draft for new review', async () => {
  const props = options();
  props.execute.mockImplementation(async (command) => ({
    command_id: command.command_id,
    status: 'rejected',
  }));
  render(<CapabilitySettings {...props} />);
  await enterDraft();
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));
  await screen.findByText(/save was rejected/);
  expect(
    screen.queryByRole('button', { name: 'Check original save' }),
  ).not.toBeInTheDocument();
  expect(screen.getByLabelText('Command')).toHaveValue('synthetic-executable');
  expect(props.session.getSnapshot().pending).toBeNull();
});

it('tombstones private drafts and late execution settlement after authentication loss', async () => {
  const props = options();
  const pending = deferred<McpConfigurationReceipt>();
  props.execute.mockReturnValue(pending.promise);
  render(<CapabilitySettings {...props} />);
  await enterDraft();
  fireEvent.click(screen.getByText('More settings'));
  fireEvent.change(screen.getByLabelText('Additional settings (JSON)'), {
    target: { value: '{"env":{"KEY":"private-draft"}}' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));
  await waitFor(() => expect(props.execute).toHaveBeenCalledOnce());
  const command = props.execute.mock.calls[0][0];
  act(() => props.session.dispose());
  await act(async () =>
    pending.resolve({
      command_id: command.command_id,
      status: 'completed',
      mcp_configuration: { status: 'saved', revision: 'f'.repeat(64) },
    }),
  );
  expect(JSON.stringify(props.session.getSnapshot())).not.toContain(
    'private-draft',
  );
  expect(props.session.getSnapshot().pending).toBeNull();
  expect(props.session.hasRetained()).toBe(false);
  expect(screen.getByText(/Sign in again/)).toBeVisible();
});

it('preserves in-flight ownership across remount without a duplicate save', async () => {
  const props = options();
  const pending = deferred<McpConfigurationReceipt>();
  props.execute.mockReturnValue(pending.promise);
  const first = render(<CapabilitySettings {...props} />);
  await enterDraft();
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));
  await waitFor(() => expect(props.execute).toHaveBeenCalledOnce());
  first.unmount();
  render(<CapabilitySettings {...props} />);
  expect(
    screen.getByRole('button', { name: 'Check original save' }),
  ).toBeDisabled();
  const command = props.execute.mock.calls[0][0];
  await act(async () =>
    pending.resolve({
      command_id: command.command_id,
      status: 'partial',
      mcp_configuration: { status: 'partial', revision: null },
    }),
  );
  expect(props.execute).toHaveBeenCalledTimes(1);
  expect(
    screen.getByRole('button', { name: 'Check original save' }),
  ).toBeEnabled();
});

it('replaces bounded pages and preserves search on First and Next', async () => {
  const props = options();
  props.load
    .mockResolvedValueOnce({ ...page, total: 51, next_cursor: 'cursor-1' })
    .mockResolvedValueOnce({
      ...page,
      total: 51,
      items: [
        { ...page.items[0], server_id: 'e'.repeat(64), name: 'Last synthetic' },
      ],
    });
  render(<CapabilitySettings {...props} />);
  await rowMenu('Synthetic');
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  await rowMenu('Last synthetic');
  expect(
    screen.queryByRole('button', { name: 'More actions for Synthetic' }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'First page' }));
  await rowMenu('Synthetic');
  expect(props.load.mock.calls[1][0]).toEqual({
    query: '',
    cursor: 'cursor-1',
  });
  expect(props.load.mock.calls[2][0]).toEqual({ query: '', cursor: undefined });
});

it('supports explicit rename and a pasted configuration without launch activity', async () => {
  const props = options();
  render(<CapabilitySettings {...props} />);
  await chooseRow('Synthetic', 'Rename…');
  expect(
    screen.getByRole('dialog', { name: 'Rename Synthetic' }),
  ).toBeVisible();
  fireEvent.change(screen.getByLabelText('New server name'), {
    target: { value: 'Renamed synthetic' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
  await waitFor(() => expect(props.review).toHaveBeenCalledTimes(1));
  expect(props.review.mock.calls[0][0].intent).toEqual({
    operation: 'rename',
    server_id: serverId,
    fields: { name: 'Renamed synthetic' },
  });
  await screen.findByText(/^Saved\. It stays turned off/);
  const dialog = await openAdd();
  fireEvent.click(within(dialog).getByRole('radio', { name: 'Paste JSON' }));
  fireEvent.change(screen.getByLabelText('Server configuration (JSON)'), {
    target: { value: '{"mcpServers":{"new":{"command":"synthetic"}}}' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Add from JSON' }));
  await waitFor(() => expect(props.review).toHaveBeenCalledTimes(2));
  expect(props.review.mock.calls[1][0].intent).toEqual({
    operation: 'import',
    import_json: '{"mcpServers":{"new":{"command":"synthetic"}}}',
  });
  await waitFor(() => expect(props.execute).toHaveBeenCalledTimes(2));
});

it('aborts cold reads on authentication disposal and ignores late saved rows', async () => {
  const props = options();
  const pending = deferred<McpConfigurationPage>();
  props.load.mockReturnValue(pending.promise);
  render(<CapabilitySettings {...props} />);
  act(() => props.session.dispose());
  expect(props.load.mock.calls[0][1].aborted).toBe(true);
  await act(async () => pending.resolve(page));
  expect(
    screen.queryByRole('button', { name: 'More actions for Synthetic' }),
  ).not.toBeInTheDocument();
  expect(props.session.getSnapshot().page).toBeNull();
});

it('admits one save during synchronous repeated clicks', async () => {
  const props = options();
  const pending = deferred<McpConfigurationReceipt>();
  props.execute.mockReturnValue(pending.promise);
  render(<CapabilitySettings {...props} />);
  await enterDraft();
  const save = screen.getByRole('button', { name: 'Add' });
  act(() => {
    save.click();
    save.click();
  });
  await waitFor(() => expect(props.execute).toHaveBeenCalledTimes(1));
  act(() => props.session.dispose());
  await act(async () => pending.reject(Error('synthetic cancellation')));
});

it('rejects malformed and oversized drafts before review or execution', async () => {
  const props = options();
  render(<CapabilitySettings {...props} />);
  const dialog = await openAdd();
  // Adding waits for a name and a command; then the malformed arguments.
  expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Server name'), {
    target: { value: 'Malformed' },
  });
  fireEvent.change(screen.getByLabelText('Command'), {
    target: { value: 'synthetic-executable' },
  });
  fireEvent.change(screen.getByLabelText('Arguments (one per line)'), {
    target: { value: '[not json' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));
  await screen.findByText(/could not be validated/);
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole('radio', { name: 'Paste JSON' }));
  fireEvent.change(screen.getByLabelText('Server configuration (JSON)'), {
    target: { value: 'x'.repeat(131072) },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Add from JSON' }));
  await screen.findByText(/could not be validated/);
  expect(props.review).not.toHaveBeenCalled();
});

it('refuses an oversized loaded page without retaining unlimited rows', async () => {
  const props = options();
  props.load.mockResolvedValue({
    ...page,
    items: Array.from({ length: 51 }, (_, i) => ({
      ...page.items[0],
      server_id: String(i),
    })),
  });
  render(<CapabilitySettings {...props} />);
  await screen.findByText(/Saved MCP settings are unavailable/);
  expect(props.session.getSnapshot().page).toBeNull();
  expect(
    screen.queryByRole('button', { name: 'More actions for Synthetic' }),
  ).not.toBeInTheDocument();
});

it('says On or Off from the saved setting and offers no connection actions in the list', async () => {
  const props = options();
  props.load.mockResolvedValue({
    ...page,
    total: 2,
    items: [
      page.items[0],
      {
        ...page.items[0],
        server_id: 'e'.repeat(64),
        name: 'Paused',
        enabled: false,
      },
    ],
  });
  render(<CapabilitySettings {...props} />);
  await rowMenu('Paused');
  expect(screen.getByText('On')).toBeVisible();
  expect(screen.getByText('Off')).toBeVisible();
  expect(
    screen.queryByRole('button', {
      name: /^(Connect|Disconnect|Retry|Turn on)/,
    }),
  ).toBeNull();
  await chooseRow('Synthetic', 'Edit settings…');
  expect(screen.getByRole('dialog', { name: 'Edit Synthetic' })).toBeVisible();
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
});

it('shows only the saved server it is scoped to', async () => {
  const props = options();
  props.load.mockResolvedValue({
    ...page,
    total: 2,
    items: [
      page.items[0],
      { ...page.items[0], server_id: 'e'.repeat(64), name: 'Other' },
    ],
  });
  render(<CapabilitySettings {...props} only={serverId} />);
  await rowMenu('Synthetic');
  expect(
    screen.queryByRole('button', { name: 'More actions for Other' }),
  ).not.toBeInTheDocument();
  await chooseRow('Synthetic', 'Rename…');
  expect(
    screen.getByRole('dialog', { name: 'Rename Synthetic' }),
  ).toBeVisible();
});

it('opens Add once the saved servers load when asked to start adding', async () => {
  const props = options();
  const loaded = deferred<McpConfigurationPage>();
  props.load.mockReturnValue(loaded.promise);
  render(<CapabilitySettings {...props} startAdd />);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await act(async () => loaded.resolve(page));
  const dialog = await screen.findByRole('dialog', { name: 'Add a server' });
  expect(within(dialog).getByLabelText('Server name')).toBeVisible();
  // It opens once: closed, it stays closed while the page is used.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Close dialog' }));
  await waitFor(() =>
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Search servers' }));
  expect(
    screen.getByRole('searchbox', { name: 'Search servers' }),
  ).toBeVisible();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(props.review).not.toHaveBeenCalled();
});
