import type { ClientPlatform } from './types';

/** Keep browser permission handling at the platform boundary. */
export async function writeClipboardText(
  text: string,
  writer?: ClientPlatform['writeClipboard'],
): Promise<boolean> {
  try {
    if (writer) return (await writer(text)).status === 'ok';
    if (typeof navigator.clipboard?.writeText !== 'function') return false;
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
