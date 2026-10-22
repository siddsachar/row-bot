import { useEffect, useRef, useState } from 'react';
import { clientError } from '../../api/errors';
import type {
  LearningPersonalModel,
  LearningSnapshot,
  LearningTraining,
} from '../../api/types';
import { useNotify, useOverlay } from '../../ui/overlays';
import { Button } from '../../ui/primitives';
import type { LearningAction, LearningIO } from './learning-state';
import {
  examplesWords,
  plural,
  quietHoursWords,
  stepWords,
  timeLeftWords,
  TRAINING_FAILURES,
} from './words';

type Action = {
  label: string;
  action: LearningAction;
  primary?: boolean;
  confirm?: boolean;
};

/** What the person sees about the training run: words, progress and the way on. */
export type TrainingView = {
  title: string;
  detail?: string;
  /** Steps done of the plan; `null` while starting (no number yet). */
  progress?: { value: number; max: number } | null;
  actions: Action[];
  /** For the status region: changes only at milestones, never every step. */
  announce: string;
};

const WAITING_FOR: Record<string, string> = {
  chat: 'A chat is running.',
  workflow: 'A workflow is running.',
  indexing: 'Row-Bot is reading documents.',
  dream: 'Dream Cycle is running.',
  in_use: 'You’re using Row-Bot.',
  gpu_busy:
    'Another app is using the graphics card’s memory, such as a local model that’s still loaded.',
  memory_busy:
    'Other apps are using this computer’s memory (RAM and virtual memory). Closing some frees it.',
};

function name(training: LearningTraining) {
  return training.version ? `training v${training.version}` : 'training';
}

function capital(text: string) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * The training run in words (designs/learning-ux.md §5, §9): "Training v1 · step 340 of
 * 1,000 · about 40 min left · pauses when you use Row-Bot", "Pausing after this step…",
 * "Training stopped when Row-Bot closed. It can continue from step 600." Never a raw state.
 */
