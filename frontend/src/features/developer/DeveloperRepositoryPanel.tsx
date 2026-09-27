import { useEffect, useRef, useSyncExternalStore, type ReactNode } from 'react';
import {
  ArrowUpFromLine,
  GitBranch,
  GitCommitHorizontal,
  GitPullRequest,
  Sparkles,
} from 'lucide-react';
import { clientError } from '../../api/errors';
import { humanizeToken } from '../../ui/format';
import {
  Button,
  Disclosure,
  Field,
  IconButton,
  Input,
  Menu,
  Select,
  Skeleton,
  Toggle,
} from '../../ui/primitives';
import type { Suggestion } from './commit-suggestion';
import { parseTracking } from './git-status';

export type DeveloperRepositoryAction =
  | 'developer.repository.branch.create'
  | 'developer.repository.branch.switch'
  | 'developer.repository.commit'
  | 'developer.repository.push'
  | 'developer.repository.pull_request'
  | 'developer.repository.worktree.create'
  | 'developer.repository.worktree.preserve'
  | 'developer.repository.sandbox.configure'
  | 'developer.repository.sandbox.rebuild'
  | 'developer.repository.sandbox.cleanup';

function requiresConfirmation(action: DeveloperRepositoryAction) {
  return [
    'developer.repository.push',
    'developer.repository.pull_request',
    'developer.repository.sandbox.rebuild',
    'developer.repository.sandbox.cleanup',
  ].includes(action);
}

export type DeveloperRepositorySnapshot = {
  schema_version: 1;
  resource_id: string;
  conversation_id: string;
  binding_id: string;
  binding_revision: string;
  resource_revision: string;
  revision: string;
  workspace_name: string;
  trusted: boolean;
  repository: {
    state: 'ready' | 'plain_folder';
    is_git: boolean;
    is_root: boolean;
    branch: string;
    detached: boolean;
    dirty: boolean;
    remote_configured: boolean;
    tracking_summary: string;
    /** Local branches, most recent first (newer servers). */
    branches?: string[];
  };
  worktrees: {
    worktree_id: string;
    branch: string;
    status: string;
    cleanup_state: string;
    current: boolean;
    owned_by_conversation: boolean;
    source_dirty: boolean;
    seeded_current_changes: boolean;
    has_error: boolean;
  }[];
  sandbox: {
    execution_mode: 'local' | 'docker';
    network: 'off' | 'ask' | 'on';
    image: string;
    pending_imports: number;
    owned_processes: number;
    runtime_status: string;
  };
  availability: Record<string, { available: boolean; code: string | null }>;
};

export type DeveloperRepositoryReview = {
  schema_version: 1;
  action: DeveloperRepositoryAction;
  resource_id: string;
  conversation_id: string;
  binding_id: string;
  binding_revision: string;
  resource_revision: string;
  revision: string;
  policy_action: string;
  policy_decision: 'allow' | 'ask' | 'block';
  approval_required: boolean;
  disclosures: string[];
  action_digest: string;
  review_id?: string;
};

export type DeveloperRepositoryCommand = {
  command_id: string;
  type: DeveloperRepositoryAction;
  payload: Record<string, unknown>;
};

export type DeveloperRepositoryReceipt = {
  schema_version: 1;
  command_id: string;
  action: DeveloperRepositoryAction;
  resource_id: string;
  conversation_id: string;
  status: 'completed' | 'partial' | 'rejected';
  code: string | null;
  revision: string | null;
  worktree_id: string | null;
  external_url: string | null;
};

type Attempt = {
  command: DeveloperRepositoryCommand;
  review: DeveloperRepositoryReview;
};
type Drafts = {
  branch: string;
  commitMessage: string;
  commitPaths: string;
  pullTitle: string;
  pullBody: string;
  pullDraft: boolean;
  objective: string;
  preserveReason: string;
  executionMode: 'local' | 'docker';
  sandboxNetwork: 'off' | 'ask' | 'on';
  sandboxImage: string;
};
type State = {
  active: boolean;
  snapshot: DeveloperRepositorySnapshot | null;
  drafts: Drafts;
  reviewed: Attempt | null;
  pending: Attempt | null;
  reading: boolean;
  busy: boolean;
  error: string;
  message: string;
};

