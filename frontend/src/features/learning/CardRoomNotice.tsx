import { useOverlay } from '../../ui/overlays';
import { Button } from '../../ui/primitives';
import { clientError } from '../../api/errors';
import type { LearningSnapshot } from '../../api/types';
import type { LearningFields, LearningIO } from './learning-state';
import { plural, sizeWords } from './words';

type Room = NonNullable<LearningSnapshot['personal_model']['room']>;

/**
 * O112: training has waited two or more quiet-hours nights for room on the graphics card. Say so, with
 * what it needs and what the card had, and offer the next smaller model (one tap, then it asks first:
 * it downloads). Never a silent forever-wait.
 */
export function CardRoomNotice({
  io,
  room,
  model,
}: {
  io: LearningIO;
  room: Room;
  model: string;
}) {
  const overlay = useOverlay();
  const smaller = room.smaller;
  const switchTo = (opener: HTMLElement | null) => {
    if (!smaller) return;
    overlay.open({
      kind: 'alert',
      title: `Use ${smaller.name} instead?`,
      description: `Row-Bot downloads ${smaller.name} (${sizeWords(smaller.download_bytes)}) and trains it in quiet hours instead of ${model}.`,
      confirmLabel: `Use ${smaller.name}`,
      returnFocusTo: opener,
      onConfirm: () => {
        void io
          .run('use_smaller_model', {
            model: smaller.id as LearningFields['model'],
          })
          .then(
            (receipt) => overlay.notify(receipt.summary),
            (cause: unknown) =>
              overlay.notify(clientError(cause).message, 'danger'),
          );
      },
    });
  };
  return (
    <div
      className="learning-banner"
      data-tone="warning"
      role="status"
      aria-label="No room on the graphics card"
    >
      <p>
        <strong>{`No room on the graphics card for ${plural(room.nights, 'night')}. `}</strong>
        {`Training ${model} needs about ${Math.ceil(room.need_gb)} GB free on it; it had about ${Math.floor(room.free_gb)} GB (your display and other apps use the rest).`}
        {smaller
          ? ''
          : ' Close apps that use the graphics card before quiet hours, or train on Hugging Face instead.'}
      </p>
      {smaller && (
        <Button
          className="small"
          variant="primary"
          onClick={(event) => switchTo(event.currentTarget)}
        >
          {`Use ${smaller.name} instead…`}
        </Button>
      )}
    </div>
  );
}
