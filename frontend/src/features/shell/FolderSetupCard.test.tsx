import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import ApprovalCard from './ApprovalCard';

const approval = vi.fn();
const intent = vi.fn();
const workspaceFor = vi.fn();
const discover = vi.fn();
const selectFolder = vi.fn();
vi.mock('../../runtime', () => ({
  useRuntime: () => ({
    controller: { approval, intent, workspaceFor },
    platform: { discover, selectFolder },
  }),
}));
vi.mock('../../ui/overlays', () => ({
  useOverlay: () => ({ open: vi.fn(), notify: vi.fn(), close: vi.fn() }),
}));

const REPO = 'https://github.com/example/demo.git';
const base = {
  id: 'approval-a',
  status: 'pending',
  revision: '3',
  nonce: 'nonce-a',
  action_label: 'use_code_folder',
  reason: 'Choose the folder on this computer; Row-Bot adds it.',
  policy_revision: '1',
};
const folderCard = {
  ...base,
  setup: { kind: 'folder', label: 'Use an existing folder', folders: [] },
};
const approve = [
  'approval-a',
  'approval.resolve',
  { decision: 'approve', nonce: 'nonce-a' },
  '3',
];

beforeEach(() => {
  for (const mock of [approval, intent, workspaceFor, discover, selectFolder])
    mock.mockReset();
  approval.mockResolvedValue(folderCard);
  workspaceFor.mockResolvedValue({ revision: '7' });
  discover.mockResolvedValue({ status: 'ok', value: { kind: 'pywebview' } });
  selectFolder.mockResolvedValue({
    status: 'ok',
    value: { kind: 'folder', reference: 'grant-1' },
  });
  intent.mockResolvedValue({ status: 'completed', command_id: 'setup-1' });
});

async function renderCard(name: string) {
  render(<ApprovalCard id="approval-a" conversationId="conversation-a" />);
  const card = await screen.findByRole('complementary', { name });
  await act(async () => {});
  return card;
}

async function click(name: string) {
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name }));
  });
}

it('Choose folder picks on this computer, adds it like Add resource, then lets the turn go on', async () => {
  const card = await renderCard('Use an existing folder');
  expect(card).toHaveTextContent(
    'Choose the folder on this computer; Row-Bot adds it.',
  );
  // A folder card never answers from the keyboard shortcut.
  await act(async () => {
    fireEvent.keyDown(document.body, { key: 'Enter', ctrlKey: true });
  });
  expect(intent).not.toHaveBeenCalled();
  await click('Choose folder');
  expect(selectFolder).toHaveBeenCalledWith(undefined, {
    intentId: expect.any(String),
    intent: 'resource_setup',
    conversationId: 'conversation-a',
    destination: 'workspace:existing_folder',
  });
  expect(intent.mock.calls).toEqual([
    [
      'conversation-a',
      'resource.setup',
      { kind: 'workspace', intent: 'create', folder_grant: 'grant-1' },
      '7',
    ],
    approve,
  ]);
  expect(await screen.findByRole('status')).toHaveTextContent(
    'Going on in the folder…',
  );
});

it('a cancelled pick adds nothing and keeps the turn waiting', async () => {
  selectFolder.mockResolvedValue({ status: 'cancelled' });
  await renderCard('Use an existing folder');
  await click('Choose folder');
  expect(intent).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Choose folder' })).toBeEnabled();
});

it('several folders with that name: the person picks one, bound like the reuse list', async () => {
  approval.mockResolvedValue({
    ...folderCard,
    reason: 'More than one code folder is called “Site”.',
    setup: {
      ...folderCard.setup,
      folders: [
        { resource_id: 'site-a', name: 'Site', revision: 'r-a' },
        { resource_id: 'site-b', name: 'Site (work)', revision: 'r-b' },
      ],
    },
  });
  await renderCard('Which code folder?');
  expect(
    screen.getByRole('button', { name: 'Choose another folder' }),
  ).toBeVisible();
  await click('Site (work)');
  expect(selectFolder).not.toHaveBeenCalled();
  expect(intent.mock.calls).toEqual([
    [
      'conversation-a',
      'resource.setup',
      {
        kind: 'workspace',
        intent: 'add',
        resource_id: 'site-b',
        expected_resource_revision: 'r-b',
      },
      '7',
    ],
    approve,
  ]);
});