const blankDrafts = (): Drafts => ({
  branch: '',
  commitMessage: '',
  commitPaths: '',
  pullTitle: '',
  pullBody: '',
  pullDraft: true,
  objective: '',
  preserveReason: '',
  executionMode: 'local',
  sandboxNetwork: 'off',
  sandboxImage: 'row-bot-sandbox:latest',
});

/** Retained only for one authenticated resource/conversation binding. */
export function createDeveloperRepositorySession(scope: string) {
  let state: State = {
    active: true,
    snapshot: null,
    drafts: blankDrafts(),
    reviewed: null,
    pending: null,
    reading: false,
    busy: false,
    error: '',
    message: '',
  };
  let read: AbortController | null = null;
  const listeners = new Set<() => void>();
  const update = (patch: Partial<State>) => {
    if (!state.active) return;
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  };
  return {
    scope,
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    update,
    beginRead: () => {
      read?.abort();
      read = new AbortController();
      return read;
    },
    endRead: (current: AbortController) => {
      if (read === current) read = null;
    },
    hasRetained: () =>
      state.active && Boolean(state.reviewed || state.pending || state.busy),
    dispose: () => {
      read?.abort();
      read = null;
      state = {
        ...state,
        active: false,
        snapshot: null,
        drafts: blankDrafts(),
        reviewed: null,
        pending: null,
        reading: false,
        busy: false,
        error: '',
        message: '',
      };
      listeners.forEach((listener) => listener());
      listeners.clear();
    },
  };
}

export type DeveloperRepositorySession = ReturnType<
  typeof createDeveloperRepositorySession
>;
export type DeveloperRepositoryPanelProps = {
  scope: string;
  visible: boolean;
  session: DeveloperRepositorySession;
  load: (signal: AbortSignal) => Promise<DeveloperRepositorySnapshot>;
  review: (
    action: DeveloperRepositoryAction,
    payload: Record<string, unknown>,
    signal: AbortSignal,
  ) => Promise<DeveloperRepositoryReview>;
  execute: (
    command: DeveloperRepositoryCommand,
    review: DeveloperRepositoryReview,
  ) => Promise<DeveloperRepositoryReceipt>;
  /** Changed files the commit can include (from the inspector). */
  changedFiles?: { path: string; status: string }[];
  commitSuggestion?: Suggestion | null;
  pullRequestSuggestion?: Suggestion | null;
  /** Re-read the repository when this changes (the inspector refreshed). */
  revisionKey?: string;
  /** Called after a confirmed change so the inspector can re-read. */
  onChanged?: () => void;
  /** Extra Advanced sections (sandbox imports, custom tools). */
  advanced?: ReactNode;
};

function assertSnapshot(
  value: DeveloperRepositorySnapshot,
  scope: string,
): DeveloperRepositorySnapshot {
  if (
    value.schema_version !== 1 ||
    `${value.conversation_id}:${value.resource_id}:${value.binding_id}:${value.binding_revision}` !==
      scope ||
    value.worktrees.length > 32 ||
    Object.keys(value.availability).length > 20
  )
    throw new Error(
      'Developer repository response did not match this workspace.',
    );
  return value;
}

function paths(value: string) {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 50);
}

function capability(
  snapshot: DeveloperRepositorySnapshot,
  action: DeveloperRepositoryAction,
) {
  return (
    snapshot.availability[action] ?? {
      available: false,
      code: 'capability_unavailable',
    }
  );
}

function labelCode(value: string | null) {
  return String(value ?? 'unavailable').replaceAll('_', ' ');
}

const REASONS: Record<string, string> = {
  git_root_required: "Open the repository's top folder to use Git here.",
  clean_git_root_required: 'Commit or undo changes before switching branches.',
  dirty_git_root_required: 'Nothing to commit.',
  git_remote_required: 'No remote is set up for this repository.',
  worktree_exists: 'This conversation already has a worktree.',
  worktree_unavailable: 'This conversation has no worktree yet.',
  sandbox_busy_or_pending_import:
    'Finish running commands and sandbox imports first.',
  docker_sandbox_required: 'Only for the Docker sandbox.',
};

function reasonText(code: string | null) {
  return code ? (REASONS[code] ?? humanizeToken(code)) : '';
}