export function trainingView(
  training: LearningTraining,
  snapshot: Pick<LearningSnapshot, 'quiet_hours'> & {
    personal_model?: Pick<LearningPersonalModel, 'examples' | 'version'>;
  },
): TrainingView | null {
  const examples = snapshot.personal_model?.examples ?? null;
  const uses = examples?.examples
    ? ` · uses ${plural(examples.examples, 'example')}`
    : '';
  const run = name(training);
  const Run = capital(run);
  const steps = stepWords(training.step, training.steps_total);
  const progress =
    training.steps_total && training.steps_total > 0
      ? { value: training.step, max: training.steps_total }
      : null;
  const hours = quietHoursWords(
    snapshot.quiet_hours.window_start,
    snapshot.quiet_hours.window_end,
  );
  const from = training.checkpoint_step
    ? `It can continue from step ${training.checkpoint_step.toLocaleString()}.`
    : 'It starts again from the beginning.';
  const stop: Action = { label: 'Stop', action: 'stop_training' };
  const trainNow: Action[] = training.can_train_now
    ? [{ label: 'Train now', action: 'train_now', primary: true }]
    : [];
  const recovered =
    training.recovered === 'previous_checkpoint'
      ? 'Its newest saved step couldn’t be read, so it went back to the one before.'
      : training.recovered === 'start_over'
        ? 'No saved step could be read, so it started again from the beginning.'
        : '';
  switch (training.state) {
    case 'preparing':
      return {
        title: `Starting ${run}…`,
        detail: 'It pauses when you use Row-Bot.',
        progress: null,
        actions: [stop],
        announce: `Starting ${run}.`,
      };
    case 'running': {
      const parts = [`${Run}`, steps];
      if (training.seconds_left !== null && training.seconds_left !== undefined)
        parts.push(timeLeftWords(training.seconds_left));
      parts.push('pauses when you use Row-Bot');
      const share = progress
        ? Math.floor((progress.value / progress.max) * 10) * 10
        : 0;
      return {
        title: parts.join(' · '),
        detail: recovered || undefined,
        progress,
        actions: [stop],
        announce: `${Run}: ${share}% done.`,
      };
    }
    case 'pausing':
      return {
        title: 'Pausing after this step…',
        detail: `${Run} · ${steps}`,
        progress,
        actions: [stop],
        announce: 'Pausing training after this step.',
      };
    case 'waiting': {
      const card = training.runner.reason === 'gpu_busy';
      const memory = training.runner.reason === 'memory_busy';
      const title = card
        ? `${Run} starts when the graphics card is free.`
        : memory
          ? `${Run} starts when this computer has the memory free.`
          : `${Run} starts when Row-Bot is free.`;
      return {
        title,
        detail:
          WAITING_FOR[training.runner.reason ?? 'in_use'] ?? WAITING_FOR.in_use,
        actions: [{ label: 'Cancel', action: 'stop_training' }],
        announce: title,
      };
    }
    case 'paused': {
      const ask: Action[] = [
        { label: 'Continue', action: 'continue_training', primary: true },
        { label: 'Discard', action: 'discard_training', confirm: true },
      ];
      if (training.pause_reason === 'app_closed')
        return {
          title: `Training stopped when Row-Bot closed. ${from}`,
          detail: recovered || undefined,
          actions: ask,
          announce: 'Training stopped when Row-Bot closed.',
        };
      if (training.pause_reason === 'worker_stopped')
        return {
          title: `Training stopped unexpectedly. ${from}`,
          detail: recovered || undefined,
          actions: ask,
          announce: 'Training stopped unexpectedly.',
        };
      if (training.pause_reason === 'you')
        return {
          title: `You stopped ${run}. ${from}`,
          detail: recovered || undefined,
          actions: ask,
          announce: `You stopped ${run}.`,
        };
      const when =
        training.pause_reason === 'learning_paused' ||
        training.pause_reason === 'learning_off'
          ? 'It continues when you resume Learning.'
          : training.resumes === 'when_free'
            ? 'It continues when you’re done.'
            : `It continues tonight in quiet hours (${hours}).`;
      const title =
        training.pause_reason === 'local_gpu'
          ? `${Run} stopped at once so your chat could use the graphics card. It continues from step ${(training.checkpoint_step ?? 0).toLocaleString()}.`
          : training.pause_reason === 'learning_paused'
            ? `${Run} paused at ${steps} because Learning is paused.`
            : `${Run} paused at ${steps} while you use Row-Bot.`;
      return {
        title,
        detail: [when, recovered].filter(Boolean).join(' '),
        progress,
        actions: trainNow,
        announce: `${Run} paused.`,
      };
    }
    case 'failed': {
      const failure =
        TRAINING_FAILURES[training.failure ?? 'engine_error'] ??
        TRAINING_FAILURES.engine_error;
      return {
        title: failure.text,
        actions: [
          { label: failure.action, action: 'use_recommended', primary: true },
        ],
        announce: `${failure.text.split('. ')[0]}.`,
      };
    }
    case 'evaluating':
      // The version's own card says how it is being prepared and made ready.
      if (
        training.version &&
        snapshot.personal_model?.version?.version === training.version
      )
        return null;
      return {
        title: `Trained ${training.version ? `v${training.version}` : 'a new version'} · ${(training.steps_total ?? training.step).toLocaleString()} steps.`,
        detail:
          'Row-Bot prepares it to run next, and asks you before using it.',
        actions: [],
        announce: `Finished ${run}.`,
      };
    case 'planned': {
      const reason = training.runner.reason;
      const title =
        reason === 'train_off'
          ? `${Run} runs only when you choose Train now: Train during quiet hours is off.`
          : reason === 'after_dream'
            ? `${Run} runs tonight in quiet hours, after Dream Cycle${uses}.`
            : reason === 'now'
              ? `${Run} starts in a moment${uses}.`
              : reason === 'gpu_busy'
                ? `${Run} starts when the graphics card is free${uses}.`
                : reason === 'memory_busy'
                  ? `${Run} starts when this computer has the memory free${uses}.`
                  : `${Run} runs tonight in quiet hours (${hours})${uses}.`;
      return {
        title,
        detail:
          reason === 'gpu_busy' || reason === 'memory_busy'
            ? `${WAITING_FOR[reason]} It pauses when you use Row-Bot.`
            : 'It pauses when you use Row-Bot.',
        actions: trainNow,
        announce: `${Run} is planned.`,
      };
    }
    case 'none': {
      const reason = training.runner.reason;
      if (reason === 'no_data' && examples)
        return {
          title: `Needs more examples · ${examplesWords(examples.examples, examples.needed)}`,
          detail: `Training starts once Row-Bot has ${plural(examples.needed, 'example')} of your work it can learn from. Keep using Row-Bot; Teach on a reply that went wrong helps too.`,
          actions: [],
          announce: '',
        };
      if (reason === 'no_data')
        return {
          title:
            'Nothing to train yet. Training starts once Row-Bot has enough examples from your work.',
          actions: [],
          announce: '',
        };
      if (reason === 'planning')
        return {
          title: examples
            ? `Getting ${plural(examples.examples, 'example')} ready to train…`
            : 'Getting your examples ready to train…',
          detail:
            'Then it trains tonight in quiet hours, or sooner with Train now.',
          actions: [],
          announce: '',
        };
      if (reason === 'up_to_date') {
        const latest = snapshot.personal_model?.version;
        return {
          title: latest
            ? `v${latest.version} has learned from everything so far.`
            : 'Your personal model has learned from everything so far.',
          detail: `The next version trains once there are enough new examples${examples ? ` (${examples.new_examples.toLocaleString()} so far)` : ''}.`,
          actions: [],
          announce: '',
        };
      }
      return null;
    }
    default:
      return null;
  }
}

