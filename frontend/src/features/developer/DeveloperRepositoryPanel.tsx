import { useEffect, useRef, useSyncExternalStore } from 'react';
import { clientError } from '../../api/errors';
import {
  Button,
  ErrorState,
  Field,
  Input,
  Select,
  Skeleton,
} from '../../ui/primitives';

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
    if (props.visible && inScope) void refresh();
    // The authenticated owner owns callback identity; the exact scope and
    // visibility transitions are the only automatic read triggers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.visible, props.scope, inScope]);

  const patchDrafts = (patch: Partial<Drafts>) =>
    session.update({ drafts: { ...session.getSnapshot().drafts, ...patch } });

  const prepare = async (
    action: DeveloperRepositoryAction,
    extra: Record<string, unknown> = {},
  ) => {
    const current = session.getSnapshot();
    if (locked || !current.snapshot || current.pending) return;
    const allowed = capability(current.snapshot, action);
    if (!allowed.available) {
      session.update({
        error: `Action unavailable: ${labelCode(allowed.code)}.`,
      });
      return;
    }
    const request = session.beginRead();
    session.update({ reviewed: null, error: '', message: '', reading: true });
    const payload = { revision: current.snapshot.revision, ...extra };
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
        review.binding_revision !== current.snapshot.binding_revision
      )
        throw new Error('Developer repository review target did not match.');
      session.update({
        reviewed: {
          review,
          command: { command_id: crypto.randomUUID(), type: action, payload },
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

  const apply = async (recover = false) => {
    const current = session.getSnapshot();
    if (locked) return;
    const attempt = recover ? current.pending : current.reviewed;
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
              ? 'Developer repository change completed.'
              : `Developer repository change was rejected (${labelCode(receipt.code)}).`,
        });
        if (receipt.status === 'completed') await refresh(true);
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
      <ErrorState title="Developer access ended">
        Sign in again to manage the repository.
      </ErrorState>
    );
  if (!inScope)
    return (
      <ErrorState title="Developer workspace changed">
        Reopen repository controls for the current workspace.
      </ErrorState>
    );
  if (!state.snapshot && state.reading)
    return <Skeleton label="Reading Developer repository" />;
  if (!state.snapshot)
    return (
      <ErrorState
        title="Repository status unavailable"
        action={<Button onClick={() => void refresh()}>Try again</Button>}
      >
        {state.error || 'Open a Developer workspace to inspect its repository.'}
      </ErrorState>
    );

  const snapshot = state.snapshot;
  const available = (action: DeveloperRepositoryAction) =>
    capability(snapshot, action).available;
  return (
    <section className="stack" aria-label="Developer repository controls">
      <header className="section-heading">
        <div>
          <p className="eyebrow">Developer</p>
          <h2>Repository &amp; sandbox</h2>
          <p className="muted">{snapshot.workspace_name}</p>
        </div>
        <Button disabled={locked} onClick={() => void refresh()}>
          Refresh
        </Button>
      </header>

      {state.error && (
        <ErrorState title="Repository action needs attention">
          {state.error}
        </ErrorState>
      )}
      {state.message && <p role="status">{state.message}</p>}

      <article className="card stack">
        <h3>Git repository</h3>
        <p>
          {snapshot.repository.is_git
            ? `${snapshot.repository.branch || 'Detached HEAD'} · ${snapshot.repository.dirty ? 'Local changes' : 'Clean'}`
            : 'This workspace is a plain folder.'}
        </p>
        {snapshot.repository.tracking_summary && (
          <p className="muted">{snapshot.repository.tracking_summary}</p>
        )}
        <Field label="Branch name">
          <Input
            value={state.drafts.branch}
            disabled={locked}
            onChange={(event) => patchDrafts({ branch: event.target.value })}
          />
        </Field>
        <div className="button-row">
          <Button
            disabled={
              locked ||
              !available('developer.repository.branch.create') ||
              !state.drafts.branch.trim()
            }
            onClick={() =>
              void prepare('developer.repository.branch.create', {
                branch: state.drafts.branch.trim(),
              })
            }
          >
            Review new branch
          </Button>
          <Button
            disabled={
              locked ||
              !available('developer.repository.branch.switch') ||
              !state.drafts.branch.trim()
            }
            onClick={() =>
              void prepare('developer.repository.branch.switch', {
                branch: state.drafts.branch.trim(),
              })
            }
          >
            Review branch switch
          </Button>
        </div>

        <Field label="Commit message">
          <Input
            value={state.drafts.commitMessage}
            disabled={locked}
            onChange={(event) =>
              patchDrafts({ commitMessage: event.target.value })
            }
          />
        </Field>
        <Field
          label="Commit paths"
          hint="One workspace-relative path per line. Leave empty to include all current changes."
        >
          <textarea
            className="input"
            value={state.drafts.commitPaths}
            disabled={locked}
            onChange={(event) =>
              patchDrafts({ commitPaths: event.target.value })
            }
          />
        </Field>
        <Button
          disabled={
            locked ||
            !available('developer.repository.commit') ||
            !state.drafts.commitMessage.trim()
          }
          onClick={() =>
            void prepare('developer.repository.commit', {
              message: state.drafts.commitMessage.trim(),
              paths: paths(state.drafts.commitPaths),
            })
          }
        >
          Review commit
        </Button>

        <div className="button-row">
          <Button
            disabled={locked || !available('developer.repository.push')}
            onClick={() => void prepare('developer.repository.push')}
          >
            Review push
          </Button>
        </div>
        <Field label="Pull request title">
          <Input
            value={state.drafts.pullTitle}
            disabled={locked}
            onChange={(event) => patchDrafts({ pullTitle: event.target.value })}
          />
        </Field>
        <Field label="Pull request body">
          <textarea
            className="input"
            value={state.drafts.pullBody}
            disabled={locked}
            onChange={(event) => patchDrafts({ pullBody: event.target.value })}
          />
        </Field>
        <label>
          <input
            type="checkbox"
            checked={state.drafts.pullDraft}
            disabled={locked}
            onChange={(event) =>
              patchDrafts({ pullDraft: event.target.checked })
            }
          />{' '}
          Create as draft
        </label>
        <Button
          disabled={locked || !available('developer.repository.pull_request')}
          onClick={() =>
            void prepare('developer.repository.pull_request', {
              title: state.drafts.pullTitle.trim(),
              body: state.drafts.pullBody,
              draft: state.drafts.pullDraft,
            })
          }
        >
          Review pull request
        </Button>
      </article>

      <article className="card stack">
        <h3>Managed worktree</h3>
        <p className="muted">
          Current changes are copied into one conversation-owned worktree.
        </p>
        <Field label="Worktree objective">
          <Input
            value={state.drafts.objective}
            disabled={locked}
            onChange={(event) => patchDrafts({ objective: event.target.value })}
          />
        </Field>
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
          Review managed worktree
        </Button>
        {snapshot.worktrees.map((worktree) => (
          <div className="list-row" key={worktree.worktree_id}>
            <span>
              <strong>{worktree.branch || 'Managed worktree'}</strong>
              <small>
                {worktree.status} · {worktree.cleanup_state}
              </small>
            </span>
          </div>
        ))}
        <Field label="Preservation reason">
          <Input
            value={state.drafts.preserveReason}
            disabled={locked}
            onChange={(event) =>
              patchDrafts({ preserveReason: event.target.value })
            }
          />
        </Field>
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
          Review worktree preservation
        </Button>
      </article>

      <article className="card stack">
        <h3>Execution sandbox</h3>
        <p>
          {snapshot.sandbox.pending_imports} pending imports ·{' '}
          {snapshot.sandbox.owned_processes} owned processes
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
            <option value="local">Local</option>
            <option value="docker">Docker sandbox</option>
          </Select>
        </Field>
        <Field label="Sandbox network">
          <Select
            value={state.drafts.sandboxNetwork}
            disabled={locked}
            onChange={(event) =>
              patchDrafts({
                sandboxNetwork: event.target.value as Drafts['sandboxNetwork'],
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
        <div className="button-row">
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
            Review sandbox settings
          </Button>
          <Button
            disabled={
              locked || !available('developer.repository.sandbox.rebuild')
            }
            onClick={() => void prepare('developer.repository.sandbox.rebuild')}
          >
            Review sandbox rebuild
          </Button>
          <Button
            variant="danger"
            disabled={
              locked || !available('developer.repository.sandbox.cleanup')
            }
            onClick={() => void prepare('developer.repository.sandbox.cleanup')}
          >
            Review sandbox cleanup
          </Button>
        </div>
      </article>

      <article className="card stack">
        <h3>Safety boundaries</h3>
        {[
          'developer.repository.clone',
          'developer.repository.install',
          'developer.repository.network',
          'developer.repository.delete',
        ].map((action) => (
          <p key={action} className="muted">
            {action.split('.').at(-1)}:{' '}
            {labelCode(snapshot.availability[action]?.code ?? null)}
          </p>
        ))}
      </article>

      {state.reviewed && (
        <article className="card stack" aria-label="Reviewed repository change">
          <h3>Review required</h3>
          <p>{state.reviewed.review.action}</p>
          {state.reviewed.review.disclosures.map((disclosure) => (
            <p key={disclosure}>{disclosure}</p>
          ))}
          <p className="muted">
            Policy: {state.reviewed.review.policy_decision}
          </p>
          <div className="button-row">
            <Button
              variant="primary"
              disabled={
                locked ||
                Boolean(state.pending) ||
                state.reviewed.review.policy_decision === 'block'
              }
              onClick={() => void apply()}
            >
              Apply reviewed repository change
            </Button>
            <Button
              disabled={locked || Boolean(state.pending)}
              onClick={() =>
                session.update({
                  reviewed: null,
                  message: 'Review cancelled. No repository change was made.',
                })
              }
            >
              Cancel review
            </Button>
          </div>
        </article>
      )}

      {state.pending && (
        <Button disabled={locked} onClick={() => void apply(true)}>
          Check original repository change
        </Button>
      )}
    </section>
  );
}
