import type { ClientPlatform } from './types';

/** Keep browser permission handling at the platform boundary. */
export async function writeClipboardText(
  text: string,
  writer?: ClientPlatform['writeClipboard'],
): Promise<boolean> {
  try {
    if (writer) return (await writer(text)).status === 'ok';
    return await copyText(text);
  } catch {
    return false;
  }
}

/**
 * Copy text the way this page can: the clipboard API first, then the older
 * copy command (#633). Browsers give the clipboard API only to secure pages
 * (HTTPS or localhost), so Row-Bot opened at a LAN address over plain HTTP
 * copies through a hidden text box instead.
 */
export async function copyText(text: string): Promise<boolean> {
  if (typeof navigator.clipboard?.writeText === 'function') {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Refused (no permission, or the page lost focus): the older command may still copy.
    }
  }
  return copyWithSelection(text);
}

/**
 * Copy text that isn't known yet, from the click that asks for it (U3: one
 * click starts a sign-in and copies its code once the server has it). Call it
 * inside the click: Safari copies only while the click is still being handled,
 * which the clipboard API allows by taking the text as a promise. Elsewhere the
 * text is copied when it arrives. Resolves false when nothing was copied.
 */
export async function copyTextLater(
  text: Promise<string>,
  writer?: ClientPlatform['writeClipboard'],
): Promise<boolean> {
  if (
    !writer &&
    typeof ClipboardItem === 'function' &&
    typeof navigator.clipboard?.write === 'function'
  ) {
    try {
      const blob = text.then(
        (value) => new Blob([value], { type: 'text/plain' }),
      );
      await navigator.clipboard.write([
        new ClipboardItem({ 'text/plain': blob }),
      ]);
      return true;
    } catch {
      // Refused, or the text never came: try once more below when it did.
    }
  }
  try {
    return await writeClipboardText(await text, writer);
  } catch {
    return false;
  }
}

/** Whether this page can copy at all (the clipboard API or the older command). */
export function canCopyText(): boolean {
  return (
    typeof navigator.clipboard?.writeText === 'function' ||
    typeof document.execCommand === 'function'
  );
}

/** What to say when nothing could copy: on a plain-HTTP address, how to get copying back. */
export function copyUnavailableMessage(): string {
  return window.isSecureContext
    ? 'Copy is unavailable in this browser.'
    : 'Copy is unavailable here. Open Row-Bot over HTTPS, or on this computer, to copy.';
}

function copyWithSelection(text: string): boolean {
  if (typeof document.execCommand !== 'function') return false;
  const box = document.createElement('textarea');
  box.value = text;
  box.setAttribute('readonly', '');
  box.setAttribute('aria-hidden', 'true');
  Object.assign(box.style, {
    position: 'fixed',
    top: '0',
    left: '0',
    opacity: '0',
    pointerEvents: 'none',
  });
  const focused =
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  document.body.appendChild(box);
  box.focus({ preventScroll: true });
  box.select();
  try {
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    box.remove();
    focused?.focus({ preventScroll: true });
  }
}
