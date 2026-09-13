import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type {
  WorkspaceEditableFile,
  WorkspaceEditResult,
} from '../../api/types';
import {
  workspaceEdits,
  type WorkspaceEditAttemptOwner,
} from './workspace-edits';

export type WorkspaceEditState = {
  path: string;
  snapshot: WorkspaceEditableFile | null;
  draft: string | null;
  busy: boolean;
  loading: boolean;
  uncertain: boolean;
  accessible: boolean;
  stale: boolean;
  error: string;
  notice: string;
};
type IO = {
  load: (path: string, signal?: AbortSignal) => Promise<WorkspaceEditableFile>;
  save: (
    snapshot: WorkspaceEditableFile,
    content: string,
  ) => Promise<WorkspaceEditResult>;
};

/** One bounded in-memory file intent. Async settlement outlives any React view. */
export class WorkspaceEditSession {
  private state: WorkspaceEditState;
  private view: WorkspaceEditState;
  private listeners = new Set<() => void>();
  private read: AbortController | null = null;
  private operation: Promise<void> | null = null;
  private retired = false;
  private access = true;
  private resourceRevision: string | undefined;
  private version = 0;
  constructor(
    path: string,
    private io: IO,
    private changed = () => {},
  ) {
    this.state = {
      path,
      snapshot: null,
      draft: null,
      busy: false,
      loading: false,
      uncertain: false,
      accessible: true,
      stale: false,
      error: '',
      notice: '',
    };
    this.view = this.state;
  }
  get path() {
    return this.view.path;
  }
  getSnapshot = () => this.view;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private publish(patch: Partial<WorkspaceEditState> = {}) {
    if (this.retired) return;
    this.state = { ...this.state, ...patch };
    this.view = this.access
      ? this.state
      : {
          ...this.state,
          path: '',
          snapshot: null,
          draft: null,
          accessible: false,
          error:
            'Workspace access changed. The retained edit is unavailable in this binding.',
          notice: '',
        };
    this.listeners.forEach((listener) => listener());
    this.changed();
  }
  setAuthority(accessible: boolean, revision?: string) {
    if (this.retired) return;
    this.resourceRevision = revision;
    const stale =
      !!this.state.snapshot &&
      revision !== undefined &&
      revision !== this.state.snapshot.resource_revision;
    if (this.access === accessible && stale === this.state.stale) return;
    this.access = accessible;
    if (!accessible) {
      this.read?.abort();
      this.version += 1;
    }
    this.publish({
      accessible,
      stale,
      loading: accessible ? this.state.loading : false,
    });
  }
  retained() {
    return this.state.draft !== null || this.state.busy || this.state.uncertain;
  }
  canDiscard() {
    return !this.state.busy && !this.state.uncertain;
  }
  setDraft(value: string) {
    if (
      !this.access ||
      this.retired ||
      this.state.busy ||
      this.state.uncertain ||
      this.state.stale ||
      value.length > 204800
    )
      return;
    this.publish({ draft: value, notice: '' });
  }
  async load(refresh = false) {
    if (
      !this.access ||
      this.retired ||
      this.state.busy ||
      this.state.uncertain ||
      this.state.loading ||
      (!refresh && this.state.snapshot)
    )
      return;
    this.read?.abort();
    const abort = new AbortController();
    this.read = abort;
    const version = ++this.version;
    this.publish({ loading: true, error: '' });
    try {
      const snapshot = await this.io.load(this.path, abort.signal);
      if (
        this.retired ||
        abort.signal.aborted ||
        version !== this.version ||
        !this.access
      )
        return;
      if (
        snapshot.relative_path !== this.path ||
        (snapshot.content?.length ?? 0) > 204800
      )
        throw clientError({ code: 'operation_uncertain' });
      this.publish({
        snapshot: structuredClone(snapshot),
        loading: false,
        stale:
          this.resourceRevision !== undefined &&
          this.resourceRevision !== snapshot.resource_revision,
      });
    } catch (cause) {
      if (this.retired || abort.signal.aborted || version !== this.version)
        return;
      this.publish({ error: clientError(cause).message, loading: false });
    }
  }
  commit(): Promise<void> {
    if (this.operation) return this.operation;
    const snapshot = this.state.snapshot;
    if (
      !snapshot ||
      !this.access ||
      this.retired ||
      this.state.loading ||
      (this.state.stale && !this.state.uncertain) ||
      !['text', 'missing'].includes(snapshot.status)
    )
      return Promise.resolve();
    const captured = structuredClone(snapshot);
    const content = this.state.draft ?? snapshot.content ?? '';
    this.publish({ busy: true, error: '', notice: '' });
    const operation = Promise.resolve()
      .then(() => this.io.save(captured, content))
      .then(
        (result) => {
          if (this.retired) return;
          if (
            result.resource_id !== captured.resource_id ||
            result.conversation_id !== captured.conversation_id ||
            result.binding_id !== captured.binding_id ||
            result.binding_revision !== captured.binding_revision ||
            result.relative_path !== captured.relative_path ||
            result.target !== captured.target
          ) {
            this.publish({
              uncertain: true,
              error:
                'The save response could not be matched to this file. Check its original receipt.',
            });
            return;
          }
          if (
            ['saved', 'unchanged', 'pending_import'].includes(result.status)
          ) {
            this.publish({
              snapshot: {
                ...captured,
                content,
                digest: result.digest,
                status: 'text',
                resource_revision: result.resource_revision,
                binding_revision: result.binding_revision,
              },
              draft: null,
              uncertain: false,
              stale:
                this.resourceRevision !== undefined &&
                this.resourceRevision !== result.resource_revision,
              notice:
                result.status === 'pending_import'
                  ? 'Saved in the sandbox. Review and import the pending change to update the workspace.'
                  : 'File saved. Original bytes remain available in edit recovery.',
            });
          } else {
            this.publish({
              uncertain: result.status === 'partial',
              error:
                result.status === 'partial'
                  ? 'The save is incomplete. Your draft and recovery copies are retained. Retry this same save to check its original receipt.'
                  : `The file was not saved (${result.code || result.status}). Refresh its current revision; your draft is retained.`,
            });
          }
        },
        (cause: unknown) => {
          if (!this.retired)
            this.publish({
              uncertain: true,
              error: clientError(cause).message,
            });
        },
      )
      .finally(() => {
        this.operation = null;
        if (!this.retired) this.publish({ busy: false });
      });
    this.operation = operation;
    return operation;
  }
  dispose() {
    this.read?.abort();
    this.retired = true;
    this.version += 1;
    this.state = {
      path: '',
      snapshot: null,
      draft: null,
      busy: false,
      loading: false,
      uncertain: false,
      accessible: false,
      stale: false,
      error: '',
      notice: '',
    };
    this.view = this.state;
    this.listeners.forEach((listener) => listener());
    this.listeners.clear();
  }
}

