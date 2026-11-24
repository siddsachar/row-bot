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
