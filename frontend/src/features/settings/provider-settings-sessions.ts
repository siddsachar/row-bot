import { useCallback, useSyncExternalStore } from 'react';
import type { ClientController } from '../../api/controller';
import type {
  ProviderSettingsSnapshot,
  ProviderSettingsReview,
  ProviderSettingsReceipt,
} from '../../api/types';
import type { ProviderSettingsEditorProps } from './ProviderSettingsEditor';

export type ProviderSettingsCallbacks = Pick<
  ProviderSettingsEditorProps,
  'load' | 'review' | 'apply' | 'receipt'
> & { clear?: () => void };

type Operation = 'save' | 'clear' | 'restore';
export type ProviderSettingsTransport = {
  providerSettings: (
    providerId: string,
    signal?: AbortSignal,
  ) => Promise<ProviderSettingsSnapshot>;
  reviewProviderSettings: (
    providerId: string,
    input: { provider_revision: string; operation: Operation; value?: string },
    signal?: AbortSignal,
  ) => Promise<ProviderSettingsReview>;
  executeProviderCredential: (
    providerId: string,
    revision: string,
    operation: Operation,
    value: string | undefined,
    commandId: string,
    nonce: string,
  ) => Promise<ProviderSettingsSnapshot>;
  providerSettingsReceipt: (
    providerId: string,
    commandId: string,
    signal?: AbortSignal,
  ) => Promise<ProviderSettingsReceipt>;
};

export function providerSettingsCallbacks(
  controller: ProviderSettingsTransport,
): ProviderSettingsCallbacks {
  let reviewed: { tuple: string; nonce: string } | null = null;
  let generation = 0;
  return {
    clear: () => {
      generation += 1;
      reviewed = null;
    },
    load: (id, signal) => controller.providerSettings(id, signal),
    async review(id, revision, operation, value, signal) {
      reviewed = null;
      const captured = ++generation;
      const result = await controller.reviewProviderSettings(
        id,
        {
          provider_revision: revision,
          operation,
          ...(value === undefined ? {} : { value }),
        },
        signal,
      );
      if (signal?.aborted || captured !== generation)
        throw { code: 'operation_cancelled' };
      if (
        result.provider_id !== id ||
        result.provider_revision !== revision ||
        result.operation !== operation ||
        result.snapshot.provider_id !== id ||
        result.snapshot.revision !== revision ||
        !result.nonce
      )
        throw { code: 'revision_conflict' };
      reviewed = {
        tuple: JSON.stringify([id, revision, operation, value]),
        nonce: result.nonce,
      };
      return result.snapshot;
    },
    async apply(id, revision, operation, value, commandId) {
      if (
        !reviewed ||
        reviewed.tuple !== JSON.stringify([id, revision, operation, value])
      )
        throw { code: 'approval_expired' };
      const result = await controller.executeProviderCredential(
        id,
        revision,
        operation,
        value,
        commandId,
        reviewed.nonce,
      );
      if (result.provider_id !== id) throw { code: 'operation_uncertain' };
      reviewed = null;
      return result;
    },
    async receipt(id, commandId, signal) {
      const result = await controller.providerSettingsReceipt(
        id,
        commandId,
        signal,
      );
      if (
        result.command_id !== commandId ||
        result.credential.provider_id !== id
      )
        throw { code: 'operation_uncertain' };
      if (result.status === 'uncertain') return null;
      reviewed = null;
      return result.status === 'rejected'
        ? { rejected: true as const, snapshot: result.credential }
        : result.credential;
    },
  };
}

/** Private controller-lifetime buffers. Never serialized or persisted in a browser. */
export class ProviderSettingsSession {
  private values: Record<string, unknown> = {};
  private defaults: Record<string, unknown> = {};
  private listeners = new Set<() => void>();
  private reads = new Set<AbortController>();
  private loaded: number | null = null;
  private operation: Promise<unknown> | null = null;
  private intent: readonly unknown[] | null = null;
  active = true;
  constructor(
    readonly providerId: string,
    private changed = () => {},
  ) {}
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private publish() {
    this.listeners.forEach((listener) => listener());
    this.changed();
  }
  get<T>(key: string, initial: T): T {
    if (!(key in this.defaults)) {
      this.defaults[key] = initial;
      this.values[key] = initial;
    }
    return this.values[key] as T;
  }
  set<T>(key: string, update: T | ((previous: T) => T)) {
    if (!this.active) return;
    const value =
      typeof update === 'function'
        ? (update as (previous: T) => T)(this.values[key] as T)
        : update;
    if (
      key === 'secret' &&
      (typeof value !== 'string' ||
        new TextEncoder().encode(value).length > 16384)
    ) {
      this.values = {
        ...this.values,
        error: 'The API key must be no more than 16 KiB of UTF-8 text.',
      };
      this.publish();
      return;
    }
    this.values = { ...this.values, [key]: value };
    this.publish();
  }
  beginRead(reload: number) {
    if (!this.active || this.loaded === reload) return null;
    this.loaded = reload;
    return this.read();
  }
  read() {
    const abort = new AbortController();
    this.reads.add(abort);
    return abort;
  }
  finishRead(abort: AbortController) {
    this.reads.delete(abort);
  }
  perform<T>(args: readonly unknown[], apply: () => Promise<T>): Promise<T> {
    if (!this.active)
      return Promise.reject({ code: 'authentication_required' });
    if (this.operation) return this.operation as Promise<T>;
    if (this.intent) return Promise.reject({ code: 'operation_uncertain' });
    // The only retry surface reads the original receipt. Never redispatch this body.
    this.intent = structuredClone(args);
    let result: Promise<T>;
    try {
      result = apply();
    } catch (cause) {
      result = Promise.reject(cause);
    }
    this.operation = result;
    void result
      .then(
        () => {},
        () => {},
      )
      .finally(() => {
        if (this.operation === result) this.operation = null;
        this.publish();
      });
    return result;
  }
  resolved() {
    this.intent = null;
    this.publish();
  }
  retained() {
    return !!(
      this.values.secret ||
      this.values.pending ||
      this.values.reviewed ||
      this.operation ||
      this.intent ||
      this.values.busy
    );
  }
  canDiscard() {
    return (
      !this.operation &&
      !this.values.pending &&
      !this.intent &&
      !this.values.busy
    );
  }
  dispose() {
    this.active = false;
    this.reads.forEach((read) => read.abort());
    this.reads.clear();
    this.intent = null;
    this.values = { ...this.defaults };
    this.publish();
  }
}

