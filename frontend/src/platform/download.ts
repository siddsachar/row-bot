import { pywebviewHost } from './host';
import { protect, unavailable } from './types';

function badName(name: string) {
  return (
    !name ||
    name.length > 240 ||
    /[\\/:]/u.test(name) ||
    [...name].some((char) => char.charCodeAt(0) < 32)
  );
}

/**
 * Browser save presentation over already authenticated, bounded content. A
 * name given as a function is read once the content has loaded: the save
 * starts inside the click (a browser saves only for one) even when the file
 * it saves, and so its name, is made after it.
 */
export function saveBrowserDownload(
  load: () => Promise<Blob>,
  name: string | (() => string),
  signal?: AbortSignal,
  target: Window = window,
) {
  const { document, navigator } = target;
  if (typeof name === 'string' && badName(name))
    return Promise.resolve(unavailable('invalid_name'));
  // pywebview cancels every download, so a desktop window never pretends one
  // started (B275); callers offer what works there instead.
  if (pywebviewHost(target))
    return Promise.resolve(unavailable('desktop_download_unavailable'));
  if (navigator.userActivation && !navigator.userActivation.isActive)
    return Promise.resolve(unavailable('user_gesture_required'));
  return protect(async () => {
    const blob = await load();
    if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
    const chosen = typeof name === 'string' ? name : name();
    if (badName(chosen)) throw new Error('invalid_name');
    const url = URL.createObjectURL(blob);
    try {
      const link = document.createElement('a');
      link.href = url;
      link.download = chosen;
      document.body.append(link);
      try {
        link.click();
      } finally {
        link.remove();
      }
    } finally {
      URL.revokeObjectURL(url);
    }
    return null;
  });
}
