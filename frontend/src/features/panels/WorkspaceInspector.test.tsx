import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import { createWorkspaceEditSessions } from './workspace-edit-sessions';
import type {
  WorkspaceInspector as Inspector,
  WorkspaceFile,
} from '../../api/types';
import {
  WorkspaceInspector,
  checksStatus,
  type WorkspaceInspectorProps,
} from './WorkspaceInspector';

const fixture: Inspector = {
  resource_id: 'workspace',
  project_workspace_id: 'project',
  execution_workspace_id: 'workspace',
  conversation_id: 'chat-a',
  name: 'Fixture workspace',
  snapshot_revision: '1',
  status: 'ready',
  policy: {
    execution_mode: 'local',
    approval_mode: 'approve',
    sandbox_network: 'off',
    read_only: true,
  },
  is_git: true,
  branch: 'fixture',
  dirty: true,
  changed_total: 1003,
  diff_stats: { files: 1003, additions: 10, deletions: 2 },
  commands: [
    {
      label: 'pytest',
      kind: 'test',
      status: 'not_run',
      command: 'python -m pytest',
    },
  ],
  processes: [{ pid: 42, status: 'running' }],
  todos: [],
  error: '',
};

function editingLifetime() {
  const editable = {
    resource_id: 'workspace',
    conversation_id: 'chat-a',
    relative_path: 'file-1.txt',
    resource_revision: '1',
    binding_id: 'binding',
    binding_revision: '2',
    target: 'workspace' as const,
    status: 'text' as const,
    content: 'Original',
    digest: 'a'.repeat(64),
  };
  const state = {
    selectedConversationId: 'chat-a',
    loadingConversation: false,
    handshake: {
      client_session_id: 'session',
      server_epoch: 'epoch',
      instance_id: 'instance',
    },
    workspace: {
      conversation_id: 'chat-a',
      revision: '3',
      resources: [
        {
          available: true,
          resource_revision: '1',
          binding: {
            binding_id: 'binding',
            revision: '2',
            kind: 'workspace',
            resource_id: 'workspace',
          },
        },
      ],
    },
  };
  const controller = {
    getSnapshot: () => state,
    subscribe: () => () => {},
    workspaceEditableFile: vi.fn().mockResolvedValue(editable),
    command: vi.fn(),
    receipt: vi.fn(),
    retryCommand: vi.fn(),
  };
  const owner = createWorkspaceEditSessions(
    controller as unknown as ClientController,
  );
  const options = {
    ...props(),
    editableFile: controller.workspaceEditableFile,
    saveFile: vi.fn(),
    editSessions: owner.forBinding('chat-a', 'binding'),
  };
  return { controller, owner, options, editable };
}

function chooseTab(name: string) {
  const tab = screen.queryByRole('tab', { name: new RegExp(`^${name}`) });
  if (tab) fireEvent.mouseDown(tab, { button: 0 });
}

/** Opens file-1.txt's diff, then reads the file itself in Files. */
async function openFile(path = 'file-1.txt') {
  fireEvent.mouseDown(await screen.findByRole('tab', { name: /^Changes/ }), {
    button: 0,
  });
  fireEvent.click(
    await screen.findByRole('button', { name: `Show changes in ${path}` }),
  );
  fireEvent.click(
    await screen.findByRole('button', { name: `Open ${path} in Files` }),
  );
  return screen.findByRole('region', { name: 'File text' });
}

/** Reads the same file again, as a person would from its diff. */
function rereadFile(path = 'file-1.txt') {
  chooseTab('Changes');
  fireEvent.click(
    screen.getByRole('button', { name: `Open ${path} in Files` }),
  );
}

