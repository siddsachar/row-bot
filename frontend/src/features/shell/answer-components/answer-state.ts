import { useEffect, useState, useSyncExternalStore } from 'react';
import type { ClientController } from '../../../api/controller';
import type { AnswerStateChange, AnswerStateView } from '../../../api/types';

/**
 * The person's own state on a chat's answer components (F3): checklist and
 * task-list ticks, and which reply a block's buttons sent. Read once per
 * chat, changed one tick at a time against the revision last read, so it
 * follows them to their other devices. It is kept beside the chat, never in
 * its messages: a tick never reaches Row-Bot by itself.
 */
export type AnswerState = {
  /** Block id → item id → ticked. */
  ticks: Record<string, Record<string, boolean>>;
  /** Block id → the label of the reply its buttons sent. */
  choices: Record<string, string>;
  /** Block id → a surface's data model as the person left it (generative UI). */
  surfaces: Record<string, Record<string, unknown>>;
};
type Change = Omit<AnswerStateChange, 'expected_revision'>;

function kept(view: AnswerStateView): Entry['view'] {
  return {
    revision: view.revision,
    ticks: view.ticks as AnswerState['ticks'],
    choices: view.choices as AnswerState['choices'],
    surfaces: (view.surfaces ?? {}) as AnswerState['surfaces'],
  };
}
type Entry = {
  view: AnswerState & { revision?: string };
  loading?: Promise<void>;
  /** Read from Row-Bot (or this client can't read it: a preview). */
  loaded: boolean;
  /** Changes made here; a read that started before one never replaces it. */
  changes: number;
  listeners: Set<() => void>;
};
// A read that fails (the chat shown before the connection is ready, a
// moment offline) is tried again, more slowly each time.
const RETRIES = 8;
const retryDelay = (attempt: number) => Math.min(500 * 2 ** attempt, 5000);

const EMPTY: AnswerState = { ticks: {}, choices: {}, surfaces: {} };
const entries = new Map<string, Entry>();

function entry(conversationId: string): Entry {
  let found = entries.get(conversationId);
  if (!found) {
    found = { view: EMPTY, loaded: false, changes: 0, listeners: new Set() };
    entries.set(conversationId, found);
  }
  return found;
}

function publish(conversationId: string, view: Entry['view']) {
  const found = entry(conversationId);
  found.view = view;
  for (const listener of found.listeners) listener();
}

function applied(view: Entry['view'], change: Change): Entry['view'] {
  if (change.surface_data)
    return {
      ...view,
      surfaces: {
        ...view.surfaces,
        [change.block_id]: change.surface_data as Record<string, unknown>,
      },
    };
  if (change.choice)
    return {
      ...view,
      choices: { ...view.choices, [change.block_id]: change.choice },
    };
  return {
    ...view,
    ticks: {
      ...view.ticks,
      [change.block_id]: {
        ...view.ticks[change.block_id],
        [change.item_id!]: Boolean(change.done),
      },
    },
  };
}

async function load(controller: ClientController, conversationId: string) {
  const found = entry(conversationId);
  const seen = found.changes;
  try {
    const view = await controller.answerState(conversationId);
    found.loaded = true;
    // A tick made while this read was on its way is newer: its own save
    // read the state again and keeps it, so this older answer is dropped.
    if (view && found.changes === seen) publish(conversationId, kept(view));
  } catch (error) {
    if ((error as { code?: string }).code === 'capability_unavailable')
      found.loaded = true;
  }
}

/** This chat's state, read from Row-Bot the first time it is shown. */
export function useAnswerState(
  controller: ClientController | undefined,
  conversationId: string | undefined,
): AnswerState {
  const key = conversationId ?? '';
  const view = useSyncExternalStore(
    (listener) => {
      if (!key) return () => {};
      const found = entry(key);
      found.listeners.add(listener);
      return () => found.listeners.delete(listener);
    },
    () => (key ? entry(key).view : EMPTY),
  );
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!key || !controller?.answerState) return;
    const found = entry(key);
    if (found.loaded) return;
    let live = true;
    let timer: number | undefined;
    found.loading ??= load(controller, key).finally(() => {
      found.loading = undefined;
    });
    void found.loading.then(() => {
      if (live && !found.loaded && attempt < RETRIES)
        timer = window.setTimeout(
          () => setAttempt((value) => value + 1),
          retryDelay(attempt),
        );
    });
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [controller, key, attempt]);
  return view;
}

/**
 * Shows the change at once and saves it. A change made from an older
 * revision (another device moved on) is read again and applied once more.
 * False, with the change taken back, when it couldn't be saved.
 */
export async function changeAnswerState(
  controller: ClientController | undefined,
  conversationId: string | undefined,
  change: Change,
): Promise<boolean> {
  if (!conversationId) return true;
  entry(conversationId).changes += 1;
  const before = entry(conversationId).view;
  publish(conversationId, applied(before, change));
  if (!controller?.saveAnswerState || !controller.answerState) return true;
  // Not read yet (a tick made as the chat opens): read it first.
  let revision = before.revision;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      if (!revision) {
        const fresh = kept(await controller.answerState(conversationId));
        entry(conversationId).loaded = true;
        publish(conversationId, applied(fresh, change));
        revision = fresh.revision;
      }
      const saved = await controller.saveAnswerState(conversationId, {
        ...change,
        expected_revision: revision ?? '',
      });
      publish(conversationId, kept(saved));
      return true;
    } catch (error) {
      const code = (error as { code?: string }).code;
      // A client without this store (a preview) keeps ticks on screen only.
      if (code === 'capability_unavailable') return true;
      if (code !== 'answer_state_revision_conflict' || attempt) break;
      revision = undefined;
    }
  }
  try {
    publish(conversationId, kept(await controller.answerState(conversationId)));
  } catch {
    publish(conversationId, before);
  }
  return false;
}
