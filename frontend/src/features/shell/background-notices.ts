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
): { text: string; tone?: NoticeTone } | null {
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
    tone:
      notice.level === 'error'
        ? 'danger'
        : notice.level === 'warning'
          ? 'warning'
          : undefined,
  };
}

/** Shows background notices through the app's notice primitive. */
export function useBackgroundNotices(): void {
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const notify = useRef(overlay.notify);
  useEffect(() => {
    notify.current = overlay.notify;
  });
  useEffect(
    () =>
      controller.onNotice((notice) => {
        const shown = noticeToShow(notice);
        if (shown) notify.current(shown.text, shown.tone);
      }),
    [controller],
  );
}
