import { useEffect, useRef, useState } from 'react';
import type {
  WorkspaceInspector as Inspector,
  WorkspaceChanges,
  WorkspaceDirectory,
  WorkspaceFile,
  WorkspaceDiff,
  WorkspaceChangeSetPage,
  WorkspaceChangeSetFiles,
} from '../../api/types';
import { Button, EmptyState, ErrorState, Skeleton } from '../../ui/primitives';

export type WorkspaceInspectorProps = {
  resourceId: string;
  resourceRevision: string;
  visible: boolean;
  load: (refresh?: boolean, signal?: AbortSignal) => Promise<Inspector>;
  changes: (
    revision: string,
    cursor?: string,
    signal?: AbortSignal,
  ) => Promise<WorkspaceChanges>;
  directory: (
    path: string,
    cursor?: string,
    revision?: string,
    signal?: AbortSignal,
  ) => Promise<WorkspaceDirectory>;
  file: (
    path: string,
    offset?: number,
    revision?: string,
    signal?: AbortSignal,
  ) => Promise<WorkspaceFile>;
  diff: (
    path: string,
    snapshotRevision: string,
    offset?: number,
    revision?: string,
    signal?: AbortSignal,
  ) => Promise<WorkspaceDiff>;
  changeSets: (
    snapshotRevision: string,
    cursor?: string,
    signal?: AbortSignal,
  ) => Promise<WorkspaceChangeSetPage>;
  changeSetFiles: (
    changeSetId: string,
    snapshotRevision: string,
    cursor?: string,
    signal?: AbortSignal,
  ) => Promise<WorkspaceChangeSetFiles>;
};

type Lane =
  | 'summary'
  | 'changes'
  | 'directory'
  | 'file'
  | 'diff'
  | 'ledger'
  | 'ledgerFiles';

function scopeUnavailable(error: unknown): boolean {
  if (typeof error !== 'object' || error === null || !('code' in error))
    return false;
  if ('status' in error && error.status === 401) return false;
  return [
    'resource_binding_revoked',
    'resource_unavailable',
    'not_found',
    'conversation_deleting',
    'workspace_path_denied',
    'path_denied',
    'workspace_read_hooks_unavailable',
    'capability_revoked',
    'capability_unavailable',
  ].some((code) => code === error.code);
}

