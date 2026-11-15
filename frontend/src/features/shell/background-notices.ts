import { useEffect, useRef } from 'react';
import type { Notice } from '../../api/types';
import { useRuntime } from '../../runtime';
import { useOverlay, type NoticeTone } from '../../ui/overlays';

/** Older news than this is not announced when a window first connects. */
const RECENT_MS = 10 * 60 * 1000;

/**
 * Which background notices a person sees (parity rows 10 and 11): warnings
 * and errors always, information only for jobs they started, start-up
 * warnings once, and nothing old enough to be stale.
 */
export function noticeToShow(
  notice: Notice,
  now = Date.now(),
): {
  text: string;
  tone?: NoticeTone;
  conversationId?: string;
  workflowId?: string;
} | null {
  if (notice.level === 'info' && !notice.requested) return null;
  const at = Date.parse(notice.at);
  if (!notice.startup && Number.isFinite(at) && now - at > RECENT_MS)
    return null;
  // Titles may start with an emoji; the notice's tone already says it.
  const title = notice.title.replace(/^[^\p{L}\p{N}]+/u, '').trim();
  const body = notice.message.trim();
  const text =
    notice.startup || !title
      ? body || title
      : body
        ? `${title}: ${body}`
        : title;
  return {
    text: notice.count > 1 ? `${text} (${notice.count} times)` : text,
    // A notice about a conversation (a file Row-Bot sent you) opens it.
    ...(notice.conversation_id
      ? { conversationId: notice.conversation_id }
      : {}),
    // A notice about a workflow (Row-Bot switched it off) opens it.
    ...(notice.workflow_id ? { workflowId: notice.workflow_id } : {}),
    tone:
      notice.level === 'error'
        ? 'danger'
        : notice.level === 'warning'
          ? 'warning'
          : undefined,
  };
}

/**
 * Shows background notices through the app's notice primitive. One about a
 * conversation offers Open, and isn't shown while that conversation is open
 * here (its card is already on screen). One about a workflow offers Open too.
 */
export function useBackgroundNotices(
  openConversationId: string | null = null,
  openConversation?: (conversationId: string) => void,
  openWorkflow?: (workflowId: string) => void,
): void {
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const latest = useRef({
    notify: overlay.notify,
    openConversationId,
    openConversation,
    openWorkflow,
  });
  useEffect(() => {
    latest.current = {
      notify: overlay.notify,
      openConversationId,
      openConversation,
      openWorkflow,
    };
  });
  useEffect(
    () =>
      controller.onNotice((notice) => {
        const shown = noticeToShow(notice);
        if (!shown) return;
        const { notify, openConversationId, openConversation, openWorkflow } =
          latest.current;
        const about = shown.conversationId;
        const workflow = shown.workflowId;
        if (about && about === openConversationId) return;
        notify(
          shown.text,
          shown.tone,
          about && openConversation
            ? { label: 'Open', onAction: () => openConversation(about) }
            : workflow && openWorkflow
              ? { label: 'Open', onAction: () => openWorkflow(workflow) }
              : undefined,
        );
      }),
    [controller],
  );
}