type RecordEntry = {
  conversation: string;
  binding: string;
  bindingRevision: string;
  resource: string;
  session: WorkspaceEditSession;
  attempt: WorkspaceEditAttemptOwner;
  revoked: boolean;
};
export type WorkspaceEditScopeState = {
  session: WorkspaceEditSession | null;
  paths: readonly string[];
  open: boolean;
  capacity: boolean;
  accessible: boolean;
};
export interface WorkspaceEditScope {
  subscribe: (listener: () => void) => () => void;
  getSnapshot: () => WorkspaceEditScopeState;
  open: (path: string) => boolean;
  close: () => void;
  discard: () => boolean;
}

/** Explicitly construct once per authenticated controller lifetime; no browser storage. */
export function createWorkspaceEditSessions(
  controller: ClientController,
  options: { capacity?: number } = {},
) {
  const capacity = options.capacity ?? 8;
  if (!Number.isInteger(capacity) || capacity < 1 || capacity > 32)
    throw new Error('invalid_workspace_edit_capacity');
  const entries = new Map<string, RecordEntry>();
  const listeners = new Set<() => void>();
  const selected = new Map<string, { key: string; open: boolean }>();
  let authentication = '',
    disposed = false,
    notifying = false,
    version = 0;
  function auth() {
    const handshake = controller.getSnapshot().handshake;
    return handshake
      ? JSON.stringify([
          handshake.instance_id,
          handshake.server_epoch,
          handshake.client_session_id,
        ])
      : '';
  }
  function binding(conversation: string, identity: string) {
    const state = controller.getSnapshot();
    if (
      !state.handshake ||
      state.selectedConversationId !== conversation ||
      state.workspace?.conversation_id !== conversation ||
      state.loadingConversation
    )
      return null;
    return (
      state.workspace.resources.find(
        (item) => item.binding.binding_id === identity,
      ) ?? null
    );
  }
  function notify() {
    if (notifying || disposed) return;
    version += 1;
    notifying = true;
    listeners.forEach((listener) => listener());
    notifying = false;
  }
  function sync() {
    if (disposed) return;
    const current = auth();
    if (current !== authentication) {
      authentication = current;
      entries.forEach((entry) => {
        entry.session.dispose();
        entry.attempt.pending = null;
      });
      entries.clear();
      selected.clear();
    }
    const state = controller.getSnapshot();
    entries.forEach((entry) => {
      const resource = binding(entry.conversation, entry.binding);
      const observed =
        state.selectedConversationId === entry.conversation &&
        state.workspace?.conversation_id === entry.conversation &&
        !state.loadingConversation;
      if (
        observed &&
        (!resource?.available ||
          resource.binding.kind !== 'workspace' ||
          resource.binding.resource_id !== entry.resource ||
          resource.binding.revision !== entry.bindingRevision)
      )
        entry.revoked = true;
      entry.session.setAuthority(
        !!current && !entry.revoked && !!resource?.available,
        resource?.resource_revision,
      );
    });
    notify();
  }
  authentication = auth();
  const unsubscribe = controller.subscribe(sync);
  return {
    hasRetained() {
      return [...entries.values()].some(
        (entry) => entry.session.retained() || entry.attempt.pending !== null,
      );
    },
    forBinding(conversation: string, identity: string): WorkspaceEditScope {
      const scopeKey = JSON.stringify([conversation, identity]);
      let cachedVersion = -1;
      let cached: WorkspaceEditScopeState = {
        session: null,
        paths: [],
        open: false,
        capacity: false,
        accessible: false,
      };
      let full = false;
      function eligible(entry: RecordEntry) {
        const resource = binding(conversation, identity);
        return (
          !entry.revoked &&
          entry.conversation === conversation &&
          entry.binding === identity &&
          resource?.binding.revision === entry.bindingRevision &&
          resource.binding.resource_id === entry.resource &&
          resource.available
        );
      }
      return {
        subscribe(listener) {
          listeners.add(listener);
          return () => {
            listeners.delete(listener);
          };
        },
        getSnapshot() {
          if (cachedVersion === version) return cached;
          cachedVersion = version;
          const selection = selected.get(scopeKey);
          const entry = selection ? entries.get(selection.key) : undefined;
          const available = [...entries.values()].filter(eligible);
          cached = {
            session: entry && eligible(entry) ? entry.session : null,
            paths: available.map((item) => item.session.path),
            open: !!selection?.open,
            capacity: full && entries.size >= capacity,
            accessible:
              !!authentication &&
              !!binding(conversation, identity)?.available &&
              !disposed,
          };
          return cached;
        },
        open(path) {
          sync();
          const resource = binding(conversation, identity);
          if (
            disposed ||
            !authentication ||
            !resource?.available ||
            resource.binding.kind !== 'workspace' ||
            !path ||
            path.length > 4096 ||
            path.includes('\0')
          )
            return false;
          const key = JSON.stringify([
            conversation,
            identity,
            resource.binding.revision,
            resource.binding.resource_id,
            path,
          ]);
          let entry = entries.get(key);
          if (entry?.revoked) return false;
          if (!entry) {
            if (entries.size >= capacity) {
              const clean = [...entries].find(
                ([, item]) =>
                  !item.session.retained() && item.attempt.pending === null,
              );
              if (clean) {
                clean[1].session.dispose();
                entries.delete(clean[0]);
                for (const [scope, selection] of selected)
                  if (selection.key === clean[0]) selected.delete(scope);
              }
            }
            if (entries.size >= capacity) {
              full = true;
              notify();
              return false;
            }
            const attempt: WorkspaceEditAttemptOwner = { pending: null };
            const session = new WorkspaceEditSession(
              path,
              {
                load: async (name, signal) => {
                  const value = await controller.workspaceEditableFile(
                    conversation,
                    identity,
                    name,
                    signal,
                  );
                  if (
                    value.resource_id !== entry!.resource ||
                    value.conversation_id !== conversation ||
                    value.binding_id !== identity ||
                    value.binding_revision !== entry!.bindingRevision
                  )
                    throw clientError({ code: 'resource_binding_revoked' });
                  return value;
                },
                save: workspaceEdits(
                  controller,
                  conversation,
                  identity,
                  attempt,
                ),
              },
              notify,
            );
            entry = {
              conversation,
              binding: identity,
              bindingRevision: resource.binding.revision,
              resource: resource.binding.resource_id,
              session,
              attempt,
              revoked: false,
            };
            entries.set(key, entry);
          }
          full = false;
          selected.set(scopeKey, { key, open: true });
          entry.session.setAuthority(true, resource.resource_revision);
          notify();
          return true;
        },
        close() {
          const entry = selected.get(scopeKey);
          if (entry) entry.open = false;
          notify();
        },
        discard() {
          const selection = selected.get(scopeKey);
          const entry = selection && entries.get(selection.key);
          if (
            !entry ||
            !eligible(entry) ||
            !entry.session.canDiscard() ||
            entry.attempt.pending
          )
            return false;
          entry.session.dispose();
          entries.delete(selection.key);
          selected.delete(scopeKey);
          full = false;
          notify();
          return true;
        },
      };
    },
    dispose() {
      if (disposed) return;
      unsubscribe();
      entries.forEach((entry) => {
        entry.session.dispose();
        entry.attempt.pending = null;
      });
      entries.clear();
      selected.clear();
      authentication = '';
      notify();
      disposed = true;
      listeners.clear();
    },
  };
}