async function populatePane() {
  fireEvent.click(
    await screen.findByRole('button', { name: 'Show changes in file-1.txt' }),
  );
  await screen.findByRole('region', { name: 'Diff text' });
  await screen.findByRole('button', {
    name: 'Show changes in first-change.txt',
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Open file-1.txt in Files' }),
  );
  await screen.findByRole('region', { name: 'File text' });
}

it('retains the actual Inspector draft through hidden rendering and a full panel remount', async () => {
  const { owner, controller, options } = editingLifetime();
  const first = render(<WorkspaceInspector {...options} />);
  await openFile();
  fireEvent.click(await screen.findByRole('button', { name: 'Edit file' }));
  fireEvent.change(
    await screen.findByRole('textbox', { name: 'File contents' }),
    { target: { value: 'Retained unsaved draft' } },
  );
  first.rerender(<WorkspaceInspector {...options} visible={false} />);
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  first.rerender(<WorkspaceInspector {...options} visible />);
  // Files stays selected; the retained editor reopens with its draft.
  expect(
    await screen.findByRole('textbox', { name: 'File contents' }),
  ).toHaveValue('Retained unsaved draft');
  first.unmount();
  render(
    <WorkspaceInspector
      {...options}
      editSessions={owner.forBinding('chat-a', 'binding')}
    />,
  );
  fireEvent.mouseDown(await screen.findByRole('tab', { name: /^Files/ }), {
    button: 0,
  });
  expect(
    await screen.findByRole('textbox', { name: 'File contents' }),
  ).toHaveValue('Retained unsaved draft');
  expect(
    screen.getByRole('button', { name: 'Resume edit: file-1.txt' }),
  ).toBeInTheDocument();
  expect(controller.workspaceEditableFile).toHaveBeenCalledOnce();
  owner.dispose();
});

it('settles an actual Inspector save after unmount and remounts its completed content', async () => {
  const { owner, controller, options, editable } = editingLifetime();
  let finish!: (value: unknown) => void;
  controller.command.mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const first = render(<WorkspaceInspector {...options} />);
  await openFile();
  fireEvent.click(await screen.findByRole('button', { name: 'Edit file' }));
  fireEvent.change(
    await screen.findByRole('textbox', { name: 'File contents' }),
    { target: { value: 'Saved while hidden' } },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Save file' }));
  await waitFor(() => expect(controller.command).toHaveBeenCalledOnce());
  first.unmount();
  const pendingView = render(
    <WorkspaceInspector
      {...options}
      editSessions={owner.forBinding('chat-a', 'binding')}
    />,
  );
  fireEvent.mouseDown(await screen.findByRole('tab', { name: /^Files/ }), {
    button: 0,
  });
  expect(
    await screen.findByRole('textbox', { name: 'File contents' }),
  ).toHaveValue('Saved while hidden');
  expect(screen.getByRole('textbox', { name: 'File contents' })).toBeDisabled();
  expect(owner.hasRetained()).toBe(true);
  expect(controller.command).toHaveBeenCalledOnce();
  pendingView.unmount();
  await act(async () => {
    finish({
      status: 'completed',
      workspace_edit: { ...editable, status: 'saved', digest: 'b'.repeat(64) },
    });
  });
  const second = render(
    <WorkspaceInspector
      {...options}
      editSessions={owner.forBinding('chat-a', 'binding')}
    />,
  );
  fireEvent.mouseDown(await screen.findByRole('tab', { name: /^Files/ }), {
    button: 0,
  });
  expect(
    await screen.findByRole('textbox', { name: 'File contents' }),
  ).toHaveValue('Saved while hidden');
  expect(
    screen.getByText(
      'File saved. Original bytes remain available in edit recovery.',
    ),
  ).toBeInTheDocument();
  expect(controller.command).toHaveBeenCalledOnce();
  expect(owner.hasRetained()).toBe(false);
  second.unmount();
  owner.dispose();
});

function props(): WorkspaceInspectorProps {
  return {
    resourceId: 'workspace',
    resourceRevision: '1',
    visible: true,
    load: vi.fn(async () => fixture),
    changes: vi.fn(async (_revision, cursor) => ({
      items: [
        {
          path: cursor ? 'file-1003.txt' : 'file-1.txt',
          status: 'M',
          additions: 1,
          deletions: 0,
        },
      ],
      total: 1003,
      snapshot_revision: '1',
      next_cursor: cursor ? null : 'next-change',
    })),
    directory: vi.fn(async (path, cursor) => ({
      items: path
        ? [
            {
              name: 'child.txt',
              relative_path: `${path}/child.txt`,
              kind: 'file' as const,
              previewable: true,
            },
          ]
        : cursor
          ? [
              {
                name: 'last.txt',
                relative_path: 'last.txt',
                kind: 'file' as const,
                previewable: true,
              },
            ]
          : [
              {
                name: 'nested',
                relative_path: 'nested',
                kind: 'directory' as const,
                previewable: false,
              },
            ],
      next_cursor: path || cursor ? null : 'next-file',
      directory_revision: 'directory-1',
      excluded: ['.git'],
    })),
    file: vi.fn(async (path, offset) => ({
      status: 'text' as const,
      relative_path: path,
      text: offset ? 'Second section' : '<script>inert fixture</script>',
      revision: 'file-1',
      next_offset: offset ? null : 99,
      size_bytes: 120,
    })),
    diff: vi.fn(async (_path, _snapshot, offset) => ({
      status: 'text' as const,
      text: offset ? '+next change' : '-old\n+new',
      revision: 'diff-1',
      next_offset: offset ? null : 20,
      truncated: !offset,
    })),
    changeSets: vi.fn(async (_revision, cursor) => ({
      items: [
        {
          id: cursor ? 'set-last' : 'set-first',
          summary: cursor ? 'Last changes' : 'First changes',
          reviewed: false,
          reverted: false,
          file_count: 501,
          undoable: !cursor,
        },
      ],
      next_cursor: cursor ? null : 'sets-next',
      snapshot_revision: '1',
      total: 20,
    })),
    changeSetFiles: vi.fn(async (identifier, _revision, cursor) => ({
      items: [
        {
          path: cursor ? 'last-change.txt' : 'first-change.txt',
          action: 'update',
        },
      ],
      change_set_id: identifier,
      next_cursor: cursor ? null : 'set-files-next',
      snapshot_revision: '1',
      total: 501,
    })),
  };
}

function pending<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((accept, decline) => {
    resolve = accept;
    reject = decline;
  });
  return { promise, resolve, reject };
}

describe('Developer inspector', () => {
  it('shows a status strip and opens on Changes when a repository has changes', async () => {
    const options = props();
    render(
      <WorkspaceInspector {...options} renderGit={() => <p>Git owner</p>} />,
    );
    const strip = await screen.findByRole('group', {
      name: 'Repository status',
    });
    expect(strip).toHaveTextContent('Fixture workspace');
    expect(
      screen.getByRole('button', { name: 'Branch fixture. Open Git' }),
    ).toBeInTheDocument();
    expect(strip).toHaveTextContent('1003 changed');
    expect(strip).toHaveTextContent('Local');
    expect(screen.getByRole('tab', { name: /^Changes/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByRole('tab', { name: /^Changes/ })).toHaveTextContent(
      '1003',
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Branch fixture. Open Git' }),
    );
    expect(screen.getByRole('tab', { name: /^Git/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('opens on Changes for agent changes in a folder without Git (U34)', async () => {
    const options = props();
    options.load = vi.fn(async () => ({
      ...fixture,
      is_git: false,
      changed_total: 0,
    }));
    render(<WorkspaceInspector {...options} />);
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: /^Changes/ })).toHaveAttribute(
        'aria-selected',
        'true',
      ),
    );
  });

  it('keeps the tab the person chose when agent changes arrive later', async () => {
    const options = props();
    options.load = vi.fn(async () => ({
      ...fixture,
      is_git: false,
      changed_total: 0,
    }));
    const sets = pending<Awaited<ReturnType<typeof options.changeSets>>>();
    const original = options.changeSets;
    options.changeSets = vi.fn(() => sets.promise);
    render(<WorkspaceInspector {...options} />);
    await screen.findByRole('tab', { name: /^Files/ });
    chooseTab('Run');
    sets.resolve(await original('1', undefined, new AbortController().signal));
    await waitFor(() => expect(options.changeSets).toHaveBeenCalled());
    expect(screen.getByRole('tab', { name: /^Run/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('keeps clone, install, network and delete boundaries in the safety popover', async () => {
    const user = userEvent.setup();
    const options = props();
    const unavailable = (code: string) => ({ available: false, code });
    render(
      <WorkspaceInspector
        {...options}
        repository={
          {
            availability: {
              'developer.repository.clone': unavailable('use_workspace_setup'),
              'developer.repository.install': unavailable(
                'use_workspace_process_review',
              ),
              'developer.repository.network': unavailable(
                'use_workspace_process_review',
              ),
              'developer.repository.delete': unavailable(
                'no_recoverable_repository_delete_owner',
              ),
            },
          } as unknown as WorkspaceInspectorProps['repository']
        }
      />,
    );
    await screen.findByRole('group', { name: 'Repository status' });
    await user.click(screen.getByRole('button', { name: 'Safety boundaries' }));
    const popover = await screen.findByRole('dialog', {
      name: 'Safety boundaries',
    });
    expect(popover).toHaveTextContent(
      'DeleteNot offered here: a delete could not be undone.',
    );
    expect(popover).toHaveTextContent(
      'NetworkRuns only as a reviewed command in the Run tab.',
    );
    expect(popover).toHaveTextContent(
      'CloneClone a repository through Add resource.',
    );
    expect(
      screen.queryByRole('button', { name: /^(clone|install|delete)/i }),
    ).not.toBeInTheDocument();
  });

  it('reads the folder afresh whenever it opens', async () => {
    const options = props();
    const view = render(<WorkspaceInspector {...options} />);
    await screen.findByRole('group', { name: 'Repository status' });
    expect(options.load).toHaveBeenLastCalledWith(
      true,
      expect.any(AbortSignal),
    );
    view.rerender(<WorkspaceInspector {...options} visible={false} />);
    view.rerender(<WorkspaceInspector {...options} />);
    await waitFor(() => expect(options.load).toHaveBeenCalledTimes(2));
    expect(options.load).toHaveBeenLastCalledWith(
      true,
      expect.any(AbortSignal),
    );
  });

  it('opens a plain folder on Files and says it is not a repository', async () => {
    const options = props();
    options.load = vi.fn(async () => ({
      ...fixture,
      is_git: false,
      branch: '',
      changed_total: 0,
      diff_stats: null,
    }));
    // No agent changes either: with them it opens on Changes.
    options.changeSets = vi.fn(async () => ({
      items: [],
      next_cursor: null,
      snapshot_revision: '1',
      total: 0,
    }));
    render(<WorkspaceInspector {...options} />);
    expect(
      await screen.findByText('Folder is not a Git repository'),
    ).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /^Files/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(
      screen.getByRole('button', { name: 'Open folder nested' }),
    ).toBeInTheDocument();
  });

  it('groups files under the agent change that made them and keeps other changes apart', async () => {
    const options = props();
    const onUndo = vi.fn();
    render(<WorkspaceInspector {...options} onUndo={onUndo} />);
    const group = await screen.findByRole('region', {
      name: 'Agent change: First changes',
    });
    await waitFor(() =>
      expect(group).toContainElement(
        screen.getByRole('button', {
          name: 'Show changes in first-change.txt',
        }),
      ),
    );
    expect(group).toHaveTextContent('501 files');
    expect(group).toHaveTextContent('Not reviewed');
    expect(
      screen.getByRole('region', { name: 'Other changes' }),
    ).toContainElement(
      screen.getByRole('button', { name: 'Show changes in file-1.txt' }),
    );
    expect(options.changeSetFiles).toHaveBeenCalledWith(
      'set-first',
      '1',
      undefined,
      expect.any(AbortSignal),
    );
    expect(onUndo).not.toHaveBeenCalled();
  });

  it('undoes an imported change in the panel and asks the agent to undo its own edits', async () => {
    const user = userEvent.setup();
    const options = props();
    const onUndo = vi.fn();
    const onAsk = vi.fn((_text: string) => true);
    render(<WorkspaceInspector {...options} onUndo={onUndo} onAsk={onAsk} />);
    await screen.findByRole('region', { name: 'Agent change: First changes' });
    await user.click(
      screen.getByRole('button', { name: 'More actions for First changes' }),
    );
    await user.click(
      screen.getByRole('menuitem', { name: 'Undo First changes' }),
    );
    expect(onUndo).toHaveBeenCalledWith('set-first', 'First changes');
    fireEvent.click(screen.getByRole('button', { name: 'More agent changes' }));
    await screen.findByRole('region', { name: 'Agent change: Last changes' });
    await user.click(
      screen.getByRole('button', { name: 'More actions for Last changes' }),
    );
    expect(
      screen.queryByRole('menuitem', { name: 'Undo Last changes' }),
    ).toBeNull();
    await user.click(
      screen.getByRole('menuitem', {
        name: 'Ask Row-Bot to undo Last changes',
      }),
    );
    expect(onAsk).toHaveBeenCalledWith(
      expect.stringContaining('developer_revert_agent_changes'),
    );
    expect(onAsk.mock.calls[0][0]).toContain('set-last');
    expect(
      screen.getByText('Asked Row-Bot to undo it in the chat.'),
    ).toBeInTheDocument();
  });

  it('shows a diff unified or side by side and remembers the choice', async () => {
    window.localStorage.removeItem('row-bot.diff-mode.v1');
    const options = props();
    options.diff = vi.fn(async () => ({
      status: 'text' as const,
      text: '@@ -1,2 +1,2 @@\n keep\n-old line\n+new line',
      revision: 'diff-1',
      next_offset: null,
      truncated: false,
    }));
    const view = render(<WorkspaceInspector {...options} />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Show changes in file-1.txt' }),
    );
    const table = await screen.findByRole('table', {
      name: 'Changes in file-1.txt',
    });
    expect(table).toHaveAttribute('data-mode', 'unified');
    expect(table).toHaveTextContent('Removed');
    expect(table).toHaveTextContent('Added');
    fireEvent.click(screen.getByRole('radio', { name: 'Split' }));
    expect(
      screen.getByRole('table', { name: 'Changes in file-1.txt' }),
    ).toHaveAttribute('data-mode', 'split');
    expect(window.localStorage.getItem('row-bot.diff-mode.v1')).toBe('split');
    view.unmount();
    render(<WorkspaceInspector {...props()} />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Show changes in file-1.txt' }),
    );
    expect(
      await screen.findByRole('table', { name: 'Changes in file-1.txt' }),
    ).toHaveAttribute('data-mode', 'split');
    window.localStorage.removeItem('row-bot.diff-mode.v1');
  });

  it('hands the Git tab the changed files and suggested messages', async () => {
    const options = props();
    const renderGit = vi.fn<NonNullable<WorkspaceInspectorProps['renderGit']>>(
      () => <p>Git owner</p>,
    );
    render(<WorkspaceInspector {...options} renderGit={renderGit} />);
    await screen.findByRole('button', {
      name: 'Show changes in first-change.txt',
    });
    chooseTab('Git');
    expect(screen.getByText('Git owner')).toBeVisible();
    const context = renderGit.mock.lastCall![0];
    expect(context).toMatchObject({
      revision: expect.any(String),
      isGit: true,
      branch: 'fixture',
      changedFiles: [{ path: 'file-1.txt', status: 'M' }],
    });
    expect(context.commitSuggestion?.subject).toBe('Update file-1.txt');
    expect(context.pullRequestSuggestion?.body).toContain('First changes');
  });

  it('lists detected checks and folder processes without running anything', async () => {
    const options = props();
    render(<WorkspaceInspector {...options} />);
    await screen.findByText('Fixture workspace');
    chooseTab('Run');
    const checks = screen.getByRole('list', { name: 'Detected checks' });
    expect(checks).toHaveTextContent('pytest');
    expect(checks).toHaveTextContent('python -m pytest');
    expect(checks).toHaveTextContent('Not run');
    expect(
      screen.getByRole('region', { name: 'Workspace process status' }),
    ).toHaveTextContent('PID 42');
    const renderRun = vi.fn(() => <p>Process owner</p>);
    render(<WorkspaceInspector {...props()} renderRun={renderRun} />);
    await waitFor(() =>
      expect(renderRun).toHaveBeenCalledWith({
        checks: [
          { label: 'pytest', kind: 'test', command: 'python -m pytest' },
        ],
      }),
    );
  });

  it('summarises check runs into one status', () => {
    const checks = [
      { label: 'pytest', kind: 'test', command: 'python -m pytest' },
      { label: 'lint', kind: 'lint', command: 'npm run lint' },
    ];
    const process = (command: string, extra = {}) => ({
      process_id: command,
      command_id: command,
      run_id: 'run',
      command,
      state: 'exited' as const,
      exit_code: 0,
      quiesced: true,
      ...extra,
    });
    expect(checksStatus([], []).label).toBe('No checks');
    expect(checksStatus(checks, []).label).toBe('Checks not run');
    expect(checksStatus(checks, [process('python -m pytest')]).label).toBe(
      'Some checks passed',
    );
    expect(
      checksStatus(checks, [
        process('python -m pytest'),
        process('npm run lint'),
      ]).tone,
    ).toBe('success');
    expect(
      checksStatus(checks, [
        process('python -m pytest', { exit_code: 1 }),
        process('npm run lint'),
      ]).label,
    ).toBe('Checks failed');
    expect(
      checksStatus(checks, [
        process('npm run lint', {
          state: 'running',
          quiesced: false,
          exit_code: null,
        }),
      ]).pulse,
    ).toBe(true);
    // Only the latest run of a check counts.
    expect(
      checksStatus(checks, [
        process('python -m pytest', { exit_code: 1 }),
        process('python -m pytest'),
        process('npm run lint'),
      ]).label,
    ).toBe('Checks passed');
  });

  it.each([
    ['file', 'resource_binding_revoked'],
    ['summary', 'resource_binding_revoked'],
    ['file', 'resource_unavailable'],
    ['file', 'workspace_path_denied'],
    ['file', 'conversation_deleting'],
    ['file', 'capability_unavailable'],
  ] as const)(
    'clears cached private sections after %s returns %s and allows explicit retry',
    async (lane, code) => {
      const options = props();
      render(<WorkspaceInspector {...options} />);
      await populatePane();
      vi.mocked(
        options[lane === 'summary' ? 'load' : 'file'],
      ).mockRejectedValueOnce({
        code,
        status: code === 'resource_unavailable' ? 404 : 403,
        title: 'private failure detail',
      });
      if (lane === 'summary')
        fireEvent.click(
          screen.getByRole('button', { name: 'Refresh inspector' }),
        );
      else rereadFile();
      expect(
        await screen.findByText('Workspace access changed'),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          'This workspace is unavailable or access changed. Review its binding and permissions, then retry. Your conversation is preserved.',
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText('Fixture workspace')).not.toBeInTheDocument();
      expect(screen.queryByRole('region', { name: / inspector$/ })).toBeNull();
      expect(screen.queryByLabelText('File text')).not.toBeInTheDocument();
      expect(screen.queryByLabelText('Diff text')).not.toBeInTheDocument();
      expect(
        screen.queryByLabelText('Workspace files'),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole('region', { name: 'Agent change: First changes' }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText('private failure detail'),
      ).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Retry inspector' }));
      expect(await openFile()).toHaveTextContent('inert fixture');
    },
  );

  it('aborts the entire current request cohort and rejects its late successes after denial', async () => {
    const options = props();
    render(<WorkspaceInspector {...options} />);
    await populatePane();
    chooseTab('Changes');
    const late =
      pending<Awaited<ReturnType<WorkspaceInspectorProps['diff']>>>();
    vi.mocked(options.diff).mockImplementationOnce(() => late.promise);
    fireEvent.click(screen.getByRole('button', { name: 'Next diff section' }));
    const diffSignal = vi.mocked(options.diff).mock.calls.at(-1)?.[4];
    vi.mocked(options.file).mockRejectedValueOnce({
      code: 'resource_binding_revoked',
      status: 403,
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Open file-1.txt in Files' }),
    );
    await screen.findByText('Workspace access changed');
    expect(diffSignal?.aborted).toBe(true);
    await act(async () =>
      late.resolve({
        status: 'text',
        text: '+Late secret diff',
        revision: 'late',
        next_offset: null,
        truncated: false,
      }),
    );
    expect(screen.queryByText(/Late secret diff/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Diff text')).not.toBeInTheDocument();
  });

  it('does not let a late resource A denial erase resource B', async () => {
    const options = props();
    const late = pending<WorkspaceFile>();
    options.file = vi.fn(() => late.promise);
    const view = render(<WorkspaceInspector {...options} />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Show changes in file-1.txt' }),
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Open file-1.txt in Files' }),
    );
    const next = {
      ...props(),
      resourceId: 'workspace-b',
      load: vi.fn(async () => ({ ...fixture, name: 'Workspace B' })),
    };
    view.rerender(<WorkspaceInspector {...next} />);
    await screen.findByText('Workspace B');
    await act(async () =>
      late.reject({ code: 'resource_binding_revoked', status: 403 }),
    );
    expect(screen.getByText('Workspace B')).toBeInTheDocument();
    expect(
      screen.queryByText('Workspace access changed'),
    ).not.toBeInTheDocument();
  });

  it.each([
    'inspector_unavailable',
    'rate_limited',
    'resource_revision_conflict',
  ])('retains labeled stale content for transient %s', async (code) => {
    const options = props();
    render(<WorkspaceInspector {...options} />);
    await populatePane();
    vi.mocked(options.file).mockRejectedValueOnce({ code, status: 503 });
    rereadFile();
    await screen.findByText('File unavailable');
    expect(screen.getByLabelText('File text')).toHaveTextContent(
      'inert fixture',
    );
    expect(screen.getByText(/may be out of date/)).toBeInTheDocument();
    chooseTab('Changes');
    expect(screen.getByLabelText('Diff text')).toHaveTextContent('old');
    expect(screen.getByLabelText('Diff text')).toHaveTextContent('new');
  });

  it('treats a missing file result as local to the selected file', async () => {
    const options = props();
    render(<WorkspaceInspector {...options} />);
    await populatePane();
    vi.mocked(options.file).mockResolvedValueOnce({
      status: 'missing',
      relative_path: 'file-1.txt',
      text: '',
      revision: '',
      next_offset: null,
      size_bytes: 0,
    });
    rereadFile();
    await screen.findByText('This file is gone');
    expect(screen.queryByLabelText('File text')).not.toBeInTheDocument();
    expect(screen.getByText('Fixture workspace')).toBeInTheDocument();
    chooseTab('Changes');
    expect(screen.getByLabelText('Diff text')).toBeInTheDocument();
  });

  it.each([
    { code: 'resource_binding_revoked', status: 401 },
    { code: 'action_denied', status: 403 },
    { code: 'unknown_denial', status: 403 },
  ])(
    'does not relabel controller-owned authorization errors as pane scope failures: %j',
    async (failure) => {
      const options = props();
      render(<WorkspaceInspector {...options} />);
      await populatePane();
      vi.mocked(options.file).mockRejectedValueOnce(failure);
      rereadFile();
      await screen.findByText('File unavailable');
      expect(
        screen.queryByText('Workspace access changed'),
      ).not.toBeInTheDocument();
    },
  );

  it('does not consume nonzero backward history on a failed page', async () => {
    const options = props();
    options.file = vi.fn(async (path, offset = 0) => ({
      status: 'text' as const,
      relative_path: path,
      text: `Section ${offset}`,
      revision: 'file',
      next_offset: offset + 1,
      size_bytes: 1000,
    }));
    render(<WorkspaceInspector {...options} />);
    await openFile();
    for (let offset = 1; offset <= 2; offset += 1) {
      fireEvent.click(
        screen.getByRole('button', { name: 'Next file section' }),
      );
      await waitFor(() =>
        expect(screen.getByLabelText('File text')).toHaveTextContent(
          `Section ${offset}`,
        ),
      );
    }
    vi.mocked(options.file).mockRejectedValueOnce(new Error('temporary'));
    fireEvent.click(
      screen.getByRole('button', { name: 'Previous file section' }),
    );
    await screen.findByText('File unavailable');
    expect(screen.getByLabelText('File text')).toHaveTextContent('Section 2');
    fireEvent.click(
      screen.getByRole('button', { name: 'Previous file section' }),
    );
    await waitFor(() =>
      expect(screen.getByLabelText('File text')).toHaveTextContent('Section 1'),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Previous file section' }),
    );
    await waitFor(() =>
      expect(screen.getByLabelText('File text')).toHaveTextContent('Section 0'),
    );
    expect(vi.mocked(options.file).mock.calls.map((call) => call[1])).toEqual([
      0, 1, 2, 1, 1, 0,
    ]);
    expect(
      screen.queryByRole('button', { name: 'Previous file section' }),
    ).not.toBeInTheDocument();
  });

  it('bounds previous offsets to 32 while preserving full forward traversal and an explicit restart', async () => {
    const options = props();
    options.file = vi.fn(async (path, offset = 0) => ({
      status: 'text' as const,
      relative_path: path,
      text: `Section ${offset}`,
      revision: 'file',
      next_offset: offset + 1,
      size_bytes: 1000,
    }));
    render(<WorkspaceInspector {...options} />);
    await openFile();
    // 72 page turns: the fake reads resolve at once, so one act() flush
    // settles each turn. waitFor's real-timer polling and getByRole's
    // accessible-name pass made this test overrun 5 s under load.
    for (let offset = 1; offset <= 40; offset += 1) {
      fireEvent.click(screen.getByText('Next file section'));
      await act(async () => {});
      expect(screen.getByLabelText('File text')).toHaveTextContent(
        new RegExp(`^Section ${offset}$`),
      );
    }
    for (let offset = 39; offset >= 8; offset -= 1) {
      fireEvent.click(screen.getByText('Previous file section'));
      await act(async () => {});
      expect(screen.getByLabelText('File text')).toHaveTextContent(
        new RegExp(`^Section ${offset}$`),
      );
    }
    expect(
      screen.queryByRole('button', { name: 'Previous file section' }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'First file section' }));
    await waitFor(() =>
      expect(screen.getByLabelText('File text')).toHaveTextContent(
        /^Section 0$/,
      ),
    );
    expect(
      screen.queryByRole('button', { name: 'First file section' }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Next file section' }));
    await waitFor(() =>
      expect(screen.getByLabelText('File text')).toHaveTextContent(
        /^Section 1$/,
      ),
    );
    expect(options.file).toHaveBeenLastCalledWith(
      'file-1.txt',
      1,
      'file',
      expect.any(AbortSignal),
    );
  });

  it('changes page history only after successful reads, including failed forward and backward navigation', async () => {
    const options = props();
    render(<WorkspaceInspector {...options} />);
    await openFile();
    vi.mocked(options.file).mockRejectedValueOnce(new Error('transient'));
    fireEvent.click(screen.getByRole('button', { name: 'Next file section' }));
    await screen.findByText('File unavailable');
    expect(
      screen.queryByRole('button', { name: 'Previous file section' }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Next file section' }));
    await waitFor(() =>
      expect(screen.getByLabelText('File text')).toHaveTextContent(
        'Second section',
      ),
    );
    vi.mocked(options.file).mockRejectedValueOnce(new Error('transient'));
    fireEvent.click(
      screen.getByRole('button', { name: 'Previous file section' }),
    );
    await screen.findByText('File unavailable');
    expect(screen.getByLabelText('File text')).toHaveTextContent(
      'Second section',
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Previous file section' }),
    );
    await waitFor(() =>
      expect(screen.getByLabelText('File text')).toHaveTextContent(
        'inert fixture',
      ),
    );
    expect(
      screen.queryByRole('button', { name: 'Previous file section' }),
    ).not.toBeInTheDocument();
    expect(options.file).toHaveBeenLastCalledWith(
      'file-1.txt',
      0,
      'file-1',
      expect.any(AbortSignal),
    );
  });

  it('pages changes, expands only requested folders, filters loaded files and reads bounded file sections', async () => {
    const options = props();
    render(<WorkspaceInspector {...options} />);
    expect(await screen.findByText('Fixture workspace')).toBeInTheDocument();
    expect(options.directory).toHaveBeenCalledTimes(1);
    fireEvent.click(
      await screen.findByRole('button', { name: 'More changed files' }),
    );
    expect(
      await screen.findByRole('button', {
        name: 'Show changes in file-1003.txt',
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Show changes in file-1.txt' }),
    ).toBeInTheDocument();
    chooseTab('Files');
    fireEvent.click(screen.getByRole('button', { name: 'Open folder nested' }));
    expect(
      screen.getByRole('button', { name: 'Open folder nested' }),
    ).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(
      await screen.findByRole('button', { name: 'Preview file child.txt' }),
    );
    const fileRegion = await screen.findByRole('region', { name: 'File text' });
    expect(fileRegion.textContent).toBe('<script>inert fixture</script>');
    expect(fileRegion).toHaveAttribute('tabindex', '0');
    fileRegion.focus();
    expect(fileRegion).toHaveFocus();
    expect(document.querySelector('script')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Next file section' }));
    await waitFor(() =>
      expect(screen.getByLabelText('File text')).toHaveTextContent(
        'Second section',
      ),
    );
    expect(options.file).toHaveBeenLastCalledWith(
      'nested/child.txt',
      99,
      'file-1',
      expect.any(AbortSignal),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Previous file section' }),
    );
    await waitFor(() =>
      expect(screen.getByLabelText('File text')).toHaveTextContent(
        'inert fixture',
      ),
    );
    fireEvent.change(screen.getByRole('searchbox', { name: 'Filter files' }), {
      target: { value: 'child' },
    });
    expect(
      screen.getByRole('button', { name: 'Preview file child.txt' }),
    ).toHaveTextContent('nested/child.txt');
    expect(
      screen.queryByRole('button', { name: 'Open folder nested' }),
    ).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('searchbox', { name: 'Filter files' }), {
      target: { value: '' },
    });
    fireEvent.click(
      screen.getAllByRole('button', { name: 'More files in this folder' })[0],
    );
    expect(
      await screen.findByRole('button', { name: 'Preview file last.txt' }),
    ).toBeInTheDocument();
    expect(options.directory).toHaveBeenLastCalledWith(
      '',
      'next-file',
      'directory-1',
      expect.any(AbortSignal),
    );
  });

  it('rejects late resource responses and stops reading while hidden', async () => {
    const first = pending<Inspector>();
    const options = props();
    options.load = vi.fn(() => first.promise);
    const view = render(<WorkspaceInspector {...options} />);
    const next = props();
    next.resourceId = 'workspace-b';
    next.load = vi.fn(async () => ({
      ...fixture,
      resource_id: 'workspace-b',
      name: 'Workspace B',
    }));
    view.rerender(<WorkspaceInspector {...next} />);
    expect(await screen.findByText('Workspace B')).toBeInTheDocument();
    await act(async () => first.resolve(fixture));
    expect(screen.queryByText('Fixture workspace')).not.toBeInTheDocument();
    expect(options.changes).not.toHaveBeenCalled();
    view.rerender(<WorkspaceInspector {...next} visible={false} />);
    expect(screen.queryByText('Workspace B')).not.toBeInTheDocument();
    const calls = vi.mocked(next.load).mock.calls.length;
    view.rerender(
      <WorkspaceInspector {...next} visible={false} resourceRevision="2" />,
    );
    expect(next.load).toHaveBeenCalledTimes(calls);
  });

  it('reads agent changes only for the snapshot a refresh confirmed', async () => {
    const refreshed = pending<Inspector>();
    const options = props();
    options.load = vi
      .fn()
      .mockResolvedValueOnce(fixture)
      .mockReturnValueOnce(refreshed.promise);
    render(<WorkspaceInspector {...options} />);
    await screen.findByText('Fixture workspace');
    await waitFor(() => expect(options.changeSets).toHaveBeenCalledOnce());
    expect(options.changeSets).toHaveBeenCalledWith(
      '1',
      undefined,
      expect.any(AbortSignal),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Refresh inspector' }));
    expect(options.changeSets).toHaveBeenCalledOnce();
    await act(async () =>
      refreshed.resolve({ ...fixture, snapshot_revision: '2' }),
    );
    await waitFor(() => expect(options.changeSets).toHaveBeenCalledTimes(2));
    expect(options.changeSets).toHaveBeenLastCalledWith(
      '2',
      undefined,
      expect.any(AbortSignal),
    );
  });

  it('keeps request scope when file A completes after file B', async () => {
    const deferred = pending<WorkspaceFile>();
    const options = props();
    options.file = vi.fn((path) =>
      path === 'file-1.txt'
        ? deferred.promise
        : Promise.resolve({
            status: 'text' as const,
            relative_path: path,
            text: 'Current file',
            revision: 'file-b',
            next_offset: null,
            size_bytes: 12,
          }),
    );
    render(<WorkspaceInspector {...options} />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Show changes in file-1.txt' }),
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Open file-1.txt in Files' }),
    );
    chooseTab('Changes');
    fireEvent.click(screen.getByRole('button', { name: 'More changed files' }));
    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Show changes in file-1003.txt',
      }),
    );
    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Open file-1003.txt in Files',
      }),
    );
    expect(await screen.findByLabelText('File text')).toHaveTextContent(
      'Current file',
    );
    await act(async () =>
      deferred.resolve({
        status: 'text',
        relative_path: 'file-1.txt',
        text: 'Stale file',
        revision: 'old',
        next_offset: null,
        size_bytes: 10,
      }),
    );
    expect(screen.getByLabelText('File text')).toHaveTextContent(
      'Current file',
    );
  });

  it('retries failed reads without a resource mutation or inferred test success', async () => {
    const options = props();
    options.load = vi
      .fn()
      .mockRejectedValueOnce(new Error('private path'))
      .mockResolvedValue(fixture);
    render(<WorkspaceInspector {...options} />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Retry inspector' }),
    );
    expect(await screen.findByText('Fixture workspace')).toBeInTheDocument();
    expect(screen.queryByText('private path')).not.toBeInTheDocument();
    chooseTab('Run');
    expect(
      screen.getByRole('list', { name: 'Detected checks' }),
    ).toHaveTextContent('Not run');
    expect(options.load).toHaveBeenLastCalledWith(
      true,
      expect.any(AbortSignal),
    );
  });

  it('loads an explicitly selected diff with snapshot and byte continuation revisions', async () => {
    const options = props();
    render(<WorkspaceInspector {...options} />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Show changes in file-1.txt' }),
    );
    const diffRegion = await screen.findByRole('region', { name: 'Diff text' });
    expect(diffRegion).toHaveTextContent('old');
    expect(diffRegion).toHaveTextContent('new');
    expect(diffRegion).toHaveAttribute('tabindex', '0');
    diffRegion.focus();
    expect(diffRegion).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'Next diff section' }));
    await waitFor(() =>
      expect(screen.getByLabelText('Diff text')).toHaveTextContent(
        'next change',
      ),
    );
    expect(options.diff).toHaveBeenLastCalledWith(
      'file-1.txt',
      '1',
      20,
      'diff-1',
      expect.any(AbortSignal),
    );
    fireEvent.click(screen.getByRole('button', { name: 'First diff section' }));
    await waitFor(() =>
      expect(screen.getByLabelText('Diff text')).toHaveTextContent('old'),
    );
  });

  it('reaches all agent change sets and their files through explicit continuation', async () => {
    const options = props();
    render(<WorkspaceInspector {...options} />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'More agent changes' }),
    );
    const later = await screen.findByRole('region', {
      name: 'Agent change: Last changes',
    });
    expect(options.changeSetFiles).not.toHaveBeenCalledWith(
      'set-last',
      expect.anything(),
      undefined,
      expect.anything(),
    );
    fireEvent.click(
      within(later).getByRole('button', { name: /Last changes/ }),
    );
    fireEvent.click(
      await within(later).findByRole('button', {
        name: 'More files in this change',
      }),
    );
    expect(
      await screen.findByRole('button', {
        name: 'Show changes in last-change.txt',
      }),
    ).toBeInTheDocument();
    expect(later).toBeInTheDocument();
    expect(options.changeSetFiles).toHaveBeenLastCalledWith(
      'set-last',
      '1',
      'set-files-next',
      expect.any(AbortSignal),
    );
    expect(
      screen.queryByRole('button', { name: 'Revert' }),
    ).not.toBeInTheDocument();
  });
});
