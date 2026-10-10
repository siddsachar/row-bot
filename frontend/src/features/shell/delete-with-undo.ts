import { useSyncExternalStore } from 'react';
import type { ClientController } from '../../api/controller';
import type { ConversationView } from '../../api/types';
import type { NoticeAction, NoticeTone } from '../../ui/overlays';
import { deleteOneConversation } from './ConversationLibrary';

type Notify = (
  message: string,
  tone?: NoticeTone,
  action?: NoticeAction,
) => void;

// Conversations confirmed for deletion while their Undo notice shows. The
// sidebar leaves them out, so a list refresh cannot bring one back early.
let waiting: ReadonlySet<string> = new Set();
const listeners = new Set<() => void>();
function setWaiting(next: ReadonlySet<string>) {
  waiting = next;
  listeners.forEach((listener) => listener());
}
function release(id: string) {
  const next = new Set(waiting);
  next.delete(id);
  setWaiting(next);
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
const read = () => waiting;

/** Conversations deleted with Undo still on offer; lists leave them out. */
export function useDeletingConversations(): ReadonlySet<string> {
  return useSyncExternalStore(subscribe, read, read);
}

/**
 * Delete one conversation after the person confirmed it, with Undo. It
 * leaves the lists at once (the caller first leaves it if it is open) and
 * "Deleted '…'." offers Undo. The delete is sent only when that notice ends
 * (it timed out or was dismissed); Undo brings the conversation back. A page
 * that goes away first sends nothing (its connection closes as it hides, so
 * the outcome could not be confirmed): the conversation simply stays.
 */
export function deleteWithUndo(
  controller: Pick<
    ClientController,
    'getSnapshot' | 'command' | 'forgetConversation' | 'loadMoreConversations'
  >,
  notify: Notify,
  conversation: Pick<ConversationView, 'id' | 'revision' | 'title'>,
): void {
  const { id } = conversation;
  if (waiting.has(id)) return;
  const title = conversation.title || 'this conversation';
  setWaiting(new Set(waiting).add(id));
  controller.forgetConversation(id);
  notify(`Deleted '${title}'.`, undefined, {
    label: 'Undo',
    onAction: () => {
      release(id);
      void controller.loadMoreConversations(true);
    },
    onEnd: () => {
      // The id and revision confirmed: a conversation changed since is
      // refused by the server, never something else deleted.
      void deleteOneConversation(controller, conversation)
        .then((outcome) => {
          if (outcome.status === 'deleted') {
            controller.forgetConversation(id);
            if (outcome.notice) notify(`Deleted '${title}'. ${outcome.notice}`);
          } else notify(outcome.message);
        })
        .catch(() => notify(`Couldn't delete '${title}'. Try again.`))
        .finally(() => {
          release(id);
          void controller.loadMoreConversations(true);
        });
    },
  });
}
