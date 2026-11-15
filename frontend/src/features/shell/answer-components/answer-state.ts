import { useEffect, useSyncExternalStore } from 'react';
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
};
type Change = Omit<AnswerStateChange, 'expected_revision'>;

function kept(view: AnswerStateView): Entry['view'] {
  return {
    revision: view.revision,
    ticks: view.ticks as AnswerState['ticks'],
    choices: view.choices as AnswerState['choices'],
  };
}
type Entry = {
  view: AnswerState & { revision?: string };
  loading?: Promise<void>;
  listeners: Set<() => void>;
};

const EMPTY: AnswerState = { ticks: {}, choices: {} };
const entries = new Map<string, Entry>();

function entry(conversationId: string): Entry {
  let found = entries.get(conversationId);
  if (!found) {
    found = { view: EMPTY, listeners: new Set() };
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
  const view = await controller.answerState(conversationId);
  if (view) publish(conversationId, kept(view));
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
  useEffect(() => {
    if (!key || !controller?.answerState) return;
    const found = entry(key);
    found.loading ??= load(controller, key).catch(() => {
      found.loading = undefined;
    });
  }, [controller, key]);
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
  const before = entry(conversationId).view;
  publish(conversationId, applied(before, change));
  if (!controller?.saveAnswerState || !before.revision) return true;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const current = entry(conversationId).view;
      const saved = await controller.saveAnswerState(conversationId, {
        ...change,
        expected_revision:
          attempt === 0 ? before.revision : (current.revision ?? ''),
      });
      publish(conversationId, kept(saved));
      return true;
    } catch (error) {
      const code = (error as { code?: string }).code;
      if (code !== 'answer_state_revision_conflict' || attempt) break;
      try {
        const fresh = await controller.answerState(conversationId);
        if (!fresh) break;
        publish(conversationId, applied(kept(fresh), change));
      } catch {
        break;
      }
    }
  }
  publish(conversationId, before);
  return false;
}
