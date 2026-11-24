import { useEffect, useSyncExternalStore } from 'react';
import type { BoardView } from '../../../api/types';
import { useOptionalRuntime } from '../../../runtime';

/**
 * The Home Board (generative UI, G5): the surfaces the person pinned. Read
 * once and shared by every surface on screen, so a chat's Pin and Home's
 * Board agree; each pin or unpin returns the whole Board.
 */
type Board = BoardView['items'];

let items: Board | null = null;
let loading: Promise<void> | null = null;
const listeners = new Set<() => void>();

function publish(next: Board) {
  items = next;
  for (const listener of listeners) listener();
}

type Controller = NonNullable<
  ReturnType<typeof useOptionalRuntime>
>['controller'];

async function load(controller: Controller) {
  try {
    const view = await controller.board();
    if (view) publish(view.items);
  } catch {
    // A client without the Board (a preview) shows none.
    if (items === null) publish([]);
  }
}

/** The pinned surfaces, or null before they are read. */
export function useBoard(): Board | null {
  const controller = useOptionalRuntime()?.controller;
  const board = useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => items,
  );
  useEffect(() => {
    if (!controller || items !== null) return;
    loading ??= load(controller).finally(() => {
      loading = null;
    });
  }, [controller]);
  return board;
}

/** Read the Board again (Home opening). */
export function reloadBoard(controller: Controller | undefined) {
  if (controller) void load(controller);
}

export function pinned(
  board: Board | null,
  conversationId: string | undefined,
  surfaceId: string,
) {
  return (board ?? []).find(
    (item) =>
      item.conversation_id === conversationId && item.surface_id === surfaceId,
  );
}

export async function pinSurface(
  controller: Controller | undefined,
  body: { conversation_id: string; message_id: string; surface_id: string },
) {
  if (!controller) return false;
  const view = await controller.pinToBoard(body);
  publish(view.items);
  return true;
}

export async function unpinSurface(
  controller: Controller | undefined,
  pinId: string,
) {
  if (!controller) return false;
  const view = await controller.unpinFromBoard(pinId);
  publish(view.items);
  return true;
}
