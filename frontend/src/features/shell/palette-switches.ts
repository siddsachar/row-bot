import type { ClientController } from '../../api/controller';
import type { SettingsSnapshot } from '../../api/types';
import type { SettingsPage } from '../settings/SettingsSnapshotPanels';

/**
 * Settings switches as ⌘K actions with their state (Phase 18): "turn on
 * developer tools" turns it on with the same reviewed `settings.update` the
 * switch on its Settings page sends. Only switches that page saves at once,
 * without asking first.
 */
export type PaletteSwitch = {
  id: string;
  label: string;
  /** "is" or "are", for "Developer tools are already on". */
  verb: 'is' | 'are';
  /** How people name it. */
  phrases: readonly string[];
  on: boolean;
  /** Its row in Settings. */
  href: string;
  page: SettingsPage;
  field: string;
};

type SwitchDefinition = Omit<PaletteSwitch, 'on'> & {
  read: (snapshot: SettingsSnapshot) => boolean | null | undefined;
};

const SWITCHES: readonly SwitchDefinition[] = [
  {
    id: 'developer',
    label: 'Developer tools',
    verb: 'are',
    phrases: ['developer tools', 'developer', 'dev tools', 'coding tools'],
    href: '/settings/tools#built-in-tools',
    page: 'utilities',
    field: 'developer.enabled',
    read: (snapshot) =>
      snapshot.utilities.availability === 'available'
        ? snapshot.utilities.items.find(
            (item) => item.utility_id === 'developer' && item.available,
          )?.enabled
        : null,
  },
  {
    id: 'shell',
    label: 'Shell access',
    verb: 'is',
    phrases: ['shell', 'shell access', 'run commands', 'command line'],
    href: '/settings/system#shell.enabled',
    page: 'system',
    field: 'shell.enabled',
    read: (snapshot) =>
      snapshot.system.availability === 'available'
        ? snapshot.system.shell.enabled
        : null,
  },
  {
    id: 'browser',
    label: 'Browser tool',
    verb: 'is',
    phrases: ['browser tool', 'browser', 'browse the web', 'web browsing'],
    href: '/settings/system#browser-computer-use',
    page: 'system',
    field: 'browser.enabled',
    read: (snapshot) =>
      snapshot.system.availability === 'available'
        ? snapshot.system.browser.enabled
        : null,
  },
  {
    id: 'dream-cycle',
    label: 'Dream Cycle',
    verb: 'is',
    phrases: ['dream cycle', 'dreaming', 'overnight memory'],
    href: '/settings/preferences#dream-cycle',
    page: 'preferences',
    field: 'dream_cycle.enabled',
    read: (snapshot) =>
      snapshot.preferences.availability === 'available'
        ? snapshot.preferences.dream_cycle.enabled
        : null,
  },
  {
    id: 'self-improvement',
    label: 'Learning new skills',
    verb: 'is',
    phrases: ['learn new skills', 'self improvement', 'learning skills'],
    href: '/settings/preferences#identity.self_improvement_enabled',
    page: 'preferences',
    field: 'identity.self_improvement_enabled',
    read: (snapshot) =>
      snapshot.preferences.availability === 'available'
        ? snapshot.preferences.identity.self_improvement_enabled
        : null,
  },
  {
    id: 'tracker',
    label: 'Tracking',
    verb: 'is',
    phrases: [
      'tracking',
      'track in chat',
      'habit tracker',
      'tracker',
      'habits',
    ],
    href: '/settings/tracker#tracker.enabled',
    page: 'tracker',
    field: 'enabled',
    read: (snapshot) =>
      snapshot.tracker.availability === 'available' &&
      snapshot.tracker.tool_available
        ? snapshot.tracker.enabled
        : null,
  },
];

/** Fired after ⌘K changed a setting, so an open Settings page reads it. */
export const SETTINGS_CHANGED = 'row-bot:settings-changed';

export type PaletteSwitchAction = PaletteSwitch & {
  set: (on: boolean) => void;
};

/** The switches this snapshot can show, with their state. */
export function settingsSwitches(
  snapshot: SettingsSnapshot,
  set: (target: PaletteSwitch, on: boolean) => void,
): PaletteSwitchAction[] {
  return SWITCHES.flatMap(({ read, ...definition }) => {
    const on = read(snapshot);
    if (on == null) return [];
    const target = { ...definition, on };
    return [{ ...target, set: (next: boolean) => set(target, next) }];
  });
}

/**
 * Save a switch the way its Settings page does: review at the current
 * settings revision, then run exactly the reviewed change.
 */
export async function saveSwitch(
  controller: ClientController,
  target: PaletteSwitch,
  on: boolean,
): Promise<'completed' | 'partial'> {
  const snapshot = await controller.settingsSnapshot();
  const request = {
    settings_revision: snapshot.revision,
    page: target.page,
    field: target.field,
    value: on,
  };
  const review = await controller.reviewSettingsMutation(request);
  if (
    review.operation !== 'settings.update' ||
    review.settings_revision !== request.settings_revision ||
    review.page !== request.page ||
    review.field !== request.field
  )
    throw { code: 'revision_conflict' };
  const receipt = await controller.executeSettingsMutation(
    request,
    review,
    crypto.randomUUID(),
  );
  if (receipt.status === 'rejected')
    throw { code: receipt.code ?? 'revision_conflict' };
  return receipt.status;
}