/** The training run on the Personal model view, with Stop, Continue, Discard and Train now. */
export function TrainingProgress({
  io,
  snapshot,
}: {
  io: LearningIO;
  snapshot: LearningSnapshot;
}) {
  const notify = useNotify();
  const overlay = useOverlay();
  const [busy, setBusy] = useState(false);
  const training = snapshot.training;
  const view = trainingView(training, snapshot);
  // The status region speaks only when the run changes after the view opened (a new state, or
  // another tenth of the way), never on every step and not for what is already on screen.
  const [said, setSaid] = useState('');
  const announced = useRef<string | null>(null);
  const announce = view?.announce ?? '';
  useEffect(() => {
    if (announced.current === null) {
      announced.current = announce;
      return;
    }
    if (announce && announce !== announced.current) {
      announced.current = announce;
      setSaid(announce);
    }
  }, [announce]);
  if (!view) return null;

  async function run(action: LearningAction) {
    setBusy(true);
    try {
      const receipt = await io.run(action, { job_id: training.job_id ?? '' });
      notify(receipt.summary);
    } catch (cause) {
      notify(clientError(cause).message, 'danger');
    } finally {
      setBusy(false);
    }
  }

  function choose(item: Action, opener: HTMLElement | null) {
    if (!item.confirm) {
      void run(item.action);
      return;
    }
    overlay.open({
      kind: 'alert',
      title: 'Discard this training run?',
      description: `What it trained so far (${stepWords(training.checkpoint_step ?? training.step, training.steps_total)}) is deleted. Your learning data and earlier versions are kept.`,
      confirmLabel: 'Discard',
      returnFocusTo: opener,
      onConfirm: () => void run(item.action),
    });
  }

  return (
    <section className="monitor-section" aria-label="Training">
      <header className="monitor-section-head">
        <h3>Training</h3>
      </header>
      <div className="learning-training" data-state={training.state}>
        <div className="learning-training-head">
          <div className="learning-training-words">
            <p className="learning-training-title">{view.title}</p>
            {view.detail && <p className="learning-muted">{view.detail}</p>}
          </div>
          {view.actions.length > 0 && (
            <div className="learning-actions-inline">
              {view.actions.map((item) => (
                <Button
                  key={item.action}
                  variant={item.primary ? 'primary' : undefined}
                  className="small"
                  disabled={busy}
                  onClick={(event) => choose(item, event.currentTarget)}
                >
                  {item.label}
                </Button>
              ))}
            </div>
          )}
        </div>
        {view.progress !== undefined && (
          <progress
            aria-label="Training progress"
            max={view.progress?.max}
            value={view.progress?.value}
          />
        )}
      </div>
      <p className="visually-hidden" role="status" aria-live="polite">
        {said}
      </p>
    </section>
  );
}
