import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import type { LearningPersonalModel, LearningTraining } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import {
  fakeIO,
  installedModel,
  learningSnapshot,
  runningRun,
  trainingRun,
  versionState,
} from './test-data';
import { TrainingProgress } from './TrainingProgress';
import { timeLeftWords } from './words';

/** The card's own words (the status region repeats some of them for screen readers). */
const SHOWN = { ignore: '[role="status"], script, style' };

function show(training: LearningTraining) {
  const snapshot = learningSnapshot({ state: 'on', training });
  const io = fakeIO(snapshot);
  const view = render(
    <OverlayProvider>
      <TrainingProgress io={io} snapshot={snapshot} />
    </OverlayProvider>,
  );
  return { io, view, snapshot };
}

it('shows a running training with its step, time left, the pause promise and Stop', async () => {
  const { io } = show(runningRun());
  expect(
    screen.getByText(
      'Training v1 · step 340 of 1,000 · about 40 min left · pauses when you use Row-Bot',
    ),
  ).toBeInTheDocument();
  const bar = screen.getByRole('progressbar', { name: 'Training progress' });
  expect(bar).toHaveAttribute('value', '340');
  expect(bar).toHaveAttribute('max', '1000');
  await userEvent.click(screen.getByRole('button', { name: 'Stop' }));
  expect(io.run).toHaveBeenCalledWith('stop_training', { job_id: 'job_1' });
});

