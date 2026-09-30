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

/** Rows keep Connection visible; Edit, Rename and Delete sit in their ⋯. */
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
/** Import config, Refresh and diagnostics sit in the page's ⋯. */
async function choosePage(item: string | RegExp) {
  const more = await screen.findByRole('button', { name: 'More MCP actions' });
  await act(async () => fireEvent.keyDown(more, { key: 'Enter' }));
  await act(async () =>
    fireEvent.click(screen.getByRole('menuitem', { name: item })),
  );
}

const page: McpConfigurationPage = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  availability: 'available',
  enabled: true,
  total: 1,
  next_cursor: null,
  items: [
    {
      server_id: 'b'.repeat(64),
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
async function enterDraft() {
  await rowMenu('Synthetic');
  fireEvent.click(screen.getByRole('button', { name: 'Add server' }));
  fireEvent.change(screen.getByLabelText('Server name'), {
    target: { value: 'New synthetic' },
  });
  fireEvent.change(screen.getByLabelText('New command'), {
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
  await chooseRow('Synthetic', 'Edit Synthetic');
  expect(screen.getByLabelText('New command')).toHaveValue('');
  expect(screen.getByLabelText('Additional settings (JSON)')).toHaveValue('');
  expect(screen.getByText(/runtime status unknown/)).toBeVisible();
});

it('shows a missing per-server runtime and opens existing installation controls', async () => {
  const controls = document.createElement('details');
  controls.id = 'managed-mcp-runtimes';
  controls.scrollIntoView = vi.fn();
  document.body.appendChild(controls);
  try {
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
    fireEvent.click(
      await screen.findByRole('button', { name: 'Open Node.js LTS installer' }),
    );
    expect(controls.open).toBe(true);
    expect(props.review).not.toHaveBeenCalled();
    expect(props.execute).not.toHaveBeenCalled();
  } finally {
    controls.remove();
  }
});

it('searches the MCP directory only on click and imports the chosen template disabled', async () => {
  const props = options();
  const searchDirectory = vi.fn(async () => ({
    schema_version: 1 as const,
    mode: 'curated' as const,
    items: [
      {
        id: 'fixture',
        name: 'Fixture',
        description: '<img onerror=sentinel()>',
        source: 'curated',
        publisher: 'Fixture publisher',
        transport: 'stdio',
        risk_level: 'low',
        requires_auth: false,
        sign_in_required: false,
        recommended: true,
        import_json:
          '{"mcpServers":{"fixture":{"command":"synthetic","enabled":false}}}',
      },
    ],
  }));
  const { container } = render(
    <CapabilitySettings {...props} searchDirectory={searchDirectory} />,
  );
  await rowMenu('Synthetic');
  expect(searchDirectory).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('Browse MCP servers'));
  fireEvent.change(screen.getByLabelText('Search MCP directory'), {
    target: { value: 'fixture' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search directories' }));
  expect(await screen.findByText('<img onerror=sentinel()>')).toBeVisible();
  expect(container.querySelector('img')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Import Fixture' }));
  await waitFor(() => expect(props.execute).toHaveBeenCalledTimes(1));
  expect(props.review.mock.calls[0][0].intent).toEqual({
    operation: 'import',
    import_json:
      '{"mcpServers":{"fixture":{"command":"synthetic","enabled":false}}}',
  });
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
  await rowMenu('Synthetic');
  fireEvent.click(screen.getByText('Browse MCP servers'));
  fireEvent.click(screen.getByRole('button', { name: 'Search directories' }));
}

it('reads a changed revision again, retries the import once and says so next to the entry', async () => {
  const props = options();
  const searchDirectory = directory([{ name: 'Docs' }]);
  props.review.mockRejectedValueOnce({
    code: 'revision_conflict',
    status: 409,
  });
  render(<CapabilitySettings {...props} searchDirectory={searchDirectory} />);
  await browse();
  props.load.mockResolvedValue({ ...page, revision: 'e'.repeat(64) });
  fireEvent.click(await screen.findByRole('button', { name: 'Import Docs' }));
  await waitFor(() => expect(props.execute).toHaveBeenCalledOnce());
  expect(
    props.review.mock.calls.map((call) => call[0].configuration_revision),
  ).toEqual(['a'.repeat(64), 'e'.repeat(64)]);
  const entry = screen.getByRole('article', { name: 'Docs' });
  expect(await within(entry).findByText(/^Saved/)).toBeVisible();
});

it('marks servers that sign in through the browser and offers no import for them', async () => {
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
  expect(within(notion).queryByRole('button', { name: /Import/ })).toBeNull();
  const xquik = screen.getByRole('article', { name: 'Xquik MCP' });
  expect(
    within(xquik).getByRole('button', { name: 'Import Xquik MCP' }),
  ).toBeEnabled();
});

it('opens Add server with its name field focused', async () => {
  const props = options();
  render(<CapabilitySettings {...props} />);
  await rowMenu('Synthetic');
  fireEvent.click(screen.getByRole('button', { name: 'Add server' }));
  await waitFor(() =>
    expect(screen.getByLabelText('Server name')).toHaveFocus(),
  );
  await choosePage('Import config');
  await waitFor(() =>
    expect(screen.getByLabelText('Server import JSON')).toHaveFocus(),
  );
});

it('confirms configured-server deletion, then removes the row after the original command succeeds', async () => {
  const props = options();
  render(<CapabilitySettings {...props} />);
  await chooseRow('Synthetic', 'Delete Synthetic');
  expect(props.review).not.toHaveBeenCalled();
  expect(screen.getByText('Delete MCP server')).toBeVisible();
  // After the delete the list is read again, now without the server.
  props.load.mockResolvedValue({ ...page, items: [], total: 0 });
  fireEvent.click(screen.getByRole('button', { name: 'Delete server' }));
  await waitFor(() => expect(props.execute).toHaveBeenCalledTimes(1));
  expect(props.review.mock.calls[0][0].intent).toEqual({
    operation: 'delete',
    server_id: 'b'.repeat(64),
  });
  expect(screen.queryByText('Synthetic')).not.toBeInTheDocument();
});

it('opens path-free MCP diagnostics from the passive saved snapshot', async () => {
  const props = options();
  render(<CapabilitySettings {...props} />);
  await choosePage('MCP diagnostics');
  expect(
    screen.getByRole('region', { name: 'MCP diagnostics' }),
  ).toHaveTextContent(
    'Synthetic: not started; connection unknown; unknown tools.',
  );
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
});

it('uses a compact owner-style server summary and keeps editors closed at rest', async () => {
  const props = options();
  render(<CapabilitySettings {...props} />);
  await rowMenu('Synthetic');
  expect(screen.getByText('MCP enabled')).toBeVisible();
  expect(screen.getByText('0 connected')).toBeVisible();
  expect(screen.getByText('0 enabled tools')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Add server' })).toBeVisible();
  expect(screen.getByLabelText('Server name')).not.toBeVisible();
  await choosePage('Import config');
  expect(screen.getByLabelText('Server import JSON')).toBeVisible();
});

it('preserves the exact unsent draft across full unmount', async () => {
  const props = options();
  const first = render(<CapabilitySettings {...props} />);
  await enterDraft();
  first.unmount();
  render(<CapabilitySettings {...props} />);
  expect(screen.getByLabelText('Arguments (one per line)')).toHaveValue(
    'one two\n--exact',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Save Disabled' }));
  await screen.findByText(/^Saved\. It stays turned off/);
  await waitFor(() => expect(props.execute).toHaveBeenCalledTimes(1));
  expect(props.review).toHaveBeenCalledOnce();
  expect(props.execute.mock.calls[0][0].payload.intent.fields.args).toEqual([
    'one two',
    '--exact',
  ]);
  expect(props.session.hasRetained()).toBe(false);
  // The saved list is read again and the editor starts from an empty draft.
  await waitFor(() => expect(props.load).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText('Server name')).toHaveValue('');
});

it('retains uncertain originals and only reconciles their exact request after refresh', async () => {
  const props = options();
  props.execute.mockRejectedValueOnce(Error('synthetic transport loss'));
  render(<CapabilitySettings {...props} />);
  await enterDraft();
  fireEvent.click(screen.getByRole('button', { name: 'Save Disabled' }));
  await screen.findByRole('button', { name: 'Check original save' });
  expect(screen.getByLabelText('New command')).toBeDisabled();
  const original = props.execute.mock.calls[0];
  props.load.mockResolvedValue({
    ...page,
    revision: 'e'.repeat(64),
    availability: 'recovery_required',
  });
  await choosePage('Refresh');
  await screen.findByText(/interrupted save requires recovery/);
  fireEvent.click(screen.getByRole('button', { name: 'Check original save' }));
  await screen.findByText(/^Saved\. It stays turned off/);
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
  fireEvent.click(screen.getByRole('button', { name: 'Save Disabled' }));
  await screen.findByText(/save was rejected/);
  expect(
    screen.queryByRole('button', { name: 'Check original save' }),
  ).not.toBeInTheDocument();
  expect(screen.getByLabelText('New command')).toHaveValue(
    'synthetic-executable',
  );
  expect(props.session.getSnapshot().pending).toBeNull();
});

it('tombstones private drafts and late execution settlement after authentication loss', async () => {
  const props = options();
  const pending = deferred<McpConfigurationReceipt>();
  props.execute.mockReturnValue(pending.promise);
  render(<CapabilitySettings {...props} />);
  await enterDraft();
  fireEvent.change(screen.getByLabelText('Additional settings (JSON)'), {
    target: { value: '{"env":{"KEY":"private-draft"}}' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save Disabled' }));
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
  fireEvent.click(screen.getByRole('button', { name: 'Save Disabled' }));
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

it('supports explicit rename and import without launch activity', async () => {
  const props = options();
  render(<CapabilitySettings {...props} />);
  await chooseRow('Synthetic', 'Rename Synthetic');
  fireEvent.change(screen.getByLabelText('New server name'), {
    target: { value: 'Renamed synthetic' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save Disabled' }));
  await waitFor(() => expect(props.review).toHaveBeenCalledTimes(1));
  expect(props.review.mock.calls[0][0].intent).toEqual({
    operation: 'rename',
    server_id: 'b'.repeat(64),
    fields: { name: 'Renamed synthetic' },
  });
  await screen.findByText(/^Saved\. It stays turned off/);
  await choosePage('Refresh');
  await rowMenu('Synthetic');
  fireEvent.change(screen.getByRole('combobox', { name: 'Operation' }), {
    target: { value: 'import' },
  });
  fireEvent.change(screen.getByLabelText('Server import JSON'), {
    target: { value: '{"mcpServers":{"new":{"command":"synthetic"}}}' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save Disabled' }));
  await waitFor(() => expect(props.review).toHaveBeenCalledTimes(2));
  expect(props.review.mock.calls[1][0].intent.operation).toBe('import');
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
  const save = screen.getByRole('button', { name: 'Save Disabled' });
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
  await rowMenu('Synthetic');
  fireEvent.click(screen.getByRole('button', { name: 'Add server' }));
  // Save waits for a name and a command; then the malformed arguments.
  expect(screen.getByRole('button', { name: 'Save Disabled' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Server name'), {
    target: { value: 'Malformed' },
  });
  fireEvent.change(screen.getByLabelText('New command'), {
    target: { value: 'synthetic-executable' },
  });
  fireEvent.change(screen.getByLabelText('Arguments (one per line)'), {
    target: { value: '[not json' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save Disabled' }));
  await screen.findByText(/could not be validated/);
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole('combobox', { name: 'Operation' }), {
    target: { value: 'import' },
  });
  fireEvent.change(screen.getByLabelText('Server import JSON'), {
    target: { value: 'x'.repeat(131072) },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save Disabled' }));
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
