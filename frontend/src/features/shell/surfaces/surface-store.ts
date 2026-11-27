import { useSyncExternalStore } from 'react';
import type { TranscriptRow } from '../../../api/types';
import { useOptionalRuntime } from '../../../runtime';
import type { DataModel } from './a2ui';

/**
 * What the person has entered on each surface while it is on screen, by its
 * state key (reply and surface): the chat's copy and the side panel's ("Open bigger") are the same
 * surface, so an edit in one shows in the other at once. It is saved beside
 * the chat as well (answer state); this is only the live copy.
 */
const models = new Map<string, DataModel>();
const listeners = new Map<string, Set<() => void>>();

export function setSurfaceModel(blockId: string, model: DataModel) {
  models.set(blockId, model);
  for (const listener of listeners.get(blockId) ?? []) listener();
}

/** The live data model of a surface, or null before the person changed it here. */
export function useSurfaceModel(blockId: string): DataModel | null {
  return useSyncExternalStore(
    (listener) => {
      const set = listeners.get(blockId) ?? new Set();
      set.add(listener);
      listeners.set(blockId, set);
      return () => set.delete(listener);
    },
    () => models.get(blockId) ?? null,
  );
}

type Block = TranscriptRow['blocks'][number];
type SurfaceBlock = Extract<Block, { type: 'surface' }>;

/** The latest reply's surface with this id in the loaded rows: a redrawn interface replaces the older one. */
export function latestSurface(
  rows: readonly TranscriptRow[],
  surfaceId: string,
): { block: SurfaceBlock; messageId: string } | null {
  for (let row = rows.length - 1; row >= 0; row--) {
    const blocks = rows[row].blocks;
    for (let index = blocks.length - 1; index >= 0; index--) {
      const block = blocks[index];
      if (block.type === 'surface' && block.surface_id === surfaceId)
        return { block, messageId: rows[row].message_id ?? '' };
    }
  }
  return null;
}

function loadedRows(
  state: ReturnType<
    NonNullable<
      ReturnType<typeof useOptionalRuntime>
    >['controller']['getSnapshot']
  >,
): TranscriptRow[] {
  return [...state.earlier, ...(state.projection?.rows ?? [])];
}

/** The block id of the latest surface with this id in the open chat ('' when none is loaded). */
export function useLatestSurfaceBlock(surfaceId: string): string {
  const controller = useOptionalRuntime()?.controller;
  return useSyncExternalStore(controller?.subscribe ?? (() => () => {}), () =>
    controller
      ? (latestSurface(loadedRows(controller.getSnapshot()), surfaceId)?.block
          .id ?? '')
      : '',
  );
}

/** The latest surface with this id in the open chat, for the side panel. */
export function useLatestSurface(surfaceId: string) {
  const controller = useOptionalRuntime()?.controller;
  const blockId = useLatestSurfaceBlock(surfaceId);
  if (!controller || !blockId) return null;
  return latestSurface(loadedRows(controller.getSnapshot()), surfaceId);
}

/** The drawing of this surface just before the given one in the loaded rows: what a redraw replaces. */
export function previousSurface(
  rows: readonly TranscriptRow[],
  surfaceId: string,
  blockId: string,
): SurfaceBlock | null {
  let previous: SurfaceBlock | null = null;
  for (const row of rows)
    for (const block of row.blocks) {
      if (block.type !== 'surface' || block.surface_id !== surfaceId) continue;
      if (block.id === blockId) return previous;
      previous = block;
    }
  return null;
}

/** The earlier drawing a redraw replaces in the open chat, or null. */
export function usePreviousSurface(
  surfaceId: string,
  blockId: string,
): SurfaceBlock | null {
  const controller = useOptionalRuntime()?.controller;
  const id = useSyncExternalStore(
    controller?.subscribe ?? (() => () => {}),
    () =>
      controller && surfaceId
        ? (previousSurface(
            loadedRows(controller.getSnapshot()),
            surfaceId,
            blockId,
          )?.id ?? '')
        : '',
  );
  if (!controller || !id) return null;
  return previousSurface(
    loadedRows(controller.getSnapshot()),
    surfaceId,
    blockId,
  );
}

/**
 * A redraw starts from what the person entered on the drawing it replaces, for every
 * value Row-Bot left as it was: adding a button keeps the ticks, while a value
 * Row-Bot changed on purpose shows its new one.
 */
export function carriedEdits(
  next: DataModel,
  before: DataModel,
  entered: DataModel | null | undefined,
): DataModel {
  if (!entered) return next;
  const carried: DataModel = { ...next };
  let changed = false;
  for (const [key, value] of Object.entries(entered)) {
    const same = JSON.stringify(next[key]) === JSON.stringify(before[key]);
    if (
      key in next &&
      same &&
      JSON.stringify(value) !== JSON.stringify(next[key])
    ) {
      carried[key] = value;
      changed = true;
    }
  }
  return changed ? carried : next;
}
