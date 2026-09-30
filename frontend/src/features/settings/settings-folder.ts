import { clientError } from '../../api/errors';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform } from '../../platform';
import type { SettingsFolderGrant } from './SettingsSnapshotPanels';

/**
 * A folder for a setting (the wiki vault, the workspace folder), picked in
 * the desktop window and claimed as a short-lived grant for this session.
 * The desktop server runs apart from the window, so it can't show a picker
 * of its own (B280). A browser can't pick one: it says to use the desktop
 * app.
 */
export async function pickSettingsFolder(
  platform: ClientPlatform,
  controller: Pick<ClientController, 'claimFolder'>,
  signal?: AbortSignal,
): Promise<SettingsFolderGrant> {
  const picked = await platform.selectFolder(signal, {
    intentId: crypto.randomUUID(),
    intent: 'settings_folder',
    conversationId: null,
    destination: 'settings',
  });
  if (picked.status === 'cancelled') return { status: 'cancelled' };
  if (
    picked.status === 'ok' &&
    'reference' in picked.value &&
    picked.value.kind === 'folder'
  )
    return controller.claimFolder(picked.value.reference, signal);
  throw clientError({
    code:
      picked.status === 'unavailable' && picked.reason === 'native_reconnecting'
        ? 'native_reconnecting'
        : 'folder_picker_requires_desktop',
  });
}
