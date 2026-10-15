import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { FolderBrowser } from './FolderBrowser';

const workspaceFolders = vi.fn();
const runtime = { controller: { workspaceFolders } };
vi.mock('../../runtime', () => ({ useRuntime: () => runtime }));

const crumb = (name: string, path: string) => ({ name, path });
const root = {
  path: '',
  name: 'Row-Bot',
  crumbs: [crumb('Row-Bot', '')],
  folders: [
    { name: 'Projects', path: 'Projects', is_git: false },
    { name: 'tide-app', path: 'tide-app', is_git: true },
  ],
  truncated: false,
  is_root: true,
};
const projects = {
  path: 'Projects',
  name: 'Projects',
  crumbs: [crumb('Row-Bot', ''), crumb('Projects', 'Projects')],
  folders: [],
  truncated: false,
  is_root: false,
};

beforeEach(() => {
  workspaceFolders.mockReset();
  workspaceFolders.mockImplementation(async (path: string) =>
    path === '' ? root : projects,
  );
});

async function click(name: string) {
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name }));
  });
}

it('lists folders only, opens one with a tap, and goes back by its path', async () => {
  const onUse = vi.fn();
  await act(async () => {
    render(<FolderBrowser purpose="folder" onUse={onUse} />);
  });
  expect(
    screen.getByRole('list', { name: 'Folders in Row-Bot' }),
  ).toBeVisible();
  // The workspace folder itself is never the code folder.
  expect(screen.getByRole('button', { name: 'Use Row-Bot' })).toBeDisabled();
  expect(
    screen.getByText('Open the folder to work in, then use it.'),
  ).toBeVisible();
  await click('Projects');
  expect(workspaceFolders).toHaveBeenLastCalledWith(
    'Projects',
    expect.any(AbortSignal),
  );
  // An empty folder says so, and can still be used.
  expect(
    screen.getByText('No folders inside Projects. Use it, or go back.'),
  ).toBeVisible();
  await click('Use Projects');
  expect(onUse).toHaveBeenCalledWith({
    path: 'Projects',
    name: 'Projects',
    label: 'Row-Bot › Projects',
  });
  await click('Row-Bot');
  expect(
    screen.getByRole('button', { name: 'tide-app, Git repository' }),
  ).toBeVisible();
});

it('a place to clone or create into can be the workspace folder itself', async () => {
  const onUse = vi.fn();
  await act(async () => {
    render(
      <FolderBrowser
        purpose="parent"
        onUse={onUse}
        actionLabel={() => 'Clone here'}
      />,
    );
  });
  await click('Clone here');
  expect(onUse).toHaveBeenCalledWith({
    path: '',
    name: 'Row-Bot',
    label: 'Row-Bot',
  });
});

it('a refused folder says why, with a way back to the workspace folder', async () => {
  workspaceFolders.mockImplementation(async (path: string) => {
    if (path === '') return root;
    throw { code: 'workspace_folder_refused' };
  });
  await act(async () => {
    render(<FolderBrowser purpose="folder" onUse={vi.fn()} />);
  });
  await click('Projects');
  expect(screen.getByRole('alert')).toHaveTextContent(
    "That folder is outside Row-Bot's workspace folder, so it can't be used. Choose a folder listed here.",
  );
  expect(
    screen.getByRole('button', { name: 'Use this folder' }),
  ).toBeDisabled();
  await click('Back to Row-Bot');
  expect(screen.queryByRole('alert')).toBeNull();
  expect(
    screen.getByRole('list', { name: 'Folders in Row-Bot' }),
  ).toBeVisible();
});

it('an empty workspace folder suggests cloning into it', async () => {
  workspaceFolders.mockResolvedValue({ ...root, folders: [] });
  await act(async () => {
    render(
      <FolderBrowser
        purpose="folder"
        onUse={vi.fn()}
        clone={{ confirm: true, onClone: vi.fn() }}
      />,
    );
  });
  expect(
    screen.getByText(
      'No folders in Row-Bot yet. Clone a repository here, or put a folder in it on this computer.',
    ),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Clone from URL' })).toBeEnabled();
});

it('Clone from URL clones into the folder shown, straight away where approvals allow it', async () => {
  const onClone = vi.fn();
  await act(async () => {
    render(
      <FolderBrowser
        purpose="folder"
        onUse={vi.fn()}
        clone={{ confirm: false, onClone }}
      />,
    );
  });
  await click('Projects');
  await click('Clone from URL');
  expect(screen.getByRole('button', { name: 'Clone here' })).toBeDisabled();
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Repository address' }),
    {
      target: { value: ' https://example.test/team/demo.git ' },
    },
  );
  expect(
    screen.getByText(
      "Downloads into Row-Bot › Projects, with this computer's own Git sign-in.",
    ),
  ).toBeVisible();
  await click('Clone here');
  expect(
    screen.queryByRole('group', { name: 'Confirm the download' }),
  ).toBeNull();
  expect(onClone).toHaveBeenCalledWith('https://example.test/team/demo.git', {
    path: 'Projects',
    name: 'Projects',
    label: 'Row-Bot › Projects',
  });
});

it('says when a folder holds more folders than are listed', async () => {
  workspaceFolders.mockResolvedValue({ ...root, truncated: true });
  await act(async () => {
    render(<FolderBrowser purpose="folder" onUse={vi.fn()} />);
  });
  expect(
    screen.getByText('Only the first 2 folders are listed.'),
  ).toBeVisible();
});
