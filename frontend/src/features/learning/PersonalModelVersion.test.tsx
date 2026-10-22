import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import type { LearningPersonalModel } from '../../api/types';
import { createFakePlatform } from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import {
  RunsInSheet,
  runsInWords,
  VersionCard,
  versionFileWords,
  versionView,
} from './PersonalModelVersion';
import {
  fakeIO,
  installedModel,
  learningSnapshot,
  versionState,
} from './test-data';

const RUNTIMES: LearningPersonalModel['runtimes'] = [
  { id: 'ollama', name: 'Ollama', automated: true, installed: true },
  { id: 'lmstudio', name: 'LM Studio', automated: false, installed: null },
  {
    id: 'llama_cpp',
    name: 'llama.cpp server',
    automated: false,
    installed: null,
  },
];

function card(model: LearningPersonalModel) {
  const io = fakeIO(learningSnapshot({ state: 'on', personal_model: model }));
  const opened: string[] = [];
  render(
    <RuntimeContext.Provider
      value={{ controller: {} as never, platform: createFakePlatform() }}
    >
      <OverlayProvider>
        <VersionCard
          io={io}
          model={model}
          onRunsIn={() => opened.push('runs_in')}
        />
      </OverlayProvider>
    </RuntimeContext.Provider>,
  );
  return { io, opened };
}

it('says a ready version waits for its comparison and that the main model still answers', () => {
  const view = versionView(versionState());
  expect(view.title).toBe('v1 is ready to compare');
  expect(view.detail).toBe(
    'Trained on 154 examples of your work · runs in Ollama.',
  );
  expect(view.note).toBe(
    'Row-Bot compares it with your main model before asking you to use it. Until then your main model answers everything.',
  );
  expect(view.actions).toEqual([{ label: 'Compare now', kind: 'compare' }]);
});

it('follows the export step by step, then the checks in the runtime', () => {
  expect(
    versionView(versionState({ phase: 'exporting', step: 'convert' })),
  ).toMatchObject({
    title: 'Preparing v1 to run on this computer…',
    detail: expect.stringContaining('(step 2 of 3)'),
  });
  expect(
    versionView(versionState({ phase: 'making_ready', runtime: 'ollama' })),
  ).toMatchObject({
    title: 'Making v1 ready in Ollama…',
    detail:
      'Loading it and checking it can answer and finish a task that uses a tool.',
  });
});

it('says a version that couldn’t start leaves the main model in use, with a way out', async () => {
  const model = installedModel({
    runtime: 'lmstudio',
    runtimes: RUNTIMES,
    version: versionState({
      phase: 'runtime_failed',
      runtime: 'lmstudio',
      failure: 'probe_failed',
    }),
  });
  const { io } = card(model);

  expect(
    screen.getByText(
      'v1 couldn’t start in LM Studio. Your main model is still in use.',
    ),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Try Ollama' }));
  expect(io.run).toHaveBeenCalledWith('choose_runtime', { runtime: 'ollama' });
  await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
  expect(io.run).toHaveBeenLastCalledWith('make_ready');
});

it('offers to change where it runs when Ollama itself couldn’t run it', async () => {
  const { opened } = card(
    installedModel({
      runtimes: RUNTIMES,
      version: versionState({
        phase: 'runtime_failed',
        failure: 'tool_turn_failed',
      }),
    }),
  );
  await userEvent.click(
    screen.getByRole('button', { name: 'Change where it runs' }),
  );
  expect(opened).toEqual(['runs_in']);
});

it('shows the one command to load it where the person starts the runtime, and copies it', async () => {
  const command =
    'llama-server -m "C:\\v1\\row-bot-personal-v1-Q8_0.gguf" -a row-bot-personal-v1 --host 127.0.0.1 --port 8181';
  card(
    installedModel({
      runtime: 'llama_cpp',
      runtimes: RUNTIMES,
      version: versionState({
        phase: 'waiting_for_you',
        runtime: 'llama_cpp',
        failure: 'not_running',
        command,
      }),
    }),
  );
  expect(screen.getByText('Load v1 in llama.cpp server')).toBeInTheDocument();
  expect(screen.getByText(command)).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Check again' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Copy command' }),
  ).toHaveAccessibleDescription(command);
});

it('says how much disk an export needs when it ran out', () => {
  const view = versionView(
    versionState({
      phase: 'export_failed',
      failure: 'not_enough_disk',
      needs_bytes: 9_400_000_000,
      free_bytes: 2_100_000_000,
    }),
  );
  expect(view.title).toMatch(/^v1 couldn’t be prepared to run\./);
  expect(view.detail).toBe(
    'It needs 9.4\u00a0GB free; 2.1\u00a0GB free. What it learned is kept.',
  );
  expect(view.actions).toEqual([
    { label: 'Try again', kind: 'make_ready', primary: true },
  ]);
});

it('lets the person choose where personal models run', async () => {
  const model = installedModel({ runtime: 'ollama', runtimes: RUNTIMES });
  const io = fakeIO(learningSnapshot({ state: 'on', personal_model: model }));
  render(
    <OverlayProvider>
      <RunsInSheet open onOpenChange={() => undefined} io={io} model={model} />
    </OverlayProvider>,
  );
  const sheet = screen.getByRole('dialog', {
    name: 'Where your personal model runs',
  });
  expect(
    within(sheet).getByRole('radio', { name: /Ollama · recommended/ }),
  ).toBeChecked();
  const use = within(sheet).getByRole('button', { name: 'Use Ollama' });
  expect(use).toBeDisabled();
  await userEvent.click(
    within(sheet).getByRole('radio', { name: /LM Studio/ }),
  );
  await userEvent.click(
    within(sheet).getByRole('button', { name: 'Use LM Studio' }),
  );
  expect(io.run).toHaveBeenCalledWith('choose_runtime', {
    runtime: 'lmstudio',
  });
});

it('marks the runtime a version passed its check in, and says its size without its file name', () => {
  const ready = installedModel({ runtime: 'ollama', version: versionState() });
  expect(runsInWords(ready)).toBe('Ollama ✓');
  expect(
    runsInWords(
      installedModel({ runtime: 'lmstudio', version: versionState() }),
    ),
  ).toBe('LM Studio');
  // O54: main screens say "v1 · 2 GB"; file names belong to Export… and Experiment details.
  expect(versionFileWords(versionState())).toBe('v1 · 2\u00a0GB');
});
