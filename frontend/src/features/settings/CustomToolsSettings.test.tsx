import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import type {
  CustomToolLibrary,
  CustomToolLibraryReceipt,
} from '../../api/types';
import CustomToolsSettings from './CustomToolsSettings';

const customToolLibrary = vi.fn();
const executeCustomToolLibrary = vi.fn();
const discover = vi.fn();
const selectFolder = vi.fn();
const open = vi.fn();
const close = vi.fn();
vi.mock('../../runtime', () => ({
  useRuntime: () => ({
    controller: { customToolLibrary, executeCustomToolLibrary },
    platform: { discover, selectFolder },
  }),
}));
vi.mock('../../ui/overlays', () => ({
  useOverlay: () => ({ open, close, notify: vi.fn() }),
}));

const command = (name: string, text: string) => ({
  name,
  description: `${name} it`,
  command: text,
});
const library: CustomToolLibrary = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  tools: [
    {
      id: 'weather',
      name: 'Weather',
      version: '1.0.0',
      enabled: false,
      available_in_chat: false,
      commands: [
        command('Today', 'python weather.py'),
        command('Fetch', 'curl https://example.invalid/data'),
      ],
      folder: 'weather-cli',
      draft_id: '',
    },
  ],
  drafts: [
    {
      id: 'notes-draft',
      name: 'Notes',
      version: '0.1.0',
      commands: [command('List', 'python notes.py')],
      warnings: ['No tests found.'],
      test_results: {},
      python_project: true,
      setup_ok: false,
      status: 'draft',
      created_tool_id: '',
      folder: 'notes',
    },
  ],
};
const receipt = (
  extra: Partial<CustomToolLibraryReceipt> = {},
): CustomToolLibraryReceipt => ({
  command_id: 'c',
  status: 'completed',
  summary: 'Done.',
  snapshot: library,
  approval: null,
  test: null,
  ...extra,
});

beforeEach(() => {
  vi.clearAllMocks();
  customToolLibrary.mockResolvedValue(library);
  executeCustomToolLibrary.mockResolvedValue(receipt());
  discover.mockResolvedValue({ status: 'ok', value: { kind: 'pywebview' } });
});

it('lists tools and unfinished drafts with their folders and states', async () => {
  render(<CustomToolsSettings />);
  expect(await screen.findByText('Weather')).toBeInTheDocument();
  expect(screen.getByText('2 commands · weather-cli')).toBeInTheDocument();
  expect(screen.getByText('Off')).toBeInTheDocument();
  expect(screen.getByText('Draft — not set up yet')).toBeInTheDocument();
  expect(screen.getByRole('switch', { name: 'Use Weather' })).not.toBeChecked();
});

it('switches a tool on through the library command', async () => {
  render(<CustomToolsSettings />);
  const toggle = await screen.findByRole('switch', { name: 'Use Weather' });
  await act(async () => fireEvent.click(toggle));
  expect(executeCustomToolLibrary).toHaveBeenCalledWith(
    expect.objectContaining({
      action: 'enable',
      revision: library.revision,
      payload: { tool_id: 'weather', enabled: true },
    }),
  );
});

it('asks with the approval card before a test command that needs approval runs', async () => {
  const user = userEvent.setup();
  executeCustomToolLibrary
    .mockResolvedValueOnce(
      receipt({
        status: 'approval_required',
        summary: 'Weather: this command needs your approval before it runs.',
        approval: {
          command_name: 'Fetch',
          command: 'curl https://example.invalid/data',
          label: 'Network',
          reason: 'It uses the network.',
          nonce: 'b'.repeat(64),
        },
      }),
    )
    .mockResolvedValueOnce(
      receipt({
        summary: 'Command passed.',
        test: {
          ran: true,
          ok: true,
          returncode: 0,
          stdout: 'data',
          stderr: '',
          setup_hint: '',
        },
      }),
    );
  render(<CustomToolsSettings />);
  await user.click(
    await screen.findByRole('button', { name: 'Show details for Weather' }),
  );
  await user.click(screen.getByRole('button', { name: 'Test Fetch' }));
  const card = await screen.findByRole('complementary', {
    name: 'Approval required: Run “Fetch” once?',
  });
  expect(card).toHaveTextContent('It uses the network.');
  expect(card).toHaveTextContent('curl https://example.invalid/data');
  expect(executeCustomToolLibrary).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole('button', { name: 'Approve' }));
  expect(executeCustomToolLibrary).toHaveBeenLastCalledWith(
    expect.objectContaining({
      action: 'test',
      payload: {
        tool_id: 'weather',
        command_name: 'Fetch',
        approval_nonce: 'b'.repeat(64),
      },
    }),
  );
  expect(await screen.findByText('Fetch: passed')).toBeInTheDocument();
});

it('runs nothing when the approval is denied', async () => {
  const user = userEvent.setup();
  executeCustomToolLibrary.mockResolvedValueOnce(
    receipt({
      status: 'approval_required',
      approval: {
        command_name: 'Fetch',
        command: 'curl https://example.invalid/data',
        label: 'Network',
        reason: 'It uses the network.',
        nonce: 'b'.repeat(64),
      },
    }),
  );
  render(<CustomToolsSettings />);
  await user.click(
    await screen.findByRole('button', { name: 'Show details for Weather' }),
  );
  await user.click(screen.getByRole('button', { name: 'Test Fetch' }));
  await user.click(await screen.findByRole('button', { name: 'Deny' }));
  expect(screen.getByText('Nothing ran.')).toBeInTheDocument();
  expect(executeCustomToolLibrary).toHaveBeenCalledTimes(1);
});

it('adds from a folder picked in the desktop app, and explains it elsewhere', async () => {
  const user = userEvent.setup();
  selectFolder.mockResolvedValue({
    status: 'ok',
    value: { kind: 'folder', reference: 'grant-1' },
  });
  const view = render(<CustomToolsSettings />);
  await screen.findByText('Weather');
  await user.click(screen.getByRole('button', { name: 'Add from a folder' }));
  expect(executeCustomToolLibrary).toHaveBeenCalledWith(
    expect.objectContaining({
      action: 'inspect',
      payload: {},
      folder_grant: 'grant-1',
    }),
  );
  view.unmount();
  discover.mockResolvedValue({ status: 'ok', value: { kind: 'browser' } });
  render(<CustomToolsSettings />);
  expect(
    await screen.findByText(/needs the Row-Bot desktop app/),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Add from a folder' }),
  ).toBeDisabled();
});

it('confirms before removing and keeps the files', async () => {
  const user = userEvent.setup();
  render(<CustomToolsSettings />);
  await user.click(
    await screen.findByRole('button', { name: 'More actions for Weather' }),
  );
  await user.click(screen.getByRole('menuitem', { name: 'Remove…' }));
  const confirm = open.mock.calls[0][0];
  expect(confirm.title).toBe('Remove Weather?');
  expect(confirm.description).toMatch(/files stay in the folder/);
  expect(executeCustomToolLibrary).not.toHaveBeenCalled();
  await act(async () => confirm.onConfirm());
  expect(executeCustomToolLibrary).toHaveBeenCalledWith(
    expect.objectContaining({
      action: 'remove',
      payload: { tool_id: 'weather' },
    }),
  );
});
