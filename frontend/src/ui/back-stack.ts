/**
 * Platform Back closes what is open first (A3, D16). Each open sheet, menu or
 * dialog holds one same-URL history entry, so Android's back gesture, the
 * browser's Back and a native shell's Back close the topmost one, one level
 * per Back, before leaving the page.
 *
 * Closing one any other way (×, a swipe, Escape, a choice, unmounting) gives
 * its entry back exactly once, and never steps back over a page: a
 * navigation made while a layer's entry is current takes that entry over (the
 * route takes the sheet's step, so Back from it returns to where the sheet
 * was opened). A layer that closes and a navigation or another layer that
 * follows at once share the entry instead of stepping back and forward.
 */
import { useEffect, useEffectEvent, useRef } from 'react';

const MARK = '__row_bot_overlay';
/** Entries left by an earlier page load are never this load's layers. */
const LOAD = Math.random().toString(36).slice(2, 10);

type Layer = {
  id: string;
  /** Back reached it: close it. `false` means it stays open (it gets a new entry). */
  onBack: () => boolean | void;
  /**
   * `queued`: waits for our own step back to land before it gets its entry;
   * `pushed`: its entry is in the history; `lent`: a navigation took its
   * entry over while it stays open; `released`: closed, its entry waits to be
   * stepped back over (or reused).
   */
  entry: 'queued' | 'pushed' | 'lent' | 'released';
};

let layers: Layer[] = [];
let next = 0;
/** Our own steps back whose popstate has not arrived yet. */
let traversing = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
let queued = false;
/** Set while this module writes the history itself. */
let own = false;
let installed = false;

function markOf(state: unknown): string | undefined {
  if (!state || typeof state !== 'object') return undefined;
  const mark = (state as Record<string, unknown>)[MARK];
  if (!mark || typeof mark !== 'object') return undefined;
  const id = (mark as { id?: unknown }).id;
  return typeof id === 'string' ? id : undefined;
}

const current = () => markOf(window.history.state);

function stateWith(id: string) {
  const existing: unknown = window.history.state;
  const base =
    existing && typeof existing === 'object' && !Array.isArray(existing)
      ? { ...(existing as Record<string, unknown>) }
      : {};
  return { ...base, [MARK]: { id } };
}

function write(method: 'pushState' | 'replaceState', id: string) {
  own = true;
  try {
    window.history[method](stateWith(id), '', window.location.href);
  } finally {
    own = false;
  }
}

/** Gives back the entries of closed layers on top and pushes the waiting ones. */
function settle() {
  if (traversing > 0) return;
  let closed = 0;
  while (
    closed < layers.length &&
    layers[layers.length - 1 - closed].entry === 'released'
  )
    closed += 1;
  if (closed > 0) {
    const top = layers[layers.length - 1];
    layers = layers.slice(0, layers.length - closed);
    // Only our own entry is stepped back over; one a route covered stays.
    if (current() === top.id) {
      traversing += 1;
      window.history.go(-closed);
      return;
    }
  }
  for (const layer of layers) {
    if (layer.entry === 'lent') layer.entry = 'queued';
    if (layer.entry === 'queued') {
      write('pushState', layer.id);
      layer.entry = 'pushed';
    }
  }
}

/**
 * After the code that closed a layer has run: a navigation or a new layer in
 * the same turn reuses its entry. Soon after, so a page load that follows
 * (a test's own navigation) is not undone by a late step back.
 */
function settleSoon() {
  if (queued) return;
  queued = true;
  queueMicrotask(() => {
    queued = false;
    settle();
  });
}

/** A layer still open once its navigation has settled gets a new entry. */
function restoreSoon() {
  if (timer !== undefined) return;
  timer = setTimeout(() => {
    timer = undefined;
    settle();
  }, 0);
}

function popped() {
  if (traversing > 0) {
    traversing -= 1;
    settle();
    return;
  }
  // Back (or Forward) moved to another entry: every layer above it closes,
  // the topmost first.
  const at = layers.findIndex((layer) => layer.id === current());
  const passed = layers.slice(at + 1).reverse();
  layers = layers.slice(0, at + 1);
  const kept: Layer[] = [];
  for (const layer of passed) {
    if (layer.entry === 'released') continue;
    if (layer.onBack() === false) kept.unshift(layer);
  }
  for (const layer of kept) {
    layer.entry = 'queued';
    layers.push(layer);
  }
  settle();
}

/**
 * A navigation (a route's push or replace) while the top layer's entry is
 * current takes that entry: it is replaced rather than covered.
 */
function navigating(state: unknown): boolean {
  const top = layers[layers.length - 1];
  if (!top || markOf(state) !== undefined || current() !== top.id) return false;
  if (top.entry === 'released') layers.pop();
  else if (top.entry === 'pushed') {
    top.entry = 'lent';
    restoreSoon();
  } else return false;
  return true;
}

function install() {
  if (installed) return;
  installed = true;
  window.addEventListener('popstate', popped);
  const { history } = window;
  const push = history.pushState;
  const replace = history.replaceState;
  history.pushState = function pushState(
    this: History,
    ...args: Parameters<History['pushState']>
  ) {
    if (!own && navigating(args[0])) return replace.apply(this, args);
    return push.apply(this, args);
  };
  history.replaceState = function replaceState(
    this: History,
    ...args: Parameters<History['replaceState']>
  ) {
    if (!own) navigating(args[0]);
    return replace.apply(this, args);
  };
}

/**
 * Holds one history entry while something is open; Back calls `onBack`.
 * Returns the release, which gives the entry back once (later calls do
 * nothing). Layers stack in the order they are held.
 */
export function holdBack(onBack: () => boolean | void): () => void {
  if (typeof window === 'undefined') return () => {};
  install();
  const layer: Layer = {
    id: `${LOAD}-${(next += 1)}`,
    onBack,
    entry: 'queued',
  };
  const top = layers[layers.length - 1];
  if (top?.entry === 'released' && current() === top.id) {
    // The layer that just closed hands its entry on: no step back and forth.
    layers.pop();
    write('replaceState', layer.id);
    layer.entry = 'pushed';
  } else if (traversing === 0) {
    write('pushState', layer.id);
    layer.entry = 'pushed';
  }
  layers.push(layer);
  return () => {
    if (!layers.includes(layer)) return;
    if (layer.entry === 'pushed') {
      layer.entry = 'released';
      settleSoon();
    } else if (layer.entry !== 'released')
      layers = layers.filter((item) => item !== layer);
  };
}

/**
 * Holds `level` stacked entries (a task, a task opened over it, a
 * confirmation over those); each Back calls `onBack` once, for the top one.
 * Unmounting gives every entry back.
 */
export function useBackLevels(
  level: number,
  onBack: () => boolean | void,
): void {
  const held = useRef<Array<() => void>>([]);
  const back = useEffectEvent(onBack);
  useEffect(() => {
    while (held.current.length < level) {
      const index = held.current.length;
      held.current.push(
        holdBack(() => {
          const stays = back() === false;
          if (!stays) held.current.splice(index);
          return !stays;
        }),
      );
    }
    while (held.current.length > level) held.current.pop()?.();
  }, [level]);
  useEffect(
    () => () => {
      held.current
        .splice(0)
        .reverse()
        .forEach((release) => release());
    },
    [],
  );
}

/** One entry while `open`: Back calls `onBack` (see holdBack). */
export function useBackLayer(
  open: boolean,
  onBack: () => boolean | void,
): void {
  useBackLevels(open ? 1 : 0, onBack);
}
