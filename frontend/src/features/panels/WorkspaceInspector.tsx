import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import * as TabsPrimitive from '@radix-ui/react-tabs';
import * as Popover from '@radix-ui/react-popover';
import {
  ArrowLeft,
  ChevronRight,
  Columns2,
  File as FileIcon,
  Folder,
  FolderGit2,
  GitBranch,
  Info,
  Pencil,
  RefreshCw,
  Rows2,
  Sparkles,
} from 'lucide-react';
import type {
  WorkspaceInspector as Inspector,
  WorkspaceChanges,
  WorkspaceDirectory,
  WorkspaceFile,
  WorkspaceDiff,
  WorkspaceChangeSetPage,
  WorkspaceChangeSetFiles,
} from '../../api/types';
type WorkspaceChangeSet = WorkspaceChangeSetPage['items'][number];
import { humanizeToken } from '../../ui/format';
import {
  Button,
  Disclosure,
  IconButton,
  Input,
  Menu,
  Segmented,
  Skeleton,
  StatusDot,
  type Tone,
} from '../../ui/primitives';
import WorkspaceFileEditor, {
  type WorkspaceFileEditorProps,
} from './WorkspaceFileEditor';
import {
  WorkspaceEditSession,
  type WorkspaceEditScope,
  type WorkspaceEditScopeState,
} from './workspace-edit-sessions';
import CodeView from '../developer/CodeView';
import DiffView, { type DiffMode } from '../developer/DiffView';
import { parseTracking } from '../developer/git-status';
import {
  suggestCommit,
  suggestPullRequest,
  type Suggestion,
} from '../developer/commit-suggestion';
import type { DeveloperRepositorySnapshot } from '../developer/DeveloperRepositoryPanel';
import type { WorkspaceProcessInfo } from './WorkspaceProcesses';

const emptyEditScope: WorkspaceEditScopeState = {
  session: null,
  paths: [],
  open: false,
  capacity: false,
  accessible: false,
};
const noEditSubscription = () => () => {};
const noEditSnapshot = () => emptyEditScope;
const DIFF_MODE_KEY = 'row-bot.diff-mode.v1';
/** Agent change sets whose files load with the Changes tab. */
const EAGER_CHANGE_SETS = 10;

export type InspectorTab = 'changes' | 'files' | 'run' | 'git';
export type InspectorCheck = { label: string; kind: string; command: string };
export type RunContext = { checks: InspectorCheck[] };
export type GitContext = {
  /** The inspector's snapshot; the Git tab re-reads when it changes. */
  revision: string;
  isGit: boolean;
  branch: string;
  changedFiles: { path: string; status: string }[];
  commitSuggestion: Suggestion | null;
  pullRequestSuggestion: Suggestion | null;
};

export type WorkspaceInspectorProps = {
  onUndo?: (changeSetId: string, summary: string) => void;
  /** Hands a request to the conversation (true when it was taken). */
  onAsk?: (text: string) => boolean;
  resourceId: string;
  resourceRevision: string;
  refreshToken?: number;
  visible: boolean;
  editableFile?: WorkspaceFileEditorProps['load'];
  saveFile?: WorkspaceFileEditorProps['save'];
  editSessions?: WorkspaceEditScope;
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
  /** Git state from the repository owner (ahead/behind, remote). */
  repository?: DeveloperRepositorySnapshot | null;
  /** Processes this conversation started here, for the checks status. */
  processes?: readonly WorkspaceProcessInfo[];
  renderRun?: (context: RunContext) => ReactNode;
  renderGit?: (context: GitContext) => ReactNode;
  /** A change set's Undo review, shown in Changes. */
  undo?: ReactNode;
  /** Sandbox changes waiting to be imported, shown in Changes. */
  imports?: ReactNode;
};

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

const STATUS_WORDS: Record<string, string> = {
  M: 'Modified',
  A: 'Added',
  D: 'Deleted',
  R: 'Renamed',
  C: 'Copied',
  U: 'Conflict',
  '??': 'New',
};
function statusWord(status: string) {
  const code = status.trim();
  return STATUS_WORDS[code] ?? STATUS_WORDS[code.charAt(0)] ?? 'Changed';
}
function statusLetter(status: string) {
  const code = status.trim();
  return code === '??' ? 'U' : code.charAt(0) || '•';
}
function splitPath(path: string) {
  const index = path.lastIndexOf('/');
  return index < 0
    ? { folder: '', name: path }
    : { folder: path.slice(0, index + 1), name: path.slice(index + 1) };
}
function readDiffMode(): DiffMode {
  try {
    return window.localStorage.getItem(DIFF_MODE_KEY) === 'split'
      ? 'split'
      : 'unified';
  } catch {
    return 'unified';
  }
}

type ChecksState = { tone: Tone; label: string; pulse: boolean };
/** One status for the detected checks from the processes that ran them. */
export function checksStatus(
  checks: InspectorCheck[],
  processes: readonly WorkspaceProcessInfo[],
): ChecksState {
  if (!checks.length)
    return { tone: 'neutral', label: 'No checks', pulse: false };
  const latest = checks.map((check) =>
    [...processes].reverse().find((item) => item.command === check.command),
  );
  if (latest.some((item) => item && !item.quiesced && item.state !== 'failed'))
    return { tone: 'info', label: 'Checks running', pulse: true };
  if (
    latest.some(
      (item) =>
        item &&
        (item.state === 'failed' ||
          (item.exit_code !== null && item.exit_code !== 0)),
    )
  )
    return { tone: 'danger', label: 'Checks failed', pulse: false };
  if (latest.every((item) => item && item.exit_code === 0))
    return { tone: 'success', label: 'Checks passed', pulse: false };
  if (latest.some((item) => item && item.exit_code === 0))
    return { tone: 'success', label: 'Some checks passed', pulse: false };
  return { tone: 'neutral', label: 'Checks not run', pulse: false };
}

