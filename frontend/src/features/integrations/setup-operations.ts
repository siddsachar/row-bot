import { useMemo, useSyncExternalStore } from 'react';
import type { IntegrationItem } from '../../api/types';
import { readRetainedCommand, retainCommand } from '../../api/retained-command';

// UI exclusion only. Canonical receipts and backend admission remain authoritative.
// A package and its children share the same lifecycle owner; other owners do not.
type Group = {
  busy: boolean;
  scopes: Set<string>;
  volatile: Map<string, string>;
};
const groups = new Map<string, Group>();
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};
function groupFor(item: IntegrationItem | null): Group {
  const identity = item?.parent_id ?? item?.id ?? '';
  let group = groups.get(identity);
  if (!group) {
    group = { busy: false, scopes: new Set(), volatile: new Map() };
    groups.set(identity, group);
  }
  for (const row of item ? [item, ...item.children] : []) {
    const scope = `integration-${row.kind}:${row.id}`;
    group.scopes.add(scope);
    if (row.kind === 'mcp') group.scopes.add(scope + ':auth');
    if (row.kind === 'skill') group.scopes.add(scope + ':maintenance');
  }
  if (item?.parent_id) group.scopes.add(`integration-plugin:${item.parent_id}`);
  if (item) group.scopes.add(`integration-lifecycle:${identity}`);
  return group;
}
function hasPending(group: Group): boolean {
  return [...group.scopes].some((scope) =>
    Boolean(readRetainedCommand(scope) || group.volatile.get(scope)),
  );
}
export function settleSetupOperation(commandId: string): void {
  for (const group of groups.values())
    for (const scope of group.scopes)
      if (
        (readRetainedCommand(scope) || group.volatile.get(scope)) === commandId
      ) {
        retainCommand(scope, '');
        group.volatile.delete(scope);
      }
  notify();
}
export function useSetupOperations(item: IntegrationItem | null) {
  const group = groupFor(item);
  const state = useSyncExternalStore(
    subscribe,
    () => (group.busy ? 2 : 0) | (hasPending(group) ? 1 : 0),
  );
  const actions = useMemo(
    () => ({
      read: (scope: string) =>
        readRetainedCommand(scope) || group.volatile.get(scope) || '',
      retain: (scope: string, id: string) => {
        const original =
          readRetainedCommand(scope) || group.volatile.get(scope);
        if (id && original && id !== original)
          throw new Error(
            'Check the original setup operation before making another change.',
          );
        group.scopes.add(scope);
        retainCommand(scope, id);
        // Preserve recovery in this app session when browser storage is blocked.
        if (id && readRetainedCommand(scope) !== id)
          group.volatile.set(scope, id);
        else group.volatile.delete(scope);
        notify();
      },
      run: async <T>(
        action: () => Promise<T>,
        recovery = false,
      ): Promise<T> => {
        if (group.busy || (!recovery && hasPending(group)))
          throw new Error(
            'Check the original setup operation before making another change.',
          );
        group.busy = true;
        notify();
        try {
          return await action();
        } finally {
          group.busy = false;
          notify();
        }
      },
    }),
    [group],
  );
  return { ...actions, blocked: state !== 0, busy: Boolean(state & 2) };
}
