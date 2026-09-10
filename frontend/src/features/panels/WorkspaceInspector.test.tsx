import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type {
  WorkspaceInspector as Inspector,
  WorkspaceFile,
} from '../../api/types';
import {
  WorkspaceInspector,
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
  commands: [{ label: 'pytest', kind: 'test', status: 'not_run' }],
  processes: [{ pid: 42, status: 'running' }],
  todos: [],
  error: '',
};

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

async function populatePane() {
  fireEvent.click(await screen.findByRole('button', { name: 'file-1.txt' }));
  await screen.findByRole('region', { name: 'File text' });
  fireEvent.click(screen.getByRole('button', { name: 'Show diff file-1.txt' }));
  await screen.findByRole('region', { name: 'Diff text' });
  fireEvent.click(screen.getByRole('button', { name: 'Load agent changes' }));
  fireEvent.click(await screen.findByRole('button', { name: 'First changes' }));
  await screen.findByRole('button', { name: 'first-change.txt' });
}

describe('read-only workspace Inspector', () => {
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
      fireEvent.click(
        screen.getByRole('button', {
          name: lane === 'summary' ? 'Refresh inspector' : 'file-1.txt',
        }),
      );
      expect(
        await screen.findByText('Workspace access changed'),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          'This workspace is unavailable or access changed. Review its binding and permissions, then retry. Your conversation is preserved.',
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText('Fixture workspace')).not.toBeInTheDocument();
      expect(screen.queryByLabelText('File text')).not.toBeInTheDocument();
      expect(screen.queryByLabelText('Diff text')).not.toBeInTheDocument();
      expect(
        screen.queryByLabelText('Workspace files'),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole('button', { name: 'First changes' }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole('button', { name: 'first-change.txt' }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText('private failure detail'),
      ).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Retry inspector' }));
      fireEvent.click(
        await screen.findByRole('button', { name: 'file-1.txt' }),
      );
      expect(await screen.findByLabelText('File text')).toHaveTextContent(
        'inert fixture',
      );
    },
  );

  it('aborts the entire current request cohort and rejects its late successes after denial', async () => {
    const options = props();
    render(<WorkspaceInspector {...options} />);
    await populatePane();
    const late =
      pending<Awaited<ReturnType<WorkspaceInspectorProps['diff']>>>();
    vi.mocked(options.diff).mockImplementationOnce(() => late.promise);
    fireEvent.click(screen.getByRole('button', { name: 'Next diff section' }));
    const diffSignal = vi.mocked(options.diff).mock.calls.at(-1)?.[4];
    vi.mocked(options.file).mockRejectedValueOnce({
      code: 'resource_binding_revoked',
      status: 403,
    });
    fireEvent.click(screen.getByRole('button', { name: 'file-1.txt' }));
    await screen.findByText('Workspace access changed');
    expect(diffSignal?.aborted).toBe(true);
    await act(async () =>
      late.resolve({
        status: 'text',
        text: 'Late secret diff',
        revision: 'late',
        next_offset: null,
        truncated: false,
      }),
    );
    expect(screen.queryByText('Late secret diff')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Diff text')).not.toBeInTheDocument();
  });

  it('does not let a late resource A denial erase resource B', async () => {
    const options = props();
    const late = pending<WorkspaceFile>();
    options.file = vi.fn(() => late.promise);
    const view = render(<WorkspaceInspector {...options} />);
    fireEvent.click(await screen.findByRole('button', { name: 'file-1.txt' }));
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
    fireEvent.click(screen.getByRole('button', { name: 'file-1.txt' }));
    await screen.findByText('File unavailable');
    expect(screen.getByLabelText('File text')).toHaveTextContent(
      'inert fixture',
    );
    expect(screen.getByLabelText('Diff text')).toHaveTextContent('-old +new');
    expect(screen.getByRole('status')).toHaveTextContent(
      'last confirmed workspace state and may be stale',
    );
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
    fireEvent.click(screen.getByRole('button', { name: 'file-1.txt' }));
    await screen.findByText('File missing');
    expect(screen.queryByLabelText('File text')).not.toBeInTheDocument();
    expect(screen.getByText('Fixture workspace')).toBeInTheDocument();
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
      fireEvent.click(screen.getByRole('button', { name: 'file-1.txt' }));
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
    fireEvent.click(await screen.findByRole('button', { name: 'file-1.txt' }));
    await screen.findByLabelText('File text');
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
    fireEvent.click(await screen.findByRole('button', { name: 'file-1.txt' }));
    await screen.findByLabelText('File text');
    for (let offset = 1; offset <= 40; offset += 1) {
      fireEvent.click(
        screen.getByRole('button', { name: 'Next file section' }),
      );
      await waitFor(() =>
        expect(screen.getByLabelText('File text')).toHaveTextContent(
          new RegExp(`^Section ${offset}$`),
        ),
      );
    }
    for (let offset = 39; offset >= 8; offset -= 1) {
      fireEvent.click(
        screen.getByRole('button', { name: 'Previous file section' }),
      );
      await waitFor(() =>
        expect(screen.getByLabelText('File text')).toHaveTextContent(
          new RegExp(`^Section ${offset}$`),
        ),
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
    fireEvent.click(await screen.findByRole('button', { name: 'file-1.txt' }));
    await screen.findByLabelText('File text');
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
  it('shows truthful policy and paged changes, expands only requested folders and reads bounded file sections', async () => {
    const options = props();
    render(<WorkspaceInspector {...options} />);
    expect(await screen.findByText('Fixture workspace')).toBeInTheDocument();
    expect(screen.getByText('pytest · Not run')).toBeInTheDocument();
    expect(screen.getByText('PID 42 · running')).toBeInTheDocument();
    expect(options.directory).toHaveBeenCalledTimes(1);
    fireEvent.click(
      await screen.findByRole('button', { name: 'More changed files' }),
    );
    expect(
      await screen.findByRole('button', { name: 'file-1003.txt' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'file-1.txt' }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open folder nested' }));
    fireEvent.click(
      await screen.findByRole('button', { name: 'Preview file child.txt' }),
    );
    const fileRegion = await screen.findByRole('region', { name: 'File text' });
    expect(fileRegion).toHaveTextContent('<script>inert fixture</script>');
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
    fireEvent.click(screen.getByRole('button', { name: 'Workspace root' }));
    fireEvent.click(
      await screen.findByRole('button', { name: 'More files in this folder' }),
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
    fireEvent.click(await screen.findByRole('button', { name: 'file-1.txt' }));
    fireEvent.click(screen.getByRole('button', { name: 'More changed files' }));
    fireEvent.click(
      await screen.findByRole('button', { name: 'file-1003.txt' }),
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
    expect(screen.getByText('pytest · Not run')).toBeInTheDocument();
    expect(options.load).toHaveBeenLastCalledWith(
      true,
      expect.any(AbortSignal),
    );
  });

  it('loads an explicitly selected diff with snapshot and byte continuation revisions', async () => {
    const options = props();
    render(<WorkspaceInspector {...options} />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Show diff file-1.txt' }),
    );
    const diffRegion = await screen.findByRole('region', { name: 'Diff text' });
    expect(diffRegion).toHaveTextContent('-old +new');
    expect(diffRegion).toHaveAttribute('tabindex', '0');
    diffRegion.focus();
    expect(diffRegion).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'Next diff section' }));
    await waitFor(() =>
      expect(screen.getByLabelText('Diff text')).toHaveTextContent(
        '+next change',
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
      expect(screen.getByLabelText('Diff text')).toHaveTextContent('-old +new'),
    );
  });

  it('reaches all agent change sets and their files through explicit continuation', async () => {
    const options = props();
    render(<WorkspaceInspector {...options} />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Load agent changes' }),
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'More agent changes' }),
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Last changes' }),
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'More files in change set' }),
    );
    expect(
      await screen.findByRole('button', { name: 'last-change.txt' }),
    ).toBeInTheDocument();
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
