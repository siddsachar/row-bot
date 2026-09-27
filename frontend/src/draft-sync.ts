import type { ClientController } from './api/controller';

/**
 * Keep the open conversation's draft in step with other windows of this app
 * (the desktop Buddy and the main window, or two tabs): saved drafts are
 * announced on a same-origin channel (ids only), and a window re-reads its
 * draft when it regains focus in case an announcement was missed.
 */
export function bindDraftSync(
  controller: Pick<
    ClientController,
    'bindDraftChannel' | 'refreshDraft' | 'getSnapshot'
  >,
  target: Window = window,
): () => void {
  const Channel = (
    target as Window & { BroadcastChannel?: typeof BroadcastChannel }
  ).BroadcastChannel;
  const unbind =
    typeof Channel === 'function'
      ? controller.bindDraftChannel(new Channel('row-bot-client'))
      : () => {};
  const refresh = () => {
    if (target.document.visibilityState === 'hidden') return;
    const id = controller.getSnapshot().selectedConversationId;
    if (id) void controller.refreshDraft(id);
  };
  target.addEventListener('focus', refresh);
  target.document.addEventListener('visibilitychange', refresh);
  return () => {
    unbind();
    target.removeEventListener('focus', refresh);
    target.document.removeEventListener('visibilitychange', refresh);
  };
}
