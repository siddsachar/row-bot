import type { ReactNode } from 'react';
import type { SettingsSnapshot } from '../../api/types';
import {
  PreferencesSnapshotPanel,
  type SettingsMutationIO,
} from './SettingsSnapshotPanels';

/**
 * General › Preferences, and the Updates and Data pages that share the
 * preferences snapshot. Appearance (local to this device) has its own page.
 */
export default function Preferences({
  snapshot,
  mutation,
  snapshotState,
  showUpdateControls = false,
  part = 'preferences',
  skillsMoved = false,
}: {
  snapshot?: SettingsSnapshot['preferences'];
  mutation?: SettingsMutationIO | null;
  snapshotState?: ReactNode;
  showUpdateControls?: boolean;
  part?: 'preferences' | 'updates' | 'data';
  /** "Learn new skills" moved to Settings › Learning (D29). */
  skillsMoved?: boolean;
}) {
  return (
    <div className={`stack settings-preferences settings-${part}`}>
      {snapshot && mutation ? (
        <PreferencesSnapshotPanel
          snapshot={snapshot}
          mutation={mutation}
          showUpdateControls={showUpdateControls}
          part={part}
          skillsMoved={skillsMoved}
        />
      ) : (
        snapshotState
      )}
    </div>
  );
}
