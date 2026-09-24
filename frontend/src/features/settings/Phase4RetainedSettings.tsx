import type { SettingsSnapshot } from '../../api/types';
import type { ClientPlatform } from '../../platform';
import {
  AccountsSnapshotPanel,
  SystemSnapshotPanel,
  TrackerSnapshotPanel,
  UtilitiesSnapshotPanel,
  VoiceSnapshotPanel,
  type SettingsMutationIO,
  type SettingsFolderPicker,
} from './SettingsSnapshotPanels';

export type Phase4RetainedSetting =
  'voice' | 'accounts' | 'tracker' | 'utilities' | 'system';

export default function Phase4RetainedSettings({
  setting,
  snapshot,
  mutation,
  selectedConversationId,
  pickFolder,
  showAccountActions = false,
  writeClipboard,
}: {
  setting: Phase4RetainedSetting;
  snapshot: SettingsSnapshot;
  mutation: SettingsMutationIO;
  selectedConversationId: string | null;
  pickFolder?: SettingsFolderPicker;
  showAccountActions?: boolean;
  writeClipboard?: ClientPlatform['writeClipboard'];
}) {
  if (setting === 'voice')
    return (
      <VoiceSnapshotPanel
        snapshot={snapshot.voice}
        conversationId={selectedConversationId}
        mutation={mutation}
      />
    );
  if (setting === 'accounts')
    return (
      <AccountsSnapshotPanel
        snapshot={snapshot.accounts}
        mutation={mutation}
        showActions={showAccountActions}
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
    />
  );
}
