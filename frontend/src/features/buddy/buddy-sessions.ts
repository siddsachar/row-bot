import type { ClientController } from '../../api/controller';
import type { BuddyReceipt as WireReceipt } from '../../api/types';
import {
  createBuddyPanelSession,
  type BuddyPanelSession,
  type BuddyReceipt,
} from './BuddyPanel';

function receipt(value: WireReceipt): BuddyReceipt {
  return {
    command_id: value.command_id,
    status: value.status,
    ...(value.code ? { code: value.code } : {}),
    ...(value.buddy_revision ? { buddy_revision: value.buddy_revision } : {}),
    ...(value.hatch ? { hatch: value.hatch } : {}),
    ...(value.removal ? { removal: value.removal } : {}),
    ...(value.cancel_requested !== null && value.cancel_requested !== undefined
      ? { cancel_requested: value.cancel_requested }
      : {}),
  };
}

/** Bounded private conversation sessions inside the existing authenticated owner. */
export function createBuddySessions(controller: ClientController) {
  const entries = new Map<string, BuddyPanelSession>();
  const identity = () => {
    const h = controller.getSnapshot().handshake;
    return h
      ? JSON.stringify([h.instance_id, h.server_epoch, h.client_session_id])
      : '';
  };
  const authentication = identity();
  let disposed = false;
  const guard = () => {
    if (disposed || !authentication || identity() !== authentication)
      throw new Error('authentication_required');
  };
  return {
    get(conversation: string) {
      guard();
      const prior = entries.get(conversation);
      if (prior) return prior;
      if (entries.size >= 8) {
        const settled = [...entries].find(
          ([, session]) => !session.hasRetained(),
        );
        if (!settled) throw new Error('buddy_session_full');
        settled[1].purge();
        entries.delete(settled[0]);
      }
      const session = createBuddyPanelSession(
        {
          snapshot: (signal) => controller.buddy(conversation, signal),
          packs: (cursor) => controller.buddyPacks(conversation, cursor),
          pack: (id) => controller.buddyPack(conversation, id),
          review: (request) => controller.reviewBuddy(conversation, request),
          execute: async (command) =>
            receipt(await controller.executeBuddy(conversation, command)),
          receipt: async (command) =>
            receipt(await controller.buddyReceipt(conversation, command)),
        },
        guard,
      );
      entries.set(conversation, session);
      return session;
    },
    hasRetained: () =>
      !disposed &&
      [...entries.values()].some((session) => session.hasRetained()),
    dispose() {
      disposed = true;
      entries.forEach((session) => session.purge());
      entries.clear();
    },
  };
}