it('says it is pausing until the worker has really paused', () => {
  show(runningRun({ state: 'pausing', seconds_left: null }));
  expect(screen.getByText('Pausing after this step…')).toBeInTheDocument();
  expect(
    screen.getByText('Training v1 · step 340 of 1,000'),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled();
});

it('shows a run paused while the person uses Row-Bot, and when it continues', async () => {
  const { io } = show(
    runningRun({
      state: 'paused',
      pause_reason: 'in_use',
      resumes: 'when_free',
      seconds_left: null,
      can_train_now: true,
    }),
  );
  expect(
    screen.getByText(
      'Training v1 paused at step 340 of 1,000 while you use Row-Bot.',
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByText('It continues when you’re done.'),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Train now' }));
  expect(io.run).toHaveBeenCalledWith('train_now', { job_id: 'job_1' });
});

it('says a chat on a local model stopped training at once, and where it continues from', () => {
  show(
    runningRun({
      state: 'paused',
      pause_reason: 'local_gpu',
      resumes: 'quiet_hours',
      step: 330,
      checkpoint_step: 330,
    }),
  );
  expect(
    screen.getByText(
      'Training v1 stopped at once so your chat could use the graphics card. It continues from step 330.',
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/^It continues tonight in quiet hours/),
  ).toBeInTheDocument();
});

it('offers Continue or Discard after Row-Bot closed during training, and confirms Discard', async () => {
  const { io } = show(
    runningRun({
      state: 'paused',
      pause_reason: 'app_closed',
      resumes: 'ask',
      step: 600,
      checkpoint_step: 600,
      can_train_now: true,
    }),
  );
  expect(
    screen.getByText(
      'Training stopped when Row-Bot closed. It can continue from step 600.',
    ),
  ).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Train now' })).toBeNull();
  await userEvent.click(screen.getByRole('button', { name: 'Continue' }));
  expect(io.run).toHaveBeenLastCalledWith('continue_training', {
    job_id: 'job_1',
  });
  await userEvent.click(screen.getByRole('button', { name: 'Discard' }));
  const confirm = screen.getByRole('alertdialog', {
    name: 'Discard this training run?',
  });
  expect(confirm).toHaveTextContent(
    'What it trained so far (step 600 of 1,000) is deleted.',
  );
  await userEvent.click(
    within(confirm).getByRole('button', { name: 'Discard' }),
  );
  expect(io.run).toHaveBeenLastCalledWith('discard_training', {
    job_id: 'job_1',
  });
});

it('words a stop by the person, an unexpected stop and a fallback to an earlier step', () => {
  const first = show(
    runningRun({
      state: 'paused',
      pause_reason: 'you',
      resumes: 'ask',
      checkpoint_step: 330,
    }),
  );
  expect(
    screen.getByText('You stopped training v1. It can continue from step 330.'),
  ).toBeInTheDocument();
  first.view.unmount();
  show(
    runningRun({
      state: 'paused',
      pause_reason: 'worker_stopped',
      resumes: 'ask',
      checkpoint_step: 290,
      recovered: 'previous_checkpoint',
    }),
  );
  expect(
    screen.getByText(
      'Training stopped unexpectedly. It can continue from step 290.',
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      'Its newest saved step couldn’t be read, so it went back to the one before.',
    ),
  ).toBeInTheDocument();
});

it('shows running out of GPU memory in plain words with a way out', async () => {
  const { io } = show(
    runningRun({
      state: 'failed',
      failure: 'out_of_gpu_memory',
      runner: { state: 'failed', reason: 'out_of_gpu_memory' },
    }),
  );
  expect(
    screen.getByText(
      'Training stopped: not enough GPU memory. A smaller starting model or shorter tasks fit. Earlier versions are kept.',
      SHOWN,
    ),
  ).toBeInTheDocument();
  await userEvent.click(
    screen.getByRole('button', { name: 'Use recommended settings' }),
  );
  expect(io.run).toHaveBeenCalledWith('use_recommended', { job_id: 'job_1' });
});

it('shows a planned run with Train now, and says when there is nothing to train', () => {
  const first = show(
    trainingRun({
      state: 'planned',
      job_id: 'job_2',
      version: 2,
      steps_total: 1000,
      can_train_now: true,
      runner: { state: 'scheduled', reason: 'quiet_hours' },
    }),
  );
  expect(
    screen.getByText(/^Training v2 runs tonight in quiet hours \(/, SHOWN),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Train now' })).toBeInTheDocument();
  first.view.unmount();
  show(trainingRun({ runner: { state: 'nothing_to_do', reason: 'no_data' } }));
  expect(
    screen.getByText(
      'Nothing to train yet. Training starts once Row-Bot has enough examples from your work.',
    ),
  ).toBeInTheDocument();
  expect(screen.queryByRole('button')).toBeNull();
});

function withModel(
  training: LearningTraining,
  model: Partial<LearningPersonalModel>,
) {
  const snapshot = learningSnapshot({
    state: 'on',
    training,
    personal_model: installedModel(model),
  });
  render(
    <OverlayProvider>
      <TrainingProgress io={fakeIO(snapshot)} snapshot={snapshot} />
    </OverlayProvider>,
  );
}

it('says how many more examples training needs, then that it is getting them ready', () => {
  withModel(
    trainingRun({ runner: { state: 'nothing_to_do', reason: 'no_data' } }),
    {
      examples: { examples: 37, needed: 100, new_examples: 37, left_out: 4 },
    },
  );
  expect(
    screen.getByText(/^Needs more examples · 37 of 100/, SHOWN),
  ).toBeInTheDocument();
  cleanup();
  withModel(
    trainingRun({ runner: { state: 'nothing_to_do', reason: 'planning' } }),
    {
      examples: { examples: 154, needed: 100, new_examples: 154, left_out: 11 },
    },
  );
  expect(
    screen.getByText('Getting 154 examples ready to train…', SHOWN),
  ).toBeInTheDocument();
});

it('says a version has learned from everything so far', () => {
  withModel(
    trainingRun({ runner: { state: 'trained', reason: 'up_to_date' } }),
    {
      examples: { examples: 160, needed: 100, new_examples: 6, left_out: 0 },
      version: versionState(),
    },
  );
  expect(
    screen.getByText('v1 has learned from everything so far.', SHOWN),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      'The next version trains once there are enough new examples (6 so far).',
      SHOWN,
    ),
  ).toBeInTheDocument();
});

it('waits for the graphics card while another app holds its memory', () => {
  withModel(
    trainingRun({
      state: 'planned',
      job_id: 'job_1',
      version: 1,
      steps_total: 39,
      can_train_now: true,
      runner: { state: 'waiting', reason: 'gpu_busy' },
    }),
    {
      examples: { examples: 154, needed: 100, new_examples: 154, left_out: 11 },
    },
  );
  expect(
    screen.getByText(
      'Training v1 starts when the graphics card is free · uses 154 examples.',
      SHOWN,
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/^Another app is using the graphics card’s memory/, SHOWN),
  ).toBeInTheDocument();
});

it('waits for the computer’s memory while other apps hold it (O105)', () => {
  withModel(
    trainingRun({
      state: 'planned',
      job_id: 'job_1',
      version: 1,
      steps_total: 39,
      can_train_now: true,
      runner: { state: 'waiting', reason: 'memory_busy' },
    }),
    {
      examples: { examples: 154, needed: 100, new_examples: 154, left_out: 11 },
    },
  );
  expect(
    screen.getByText(
      'Training v1 starts when this computer has the memory free · uses 154 examples.',
      SHOWN,
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      'Other apps are using this computer’s memory (RAM and virtual memory). Closing some frees it. It pauses when you use Row-Bot.',
      SHOWN,
    ),
  ).toBeInTheDocument();
});

it('leaves a finished run to its version’s card', () => {
  withModel(
    trainingRun({
      state: 'evaluating',
      job_id: 'job_1',
      version: 1,
      step: 39,
      steps_total: 39,
      runner: { state: 'trained', reason: null },
    }),
    { version: versionState({ phase: 'exporting', step: 'merge' }) },
  );
  expect(screen.queryByRole('region', { name: 'Training' })).toBeNull();
});

it('shows nothing while there is no training engine', () => {
  show(trainingRun());
  expect(screen.queryByRole('region', { name: 'Training' })).toBeNull();
});

it('announces changes through the status region at milestones, never every step', () => {
  const snapshot = learningSnapshot({ state: 'on', training: runningRun() });
  const io = fakeIO(snapshot);
  const at = (training: LearningTraining) => (
    <OverlayProvider>
      <TrainingProgress
        io={io}
        snapshot={learningSnapshot({ state: 'on', training })}
      />
    </OverlayProvider>
  );
  const { rerender } = render(at(runningRun()));
  const region = within(screen.getByRole('region', { name: 'Training' }));
  // What is on screen when the view opens isn't read out again.
  expect(region.getByRole('status')).toHaveTextContent('');
  rerender(at(runningRun({ step: 345 })));
  expect(region.getByRole('status')).toHaveTextContent('');
  rerender(at(runningRun({ step: 410 })));
  expect(region.getByRole('status')).toHaveTextContent(
    'Training v1: 40% done.',
  );
  rerender(at(runningRun({ step: 410, state: 'pausing' })));
  expect(region.getByRole('status')).toHaveTextContent(
    'Pausing training after this step.',
  );
});

it('words the time left in minutes and hours', () => {
  expect(timeLeftWords(20)).toBe('less than a minute left');
  expect(timeLeftWords(2400)).toBe('about 40 min left');
  expect(timeLeftWords(6000)).toBe('about 1 h 40 min left');
  expect(timeLeftWords(7200)).toBe('about 2 h left');
});

it('says plainly while Hugging Face waits for a free card, and that cancelling then costs nothing', () => {
  show(
    trainingRun({
      state: 'running',
      job_id: 'job_9',
      version: 24,
      where: 'hugging_face',
      remote: {
        phase: 'running',
        stage: 'setting_up',
        cost_usd: 0,
        minutes: 0,
        lost: false,
        flavor: 'l40sx1',
        waiting_for_gpu: true,
        gpu_label: '1x Nvidia L40S',
        cleaned: false,
        cleanup_failed: false,
        url: null,
      },
    }),
  );

  expect(
    screen.getByText(
      'Hugging Face is waiting for a free 1x Nvidia L40S. No charge while it waits.',
      SHOWN,
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      'Training v24 starts as soon as one is free; cancelling now costs nothing.',
      SHOWN,
    ),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
});
