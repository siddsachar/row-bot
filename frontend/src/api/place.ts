import { useSyncExternalStore } from 'react';
import type { Place } from './types';

/**
 * Where Row-Bot runs, from this browser's side (U7), in UX.md's words. The
 * server says the place in the handshake: "this computer" only when the
 * browser is on the computer Row-Bot runs on; otherwise "your Row-Bot server",
 * named after its host when one runs it.
 */
export type PlaceWords = {
  kind: Place['kind'];
  /** A title or label: "This computer", "Your Row-Bot server". */
  label: string;
  /** Inside a label: "this computer", "your Row-Bot server". */
  name: string;
  /**
   * In a sentence, its first use on a page: "this computer", "your Row-Bot
   * server at PikaPods", "the computer Row-Bot runs on".
   */
  noun: string;
  /** In a sentence after that: "this computer", "your server", "the computer Row-Bot runs on". */
  again: string;
  /** `noun` with "on": "on this computer", "on your Row-Bot server at PikaPods"… */
  on: string;
  /** `again` with "on": "on this computer", "on your server"… */
  onAgain: string;
};

const THIS_COMPUTER: PlaceWords = {
  kind: 'this_computer',
  label: 'This computer',
  name: 'this computer',
  noun: 'this computer',
  again: 'this computer',
  on: 'on this computer',
  onAgain: 'on this computer',
};

/** The words for a place; before the handshake, the desktop's own. */
export function placeWords(place: Place | null | undefined): PlaceWords {
  if (!place || place.kind === 'this_computer') return THIS_COMPUTER;
  const noun =
    place.kind === 'server'
      ? 'the computer Row-Bot runs on'
      : place.host_name
        ? `your Row-Bot server at ${place.host_name}`
        : 'your Row-Bot server';
  const again = place.kind === 'server' ? noun : 'your server';
  return {
    kind: place.kind,
    label: 'Your Row-Bot server',
    name: 'your Row-Bot server',
    noun,
    again,
    on: `on ${noun}`,
    onAgain: `on ${again}`,
  };
}

// The place from the latest handshake (main.tsx follows it), for words made
// outside a component too (api/errors.ts).
let current = THIS_COMPUTER;
const listeners = new Set<() => void>();

export function setPlace(place: Place | null | undefined) {
  const next = placeWords(place);
  if (next.kind === current.kind && next.noun === current.noun) return;
  current = next;
  for (const listener of listeners) listener();
}

/** The words for where Row-Bot runs, as the latest handshake said. */
export function currentPlace(): PlaceWords {
  return current;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The words for where Row-Bot runs; a page re-renders when the place changes. */
export function usePlace(): PlaceWords {
  return useSyncExternalStore(subscribe, currentPlace);
}
