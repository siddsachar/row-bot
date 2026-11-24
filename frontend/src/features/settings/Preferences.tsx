import { useState, type ReactNode } from 'react';
import type { SettingsSnapshot } from '../../api/types';
import MemoryReviewDialog from '../knowledge/MemoryReviewDialog';
import type { MemoryReviewIO } from '../knowledge/memory-review-io';
import { usePhone } from '../../ui/on-your-computer';
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
  host = null,
  reviewIO,
}: {
  snapshot?: SettingsSnapshot['preferences'];
  mutation?: SettingsMutationIO | null;
  snapshotState?: ReactNode;
  showUpdateControls?: boolean;
  part?: 'preferences' | 'updates' | 'data';
  /** The hosting company that runs this Row-Bot (H13), when Row-Bot knows it. */
  host?: SettingsSnapshot['system']['hosted_by'];
  /** Review memory, offered beside the Dream Cycle's overnight tidy-up. */
  reviewIO?: MemoryReviewIO;
}) {
  const [reviewing, setReviewing] = useState(false);
  // Review memory is clean-up, which is on the computer (B4, D12 A).
  const review = usePhone() ? undefined : reviewIO;
  return (
    <div className={`stack settings-preferences settings-${part}`}>
      {snapshot && mutation ? (
        <PreferencesSnapshotPanel
          snapshot={snapshot}
          mutation={mutation}
          showUpdateControls={showUpdateControls}
          part={part}
          host={host}
          onReviewMemory={review ? () => setReviewing(true) : undefined}
        />
      ) : (
        snapshotState
      )}
      {review && (
        <MemoryReviewDialog
          open={reviewing}
          onOpenChange={setReviewing}
          io={review}
        />
      )}
    </div>
  );
}
