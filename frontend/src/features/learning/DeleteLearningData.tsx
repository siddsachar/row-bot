import { useCallback } from 'react';
import { clientError } from '../../api/errors';
import { useOverlay } from '../../ui/overlays';
import type { LearningIO } from './learning-state';
import { sizeWords } from './words';

/** What "Delete learning data…" removes, and what it keeps (designs/learning-ux.md §8, O1). */
export function DeleteLearningDataDetails({ bytes }: { bytes: number }) {
  return (
    <div className="learning-delete-details">
      <p>This deletes, from this computer:</p>
      <ul>
        <li>
          everything Learning recorded from your chats, workflows and goals;
        </li>
        <li>what you taught Row-Bot;</li>
        <li>its test results and the tasks it tests with.</li>
      </ul>
      <p>
        {sizeWords(bytes)} in all. Learning’s settings are kept, and chats you
        switched off stay switched off.
      </p>
    </div>
  );
}

/**
 * Open the confirm for deleting learning data; it says what goes and runs the
 * command once confirmed. The personal-model versions trained on the data are
 * listed once there are any (none can exist before L2).
 */
export function useDeleteLearningData(
  io: LearningIO | null,
  onDeleted?: () => void,
) {
  const overlay = useOverlay();
  return useCallback(
    (opener?: HTMLElement | null) => {
      if (!io?.snapshot) return;
      overlay.open({
        kind: 'alert',
        title: 'Delete learning data?',
        description: 'This can’t be undone.',
        content: <DeleteLearningDataDetails bytes={io.snapshot.data.bytes} />,
        confirmLabel: 'Delete learning data',
        returnFocusTo: opener ?? null,
        onConfirm: () => {
          void io.run('delete_data').then(
            (receipt) => {
              overlay.notify(receipt.summary);
              onDeleted?.();
            },
            (cause: unknown) =>
              overlay.notify(clientError(cause).message, 'danger'),
          );
        },
      });
    },
    [io, onDeleted, overlay],
  );
}
