import { clientError } from '../../api/errors';
import type { SaveOfferRequest } from '../../api/types';
import type { CapabilityResult, SavedFile } from '../../platform/types';
import { unavailable } from '../../platform/types';
import { useOptionalRuntime } from '../../runtime';
import { useNotify } from '../../ui/overlays';

export type SaveSource = SaveOfferRequest['source'];
type Notify = ReturnType<typeof useNotify>;

/**
 * The one way Row-Bot saves a file (5.3.3), for every Download and Export
 * button: the desktop app's Save dialog asks where, then "Saved …" offers Show
 * in folder; a browser downloads it; while a desktop window reconnects, a copy
 * goes into Exports and the notice says so. The person always hears where it
 * went. Called straight from the click: a browser saves only for a gesture.
 * Null outside the app (a preview of a message alone): nothing to save with.
 */
export function useSaveFile() {
  const runtime = useOptionalRuntime();
  const notify = useNotify();
  if (!runtime) return null;
  const { controller, platform } = runtime;
  return async (source: SaveSource): Promise<CapabilityResult<SavedFile>> => {
    let offer: { reference: string; file_name: string };
    try {
      offer = await controller.saveOffer(source);
    } catch (cause) {
      // The file itself is gone or not allowed: its own reason, in words.
      return unavailable(clientError(cause).code || 'operation_failed');
    }
    const result = await platform
      .save(offer.reference, offer.file_name)
      .catch((): CapabilityResult<SavedFile> =>
        unavailable('operation_failed'),
      );
    if (result.status === 'ok')
      announceSaved(notify, result.value, offer.file_name);
    return result;
  };
}

/** Text as base64 of its UTF-8 bytes, for a content save. */
export function textBase64(text: string) {
  return bytesBase64(new TextEncoder().encode(text));
}

export function bytesBase64(bytes: Uint8Array) {
  let binary = '';
  for (let at = 0; at < bytes.length; at += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(at, at + 0x8000));
  return btoa(binary);
}

/** What to say when a save didn't happen ('' when it did or was cancelled). */
export function saveProblem(result: CapabilityResult<SavedFile>, what: string) {
  if (result.status !== 'unavailable') return '';
  return result.reason === 'user_gesture_required'
    ? `Press it again to save ${what}.`
    : result.reason === 'save_failed'
      ? 'Row-Bot couldn’t write the file there. Choose another folder and try again.'
      : result.reason === 'operation_failed' || !result.reason
        ? `Couldn’t save ${what}. Try again.`
        : clientError({ code: result.reason }).message;
}

/** The notice after a save: where it went, and Show in folder where it can. */
export function announceSaved(notify: Notify, saved: SavedFile, name: string) {
  const show = (reveal: () => Promise<boolean>) => ({
    label: 'Show in folder',
    onAction: () =>
      void reveal().then((shown) => {
        if (!shown)
          notify(`Row-Bot couldn’t find ${name} any more.`, 'warning');
      }),
  });
  if (saved.kind === 'exports')
    notify(
      `Saved ${saved.fileName} to ${saved.folder}`,
      undefined,
      show(saved.reveal),
    );
  else if (saved.kind === 'file')
    notify(`Saved ${name}`, undefined, saved.reveal && show(saved.reveal));
  else notify(`Downloading ${name}`);
}
