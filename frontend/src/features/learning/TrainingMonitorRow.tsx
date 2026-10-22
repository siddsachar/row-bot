import { GraduationCap, Square } from 'lucide-react';
import type { LearningTraining } from '../../api/types';
import { Button, EntityRow, type Tone } from '../../ui/primitives';
import { stepWords, timeLeftWords } from './words';

/**
 * Monitor's row for the personal model's training run (designs/learning-ux.md, "Where it
 * lives"): "Personal model v1 · Training · step 340 of 1,000 · about 40 min left · pauses
 * when you use Row-Bot", with Stop. A short title, so it fits a phone's row.
 */
export function trainingRowWords(training: LearningTraining): {
  title: string;
  /** For Stop's accessible name: "training personal model v1". */
  what: string;
  status: { tone: Tone; label: string };
  meta: string;
} {
  const title = training.version
    ? `Personal model v${training.version}`
    : 'Personal model';
  const steps = stepWords(training.step, training.steps_total);
  const status =
    training.state === 'pausing'
      ? { tone: 'neutral' as Tone, label: 'Pausing after this step…' }
      : training.state === 'preparing'
        ? { tone: 'neutral' as Tone, label: 'Starting to train…' }
        : { tone: 'accent' as Tone, label: `Training · ${steps}` };
  const meta: string[] = [];
  if (training.state === 'pausing') meta.push(`Training · ${steps}`);
  if (
    training.state === 'running' &&
    training.seconds_left !== null &&
    training.seconds_left !== undefined
  )
    meta.push(timeLeftWords(training.seconds_left));
  if (training.state !== 'pausing') meta.push('pauses when you use Row-Bot');
  return {
    title,
    what: `training ${title.charAt(0).toLowerCase()}${title.slice(1)}`,
    status,
    meta: meta.join(' · '),
  };
}

export function TrainingMonitorRow({
  training,
  busy,
  onStop,
  onOpen,
}: {
  training: LearningTraining;
  busy: boolean;
  onStop: () => void;
  onOpen?: () => void;
}) {
  const words = trainingRowWords(training);
  return (
    <EntityRow
      className="process-row"
      title={words.title}
      icon={<GraduationCap size={16} />}
      status={words.status}
      meta={words.meta}
      action={
        <Button
          className="small"
          aria-label={`Stop ${words.what}`}
          disabled={busy}
          onClick={onStop}
        >
          <Square size={12} aria-hidden />
          Stop
        </Button>
      }
      menuLabel={`More for ${words.title}`}
      menu={onOpen ? [{ label: 'Open Personal model', onSelect: onOpen }] : []}
    />
  );
}