const DONE_WORDS: Partial<Record<DeveloperRepositoryAction, string>> = {
  'developer.repository.branch.create': 'Branch created.',
  'developer.repository.branch.switch': 'Switched branch.',
  'developer.repository.commit': 'Committed.',
  'developer.repository.push': 'Pushed.',
  'developer.repository.pull_request': 'Pull request opened.',
  'developer.repository.worktree.create': 'Worktree created.',
  'developer.repository.worktree.preserve': 'Worktree kept.',
  'developer.repository.sandbox.configure': 'Sandbox settings saved.',
  'developer.repository.sandbox.rebuild': 'Sandbox rebuilt.',
  'developer.repository.sandbox.cleanup': 'Sandbox cleaned up.',
};

const POLICY_WORDS: Record<string, string> = {
  allow: 'Allowed',
  ask: 'Asks first',
  block: 'Blocked',
};

export default function DeveloperRepositoryPanel(
  props: DeveloperRepositoryPanelProps,
) {
  const callbacks = useRef(props);
  callbacks.current = props;
  const { session } = props;
  const state = useSyncExternalStore(
    session.subscribe,
    session.getSnapshot,
    session.getSnapshot,
  );
  const inScope = props.scope === session.scope;
  const locked = !state.active || !inScope || state.reading || state.busy;

  const refresh = async (preserveMessage = false) => {
    if (!props.visible || !state.active || !inScope) return;
    const request = session.beginRead();
    session.update({
      reading: true,
      error: '',
      ...(preserveMessage ? {} : { message: '' }),
    });
    try {
      const result = assertSnapshot(
        await callbacks.current.load(request.signal),
        props.scope,
      );
      if (request.signal.aborted) return;
      session.update({
        snapshot: result,
        drafts: {
          ...session.getSnapshot().drafts,
          executionMode: result.sandbox.execution_mode,
          sandboxNetwork: result.sandbox.network,
          sandboxImage: result.sandbox.image,
        },
      });
    } catch (error) {
      if (!request.signal.aborted)
        session.update({ error: clientError(error).message });
    } finally {
      session.endRead(request);
      if (!request.signal.aborted) session.update({ reading: false });
    }
  };

  useEffect(() => {
    // A re-read after the inspector refreshes keeps the last outcome line.
    if (props.visible && inScope) void refresh(true);
    // The authenticated owner owns callback identity; the exact scope and
    // visibility transitions are the only automatic read triggers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.visible, props.scope, inScope, props.revisionKey]);

  const patchDrafts = (patch: Partial<Drafts>) =>
    session.update({ drafts: { ...session.getSnapshot().drafts, ...patch } });

  const prepare = async (
    action: DeveloperRepositoryAction,
    extra: Record<string, unknown> = {},
  ) => {
    const current = session.getSnapshot();
    if (
      locked ||
      current.reading ||
      current.busy ||
      !current.snapshot ||
      current.pending ||
      current.reviewed
    )
      return;
    const allowed = capability(current.snapshot, action);
    if (!allowed.available) {
      session.update({
        error: reasonText(allowed.code) || 'This action is unavailable.',
      });
      return;
    }
    const request = session.beginRead();
    session.update({ reviewed: null, error: '', message: '', reading: true });
    const payload = { revision: current.snapshot.revision, ...extra };
    let direct: Attempt | null = null;
    try {
      const review = await callbacks.current.review(
        action,
        payload,
        request.signal,
      );
      if (request.signal.aborted) return;
      if (
        review.action !== action ||
        review.resource_id !== current.snapshot.resource_id ||
        review.conversation_id !== current.snapshot.conversation_id ||
        review.binding_id !== current.snapshot.binding_id ||
        review.binding_revision !== current.snapshot.binding_revision ||
        review.resource_revision !== current.snapshot.resource_revision ||
        review.revision !== current.snapshot.revision
      )
        throw new Error('Developer repository review target did not match.');
      if (review.policy_decision === 'block') {
        session.update({
          error: 'The current repository policy blocks this action.',
        });
        return;
      }
      const attempt: Attempt = {
        review,
        command: { command_id: crypto.randomUUID(), type: action, payload },
      };
      session.update({ reviewed: attempt });
      if (!requiresConfirmation(action)) direct = attempt;
    } catch (error) {
      if (!request.signal.aborted)
        session.update({ error: clientError(error).message });
    } finally {
      session.endRead(request);
      if (!request.signal.aborted) session.update({ reading: false });
    }
    if (direct) await apply(false, direct);
  };

  const apply = async (recover = false, direct?: Attempt) => {
    const current = session.getSnapshot();
    if (locked && !direct) return;
    const attempt = direct ?? (recover ? current.pending : current.reviewed);
    if (!attempt || (!recover && attempt.review.policy_decision === 'block'))
      return;
    session.update({
      busy: true,
      pending: attempt,
      reviewed: attempt,
      error: '',
      message: '',
    });
    try {
      const receipt = await callbacks.current.execute(
        attempt.command,
        attempt.review,
      );
      if (
        receipt.command_id !== attempt.command.command_id ||
        receipt.action !== attempt.command.type ||
        receipt.resource_id !== attempt.review.resource_id ||
        receipt.conversation_id !== attempt.review.conversation_id
      ) {
        session.update({
          pending: attempt,
          error: 'Developer repository receipt target did not match.',
          message:
            'The original change is unconfirmed. Check the same command before doing anything else.',
        });
        return;
      }
      if (receipt.status === 'partial') {
        session.update({
          pending: attempt,
          message: `The original change is unconfirmed (${labelCode(receipt.code)}).`,
        });
      } else {
        session.update({
          pending: null,
          reviewed: null,
          message:
            receipt.status === 'completed'
              ? (DONE_WORDS[attempt.command.type] ?? 'Done.')
              : `The repository change was refused: ${reasonText(receipt.code) || labelCode(receipt.code)}.`,
        });
        if (receipt.status === 'completed') {
          await refresh(true);
          callbacks.current.onChanged?.();
        }
      }
    } catch (error) {
      session.update({
        pending: attempt,
        error: clientError(error).message,
        message:
          'The original change is unconfirmed. Check the same command before doing anything else.',
      });
    } finally {
      session.update({ busy: false });
    }
  };

  if (!state.active)
    return (
      <div className="dev-error-card" role="alert">
        <strong>Developer access ended</strong>
        <p>Sign in again to manage the repository.</p>
      </div>
    );
  if (!inScope)
    return (
      <div className="dev-error-card" role="alert">
        <strong>Developer workspace changed</strong>
        <p>Reopen the Git tab for the current workspace.</p>
      </div>
    );
  if (!state.snapshot && state.reading)
    return <Skeleton label="Reading Developer repository" />;
  if (!state.snapshot)
    return (
      <div className="dev-error-card" role="alert">
        <strong>Repository status unavailable</strong>
        <p>{state.error || 'The repository could not be read.'}</p>
        <Button onClick={() => void refresh()}>Retry</Button>
      </div>
    );

  const snapshot = state.snapshot;
  const repo = snapshot.repository;
  const available = (action: DeveloperRepositoryAction) =>
    capability(snapshot, action).available;
  const reason = (action: DeveloperRepositoryAction) =>
    reasonText(capability(snapshot, action).code);
  const changed = props.changedFiles ?? [];
  const selected = state.drafts.commitPaths
    ? paths(state.drafts.commitPaths)
    : null;
  const selectedCount = selected ? selected.length : changed.length;
  const branches = Array.from(
    new Set([repo.branch, ...(repo.branches ?? [])].filter(Boolean)),
  );
  const confirm =
    state.reviewed && requiresConfirmation(state.reviewed.command.type)
      ? state.reviewed
      : null;
  const disclosureWords: Record<string, string> = {
    'developer.repository.push': 'Push the current branch',
    'developer.repository.pull_request': 'Open a pull request',
    'developer.repository.sandbox.rebuild': 'Rebuild the sandbox',
    'developer.repository.sandbox.cleanup': 'Clean up the sandbox',
  };
  return (
    <section className="dev-git" aria-label="Developer repository controls">
      {state.error && (
        <div className="dev-error-card" role="alert">
          <strong>Repository action needs attention</strong>
          <p>{state.error}</p>
          <Button disabled={locked} onClick={() => void refresh()}>
            Retry
          </Button>
        </div>
      )}
      {state.message && (
        <p className="dev-git-status" role="status">
          {state.message}
        </p>
      )}
      {confirm && (
        <div
          className="dev-confirm"
          role="group"
          aria-label="Confirm repository change"
        >
          <strong>
            {disclosureWords[confirm.review.action] ??
              humanizeToken(confirm.review.action.split('.').at(-1))}
            ?
          </strong>
          {confirm.review.disclosures.map((disclosure) => (
            <p key={disclosure}>{disclosure}</p>
          ))}
          <p className="muted">
            Policy: {POLICY_WORDS[confirm.review.policy_decision] ?? 'Review'}
          </p>
          <div className="action-cluster">
            <Button
              disabled={locked || Boolean(state.pending)}
              onClick={() =>
                session.update({
                  reviewed: null,
                  message: 'Cancelled. No repository change was made.',
                })
              }
            >
              Keep current state
            </Button>
            <Button
              variant={
                confirm.review.action.startsWith(
                  'developer.repository.sandbox.',
                )
                  ? 'danger'
                  : 'primary'
              }
              disabled={
                locked ||
                Boolean(state.pending) ||
                confirm.review.policy_decision === 'block'
              }
              onClick={() => void apply()}
            >
              Confirm repository action
            </Button>
          </div>
        </div>
      )}
      {state.pending && (
        <div className="dev-confirm" role="group">
          <p>
            The last repository change is not confirmed. Check the same change
            before starting another one.
          </p>
          <Button disabled={locked} onClick={() => void apply(true)}>
            Check original repository change
          </Button>
        </div>
      )}
      {!repo.is_git ? (
        <p className="dev-empty">
          This folder is not a Git repository. Branches, commits and pull
          requests appear here once it is one.
        </p>
      ) : (
        <>
          <section className="dev-git-section" aria-label="Branch">
            <h4>Branch</h4>
            <div className="dev-git-row">
              <Menu
                label="Switch branch"
                className="dev-branch-menu"
                actions={branches.map((name) => ({
                  label: name,
                  selected: name === repo.branch,
                  disabled:
                    locked ||
                    name === repo.branch ||
                    !available('developer.repository.branch.switch'),
                  onSelect: () =>
                    void prepare('developer.repository.branch.switch', {
                      branch: name,
                    }),
                }))}
              >
                <GitBranch size={14} aria-hidden />
                <span>{repo.branch || 'Detached HEAD'}</span>
              </Menu>
              <span className="dev-git-meta">
                {repo.dirty ? 'Local changes' : 'Clean'}
                {repo.tracking_summary &&
                  parseTracking(repo.tracking_summary).upstream &&
                  ` · tracks ${parseTracking(repo.tracking_summary).upstream}`}
              </span>
            </div>
            {!available('developer.repository.branch.switch') && (
              <p className="dev-git-reason">
                {reason('developer.repository.branch.switch')}
              </p>
            )}
            <form
              className="dev-git-row"
              onSubmit={(event) => {
                event.preventDefault();
                if (!state.drafts.branch.trim()) return;
                void prepare('developer.repository.branch.create', {
                  branch: state.drafts.branch.trim(),
                });
              }}
            >
              <Input
                aria-label="Branch name"
                placeholder="New branch name"
                value={state.drafts.branch}
                disabled={locked}
                onChange={(event) =>
                  patchDrafts({ branch: event.target.value })
                }
              />
              <Button
                type="submit"
                disabled={
                  locked ||
                  !available('developer.repository.branch.create') ||
                  !state.drafts.branch.trim()
                }
              >
                Create branch
              </Button>
            </form>
          </section>

          <section className="dev-git-section" aria-label="Commit">
            <h4>Commit</h4>
            <div className="dev-commit-box">
              <textarea
                className="input"
                aria-label="Commit message"
                placeholder="Describe the change"
                rows={3}
                value={state.drafts.commitMessage}
                disabled={locked}
                onChange={(event) =>
                  patchDrafts({ commitMessage: event.target.value })
                }
              />
              <IconButton
                size="sm"
                label="Suggest message"
                className="dev-suggest"
                disabled={locked || !props.commitSuggestion}
                onClick={() => {
                  const suggestion = props.commitSuggestion;
                  if (!suggestion) return;
                  patchDrafts({
                    commitMessage: suggestion.body
                      ? `${suggestion.subject}\n\n${suggestion.body}`
                      : suggestion.subject,
                  });
                }}
              >
                <Sparkles size={14} aria-hidden />
              </IconButton>
            </div>
            {changed.length > 0 && (
              <Disclosure
                summary="Files to commit"
                meta={
                  selected
                    ? `${selectedCount} of ${changed.length}`
                    : `All ${changed.length}`
                }
                className="dev-disclosure"
              >
                <ul className="dev-commit-files">
                  {changed.map((file) => {
                    const checked = !selected || selected.includes(file.path);
                    return (
                      <li key={file.path}>
                        <label>
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={locked}
                            onChange={() => {
                              const current =
                                selected ?? changed.map((item) => item.path);
                              const next = checked
                                ? current.filter((path) => path !== file.path)
                                : [...current, file.path];
                              patchDrafts({
                                commitPaths:
                                  next.length === changed.length
                                    ? ''
                                    : next.join('\n'),
                              });
                            }}
                          />
                          <span>{file.path}</span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              </Disclosure>
            )}
            <div className="dev-git-row">
              <Button
                variant="primary"
                disabled={
                  locked ||
                  !available('developer.repository.commit') ||
                  !state.drafts.commitMessage.trim() ||
                  selectedCount === 0
                }
                onClick={() =>
                  void prepare('developer.repository.commit', {
                    message: state.drafts.commitMessage.trim(),
                    paths: selected ?? [],
                  })
                }
              >
                <GitCommitHorizontal size={14} aria-hidden />
                {selected
                  ? `Commit ${selectedCount} ${selectedCount === 1 ? 'file' : 'files'}`
                  : 'Commit changes'}
              </Button>
              {!available('developer.repository.commit') && (
                <span className="dev-git-reason">
                  {reason('developer.repository.commit')}
                </span>
              )}
            </div>
          </section>

          <section className="dev-git-section" aria-label="Remote">
            <h4>Remote</h4>
            <div className="dev-git-row">
              <Button
                disabled={locked || !available('developer.repository.push')}
                onClick={() => void prepare('developer.repository.push')}
              >
                <ArrowUpFromLine size={14} aria-hidden />
                Push branch
              </Button>
              <span className="dev-git-reason">
                {repo.remote_configured
                  ? 'Pushing asks for confirmation first.'
                  : 'No remote is set up, so nothing can be pushed.'}
              </span>
            </div>
            <Disclosure
              summary="Pull request"
              className="dev-disclosure"
              meta={repo.remote_configured ? undefined : 'Needs a remote'}
            >
              <div className="dev-pr-form">
                <div className="dev-git-row">
                  <Input
                    aria-label="Pull request title"
                    placeholder="Title"
                    value={state.drafts.pullTitle}
                    disabled={locked}
                    onChange={(event) =>
                      patchDrafts({ pullTitle: event.target.value })
                    }
                  />
                  <IconButton
                    size="sm"
                    label="Suggest pull request text"
                    disabled={locked || !props.pullRequestSuggestion}
                    onClick={() => {
                      const suggestion = props.pullRequestSuggestion;
                      if (suggestion)
                        patchDrafts({
                          pullTitle: suggestion.subject,
                          pullBody: suggestion.body,
                        });
                    }}
                  >
                    <Sparkles size={14} aria-hidden />
                  </IconButton>
                </div>
                <textarea
                  className="input"
                  aria-label="Pull request body"
                  placeholder="What changed and how it was tested"
                  rows={5}
                  value={state.drafts.pullBody}
                  disabled={locked}
                  onChange={(event) =>
                    patchDrafts({ pullBody: event.target.value })
                  }
                />
                <label className="dev-toggle-row">
                  <Toggle
                    label="Create as draft"
                    checked={state.drafts.pullDraft}
                    disabled={locked}
                    onChange={(event) =>
                      patchDrafts({ pullDraft: event.target.checked })
                    }
                  />
                  <span>Create as draft</span>
                </label>
                <div className="dev-git-row">
                  <Button
                    disabled={
                      locked ||
                      !available('developer.repository.pull_request') ||
                      !state.drafts.pullTitle.trim()
                    }
                    onClick={() =>
                      void prepare('developer.repository.pull_request', {
                        title: state.drafts.pullTitle.trim(),
                        body: state.drafts.pullBody,
                        draft: state.drafts.pullDraft,
                      })
                    }
                  >
                    <GitPullRequest size={14} aria-hidden />
                    Open pull request
                  </Button>
                  {!available('developer.repository.pull_request') && (
                    <span className="dev-git-reason">
                      {reason('developer.repository.pull_request')}
                    </span>
                  )}
                </div>
              </div>
            </Disclosure>
          </section>
        </>
      )}

      <Disclosure summary="Advanced" className="dev-disclosure dev-advanced">
        <section className="dev-git-section" aria-label="Managed worktree">
          <h4>Worktree</h4>
          <p className="muted">
            A conversation-owned Git worktree, seeded with the current changes,
            keeps agent work apart from the project folder.
          </p>
          <ul className="dev-worktrees">
            {snapshot.worktrees.map((worktree) => (
              <li key={worktree.worktree_id}>
                <strong>{worktree.branch || 'Managed worktree'}</strong>
                <span className="muted">
                  {worktree.status} · {worktree.cleanup_state}
                </span>
              </li>
            ))}
          </ul>
          <div className="dev-git-row">
            <Input
              aria-label="Worktree objective"
              placeholder="What the worktree is for"
              value={state.drafts.objective}
              disabled={locked}
              onChange={(event) =>
                patchDrafts({ objective: event.target.value })
              }
            />
            <Button
              disabled={
                locked || !available('developer.repository.worktree.create')
              }
              onClick={() =>
                void prepare('developer.repository.worktree.create', {
                  objective: state.drafts.objective.trim(),
                  seed_mode: 'current_changes',
                })
              }
            >
              Create managed worktree
            </Button>
          </div>
          <div className="dev-git-row">
            <Input
              aria-label="Preservation reason"
              placeholder="Why keep this worktree"
              value={state.drafts.preserveReason}
              disabled={locked}
              onChange={(event) =>
                patchDrafts({ preserveReason: event.target.value })
              }
            />
            <Button
              disabled={
                locked || !available('developer.repository.worktree.preserve')
              }
              onClick={() =>
                void prepare('developer.repository.worktree.preserve', {
                  reason: state.drafts.preserveReason.trim(),
                })
              }
            >
              Preserve worktree
            </Button>
          </div>
        </section>
        <section className="dev-git-section" aria-label="Execution sandbox">
          <h4>Sandbox</h4>
          <p className="muted">
            {snapshot.sandbox.pending_imports} pending{' '}
            {snapshot.sandbox.pending_imports === 1 ? 'import' : 'imports'} ·{' '}
            {snapshot.sandbox.owned_processes} running{' '}
            {snapshot.sandbox.owned_processes === 1 ? 'process' : 'processes'}
          </p>
          <Field label="Execution mode">
            <Select
              value={state.drafts.executionMode}
              disabled={locked}
              onChange={(event) =>
                patchDrafts({
                  executionMode: event.target.value as Drafts['executionMode'],
                })
              }
            >
              <option value="local">This computer</option>
              <option value="docker">Docker sandbox</option>
            </Select>
          </Field>
          <Field label="Sandbox network">
            <Select
              value={state.drafts.sandboxNetwork}
              disabled={locked}
              onChange={(event) =>
                patchDrafts({
                  sandboxNetwork: event.target
                    .value as Drafts['sandboxNetwork'],
                })
              }
            >
              <option value="off">Off</option>
              <option value="ask">Ask</option>
              <option value="on">On</option>
            </Select>
          </Field>
          <Field label="Sandbox image">
            <Input
              value={state.drafts.sandboxImage}
              disabled={locked}
              onChange={(event) =>
                patchDrafts({ sandboxImage: event.target.value })
              }
            />
          </Field>
          <div className="dev-git-row">
            <Button
              disabled={
                locked ||
                !available('developer.repository.sandbox.configure') ||
                !state.drafts.sandboxImage.trim()
              }
              onClick={() =>
                void prepare('developer.repository.sandbox.configure', {
                  execution_mode: state.drafts.executionMode,
                  sandbox_network: state.drafts.sandboxNetwork,
                  sandbox_image: state.drafts.sandboxImage.trim(),
                })
              }
            >
              Save sandbox settings
            </Button>
          </div>
          <div
            className="dev-danger"
            role="group"
            aria-label="Sandbox danger zone"
          >
            <p className="muted">
              Rebuilding replaces the sandbox container and its shadow copy;
              cleaning up removes them. Both ask for confirmation.
            </p>
            <div className="action-cluster">
              <Button
                variant="ghost"
                disabled={
                  locked || !available('developer.repository.sandbox.rebuild')
                }
                onClick={() =>
                  void prepare('developer.repository.sandbox.rebuild')
                }
              >
                Rebuild sandbox
              </Button>
              <Button
                variant="ghost"
                className="dev-danger-action"
                disabled={
                  locked || !available('developer.repository.sandbox.cleanup')
                }
                onClick={() =>
                  void prepare('developer.repository.sandbox.cleanup')
                }
              >
                Clean up sandbox
              </Button>
            </div>
          </div>
        </section>
        {props.advanced}
      </Disclosure>
    </section>
  );
}