/** Immutable query pages only; authority and resource bindings stay with the caller. */
export function WorkspaceInspector(props: WorkspaceInspectorProps) {
  const callbacks = useRef(props);
  callbacks.current = props;
  const epoch = useRef(0);
  const requests = useRef<Partial<Record<Lane, AbortController>>>({});
  const [owner, setOwner] = useState('');
  const [summary, setSummary] = useState<Inspector | null>(null);
  const [changed, setChanged] = useState<WorkspaceChanges | null>(null);
  const [listing, setListing] = useState<WorkspaceDirectory | null>(null);
  const [path, setPath] = useState('');
  const [preview, setPreview] = useState<WorkspaceFile | null>(null);
  const [filePath, setFilePath] = useState('');
  const [fileOffsets, setFileOffsets] = useState<number[]>([]);
  const [fileOffset, setFileOffset] = useState(0);
  const [diffPreview, setDiffPreview] = useState<WorkspaceDiff | null>(null);
  const [diffPath, setDiffPath] = useState('');
  const [diffOffset, setDiffOffset] = useState(0);
  const [ledger, setLedger] = useState<WorkspaceChangeSetPage | null>(null);
  const [ledgerFiles, setLedgerFiles] =
    useState<WorkspaceChangeSetFiles | null>(null);
  const [changeSetId, setChangeSetId] = useState('');
  const [busy, setBusy] = useState<Partial<Record<Lane, boolean>>>({});
  const [errors, setErrors] = useState<Partial<Record<Lane, boolean>>>({});
  const [accessChanged, setAccessChanged] = useState(false);

  function cancelQueries() {
    epoch.current += 1;
    Object.values(requests.current).forEach((request) => request?.abort());
    requests.current = {};
  }

  async function query<T>(
    lane: Lane,
    operation: (signal: AbortSignal) => Promise<T>,
    accept: (result: T) => void,
  ) {
    if (!callbacks.current.visible) return;
    requests.current[lane]?.abort();
    const request = new AbortController();
    requests.current[lane] = request;
    const version = epoch.current;
    const resource = callbacks.current.resourceId;
    const revision = callbacks.current.resourceRevision;
    setBusy((current) => ({ ...current, [lane]: true }));
    setErrors((current) => ({ ...current, [lane]: false }));
    const current = () =>
      version === epoch.current &&
      !request.signal.aborted &&
      callbacks.current.visible &&
      resource === callbacks.current.resourceId &&
      revision === callbacks.current.resourceRevision;
    try {
      const result = await operation(request.signal);
      if (current()) accept(result);
    } catch (error) {
      if (!current()) return;
      if (scopeUnavailable(error)) {
        cancelQueries();
        clearPages();
        setOwner('');
        setSummary(null);
        setBusy({});
        setErrors({});
        setAccessChanged(true);
      } else setErrors((value) => ({ ...value, [lane]: true }));
    } finally {
      if (current()) setBusy((value) => ({ ...value, [lane]: false }));
    }
  }

  function loadChanges(revision: string, cursor?: string) {
    return query(
      'changes',
      (signal) => callbacks.current.changes(revision, cursor, signal),
      setChanged,
    );
  }

  function loadDirectory(nextPath: string, cursor?: string, revision?: string) {
    if (nextPath !== path) setListing(null);
    setPath(nextPath);
    return query(
      'directory',
      (signal) =>
        callbacks.current.directory(nextPath, cursor, revision, signal),
      setListing,
    );
  }

  function loadFile(
    nextPath: string,
    offset = 0,
    revision?: string,
    earlier = false,
  ) {
    if (nextPath !== filePath) setPreview(null);
    setFilePath(nextPath);
    return query(
      'file',
      (signal) => callbacks.current.file(nextPath, offset, revision, signal),
      (result) => {
        if (nextPath !== filePath || offset === 0) setFileOffsets([]);
        else if (earlier) setFileOffsets((value) => value.slice(0, -1));
        else if (offset !== fileOffset)
          setFileOffsets((value) => [...value, fileOffset].slice(-32));
        setFileOffset(offset);
        setPreview(result);
      },
    );
  }

  function clearPages() {
    setListing(null);
    setPath('');
    setChanged(null);
    setPreview(null);
    setFilePath('');
    setFileOffsets([]);
    setFileOffset(0);
    setDiffPreview(null);
    setDiffPath('');
    setDiffOffset(0);
    setLedger(null);
    setLedgerFiles(null);
    setChangeSetId('');
  }

  function refresh(force = true) {
    cancelQueries();
    setBusy({});
    setErrors({});
    setAccessChanged(false);
    clearPages();
    return query(
      'summary',
      (signal) => callbacks.current.load(force, signal),
      (result) => {
        setOwner(callbacks.current.resourceId);
        setSummary(result);
        void loadChanges(result.snapshot_revision);
        void loadDirectory('');
      },
    );
  }

  function loadDiff(
    nextPath: string,
    snapshotRevision: string,
    offset = 0,
    revision?: string,
  ) {
    if (nextPath !== diffPath) setDiffPreview(null);
    setDiffPath(nextPath);
    return query(
      'diff',
      (signal) =>
        callbacks.current.diff(
          nextPath,
          snapshotRevision,
          offset,
          revision,
          signal,
        ),
      (result) => {
        setDiffPreview(result);
        setDiffOffset(offset);
      },
    );
  }

  function loadLedger(snapshotRevision: string, cursor?: string) {
    return query(
      'ledger',
      (signal) =>
        callbacks.current.changeSets(snapshotRevision, cursor, signal),
      setLedger,
    );
  }

  function loadLedgerFiles(
    identifier: string,
    snapshotRevision: string,
    cursor?: string,
  ) {
    if (identifier !== changeSetId) setLedgerFiles(null);
    setChangeSetId(identifier);
    return query(
      'ledgerFiles',
      (signal) =>
        callbacks.current.changeSetFiles(
          identifier,
          snapshotRevision,
          cursor,
          signal,
        ),
      setLedgerFiles,
    );
  }

  useEffect(() => {
    if (props.visible) void refresh(false);
    else cancelQueries();
    return cancelQueries;
    // Callback identity changes do not create another subscription or Git scan.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.resourceId, props.resourceRevision, props.visible]);

  const current = owner === props.resourceId ? summary : null;
  if (!props.visible) return null;
  if (accessChanged)
    return (
      <ErrorState
        title="Workspace access changed"
        action={<Button onClick={() => void refresh()}>Retry inspector</Button>}
      >
        This workspace is unavailable or access changed. Review its binding and
        permissions, then retry. Your conversation is preserved.
      </ErrorState>
    );
  if (!current)
    return errors.summary ? (
      <ErrorState
        title="Workspace unavailable"
        action={<Button onClick={() => void refresh()}>Retry inspector</Button>}
      >
        The workspace could not be read. Your conversation is preserved.
      </ErrorState>
    ) : (
      <Skeleton label="Loading workspace inspector" />
    );

  return (
    <section
      className="stack"
      style={{ minWidth: 0, overflowWrap: 'anywhere' }}
      aria-label={`${current.name} inspector`}
    >
      <div className="field-row">
        <h2>{current.name}</h2>
        <Button disabled={busy.summary} onClick={() => void refresh()}>
          Refresh inspector
        </Button>
      </div>
      {(errors.summary || current.status !== 'ready') && (
        <ErrorState
          title="Inspector is stale"
          action={
            <Button onClick={() => void refresh()}>Retry inspector</Button>
          }
        >
          This is the last confirmed workspace state. Refresh before relying on
          it.
        </ErrorState>
      )}
      {!errors.summary && Object.values(errors).some(Boolean) && (
        <p role="status">
          Any content still shown is the last confirmed workspace state and may
          be stale. Retry the failed read before relying on it.
        </p>
      )}
      <p>
        Read-only inspection ·{' '}
        {current.policy.execution_mode === 'docker'
          ? 'Docker execution'
          : 'Local execution'}{' '}
        · Approval: {current.policy.approval_mode} · Sandbox network:{' '}
        {current.policy.sandbox_network}
      </p>
      <p className="muted">
        Setup registers a folder. It does not install dependencies, start
        processes, allocate a worktree or change source files.
      </p>
      <p>
        {current.is_git
          ? `Branch ${current.branch || 'unavailable'} · ${current.dirty ? 'Changes present' : 'Working tree clean'}`
          : 'Folder is not a Git repository'}
      </p>
      {current.project_workspace_id !== current.execution_workspace_id && (
        <p>
          Using this conversation’s allocated worktree. Project and execution
          workspace remain separate.
        </p>
      )}

      <section className="stack" aria-label="Workspace changes">
        <h3>Changes ({current.changed_total})</h3>
        {current.diff_stats && (
          <p>
            +{current.diff_stats.additions} additions · −
            {current.diff_stats.deletions} deletions
          </p>
        )}
        {errors.changes && (
          <ErrorState
            title="Changes unavailable"
            action={
              <Button
                onClick={() => void loadChanges(current.snapshot_revision)}
              >
                Retry changes
              </Button>
            }
          >
            Refresh the inspector if this revision has changed.
          </ErrorState>
        )}
        {busy.changes && <Skeleton label="Loading changed files" />}
        {changed?.items.length === 0 && (
          <p>No changed files in this snapshot.</p>
        )}
        <ul>
          {changed?.items.map((item) => (
            <li key={item.path}>
              <Button variant="ghost" onClick={() => void loadFile(item.path)}>
                {item.path}
              </Button>
              <Button
                variant="ghost"
                aria-label={`Show diff ${item.path}`}
                onClick={() =>
                  void loadDiff(item.path, current.snapshot_revision)
                }
              >
                Diff
              </Button>
              <span>
                {' '}
                {item.status} · +{item.additions} / −{item.deletions}
              </span>
            </li>
          ))}
        </ul>
        {changed?.next_cursor && (
          <Button
            disabled={busy.changes}
            onClick={() =>
              void loadChanges(
                changed.snapshot_revision,
                changed.next_cursor ?? undefined,
              )
            }
          >
            More changed files
          </Button>
        )}
        {changed && (
          <Button
            variant="ghost"
            disabled={busy.changes}
            onClick={() => void loadChanges(current.snapshot_revision)}
          >
            First changed files
          </Button>
        )}
      </section>

      {diffPath && (
        <section className="stack" aria-label="Read-only file diff">
          <h3>Diff: {diffPath}</h3>
          {busy.diff && <Skeleton label="Loading diff" />}
          {errors.diff && (
            <ErrorState
              title="Diff unavailable"
              action={
                <Button
                  onClick={() =>
                    void loadDiff(diffPath, current.snapshot_revision)
                  }
                >
                  Retry diff
                </Button>
              }
            >
              The file or snapshot may have changed. Refresh to check again.
            </ErrorState>
          )}
          {diffPreview?.status === 'text' && (
            <>
              <p>
                Diff section at byte {diffOffset}
                {diffPreview.truncated ? ' · More content available' : ''}
              </p>
              <pre
                role="region"
                className="code-sample"
                style={{ minWidth: 0, maxWidth: '100%', overflow: 'auto' }}
                tabIndex={0}
                aria-label="Diff text"
              >
                {diffPreview.text || 'No textual diff in this view.'}
              </pre>
              {diffOffset > 0 && (
                <Button
                  onClick={() =>
                    void loadDiff(diffPath, current.snapshot_revision)
                  }
                >
                  First diff section
                </Button>
              )}
              {diffPreview.next_offset !== null && (
                <Button
                  disabled={busy.diff}
                  onClick={() =>
                    void loadDiff(
                      diffPath,
                      current.snapshot_revision,
                      diffPreview.next_offset ?? undefined,
                      diffPreview.revision,
                    )
                  }
                >
                  Next diff section
                </Button>
              )}
            </>
          )}
          {diffPreview && diffPreview.status !== 'text' && (
            <EmptyState
              title={`Diff ${diffPreview.status}`}
              action={
                <Button onClick={() => void loadFile(diffPath)}>
                  Read file instead
                </Button>
              }
            >
              This read-only diff cannot be shown. Repositories with custom Git
              read hooks require their existing Developer workflow.
            </EmptyState>
          )}
        </section>
      )}

      <section className="stack" aria-label="Agent change sets">
        <h3>Agent changes</h3>
        <Button
          disabled={busy.ledger}
          onClick={() => void loadLedger(current.snapshot_revision)}
        >
          {ledger ? 'First agent changes' : 'Load agent changes'}
        </Button>
        {errors.ledger && (
          <ErrorState title="Agent changes unavailable">
            Refresh the inspector and load the change sets again.
          </ErrorState>
        )}
        {busy.ledger && <Skeleton label="Loading agent changes" />}
        {ledger && <p>{ledger.total} recorded change sets</p>}
        <ul>
          {ledger?.items.map((change) => (
            <li key={change.id}>
              <Button
                variant="ghost"
                onClick={() =>
                  void loadLedgerFiles(change.id, ledger.snapshot_revision)
                }
              >
                {change.summary || change.id}
              </Button>
              <span>
                {' '}
                · {change.file_count} files ·{' '}
                {change.reverted
                  ? 'Reverted'
                  : change.reviewed
                    ? 'Reviewed'
                    : 'Unreviewed'}
              </span>
            </li>
          ))}
        </ul>
        {ledger?.next_cursor && (
          <Button
            disabled={busy.ledger}
            onClick={() =>
              void loadLedger(
                ledger.snapshot_revision,
                ledger.next_cursor ?? undefined,
              )
            }
          >
            More agent changes
          </Button>
        )}
        {changeSetId && (
          <section aria-label="Change set files">
            <h4>Files in change set {changeSetId}</h4>
            {errors.ledgerFiles && (
              <ErrorState
                title="Change set unavailable"
                action={
                  <Button
                    onClick={() =>
                      void loadLedgerFiles(
                        changeSetId,
                        current.snapshot_revision,
                      )
                    }
                  >
                    Retry change set
                  </Button>
                }
              >
                The snapshot may have changed.
              </ErrorState>
            )}
            {busy.ledgerFiles && <Skeleton label="Loading change set files" />}
            <ul>
              {ledgerFiles?.items.map((item) => (
                <li key={`${item.action}:${item.path}`}>
                  <Button
                    variant="ghost"
                    onClick={() => void loadFile(item.path)}
                  >
                    {item.path}
                  </Button>{' '}
                  · {item.action}
                </li>
              ))}
            </ul>
            {ledgerFiles?.next_cursor && (
              <Button
                disabled={busy.ledgerFiles}
                onClick={() =>
                  void loadLedgerFiles(
                    changeSetId,
                    ledgerFiles.snapshot_revision,
                    ledgerFiles.next_cursor ?? undefined,
                  )
                }
              >
                More files in change set
              </Button>
            )}
            {ledgerFiles && (
              <p>
                {ledgerFiles.total} files in this change set. Inspection does
                not apply or revert changes.
              </p>
            )}
          </section>
        )}
      </section>

      <section className="stack" aria-label="Workspace files">
        <h3>Files</h3>
        <nav aria-label="Workspace folder location">
          <Button variant="ghost" onClick={() => void loadDirectory('')}>
            Workspace root
          </Button>
          {path && (
            <>
              <span> / {path}</span>
              <Button
                onClick={() =>
                  void loadDirectory(path.split('/').slice(0, -1).join('/'))
                }
              >
                Parent folder
              </Button>
            </>
          )}
        </nav>
        {errors.directory && (
          <ErrorState
            title="Folder unavailable"
            action={
              <Button onClick={() => void loadDirectory(path)}>
                Retry folder
              </Button>
            }
          >
            The folder may have changed or access may have been removed.
          </ErrorState>
        )}
        {busy.directory && <Skeleton label="Loading folder" />}
        <ul>
          {listing?.items.map((item) => (
            <li key={item.relative_path}>
              <Button
                variant="ghost"
                aria-label={`${item.kind === 'directory' ? 'Open folder' : 'Preview file'} ${item.name}`}
                onClick={() =>
                  item.kind === 'directory'
                    ? void loadDirectory(item.relative_path)
                    : void loadFile(item.relative_path)
                }
              >
                {item.kind === 'directory' ? `${item.name}/` : item.name}
              </Button>
            </li>
          ))}
        </ul>
        {listing?.items.length === 0 && (
          <p>This folder has no available entries.</p>
        )}
        {listing?.next_cursor && (
          <Button
            disabled={busy.directory}
            onClick={() =>
              void loadDirectory(
                path,
                listing.next_cursor ?? undefined,
                listing.directory_revision,
              )
            }
          >
            More files in this folder
          </Button>
        )}
        {listing && (
          <p className="muted">Excluded: {listing.excluded.join(', ')}.</p>
        )}
      </section>

      <section className="stack" aria-label="Read-only file preview">
        <h3>File preview{filePath ? `: ${filePath}` : ''}</h3>
        {errors.file && (
          <ErrorState
            title="File unavailable"
            action={
              <Button onClick={() => void loadFile(filePath)}>
                Retry file
              </Button>
            }
          >
            The file could not be read. Select it again to check its current
            revision.
          </ErrorState>
        )}
        {busy.file && <Skeleton label="Loading file preview" />}
        {!preview && !busy.file && <p>Select a file to read it here.</p>}
        {preview && preview.status !== 'text' && (
          <EmptyState
            title={`File ${preview.status}`}
            action={
              <Button onClick={() => void loadFile(filePath)}>
                Retry file
              </Button>
            }
          >
            A text preview is unavailable for this file.
          </EmptyState>
        )}
        {preview?.status === 'text' && (
          <>
            <p>
              {preview.size_bytes} bytes · Section at byte {fileOffset}
            </p>
            <pre
              role="region"
              className="code-sample"
              style={{ minWidth: 0, maxWidth: '100%', overflow: 'auto' }}
              tabIndex={0}
              aria-label="File text"
            >
              {preview.text}
            </pre>
            {fileOffsets.length > 0 && (
              <Button
                disabled={busy.file}
                onClick={() => {
                  const previous = fileOffsets[fileOffsets.length - 1];
                  void loadFile(filePath, previous, preview.revision, true);
                }}
              >
                Previous file section
              </Button>
            )}
            {fileOffset > 0 && (
              <Button
                disabled={busy.file}
                onClick={() => void loadFile(filePath, 0, preview.revision)}
              >
                First file section
              </Button>
            )}
            {preview.next_offset !== null && (
              <Button
                disabled={busy.file}
                onClick={() =>
                  void loadFile(
                    filePath,
                    preview.next_offset ?? undefined,
                    preview.revision,
                  )
                }
              >
                Next file section
              </Button>
            )}
          </>
        )}
      </section>

      <section aria-label="Workspace test status">
        <h3>Detected checks</h3>
        {current.commands.length ? (
          <ul>
            {current.commands.map((command) => (
              <li key={`${command.kind}:${command.label}`}>
                {command.label} · Not run
              </li>
            ))}
          </ul>
        ) : (
          <p>No checks detected.</p>
        )}
        <p className="muted">
          Running checks is unavailable in this read-only inspector.
        </p>
      </section>
      <section aria-label="Workspace process status">
        <h3>Managed processes</h3>
        {current.processes.length ? (
          <ul>
            {current.processes.map((process) => (
              <li key={process.pid}>
                PID {process.pid} · {process.status}
              </li>
            ))}
          </ul>
        ) : (
          <p>No managed processes reported.</p>
        )}
      </section>
      {current.todos.length > 0 && (
        <section aria-label="Workspace task progress">
          <h3>Task progress</h3>
          <ul>
            {current.todos.map((todo) => (
              <li key={todo.id}>
                {todo.label} · {todo.status.replaceAll('_', ' ')}
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}
