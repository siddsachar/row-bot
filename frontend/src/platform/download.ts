import { protect, unavailable } from './types';

/** Browser save presentation over already authenticated, bounded content. */
export function saveBrowserDownload(
  load: () => Promise<Blob>,
  name: string,
  signal?: AbortSignal,
  target: Window = window,
) {
  const { document, navigator } = target;
  if (
    !name ||
    name.length > 240 ||
    /[\\/:]/u.test(name) ||
    [...name].some((char) => char.charCodeAt(0) < 32)
  )
    return Promise.resolve(unavailable('invalid_name'));
  if (navigator.userActivation && !navigator.userActivation.isActive)
    return Promise.resolve(unavailable('user_gesture_required'));
  return protect(async () => {
    const blob = await load();
    if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
    const url = URL.createObjectURL(blob);
    try {
      const link = document.createElement('a');
      link.href = url;
      link.download = name;
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
