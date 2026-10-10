import type { ReactNode } from 'react';
import type { SettingsSnapshot } from '../../api/types';
import type { ClientPlatform } from '../../platform';
import {
  SystemSnapshotPanel,
  TrackerSnapshotPanel,
  UtilitiesSnapshotPanel,
  VoiceSnapshotPanel,
  type SettingsMutationIO,
  type SettingsFolderPicker,
} from './SettingsSnapshotPanels';

export type Phase4RetainedSetting =
  'voice' | 'tracker' | 'utilities' | 'system' | 'access';

export default function Phase4RetainedSettings({
  setting,
  snapshot,
  mutation,
  selectedConversationId,
  pickFolder,
  writeClipboard,
  accessNetwork,
}: {
  setting: Phase4RetainedSetting;
  snapshot: SettingsSnapshot;
  mutation: SettingsMutationIO;
  selectedConversationId: string | null;
  pickFolder?: SettingsFolderPicker;
  writeClipboard?: ClientPlatform['writeClipboard'];
  /** Access › Advanced › Network: the live network controls. */
  accessNetwork?: ReactNode;
}) {
  if (setting === 'voice')
    return (
      <VoiceSnapshotPanel
        snapshot={snapshot.voice}
        conversationId={selectedConversationId}
        mutation={mutation}
      />
    );
  if (setting === 'tracker')
    return (
      <TrackerSnapshotPanel snapshot={snapshot.tracker} mutation={mutation} />
    );
  if (setting === 'utilities')
    return (
      <UtilitiesSnapshotPanel
        snapshot={snapshot.utilities}
        mutation={mutation}
      />
    );
  return (
    <SystemSnapshotPanel
      snapshot={snapshot.system}
      mutation={mutation}
      pickFolder={pickFolder}
      writeClipboard={writeClipboard}
      part={setting === 'access' ? 'access' : 'system'}
      network={accessNetwork}
    />
  );
}