it('the clone card shows the address and clones where the person chose, with progress', async () => {
  approval.mockResolvedValue({
    ...base,
    action_label: 'clone_repository',
    reason: 'Row-Bot downloads it into a new folder “demo”.',
    setup: { kind: 'clone', label: 'demo', repo_url: REPO },
  });
  let finish: (value: unknown) => void = () => {};
  intent.mockImplementationOnce(
    () => new Promise((resolve) => (finish = resolve)),
  );
  const card = await renderCard('Clone a repository');
  expect(card).toHaveTextContent(REPO);
  await click('Choose where');
  expect(selectFolder.mock.calls[0][1].destination).toBe(
    'workspace:clone_repository',
  );
  expect(
    screen.getByRole('progressbar', { name: 'Cloning demo…' }),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Choose where' })).toBeDisabled();
  expect(intent.mock.calls[0]).toEqual([
    'conversation-a',
    'resource.setup',
    {
      kind: 'workspace',
      intent: 'create',
      folder_grant: 'grant-1',
      clone_workspace: { repo_url: REPO },
    },
    '7',
  ]);
  await act(async () => {
    finish({ status: 'completed', command_id: 'setup-1' });
  });
  expect(intent.mock.calls[1]).toEqual(approve);
  expect(screen.queryByRole('progressbar')).toBeNull();
});

it('an uncertain clone is only checked again with the same parent, never cloned twice', async () => {
  approval.mockResolvedValue({
    ...base,
    setup: { kind: 'clone', label: 'demo', repo_url: REPO },
  });
  intent.mockResolvedValueOnce({
    status: 'partial',
    code: 'workspace_clone_unconfirmed',
    folder_reselection_required: true,
    command_id: 'setup-1',
    setup_command_id: 'setup-1',
    resource_id: 'workspace-1',
    resource_revision: 'r1',
  });
  const card = await renderCard('Clone a repository');
  await click('Choose where');
  expect(card).toHaveTextContent(
    'The clone may be incomplete. Choose the same parent folder to check it; Row-Bot never repeats an uncertain clone.',
  );
  expect(intent).toHaveBeenCalledTimes(1);
  selectFolder.mockResolvedValue({
    status: 'ok',
    value: { kind: 'folder', reference: 'grant-2' },
  });
  await click('Check clone status');
  expect(selectFolder.mock.calls[1][1]).toMatchObject({
    intent: 'resource_continue',
    destination: 'workspace:clone_repository',
  });
  expect(intent.mock.calls.slice(1)).toEqual([
    [
      'conversation-a',
      'resource.continue',
      {
        setup_command_id: 'setup-1',
        expected_resource_revision: 'r1',
        folder_grant: 'grant-2',
      },
      '7',
    ],
    approve,
  ]);
  expect(
    intent.mock.calls.filter((call) => call[1] === 'resource.setup'),
  ).toHaveLength(1);
});

it('a refused setup says why, and the turn keeps waiting for another choice', async () => {
  intent.mockRejectedValueOnce({ code: 'clone_source_invalid', status: 422 });
  approval.mockResolvedValue({
    ...base,
    setup: { kind: 'clone', label: 'demo', repo_url: REPO },
  });
  await renderCard('Clone a repository');
  await click('Choose where');
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Enter a Git address without a password in it',
  );
  expect(intent).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: 'Choose where' })).toBeEnabled();
});

it('once the folder is added, a failed go-ahead is retried with Continue, never a second folder', async () => {
  intent
    .mockResolvedValueOnce({ status: 'completed', command_id: 'setup-1' })
    .mockRejectedValueOnce({ code: 'network_unavailable' })
    .mockResolvedValueOnce({ status: 'completed' });
  await renderCard('Use an existing folder');
  await click('Choose folder');
  expect(screen.getByRole('alert')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Choose folder' })).toBeNull();
  await click('Continue');
  expect(intent.mock.calls.map((call) => call[1])).toEqual([
    'resource.setup',
    'approval.resolve',
    'approval.resolve',
  ]);
});

it('a folder that could not be confirmed is never made again by itself', async () => {
  intent.mockResolvedValueOnce({
    status: 'partial',
    code: 'workspace_clone_unconfirmed',
    command_id: 'setup-1',
  });
  approval.mockResolvedValue({
    ...base,
    setup: { kind: 'clone', label: 'demo', repo_url: REPO },
  });
  await renderCard('Clone a repository');
  await click('Choose where');
  expect(screen.getByRole('alert')).toHaveTextContent(
    "Row-Bot couldn't confirm the folder was made.",
  );
  expect(
    screen.queryByRole('button', { name: 'Check clone status' }),
  ).toBeNull();
  expect(intent).toHaveBeenCalledTimes(1);
});

it('Not now answers without adding a folder', async () => {
  await renderCard('Use an existing folder');
  await click('Not now');
  expect(intent.mock.calls).toEqual([
    [
      'approval-a',
      'approval.resolve',
      { decision: 'reject', nonce: 'nonce-a' },
      '3',
    ],
  ]);
  expect(await screen.findByRole('status')).toHaveTextContent(
    'No folder was added.',
  );
});

it('a browser says to choose the folder in the desktop app or Add resource', async () => {
  discover.mockResolvedValue({ status: 'ok', value: { kind: 'browser' } });
  const card = await renderCard('Use an existing folder');
  expect(card).toHaveTextContent(
    'Choose the folder in the Row-Bot desktop app, or add it with Add resource, then Continue.',
  );
  expect(screen.queryByRole('button', { name: 'Choose folder' })).toBeNull();
  await click('Continue');
  expect(selectFolder).not.toHaveBeenCalled();
  expect(intent.mock.calls).toEqual([approve]);
});