export function useProviderSettingsValue<T>(
  session: ProviderSettingsSession,
  key: string,
  initial: T,
): [T, (update: T | ((previous: T) => T)) => void] {
  const value = useSyncExternalStore(session.subscribe, () =>
    session.get(key, initial),
  );
  const set = useCallback(
    (update: T | ((previous: T) => T)) => session.set(key, update),
    [session, key],
  );
  return [value, set];
}

export function createProviderSettingsSessions(
  controller: Pick<ClientController, 'getSnapshot' | 'subscribe'>,
  callbacks: (providerId: string) => ProviderSettingsCallbacks,
  {
    capacity = 4,
    allowProviderId = (id: string) => /^[a-z0-9_-]{1,64}$/.test(id),
  }: { capacity?: number; allowProviderId?: (id: string) => boolean } = {},
) {
  if (!Number.isInteger(capacity) || capacity < 1 || capacity > 16)
    throw new Error('invalid_provider_settings_capacity');
  const entries = new Map<
    string,
    { session: ProviderSettingsSession } & ProviderSettingsCallbacks
  >();
  const listeners = new Set<() => void>();
  const auth = () => {
    const h = controller.getSnapshot().handshake;
    return h
      ? JSON.stringify([h.instance_id, h.server_epoch, h.client_session_id])
      : '';
  };
  let authentication = auth(),
    disposed = false,
    full = false;
  let snapshot = { capacity: false, retained: [] as string[] };
  const notify = () => {
    snapshot = {
      capacity: full,
      retained: [...entries]
        .filter(([, entry]) => entry.session.retained())
        .map(([id]) => id),
    };
    listeners.forEach((listener) => listener());
  };
  const sync = () => {
    if (disposed) return;
    if (auth() !== authentication) {
      authentication = auth();
      const old = [...entries.values()];
      entries.clear();
      full = false;
      old.forEach((entry) => {
        entry.clear?.();
        entry.session.dispose();
      });
    }
    notify();
  };
  const unsubscribe = controller.subscribe(sync);
  return {
    open(providerId: string) {
      sync();
      if (disposed || !authentication || !allowProviderId(providerId))
        return null;
      let entry = entries.get(providerId);
      if (!entry) {
        if (entries.size >= capacity) {
          const clean = [...entries].find(
            ([, item]) => !item.session.retained(),
          );
          if (clean) {
            entries.delete(clean[0]);
            clean[1].clear?.();
            clean[1].session.dispose();
          }
        }
        if (entries.size >= capacity) {
          full = true;
          notify();
          return null;
        }
        const session = new ProviderSettingsSession(providerId, notify);
        const transport = callbacks(providerId);
        const guard = <T extends (...args: never[]) => Promise<unknown>>(
          fn: T,
        ): T =>
          (async (...args: Parameters<T>) => {
            sync();
            if (!session.active) throw { code: 'authentication_required' };
            const result = await fn(...args);
            sync();
            if (!session.active) throw { code: 'authentication_required' };
            return result;
          }) as T;
        entry = {
          session,
          clear: transport.clear,
          load: guard(transport.load),
          review: guard(transport.review),
          apply: guard(transport.apply),
          receipt: guard(transport.receipt),
        };
        entries.set(providerId, entry);
      }
      full = false;
      notify();
      return entry;
    },
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    getSnapshot: () => snapshot,
    canDiscard: (providerId: string) =>
      !!entries.get(providerId)?.session.canDiscard(),
    discard(providerId: string) {
      const entry = entries.get(providerId);
      if (!entry?.session.canDiscard()) return false;
      entries.delete(providerId);
      entry.clear?.();
      entry.session.dispose();
      full = false;
      notify();
      return true;
    },
    hasRetained: () =>
      [...entries.values()].some((entry) => entry.session.retained()),
    dispose() {
      unsubscribe();
      disposed = true;
      authentication = '';
      const old = [...entries.values()];
      entries.clear();
      old.forEach((entry) => {
        entry.clear?.();
        entry.session.dispose();
      });
      full = false;
      notify();
      listeners.clear();
    },
  };
}
