/**
 * Undo and redo for a design, built on its saved history. Every panel edit
 * (a restore included) first snapshots the state it replaces, newest first,
 * with timestamp ids. Undo restores the newest snapshot older than the last
 * one this chain restored; redo restores the snapshot an undo created. Any
 * other change to the design (an agent turn, an edit) ends the chain.
 */

export type HistoryEntry = { id: string; available: boolean };

export type UndoChain = {
  /** The design revision this chain produced last. */
  revision: string;
  /** Snapshots restored by undo, oldest step last. */
  undone: string[];
  /** Snapshots each undo created (the state it replaced), newest last. */
  redo: string[];
};

function order(id: string) {
  const value = Number(id);
  return Number.isFinite(value) ? value : Number.NaN;
}

function active(chain: UndoChain | null, revision: string) {
  return chain && chain.revision === revision ? chain : null;
}

/** The snapshot Undo restores next, or null when there is none. */
export function undoTarget(
  chain: UndoChain | null,
  history: HistoryEntry[],
  revision: string,
): string | null {
  const current = active(chain, revision);
  const last = current?.undone.at(-1);
  const bound = last === undefined ? Infinity : order(last);
  return (
    history.find((item) => item.available && order(item.id) < bound)?.id ?? null
  );
}

/** Chain after an undo restored `target`; `created` is the new newest snapshot. */
export function afterUndo(
  chain: UndoChain | null,
  previousRevision: string,
  target: string,
  revision: string,
  created: string | null,
): UndoChain {
  const current = active(chain, previousRevision) ?? {
    revision,
    undone: [],
    redo: [],
  };
  return {
    revision,
    undone: [...current.undone, target],
    redo:
      created && order(created) > order(target)
        ? [...current.redo, created]
        : current.redo,
  };
}

/** The snapshot Redo restores next, or null. */
export function redoTarget(
  chain: UndoChain | null,
  revision: string,
): string | null {
  return active(chain, revision)?.redo.at(-1) ?? null;
}

/** Chain after a redo; the next undo repeats the step that was redone. */
export function afterRedo(chain: UndoChain, revision: string): UndoChain {
  return {
    revision,
    undone: chain.undone.slice(0, -1),
    redo: chain.redo.slice(0, -1),
  };
}