const APPROVAL_WORDS: Record<string, string> = {
  block: 'Blocked',
  approve: 'Ask first',
  allow_all: 'Allowed',
};
const BOUNDARY_WORDS: Record<string, string> = {
  use_workspace_setup: 'Clone a repository through Add resource.',
  use_workspace_process_review:
    'Runs only as a reviewed command in the Run tab.',
  no_recoverable_repository_delete_owner:
    'Not offered here: a delete could not be undone.',
};

/**
 * The Developer inspector for a thread's code folder: a status strip and
 * Changes | Files | Run | Git. Reads are immutable pages owned here; changes
 * to the repository go through the reviewed owners passed in as slots.
 */
export function WorkspaceInspector(props: WorkspaceInspectorProps) {
  const callbacks = useRef(props);
  callbacks.current = props;
  const epoch = useRef(0);
  const requests = useRef<Record<string, AbortController>>({});
  const [owner, setOwner] = useState('');
  const [summary, setSummary] = useState<Inspector | null>(null);
  const [changed, setChanged] = useState<WorkspaceChanges | null>(null);
  const [folders, setFolders] = useState<Record<string, WorkspaceDirectory>>(
    {},
  );
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [filter, setFilter] = useState('');
  const [preview, setPreview] = useState<WorkspaceFile | null>(null);
  const [filePath, setFilePath] = useState('');
  const [fileOffsets, setFileOffsets] = useState<number[]>([]);
  const [fileOffset, setFileOffset] = useState(0);
  const [diffPreview, setDiffPreview] = useState<WorkspaceDiff | null>(null);
  const [diffPath, setDiffPath] = useState('');
  const [diffOffset, setDiffOffset] = useState(0);
  const [diffMode, setDiffModeState] = useState<DiffMode>(readDiffMode);
  const [ledger, setLedger] = useState<WorkspaceChangeSetPage | null>(null);
  const [ledgerFiles, setLedgerFiles] = useState<
    Record<string, WorkspaceChangeSetFiles>
  >({});
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, boolean>>({});
  const [accessChanged, setAccessChanged] = useState(false);
  const [notice, setNotice] = useState('');
  const [tab, setTab] = useState<{ owner: string; value: InspectorTab } | null>(
    null,
  );
  const [editPath, setEditPath] = useState('');
  const [editorOpen, setEditorOpen] = useState(false);
  const scopedEdit = useSyncExternalStore(
    props.editSessions?.subscribe ?? noEditSubscription,
    props.editSessions?.getSnapshot ?? noEditSnapshot,
  );
  const localEdit = useMemo(
    () =>
      editPath && props.resourceId
        ? new WorkspaceEditSession(editPath, {
            load: (...args) => callbacks.current.editableFile!(...args),
            save: (...args) => callbacks.current.saveFile!(...args),
          })
        : null,
    [editPath, props.resourceId],
  );
  useEffect(() => () => localEdit?.dispose(), [localEdit]);
  const selectedEditPath = props.editSessions
    ? (scopedEdit.session?.path ?? '')
    : editPath;
  const editSession = props.editSessions ? scopedEdit.session : localEdit;
  const editOpen = props.editSessions ? scopedEdit.open : editorOpen;

  function cancelQueries() {
    epoch.current += 1;
    Object.values(requests.current).forEach((request) => request?.abort());
    requests.current = {};
  }

  async function query<T>(
    lane: string,
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
      // Later pages add to the list; a new snapshot starts it again.
      (result) =>
        setChanged((value) =>
          cursor && value?.snapshot_revision === result.snapshot_revision
            ? {
                ...result,
                items: [
                  ...value.items,
                  ...result.items.filter(
                    (item) =>
                      !value.items.some((known) => known.path === item.path),
                  ),
                ],
              }
            : result,
        ),
    );
  }

  function loadFolder(path: string, cursor?: string, revision?: string) {
    return query(
      `directory:${path}`,
      (signal) => callbacks.current.directory(path, cursor, revision, signal),
      (result) =>
        setFolders((value) => ({
          ...value,
          [path]:
            cursor && value[path]
              ? {
                  ...result,
                  items: [
                    ...value[path].items,
                    ...result.items.filter(
                      (item) =>
                        !value[path].items.some(
                          (known) => known.relative_path === item.relative_path,
                        ),
                    ),
                  ],
                }
              : result,
        })),
    );
  }

  function toggleFolder(path: string) {
    const open = expanded.has(path);
    setExpanded((value) => {
      const next = new Set(value);
      if (open) next.delete(path);
      else next.add(path);
      return next;
    });
    if (!open && !folders[path]) void loadFolder(path);
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
    setFolders({});
    setExpanded(new Set());
    setChanged(null);
    setPreview(null);
    setFilePath('');
    setFileOffsets([]);
    setFileOffset(0);
    setDiffPreview(null);
    setDiffPath('');
    setDiffOffset(0);
    setLedger(null);
    setLedgerFiles({});
  }

  function loadLedgerFiles(
    identifier: string,
    snapshotRevision: string,
    cursor?: string,
  ) {
    return query(
      `ledgerFiles:${identifier}`,
      (signal) =>
        callbacks.current.changeSetFiles(
          identifier,
          snapshotRevision,
          cursor,
          signal,
        ),
      (result) =>
        setLedgerFiles((value) => ({
          ...value,
          [identifier]:
            cursor && value[identifier]
              ? {
                  ...result,
                  items: [...value[identifier].items, ...result.items],
                }
              : result,
        })),
    );
  }

  function loadLedger(snapshotRevision: string, cursor?: string) {
    return query(
      'ledger',
      (signal) =>
        callbacks.current.changeSets(snapshotRevision, cursor, signal),
      (result) => {
        setLedger((value) =>
          cursor && value
            ? { ...result, items: [...value.items, ...result.items] }
            : result,
        );
        // The newest agent change sets open with their files; the rest
        // start folded and read their files when opened.
        const live = result.items.filter((item) => !item.reverted);
        const eager = live.slice(0, cursor ? 0 : EAGER_CHANGE_SETS);
        setCollapsed((value) => {
          const next = new Set(value);
          live
            .filter((item) => !eager.includes(item))
            .forEach((item) => next.add(item.id));
          return next;
        });
        eager.forEach(
          (item) => void loadLedgerFiles(item.id, result.snapshot_revision),
        );
      },
    );
  }

  function refresh(force = true) {
    setNotice('');
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
        setTab((value) =>
          value?.owner === callbacks.current.resourceId
            ? value
            : {
                owner: callbacks.current.resourceId,
                value:
                  result.is_git && result.changed_total > 0
                    ? 'changes'
                    : 'files',
              },
        );
        void loadChanges(result.snapshot_revision);
        void loadFolder('');
        void loadLedger(result.snapshot_revision);
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

  function setDiffMode(mode: DiffMode) {
    setDiffModeState(mode);
    try {
      window.localStorage.setItem(DIFF_MODE_KEY, mode);
    } catch {
      /* The choice still applies for this session. */
    }
  }

  useEffect(() => {
    // Opening (or reopening) reads the folder as it is now: the agent, an
    // editor or Git may have changed it while no panel was watching.
    if (props.visible) void refresh(true);
    else cancelQueries();
    return cancelQueries;
    // Callback identity changes do not create another subscription or Git scan.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    props.resourceId,
    props.resourceRevision,
    props.visible,
    props.refreshToken,
  ]);

  const current = owner === props.resourceId ? summary : null;
  const checks = useMemo<InspectorCheck[]>(
    () =>
      (current?.commands ?? []).map((command) => ({
        label: command.label,
        kind: command.kind,
        command: command.command?.trim() || command.label,
      })),
    [current?.commands],
  );
  const changedItems = changed?.items ?? [];
  const setPaths = useMemo(() => {
    const paths: Record<string, string[]> = {};
    for (const [id, page] of Object.entries(ledgerFiles))
      paths[id] = page.items.map((item) => item.path);
    return paths;
  }, [ledgerFiles]);
  const suggestionSets = useMemo(
    () =>
      (ledger?.items ?? []).map((item) => ({
        summary: item.summary,
        reverted: item.reverted,
        paths: setPaths[item.id],
      })),
    [ledger, setPaths],
  );
  const gitContext: GitContext = useMemo(
    () => ({
      revision: current?.snapshot_revision ?? '',
      isGit: !!current?.is_git,
      branch: current?.branch ?? '',
      changedFiles: changedItems.map((item) => ({
        path: item.path,
        status: item.status,
      })),
      commitSuggestion: suggestCommit(changedItems, suggestionSets),
      pullRequestSuggestion: suggestPullRequest(
        changedItems,
        suggestionSets,
        current?.branch ?? '',
      ),
    }),
    // changedItems derives from `changed`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      current?.snapshot_revision,
      current?.is_git,
      current?.branch,
      changed,
      suggestionSets,
    ],
  );

  if (!props.visible) return null;
  if (accessChanged)
    return (
      <div className="dev-error-card" role="alert">
        <strong>Workspace access changed</strong>
        <p>
          This workspace is unavailable or access changed. Review its binding
          and permissions, then retry. Your conversation is preserved.
        </p>
        <Button onClick={() => void refresh()}>Retry inspector</Button>
      </div>
    );
  if (!current)
    return errors.summary ? (
      <div className="dev-error-card" role="alert">
        <strong>Workspace unavailable</strong>
        <p>The workspace could not be read. Your conversation is preserved.</p>
        <Button onClick={() => void refresh()}>Retry inspector</Button>
      </div>
    ) : (
      <Skeleton label="Loading workspace inspector" />
    );

  const activeTab = tab?.owner === props.resourceId ? tab.value : 'files';
  const chooseTab = (value: InspectorTab) =>
    setTab({ owner: props.resourceId, value });
  const repository = props.repository?.repository;
  const tracking = parseTracking(repository?.tracking_summary ?? '');
  const checkState = checksStatus(checks, props.processes ?? []);
  const stats = changedItems.reduce<Record<string, [number, number]>>(
    (map, item) => ({ ...map, [item.path]: [item.additions, item.deletions] }),
    {},
  );
  const sets = (ledger?.items ?? []).filter((item) => !item.reverted);
  const reverted = (ledger?.items ?? []).filter((item) => item.reverted);
  // In a repository, a changed file belongs to the newest agent change that
  // touched it; a change whose files are all committed or reverted since is
  // "earlier". A plain folder has no diff, so every change set stays listed.
  const complete = !!current.is_git && !!changed && !changed.next_cursor;
  const changedPaths = new Set(changedItems.map((item) => item.path));
  const assigned = new Set<string>();
  const groups = sets.map((set) => {
    const files = ledgerFiles[set.id];
    const rows = (files?.items ?? []).filter(
      (item) =>
        (!complete || changedPaths.has(item.path)) && !assigned.has(item.path),
    );
    rows.forEach((item) => assigned.add(item.path));
    return { set, files, rows };
  });
  const liveGroups = groups.filter(
    (group) => !complete || !group.files || group.rows.length > 0,
  );
  const earlierGroups = groups.filter(
    (group) => complete && group.files && group.rows.length === 0,
  );
  const otherChanges = changedItems.filter(
    (item) =>
      !assigned.has(item.path) &&
      (complete || !Object.values(setPaths).flat().includes(item.path)),
  );
  const policy = current.policy;
  const stale =
    errors.summary ||
    current.status !== 'ready' ||
    Object.entries(errors).some(([, failed]) => failed);

  // The panel can undo only sandbox imports (it keeps their originals); the
  // agent reverts its own edits, so those are asked of it in the chat.
  function undoActions(set: WorkspaceChangeSet) {
    const summary = set.summary || 'this change';
    if (set.undoable && props.onUndo)
      return [
        {
          label: `Undo ${set.summary || set.id}`,
          danger: true,
          onSelect: () => props.onUndo?.(set.id, set.summary || ''),
        },
      ];
    if (!props.onAsk) return [];
    return [
      {
        label: `Ask Row-Bot to undo ${set.summary || set.id}`,
        onSelect: () => {
          const taken = props.onAsk?.(
            `Undo your change “${summary}” in the code folder (change set ${set.id}) with developer_revert_agent_changes, then tell me which files were restored.`,
          );
          setNotice(
            taken
              ? 'Asked Row-Bot to undo it in the chat.'
              : 'The chat cannot take a message right now.',
          );
        },
      },
    ];
  }

  function fileRow(path: string, status: string, key = path) {
    const { folder, name } = splitPath(path);
    const [additions, deletions] = stats[path] ?? [0, 0];
    const selected = diffPath === path;
    return (
      <li key={key}>
        <button
          type="button"
          className="dev-file-row"
          aria-current={selected ? 'true' : undefined}
          aria-label={`Show changes in ${path}`}
          onClick={() => void loadDiff(path, current!.snapshot_revision)}
        >
          <span
            className="dev-status-letter"
            data-status={statusLetter(status)}
            title={statusWord(status)}
          >
            {statusLetter(status)}
          </span>
          <span className="dev-file-path">
            <span className="dev-file-name">{name}</span>
            {folder && <span className="dev-file-folder">{folder}</span>}
          </span>
          {(additions > 0 || deletions > 0) && (
            <span className="dev-stat">
              <span className="dev-stat-add">+{additions}</span>{' '}
              <span className="dev-stat-del">−{deletions}</span>
            </span>
          )}
        </button>
      </li>
    );
  }

  const changesTab = (
    <div className="dev-split" data-detail={diffPath ? 'true' : undefined}>
      <div className="dev-list">
        {!current.is_git && (
          <p className="dev-empty">
            This folder is not a Git repository, so there is no diff to show.
            Files and Run still work.
          </p>
        )}
        {current.is_git && (
          <div className="dev-list-header">
            <span>
              {current.changed_total === 0
                ? 'No changes'
                : `${current.changed_total} changed`}
            </span>
            {current.diff_stats &&
              (current.diff_stats.additions > 0 ||
                current.diff_stats.deletions > 0) && (
                <span className="dev-stat">
                  <span className="dev-stat-add">
                    +{current.diff_stats.additions}
                  </span>{' '}
                  <span className="dev-stat-del">
                    −{current.diff_stats.deletions}
                  </span>
                </span>
              )}
          </div>
        )}
        {errors.changes && (
          <div className="dev-error-card" role="alert">
            <strong>Changes unavailable</strong>
            <p>The changed files could not be read for this snapshot.</p>
            <Button onClick={() => void loadChanges(current.snapshot_revision)}>
              Retry
            </Button>
          </div>
        )}
        {busy.changes && !changed && <Skeleton label="Loading changed files" />}
        {liveGroups.map(({ set, files, rows }) => {
          const open = !collapsed.has(set.id);
          const additions = rows.reduce(
            (sum, item) => sum + (stats[item.path]?.[0] ?? 0),
            0,
          );
          const deletions = rows.reduce(
            (sum, item) => sum + (stats[item.path]?.[1] ?? 0),
            0,
          );
          const shown = files && complete ? rows.length : set.file_count;
          return (
            <section
              key={set.id}
              className="dev-group"
              aria-label={`Agent change: ${set.summary || set.id}`}
            >
              <div className="dev-group-header">
                <button
                  type="button"
                  className="dev-group-toggle"
                  aria-expanded={open}
                  onClick={() => {
                    setCollapsed((value) => {
                      const next = new Set(value);
                      if (open) next.add(set.id);
                      else next.delete(set.id);
                      return next;
                    });
                    if (!open && !files)
                      void loadLedgerFiles(set.id, current.snapshot_revision);
                  }}
                >
                  <ChevronRight size={13} className="dev-chevron" aria-hidden />
                  <Sparkles size={13} aria-hidden />
                  <span className="dev-group-title">
                    {set.summary || 'Agent change'}
                  </span>
                </button>
                <span className="dev-group-meta">
                  {shown} {shown === 1 ? 'file' : 'files'}
                  {(additions > 0 || deletions > 0) && (
                    <>
                      {' · '}
                      <span className="dev-stat-add">+{additions}</span>{' '}
                      <span className="dev-stat-del">−{deletions}</span>
                    </>
                  )}
                  {' · '}
                  {set.reviewed ? 'Reviewed' : 'Not reviewed'}
                </span>
                {undoActions(set).length > 0 && (
                  <Menu
                    label={`More actions for ${set.summary || 'agent change'}`}
                    iconOnly
                    actions={undoActions(set)}
                  >
                    <span aria-hidden>⋯</span>
                  </Menu>
                )}
              </div>
              {open && (
                <>
                  {errors[`ledgerFiles:${set.id}`] && (
                    <div className="dev-error-card" role="alert">
                      <strong>Change set unavailable</strong>
                      <p>The snapshot may have changed.</p>
                      <Button
                        onClick={() =>
                          void loadLedgerFiles(
                            set.id,
                            current.snapshot_revision,
                          )
                        }
                      >
                        Retry
                      </Button>
                    </div>
                  )}
                  {!files && busy[`ledgerFiles:${set.id}`] && (
                    <Skeleton label="Loading change set files" />
                  )}
                  <ul className="dev-file-list">
                    {rows.map((item) =>
                      fileRow(
                        item.path,
                        changedItems.find((change) => change.path === item.path)
                          ?.status ??
                          (item.action === 'created'
                            ? 'A'
                            : item.action === 'deleted'
                              ? 'D'
                              : 'M'),
                        `${set.id}:${item.action}:${item.path}`,
                      ),
                    )}
                  </ul>
                  {files?.next_cursor && (
                    <Button
                      variant="ghost"
                      disabled={busy[`ledgerFiles:${set.id}`]}
                      onClick={() =>
                        void loadLedgerFiles(
                          set.id,
                          files.snapshot_revision,
                          files.next_cursor ?? undefined,
                        )
                      }
                    >
                      More files in this change
                    </Button>
                  )}
                </>
              )}
            </section>
          );
        })}
        {current.is_git && otherChanges.length > 0 && (
          <section
            className="dev-group"
            aria-label={liveGroups.length ? 'Other changes' : 'Changed files'}
          >
            {liveGroups.length > 0 && (
              <div className="dev-group-header">
                <span className="dev-group-title">Other changes</span>
                <span className="dev-group-meta">
                  {otherChanges.length}{' '}
                  {otherChanges.length === 1 ? 'file' : 'files'}
                </span>
              </div>
            )}
            <ul className="dev-file-list">
              {otherChanges.map((item) => fileRow(item.path, item.status))}
            </ul>
          </section>
        )}
        {changed?.next_cursor && (
          <Button
            variant="ghost"
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
        {ledger?.next_cursor && (
          <Button
            variant="ghost"
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
        {errors.ledger && (
          <div className="dev-error-card" role="alert">
            <strong>Agent changes unavailable</strong>
            <p>The recorded agent changes could not be read.</p>
            <Button onClick={() => void loadLedger(current.snapshot_revision)}>
              Retry
            </Button>
          </div>
        )}
        {earlierGroups.length > 0 && (
          <Disclosure
            summary="Earlier agent changes"
            meta={String(earlierGroups.length)}
            className="dev-disclosure"
          >
            <p className="dev-muted-line">
              Their files are committed or back to how they were.
            </p>
            <ul className="dev-earlier" aria-label="Earlier agent changes">
              {earlierGroups.map(({ set }) => (
                <li key={set.id}>
                  <Sparkles size={13} aria-hidden />
                  <span className="dev-group-title">
                    {set.summary || 'Agent change'}
                  </span>
                  <span className="dev-group-meta">
                    {set.file_count} {set.file_count === 1 ? 'file' : 'files'}
                  </span>
                  {undoActions(set).length > 0 && (
                    <Menu
                      label={`More actions for ${set.summary || 'agent change'}`}
                      iconOnly
                      actions={undoActions(set)}
                    >
                      <span aria-hidden>⋯</span>
                    </Menu>
                  )}
                </li>
              ))}
            </ul>
          </Disclosure>
        )}
        {notice && (
          <p className="dev-git-status" role="status">
            {notice}
          </p>
        )}
        {reverted.length > 0 && (
          <p className="dev-muted-line">
            {reverted.length} undone{' '}
            {reverted.length === 1 ? 'change' : 'changes'} not shown.
          </p>
        )}
        {props.undo}
        {current.todos.length > 0 && (
          <Disclosure
            summary="Agent plan"
            meta={`${current.todos.filter((todo) => /done|complete/i.test(todo.status)).length}/${current.todos.length}`}
            className="dev-disclosure"
          >
            <ul className="dev-plan" aria-label="Workspace task progress">
              {current.todos.map((todo) => (
                <li key={todo.id}>
                  <StatusDot
                    tone={
                      /done|complete/i.test(todo.status)
                        ? 'success'
                        : /progress|running/i.test(todo.status)
                          ? 'info'
                          : 'neutral'
                    }
                    label={humanizeToken(todo.status)}
                  />
                  <span>{todo.label}</span>
                </li>
              ))}
            </ul>
          </Disclosure>
        )}
        {props.imports}
      </div>
      {diffPath && (
        <div className="dev-detail">
          <header className="dev-detail-header">
            <IconButton
              size="sm"
              className="dev-back"
              label="Back to changes"
              onClick={() => {
                setDiffPath('');
                setDiffPreview(null);
              }}
            >
              <ArrowLeft size={15} aria-hidden />
            </IconButton>
            <span className="dev-detail-path" title={diffPath}>
              <span className="dev-file-folder">
                {splitPath(diffPath).folder}
              </span>
              <span className="dev-file-name">{splitPath(diffPath).name}</span>
            </span>
            <Segmented
              size="sm"
              label="Diff layout"
              value={diffMode}
              onChange={setDiffMode}
              options={[
                {
                  value: 'unified',
                  label: 'Unified',
                  hideLabel: true,
                  icon: <Rows2 size={14} aria-hidden />,
                },
                {
                  value: 'split',
                  label: 'Split',
                  hideLabel: true,
                  icon: <Columns2 size={14} aria-hidden />,
                },
              ]}
            />
            <IconButton
              size="sm"
              label={`Open ${splitPath(diffPath).name} in Files`}
              onClick={() => {
                chooseTab('files');
                void loadFile(diffPath);
              }}
            >
              <FileIcon size={15} aria-hidden />
            </IconButton>
          </header>
          {busy.diff && !diffPreview && <Skeleton label="Loading diff" />}
          {errors.diff && (
            <div className="dev-error-card" role="alert">
              <strong>Diff unavailable</strong>
              <p>The file or snapshot may have changed since it was listed.</p>
              <Button
                onClick={() =>
                  void loadDiff(diffPath, current.snapshot_revision)
                }
              >
                Retry
              </Button>
            </div>
          )}
          {diffPreview?.status === 'text' && (
            <>
              <div
                className="dev-diff-scroll"
                role="region"
                aria-label="Diff text"
                tabIndex={0}
              >
                <DiffView
                  path={diffPath}
                  text={diffPreview.text}
                  mode={diffMode}
                />
              </div>
              {(diffOffset > 0 || diffPreview.next_offset != null) && (
                <div className="dev-pager">
                  {diffOffset > 0 && (
                    <Button
                      variant="ghost"
                      onClick={() =>
                        void loadDiff(diffPath, current.snapshot_revision)
                      }
                    >
                      First diff section
                    </Button>
                  )}
                  {diffPreview.next_offset != null && (
                    <Button
                      variant="ghost"
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
                </div>
              )}
            </>
          )}
          {diffPreview && diffPreview.status !== 'text' && (
            <div className="dev-error-card" role="status">
              <strong>
                {diffPreview.status === 'missing'
                  ? 'This file is gone'
                  : diffPreview.status === 'stale'
                    ? 'This file changed'
                    : 'No diff to show'}
              </strong>
              <p>
                {diffPreview.status === 'unavailable'
                  ? 'Repositories with custom Git read hooks show files, not diffs.'
                  : 'Read the file as it is now instead.'}
              </p>
              <Button
                onClick={() => {
                  chooseTab('files');
                  void loadFile(diffPath);
                }}
              >
                Read file instead
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );

  const query_ = filter.trim().toLowerCase();
  const filtered = query_
    ? Object.values(folders)
        .flatMap((listing) => listing.items)
        .filter((item) => item.relative_path.toLowerCase().includes(query_))
        .sort((a, b) => a.relative_path.localeCompare(b.relative_path))
    : [];
  function tree(path: string, depth: number): ReactNode {
    const listing = folders[path];
    if (!listing)
      return busy[`directory:${path}`] ? (
        <li className="dev-tree-note" style={{ paddingLeft: 12 + depth * 14 }}>
          Loading…
        </li>
      ) : null;
    return (
      <>
        {listing.items.map((item) => {
          const open = expanded.has(item.relative_path);
          const folder = item.kind === 'directory';
          return (
            <li key={item.relative_path}>
              <button
                type="button"
                className="dev-tree-row"
                style={{ paddingLeft: 8 + depth * 14 }}
                aria-expanded={folder ? open : undefined}
                aria-current={
                  !folder && filePath === item.relative_path
                    ? 'true'
                    : undefined
                }
                aria-label={`${folder ? 'Open folder' : 'Preview file'} ${item.name}`}
                onClick={() =>
                  folder
                    ? toggleFolder(item.relative_path)
                    : void loadFile(item.relative_path)
                }
              >
                {folder ? (
                  <ChevronRight
                    size={13}
                    className="dev-chevron"
                    data-open={open || undefined}
                    aria-hidden
                  />
                ) : (
                  <span className="dev-tree-spacer" aria-hidden />
                )}
                {folder ? (
                  <Folder size={14} aria-hidden />
                ) : (
                  <FileIcon size={14} aria-hidden />
                )}
                <span className="dev-tree-name">{item.name}</span>
                {!folder && stats[item.relative_path] && (
                  <span className="dev-status-dot" title="Changed" />
                )}
              </button>
              {folder && open && (
                <ul className="dev-tree">
                  {tree(item.relative_path, depth + 1)}
                </ul>
              )}
            </li>
          );
        })}
        {listing.next_cursor && (
          <li>
            <Button
              variant="ghost"
              className="dev-tree-more"
              disabled={busy[`directory:${path}`]}
              onClick={() =>
                void loadFolder(
                  path,
                  listing.next_cursor ?? undefined,
                  listing.directory_revision,
                )
              }
            >
              More files in this folder
            </Button>
          </li>
        )}
      </>
    );
  }
  const fileChanged = !!filePath && !!stats[filePath];
  const filesTab = (
    <div
      className="dev-split"
      data-detail={filePath || selectedEditPath ? 'true' : undefined}
    >
      <div className="dev-list">
        <Input
          type="search"
          aria-label="Filter files"
          placeholder="Filter loaded files"
          className="dev-filter"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        {scopedEdit.paths.length > 0 && (
          <div className="dev-resume">
            {scopedEdit.paths.map((name) => (
              <Button
                key={name}
                variant="ghost"
                onClick={() => props.editSessions!.open(name)}
              >
                <Pencil size={13} aria-hidden /> Resume edit: {name}
              </Button>
            ))}
          </div>
        )}
        {errors['directory:'] && (
          <div className="dev-error-card" role="alert">
            <strong>Folder unavailable</strong>
            <p>The folder may have changed or access may have been removed.</p>
            <Button onClick={() => void loadFolder('')}>Retry</Button>
          </div>
        )}
        <nav aria-label="Workspace files">
          {query_ ? (
            <ul className="dev-tree">
              {filtered.map((item) => (
                <li key={item.relative_path}>
                  <button
                    type="button"
                    className="dev-tree-row"
                    aria-label={`${item.kind === 'directory' ? 'Open folder' : 'Preview file'} ${item.name}`}
                    onClick={() => {
                      if (item.kind === 'directory') {
                        setFilter('');
                        if (!expanded.has(item.relative_path))
                          toggleFolder(item.relative_path);
                      } else void loadFile(item.relative_path);
                    }}
                  >
                    {item.kind === 'directory' ? (
                      <Folder size={14} aria-hidden />
                    ) : (
                      <FileIcon size={14} aria-hidden />
                    )}
                    <span className="dev-tree-name">{item.relative_path}</span>
                  </button>
                </li>
              ))}
              {!filtered.length && (
                <li className="dev-tree-note">
                  No loaded file matches. Open folders to search inside them.
                </li>
              )}
            </ul>
          ) : (
            <ul className="dev-tree">{tree('', 0)}</ul>
          )}
        </nav>
        {folders['']?.items.length === 0 && (
          <p className="dev-empty">This folder has no available entries.</p>
        )}
        {folders[''] && folders[''].excluded.length > 0 && (
          <p className="dev-muted-line">
            Not shown: {folders[''].excluded.join(', ')}
          </p>
        )}
      </div>
      {(filePath || selectedEditPath) && (
        <div className="dev-detail">
          <header className="dev-detail-header">
            <IconButton
              size="sm"
              className="dev-back"
              label="Back to files"
              onClick={() => {
                setFilePath('');
                setPreview(null);
              }}
            >
              <ArrowLeft size={15} aria-hidden />
            </IconButton>
            <span className="dev-detail-path" title={filePath}>
              <span className="dev-file-folder">
                {splitPath(filePath || selectedEditPath).folder}
              </span>
              <span className="dev-file-name">
                {splitPath(filePath || selectedEditPath).name}
              </span>
            </span>
            {preview?.status === 'text' && preview.size_bytes != null && (
              <span className="dev-muted-line">
                {preview.size_bytes.toLocaleString()} bytes
              </span>
            )}
            {fileChanged && (
              <Button
                variant="ghost"
                onClick={() => {
                  chooseTab('changes');
                  void loadDiff(filePath, current.snapshot_revision);
                }}
              >
                Show changes
              </Button>
            )}
            {props.editableFile && props.saveFile && filePath && (
              <Button
                onClick={() => {
                  if (props.editSessions) props.editSessions.open(filePath);
                  else {
                    setEditPath(filePath);
                    setEditorOpen(true);
                  }
                }}
              >
                {selectedEditPath === filePath
                  ? `Resume edit: ${filePath}`
                  : 'Edit file'}
              </Button>
            )}
          </header>
          {scopedEdit.capacity && (
            <p role="alert" className="dev-muted-line">
              The editor limit is full. Save or discard a draft before opening
              another file; uncertain saves are kept until resolved.
            </p>
          )}
          {selectedEditPath &&
            editSession &&
            props.editableFile &&
            props.saveFile && (
              <WorkspaceFileEditor
                key={`${props.resourceId}:${selectedEditPath}`}
                path={selectedEditPath}
                session={editSession}
                visible={props.visible && editOpen}
                load={props.editableFile}
                save={props.saveFile}
                close={() =>
                  props.editSessions
                    ? props.editSessions.close()
                    : setEditorOpen(false)
                }
                discard={() => {
                  if (props.editSessions) {
                    props.editSessions.discard();
                    return;
                  }
                  setEditorOpen(false);
                  setEditPath('');
                }}
              />
            )}
          {errors.file && (
            <div className="dev-error-card" role="alert">
              <strong>File unavailable</strong>
              <p>The file could not be read. It may have moved or changed.</p>
              <Button onClick={() => void loadFile(filePath)}>Retry</Button>
            </div>
          )}
          {busy.file && !preview && <Skeleton label="Loading file preview" />}
          {preview && preview.status !== 'text' && (
            <div className="dev-error-card" role="status">
              <strong>
                {preview.status === 'binary'
                  ? 'Binary file'
                  : preview.status === 'missing'
                    ? 'This file is gone'
                    : preview.status === 'stale'
                      ? 'This file changed'
                      : 'No preview'}
              </strong>
              <p>A text preview is unavailable for this file.</p>
              <Button onClick={() => void loadFile(filePath)}>Retry</Button>
            </div>
          )}
          {preview?.status === 'text' && (
            <>
              <div className="dev-code-scroll">
                <CodeView
                  path={filePath}
                  text={preview.text ?? ''}
                  firstLine={fileOffset === 0 ? 1 : 0}
                />
              </div>
              {(fileOffset > 0 || preview.next_offset != null) && (
                <div className="dev-pager">
                  {fileOffsets.length > 0 && (
                    <Button
                      variant="ghost"
                      disabled={busy.file}
                      onClick={() =>
                        void loadFile(
                          filePath,
                          fileOffsets[fileOffsets.length - 1],
                          preview.revision,
                          true,
                        )
                      }
                    >
                      Previous file section
                    </Button>
                  )}
                  {fileOffset > 0 && (
                    <Button
                      variant="ghost"
                      disabled={busy.file}
                      onClick={() =>
                        void loadFile(filePath, 0, preview.revision)
                      }
                    >
                      First file section
                    </Button>
                  )}
                  {preview.next_offset != null && (
                    <Button
                      variant="ghost"
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
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );

  return (
    <section className="dev-inspector" aria-label={`${current.name} inspector`}>
      <div className="dev-strip" role="group" aria-label="Repository status">
        <span className="dev-strip-repo" title={current.name}>
          <FolderGit2 size={14} aria-hidden />
          <span>{current.name}</span>
        </span>
        {current.is_git ? (
          <>
            <button
              type="button"
              className="dev-chip"
              aria-label={`Branch ${current.branch || 'detached'}. Open Git`}
              onClick={() => chooseTab('git')}
            >
              <GitBranch size={13} aria-hidden />
              <span>{current.branch || 'Detached'}</span>
            </button>
            {(tracking.ahead > 0 || tracking.behind > 0) && (
              <span
                className="dev-strip-item"
                title={`${tracking.ahead} ahead of, ${tracking.behind} behind ${tracking.upstream}`}
              >
                <span aria-hidden>
                  ↑{tracking.ahead} ↓{tracking.behind}
                </span>
                <span className="visually-hidden">
                  {tracking.ahead} ahead, {tracking.behind} behind{' '}
                  {tracking.upstream}
                </span>
              </span>
            )}
            <button
              type="button"
              className="dev-chip"
              onClick={() => chooseTab('changes')}
            >
              {current.changed_total === 0
                ? 'Clean'
                : `${current.changed_total} changed`}
            </button>
          </>
        ) : (
          <span className="dev-strip-item">Folder is not a Git repository</span>
        )}
        {checks.length > 0 && (
          <button
            type="button"
            className="dev-chip"
            onClick={() => chooseTab('run')}
          >
            <StatusDot
              tone={checkState.tone}
              pulse={checkState.pulse}
              label={checkState.label}
            />
            <span aria-hidden>Checks</span>
          </button>
        )}
        <span
          className="dev-strip-item"
          title={`Network in the sandbox: ${humanizeToken(policy.sandbox_network)}`}
        >
          {policy.execution_mode === 'docker' ? 'Docker sandbox' : 'Local'}
          {policy.execution_mode === 'docker'
            ? ` · network ${humanizeToken(policy.sandbox_network).toLowerCase()}`
            : ''}
        </span>
        <span className="dev-strip-actions">
          <IconButton
            size="sm"
            label="Refresh inspector"
            disabled={busy.summary}
            onClick={() => void refresh()}
          >
            <RefreshCw size={14} aria-hidden />
          </IconButton>
          <Popover.Root>
            <Popover.Trigger asChild>
              <IconButton size="sm" label="Safety boundaries">
                <Info size={14} aria-hidden />
              </IconButton>
            </Popover.Trigger>
            <Popover.Portal>
              <Popover.Content
                className="popover dev-safety"
                aria-label="Safety boundaries"
                align="end"
                sideOffset={8}
                collisionPadding={12}
              >
                <p className="dev-safety-title">Safety boundaries</p>
                <dl>
                  <dt>Approvals</dt>
                  <dd>
                    {APPROVAL_WORDS[policy.approval_mode] ??
                      policy.approval_mode}
                  </dd>
                  <dt>Runs in</dt>
                  <dd>
                    {policy.execution_mode === 'docker'
                      ? 'Docker sandbox'
                      : 'This computer'}
                  </dd>
                  <dt>Sandbox network</dt>
                  <dd>{humanizeToken(policy.sandbox_network)}</dd>
                  {(
                    [
                      ['developer.repository.clone', 'Clone'],
                      ['developer.repository.install', 'Install'],
                      ['developer.repository.network', 'Network'],
                      ['developer.repository.delete', 'Delete'],
                    ] as const
                  ).map(([action, label]) => {
                    const code = props.repository?.availability[action]?.code;
                    return code ? (
                      <div key={action} className="dev-safety-row">
                        <dt>{label}</dt>
                        <dd>{BOUNDARY_WORDS[code] ?? humanizeToken(code)}</dd>
                      </div>
                    ) : null;
                  })}
                </dl>
                <p className="muted">
                  This inspector reads files; it never changes them. Commits,
                  branches and commands go through a review first, and pushing
                  or opening a pull request asks for confirmation.
                </p>
              </Popover.Content>
            </Popover.Portal>
          </Popover.Root>
        </span>
      </div>
      {stale && (
        <div className="dev-stale" role="status">
          <span>
            Some of this may be out of date. The last confirmed state is shown.
          </span>
          <Button variant="ghost" onClick={() => void refresh()}>
            Retry
          </Button>
        </div>
      )}
      {current.project_workspace_id !== current.execution_workspace_id && (
        <p className="dev-muted-line dev-worktree-note">
          Working in this conversation’s own worktree; the project folder is
          unchanged.
        </p>
      )}
      <TabsPrimitive.Root
        className="dev-tabs"
        value={activeTab}
        onValueChange={(value) => chooseTab(value as InspectorTab)}
      >
        <TabsPrimitive.List
          className="dev-tab-list"
          aria-label="Developer inspector"
        >
          <TabsPrimitive.Trigger className="dev-tab" value="changes">
            Changes
            {current.is_git && current.changed_total > 0 && (
              <span className="dev-tab-count">{current.changed_total}</span>
            )}
          </TabsPrimitive.Trigger>
          <TabsPrimitive.Trigger className="dev-tab" value="files">
            Files
          </TabsPrimitive.Trigger>
          <TabsPrimitive.Trigger className="dev-tab" value="run">
            Run
          </TabsPrimitive.Trigger>
          {props.renderGit && (
            <TabsPrimitive.Trigger className="dev-tab" value="git">
              Git
            </TabsPrimitive.Trigger>
          )}
        </TabsPrimitive.List>
        <TabsPrimitive.Content value="changes" className="dev-tab-panel">
          {changesTab}
        </TabsPrimitive.Content>
        <TabsPrimitive.Content value="files" className="dev-tab-panel">
          {filesTab}
        </TabsPrimitive.Content>
        <TabsPrimitive.Content
          value="run"
          className="dev-tab-panel"
          forceMount
          hidden={activeTab !== 'run'}
        >
          {props.renderRun ? (
            props.renderRun({ checks })
          ) : checks.length ? (
            <ul className="dev-checks" aria-label="Detected checks">
              {checks.map((check) => (
                <li key={`${check.kind}:${check.label}`}>
                  <StatusDot tone="neutral" label="Not run" />
                  <span className="dev-check-name">{check.label}</span>
                  <code className="dev-check-command">{check.command}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p className="dev-empty">No checks detected in this folder.</p>
          )}
          {current.processes.length > 0 && (
            <section
              className="dev-run-section dev-managed"
              aria-label="Workspace process status"
            >
              <h4>Other processes in this folder</h4>
              <ul className="dev-processes">
                {current.processes.map((process) => (
                  <li key={process.pid}>
                    <span className="dev-process-main">
                      <StatusDot
                        tone={process.status === 'running' ? 'info' : 'neutral'}
                        label={
                          process.status === 'running' ? 'Running' : 'Stopped'
                        }
                      />
                      <code>PID {process.pid}</code>
                      <span className="dev-process-state">
                        {process.status === 'running' ? 'Running' : 'Stopped'}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </TabsPrimitive.Content>
        {props.renderGit && (
          <TabsPrimitive.Content
            value="git"
            className="dev-tab-panel"
            forceMount
            hidden={activeTab !== 'git'}
          >
            {props.renderGit(gitContext)}
          </TabsPrimitive.Content>
        )}
      </TabsPrimitive.Root>
    </section>
  );
}
