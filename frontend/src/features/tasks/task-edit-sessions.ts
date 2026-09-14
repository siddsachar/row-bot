import { useCallback, useRef, useSyncExternalStore } from 'react';
import type { ClientController } from '../../api/controller';
import type {
  TaskGraphSnapshot,
  TaskSaveResult,
  TaskSettingsSnapshot,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { taskEdits, type TaskCommandOwner } from './task-edits';
import { taskGraphs } from './task-graphs';
import { taskSettings } from './task-settings';

export type TaskEditKind = 'task' | 'graph' | 'settings';
type Meta = {
  active: boolean;
  dirty: boolean;
  busy: boolean;
  uncertain: boolean;
  limit: boolean;
  notice: string;
};
const MAX_DRAFT_CHARACTERS = 2 * 1024 * 1024;

/** Bounded workflow editor state; no browser persistence or secret fields. */
export class TaskEditSession {
  private values: Record<string, unknown> = {};
  private defaults: Record<string, unknown> = {};
  private listeners = new Set<() => void>();
  private meta: Meta = {
    active: true,
    dirty: false,
    busy: false,
    uncertain: false,
    limit: false,
    notice: '',
  };
  private operation: Promise<unknown> | null = null;
  private reads = new Set<AbortController>();
  private loadedRevision: number | null = null;
  private retry: (() => Promise<unknown>) | null = null;
  constructor(
    readonly kind: TaskEditKind = 'task',
    readonly taskId = '',
    private changed = () => {},
    private hasPending?: () => boolean,
    private clearPending = () => {},
  ) {}
  getMeta = () => this.meta;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private publish(patch: Partial<Meta> = {}) {
    this.meta = { ...this.meta, ...patch };
    this.listeners.forEach((listener) => listener());
    this.changed();
  }
  get<T>(key: string, initial: T): T {
    if (!(key in this.defaults)) {
      this.defaults[key] = structuredClone(initial);
      this.values[key] = structuredClone(initial);
    }
    return this.values[key] as T;
  }
  set<T>(key: string, update: T | ((previous: T) => T), dirty = false) {
    if (!this.meta.active || (dirty && (this.meta.busy || this.meta.uncertain)))
      return;
    const value =
      typeof update === 'function'
        ? (update as (previous: T) => T)(this.values[key] as T)
        : update;
    const candidate = { ...this.values, [key]: value };
    if (JSON.stringify(candidate).length > MAX_DRAFT_CHARACTERS) {
      this.publish({ limit: true });
      return;
    }
    this.values = candidate;
    this.publish({ ...(dirty ? { dirty: true } : {}), limit: false });
  }
  clean(notice = '') {
    if (this.meta.active)
      this.publish({ dirty: false, uncertain: false, notice });
  }
  beginRead(revision: number) {
    if (
      !this.meta.active ||
      this.meta.busy ||
      this.meta.uncertain ||
      this.loadedRevision === revision
    )
      return null;
    this.reads.forEach((read) => read.abort());
    this.reads.clear();
    this.loadedRevision = revision;
    const abort = new AbortController();
    this.reads.add(abort);
    return abort;
  }
  finishRead(abort: AbortController) {
    this.reads.delete(abort);
    if (abort.signal.aborted) this.loadedRevision = null;
  }
  run<T>(perform: () => Promise<T>, readOnly = false): Promise<T> {
    if (!this.meta.active)
      return Promise.reject(clientError({ code: 'authentication_required' }));
    if (this.operation) return this.operation as Promise<T>;
    if (this.meta.busy)
      return Promise.reject(clientError({ code: 'operation_uncertain' }));
    this.retry = readOnly ? null : perform;
    this.publish({ busy: true, notice: '' });
    let started: Promise<T>;
    try {
      started = perform();
    } catch (cause) {
      started = Promise.reject(cause);
    }
    const operation = started
      .then(
        (value) => {
          if (this.meta.active) {
            this.retry = null;
            this.publish({ uncertain: false });
          }
          return value;
        },
        (cause: unknown) => {
          if (this.meta.active) {
            const known = [
              'task_revision_conflict',
              'task_settings_profile_conflict',
              'invalid_task_fields',
              'invalid_task_schedule',
              'invalid_task_graph',
              'invalid_task_settings',
              'task_not_found',
              'action_denied',
            ].includes(clientError(cause).code);
            const uncertain =
              !readOnly && (this.hasPending ? this.hasPending() : !known);
            if (!uncertain) this.retry = null;
            this.publish({ uncertain });
          }
          throw cause;
        },
      )
      .finally(() => {
        this.operation = null;
        if (this.meta.active) this.publish({ busy: false });
      });
    this.operation = operation;
    return operation;
  }
  retryOperation<T>() {
    return this.retry
      ? (this.run(this.retry) as Promise<T>)
      : Promise.reject(clientError({ code: 'operation_uncertain' }));
  }
  retained() {
    return (
      this.meta.dirty ||
      this.meta.busy ||
      this.meta.uncertain ||
      !!this.hasPending?.()
    );
  }
  canDiscard() {
    return !this.meta.busy && !this.meta.uncertain && !this.hasPending?.();
  }
  dispose() {
    this.reads.forEach((read) => read.abort());
    this.reads.clear();
    this.values = structuredClone(this.defaults);
    this.retry = null;
    this.clearPending();
    this.publish({
      active: false,
      dirty: false,
      busy: false,
      uncertain: false,
      limit: false,
      notice: '',
    });
    this.listeners.clear();
  }
}

export type TaskEditEntry = {
  label: string;
  session: TaskEditSession;
  edits: ReturnType<typeof taskEdits>;
  graph: ReturnType<typeof taskGraphs>;
  settings: ReturnType<typeof taskSettings>;
};

export function useTaskEditSession(
  injected: TaskEditSession | undefined,
  kind: TaskEditKind,
  taskId = '',
) {
  const local = useRef<{
    kind: TaskEditKind;
    taskId: string;
    session: TaskEditSession;
  } | null>(null);
  if (
    !local.current ||
    local.current.kind !== kind ||
    local.current.taskId !== taskId
  )
    local.current = {
      kind,
      taskId,
      session: new TaskEditSession(kind, taskId),
    };
  return injected ?? local.current.session;
}
export function useTaskEditValue<T>(
  session: TaskEditSession,
  key: string,
  initial: T,
  dirty = false,
): [T, (update: T | ((previous: T) => T)) => void] {
  const value = useSyncExternalStore(session.subscribe, () =>
    session.get(key, initial),
  );
  const set = useCallback(
    (update: T | ((previous: T) => T)) => session.set(key, update, dirty),
    [session, key, dirty],
  );
  return [value, set];
}

export function createTaskEditSessions(
  controller: ClientController,
  { capacity = 8 }: { capacity?: number } = {},
) {
  if (!Number.isInteger(capacity) || capacity < 1 || capacity > 32)
    throw new Error('invalid_task_edit_capacity');
  const listeners = new Set<() => void>();
  const entries = new Map<
    string,
    {
      label: string;
      session: TaskEditSession;
      edits: ReturnType<typeof taskEdits>;
      graph: ReturnType<typeof taskGraphs>;
      settings: ReturnType<typeof taskSettings>;
    }
  >();
  let selected: string | null = null,
    full = false,
    disposed = false;
  const auth = () => {
    const h = controller.getSnapshot().handshake;
    return h
      ? JSON.stringify([h.instance_id, h.server_epoch, h.client_session_id])
      : '';
  };
  let authentication = auth();
  type View = {
    selected: ReturnType<typeof open> | null;
    drafts: {
      key: string;
      kind: TaskEditKind;
      taskId: string;
      label: string;
      dirty: boolean;
      busy: boolean;
      uncertain: boolean;
      canDiscard: boolean;
    }[];
    capacity: boolean;
  };
  let snapshot: View = { selected: null, drafts: [], capacity: false };
  function notify() {
    snapshot = {
      selected: selected ? (entries.get(selected) ?? null) : null,
      drafts: [...entries]
        .filter(([, entry]) => entry.session.retained())
        .map(([key, entry]) => {
          const meta = entry.session.getMeta();
          return {
            key,
            kind: entry.session.kind,
            taskId: entry.session.taskId,
            label: entry.label,
            dirty: meta.dirty,
            busy: meta.busy,
            uncertain: meta.uncertain,
            canDiscard: entry.session.canDiscard(),
          };
        }),
      capacity: full,
    };
    listeners.forEach((listener) => listener());
  }
  function sync() {
    if (disposed) return;
    const current = auth();
    if (current !== authentication) {
      authentication = current;
      const old = [...entries.values()];
      entries.clear();
      selected = null;
      full = false;
      old.forEach((entry) => entry.session.dispose());
    }
    notify();
  }
  const unsubscribe = controller.subscribe(sync);
  function open(
    kind: TaskEditKind,
    taskId = '',
    taskName = '',
  ): TaskEditEntry | null {
    sync();
    if (
      disposed ||
      !authentication ||
      !['task', 'graph', 'settings'].includes(kind) ||
      taskId.length > 128 ||
      (kind !== 'task' && !taskId)
    )
      return null;
    const key = JSON.stringify([kind, taskId]);
    let entry = entries.get(key);
    if (!entry) {
      if (entries.size >= capacity) {
        const clean = [...entries].find(([, item]) => !item.session.retained());
        if (clean) {
          entries.delete(clean[0]);
          clean[1].session.dispose();
        }
      }
      if (entries.size >= capacity) {
        full = true;
        notify();
        return null;
      }
      const editOwner: TaskCommandOwner<TaskSaveResult> = { pending: null };
      const graphOwner: TaskCommandOwner<TaskGraphSnapshot> = { pending: null };
      const settingsOwner: TaskCommandOwner<TaskSettingsSnapshot> = {
        pending: null,
      };
      const session = new TaskEditSession(
        kind,
        taskId,
        notify,
        () =>
          !!(editOwner.pending || graphOwner.pending || settingsOwner.pending),
        () => {
          editOwner.pending = null;
          graphOwner.pending = null;
          settingsOwner.pending = null;
        },
      );
      entry = {
        label: taskName.trim() || (taskId ? 'Saved workflow' : 'New workflow'),
        session,
        edits: taskEdits(controller, editOwner),
        graph: taskGraphs(controller, graphOwner),
        settings: taskSettings(controller, settingsOwner),
      };
      entries.set(key, entry);
    } else if (taskName.trim()) {
      entry.label = taskName.trim();
    }
    selected = key;
    full = false;
    notify();
    return entry;
  }
  return {
    open,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    getSnapshot: () => snapshot,
    close() {
      selected = null;
      notify();
    },
    discard(key = selected) {
      const entry = key ? entries.get(key) : undefined;
      if (!entry?.session.canDiscard()) return false;
      entries.delete(key!);
      entry.session.dispose();
      if (selected === key) selected = null;
      full = false;
      notify();
      return true;
    },
    hasRetained: () =>
      [...entries.values()].some((entry) => entry.session.retained()),
    dispose() {
      unsubscribe();
      const old = [...entries.values()];
      entries.clear();
      selected = null;
      full = false;
      authentication = '';
      disposed = true;
      old.forEach((entry) => entry.session.dispose());
      notify();
      listeners.clear();
    },
  };
}
