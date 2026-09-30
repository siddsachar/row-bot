import { useSyncExternalStore } from 'react';

/**
 * Favourite agent profiles (B268): the profiles pinned in the agent library,
 * shown as icons in the sidebar's Agents section and its New chat ▾ menu.
 * A per-device preference, like the sidebar's other remembered choices: the
 * profile store has no field for it and built-in profiles are read-only.
 */
const KEY = 'row-bot.agent-favourites.v1';
const listeners = new Set<() => void>();
let cached: { raw: string | null; ids: readonly string[] } = {
  raw: null,
  ids: [],
};
/** The list when this device can't save it; it holds for this session. */
let unsaved: string | null = null;

function snapshot(): readonly string[] {
  let raw = unsaved;
  if (raw === null)
    try {
      raw = localStorage.getItem(KEY);
    } catch {
      raw = null;
    }
  if (raw === cached.raw) return cached.ids;
  let ids: string[] = [];
  try {
    const value: unknown = JSON.parse(raw ?? '[]');
    if (Array.isArray(value))
      ids = value.filter((id): id is string => typeof id === 'string');
  } catch {
    ids = [];
  }
  cached = { raw, ids };
  return ids;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The pinned profile ids, in the order they were pinned. */
export function useAgentFavourites(): readonly string[] {
  return useSyncExternalStore(subscribe, snapshot);
}

/** Pins a profile as a favourite, or unpins it. */
export function toggleAgentFavourite(profileId: string): void {
  const current = snapshot();
  const raw = JSON.stringify(
    current.includes(profileId)
      ? current.filter((id) => id !== profileId)
      : [...current, profileId],
  );
  try {
    localStorage.setItem(KEY, raw);
    unsaved = null;
  } catch {
    unsaved = raw;
  }
  listeners.forEach((listener) => listener());
}
