import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import type {
  LearningCheckedModel,
  LearningOpenModel,
  LearningPersonalModel,
} from '../../api/types';
import { OtherModels } from './OtherModels';
import { fakeIO, installedModel, learningSnapshot } from './test-data';

function open(overrides: Partial<LearningOpenModel>): LearningOpenModel {
  return {
    id: 'hf:Qwen/Qwen3-14B',
    repo: 'Qwen/Qwen3-14B',
    name: 'Qwen3-14B',
    family: 'Qwen',
    params_b: 14.8,
    licence: 'Apache 2.0',
    licence_kind: 'permissive',
    licence_url: 'https://huggingface.co/Qwen/Qwen3-14B',
    terms: [],
    downloads: 11_000_000,
    unsupported: null,
    runs_here: true,
    runs_here_gb: 11.8,
    ...overrides,
  };
}

const LLAMA = open({
  id: 'hf:unsloth/Llama-3.1-8B-Instruct',
  repo: 'unsloth/Llama-3.1-8B-Instruct',
  name: 'Llama-3.1-8B-Instruct',
  family: 'Llama',
  params_b: 8,
  licence: 'Llama Community License',
  licence_kind: 'terms',
  terms: ['You may use and change it, also commercially.'],
});

const RESULTS = [
  open({}),
  LLAMA,
  open({
    id: 'hf:microsoft/Phi-4-mini-instruct',
    name: 'Phi-4-mini-instruct',
    unsupported:
      'Phi-4-mini-instruct packs its attention layers in a way Row-Bot’s training doesn’t adapt yet.',
  }),
  open({
    id: 'hf:meta-llama/Llama-3.1-8B-Instruct',
    repo: 'meta-llama/Llama-3.1-8B-Instruct',
    name: 'Llama-3.1-8B-Instruct (Meta)',
    licence_kind: 'gated',
  }),
  open({
    id: 'hf:Qwen/Qwen3-235B',
    name: 'Qwen3-235B',
    runs_here: false,
    runs_here_gb: 140,
  }),
];

function checked(
  overrides: Partial<LearningCheckedModel> = {},
): LearningCheckedModel {
  return {
    id: 'hf:Qwen/Qwen3-14B',
    repo: 'Qwen/Qwen3-14B',
    name: 'Qwen3-14B',
    family: 'Qwen',
    params_b: 14.8,
    licence: 'Apache 2.0',
    licence_kind: 'permissive',
    licence_url: 'https://huggingface.co/Qwen/Qwen3-14B',
    terms: [],
    licence_agreed: true,
    state: 'passed',
    stage: null,
    failure: null,
    items: [
      {
        id: 'fits',
        state: 'passed',
        words: 'Trains on this card: peak 21.2 GB of the 25.5 GB it may use.',
      },
      {
        id: 'steps',
        state: 'passed',
        words: 'Loss 2.10 → 1.60 over 4 steps; gradients finite.',
      },
      {
        id: 'export',
        state: 'passed',
        words: 'Converted to a 9.0 GB file at Q4_K_M.',
      },
      {
        id: 'tools',
        state: 'passed',
        words: 'Right 2 of 3 times.',
      },
    ],
    sample: [
      { prompt: 'Use the calculator to work out 1234 * 5678.', passed: true },
      { prompt: 'Use the calculator to work out 98765 / 5.', passed: true },
      {
        prompt: 'Use the calculator to add 4821, 3907 and 1288.',
        passed: false,
      },
    ],
    download_bytes: 29_500_000_000,
    downloaded_bytes: 29_500_000_000,
    trains_here: true,
    hugging_face_flavor: 'l40sx1',
    ...overrides,
  };
}

function show(
  models: LearningCheckedModel[] = [],
  overrides: Partial<LearningPersonalModel> = {},
) {
  const io = fakeIO(
    learningSnapshot({
      state: 'on',
      personal_model: installedModel({ other_models: models, ...overrides }),
    }),
    { models: { query: 'qwen', models: RESULTS, failure: null } },
  );
  render(<OtherModels io={io} />);
  return io;
}

async function search(text: string) {
  fireEvent.change(
    screen.getByRole('searchbox', { name: 'Search Hugging Face' }),
    { target: { value: text } },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await act(async () => undefined);
}

function results() {
  return screen.getByRole('list', { name: 'Search results' });
}

it('search says plainly what Row-Bot can try, never offering a model it can’t run after', async () => {
  const io = show();

  await search('qwen');

  expect(io.models).toHaveBeenCalledWith('qwen');
  expect(results()).toHaveTextContent(
    'packs its attention layers in a way Row-Bot’s training doesn’t adapt yet.',
  );
  expect(
    within(results()).getByRole('link', { name: 'Request access' }),
  ).toHaveAttribute(
    'href',
    'https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct',
  );
  expect(results()).toHaveTextContent(
    'Too big to run on this computer afterwards (it needs about 140 GB of the graphics card).',
  );
  expect(
    within(results()).getAllByRole('button', { name: 'Check…' }),
  ).toHaveLength(2);
});

it('a licence with terms shows its key terms and needs the person’s OK before the check', async () => {
  const io = show();
  await search('llama');

  fireEvent.click(
    within(
      within(results()).getByRole('listitem', {
        name: 'Llama-3.1-8B-Instruct',
      }),
    ).getByRole('button', { name: 'Check…' }),
  );
  const ask = screen.getByRole('group', {
    name: 'Check Llama-3.1-8B-Instruct?',
  });
  expect(ask).toHaveTextContent(
    'You may use and change it, also commercially.',
  );
  const check = within(ask).getByRole('button', { name: 'Check' });
  expect(check).toBeDisabled();
  fireEvent.click(
    within(ask).getByRole('checkbox', {
      name: /I agree to the Llama Community License/,
    }),
  );
  fireEvent.click(check);
  await act(async () => undefined);

  expect(io.run).toHaveBeenCalledWith('check_model', {
    model: 'hf:unsloth/Llama-3.1-8B-Instruct',
    licence_ok: true,
  });
});

it('without the training engine nothing can be checked, and it says what to download first', async () => {
  show([], { engine: 'not_downloaded' });

  await search('qwen');

  expect(screen.getByRole('note')).toHaveTextContent(
    'To check a model, first download the training engine (2.2 GB): Settings › Learning › Training engine.',
  );
  for (const button of within(results()).getAllByRole('button', {
    name: 'Check…',
  }))
    expect(button).toBeDisabled();
});

it('a model already checked shows its state in the results instead of another check', async () => {
  show([checked({ state: 'checking', stage: 'steps' })]);

  await search('qwen');

  const row = within(results()).getByRole('listitem', { name: 'Qwen3-14B' });
  expect(row).toHaveTextContent('Checking');
  expect(within(row).queryByRole('button', { name: 'Check…' })).toBeNull();
  // One check at a time: the others wait for it.
  expect(
    within(
      within(results()).getByRole('listitem', {
        name: 'Llama-3.1-8B-Instruct',
      }),
    ).getByRole('button', { name: 'Check…' }),
  ).toBeDisabled();
});

it('a checked model says what was checked, the tool sample as it ran, and where it trains', () => {
  show([checked()]);

  const item = within(
    screen.getByRole('list', { name: 'Checked models' }),
  ).getByRole('listitem', { name: 'Qwen3-14B' });
  expect(item).toHaveTextContent('Passed');
  expect(item).toHaveTextContent(
    'not certified · Trains on this computer and on Hugging Face',
  );
  expect(
    within(item).getByRole('list', { name: 'What was checked' }),
  ).toHaveTextContent(
    'Fits the graphics card: Passed. Trains on this card: peak 21.2 GB of the 25.5 GB it may use.',
  );
  expect(item).toHaveTextContent('The tool sample: 2 of 3 tasks right');
  expect(
    within(item).getByRole('button', { name: 'Remove Qwen3-14B' }),
  ).toBeInTheDocument();
});

it('a check under way shows its download and can be stopped', async () => {
  const io = show([
    checked({
      state: 'downloading',
      stage: 'download',
      items: [],
      sample: [],
      downloaded_bytes: 13_275_000_000,
    }),
  ]);

  const item = screen.getByRole('listitem', { name: 'Qwen3-14B' });
  expect(item).toHaveTextContent('Downloading: 13.3 GB of 29.5 GB');
  expect(within(item).queryByRole('button', { name: /^Remove/ })).toBeNull();
  fireEvent.click(within(item).getByRole('button', { name: 'Stop the check' }));
  await act(async () => undefined);

  expect(io.run).toHaveBeenCalledWith('stop_model_check', {
    model: 'hf:Qwen/Qwen3-14B',
  });
});

it('a model that passed and trains here can be used for training after a second, plain ask', async () => {
  const io = show([checked({ hugging_face_flavor: null })]);
  const item = screen.getByRole('listitem', { name: 'Qwen3-14B' });

  fireEvent.click(
    within(item).getByRole('button', { name: 'Use for training…' }),
  );
  expect(
    within(item).getByRole('group', { name: 'Use Qwen3-14B?' }),
  ).toHaveTextContent(
    'It isn’t certified: Row-Bot checked it on this computer.',
  );
  fireEvent.click(within(item).getByRole('button', { name: 'Use Qwen3-14B' }));
  await act(async () => undefined);

  expect(io.run).toHaveBeenCalledWith('change_starting_model', {
    model: 'hf:Qwen/Qwen3-14B',
  });
});

it('training on Hugging Face, a passed model is used there, its time and price said to be estimates', async () => {
  const io = show([checked({ trains_here: false })], {
    train_where: 'hugging_face',
  });
  const item = screen.getByRole('listitem', { name: 'Qwen3-14B' });
  expect(item).toHaveTextContent(
    'Trains on Hugging Face: too big to train on this graphics card',
  );

  fireEvent.click(
    within(item).getByRole('button', { name: 'Use on Hugging Face…' }),
  );
  expect(item).toHaveTextContent(
    'its time and price are estimates until a run measures them.',
  );
  fireEvent.click(within(item).getByRole('button', { name: 'Use Qwen3-14B' }));
  await act(async () => undefined);

  expect(io.run).toHaveBeenCalledWith('hf_choose_model', {
    model: 'hf:Qwen/Qwen3-14B',
  });
});

it('a model too big for this card says where it can train instead of offering it here', () => {
  show([checked({ trains_here: false })]);

  const item = screen.getByRole('listitem', { name: 'Qwen3-14B' });

  expect(
    within(item).queryByRole('button', { name: 'Use for training…' }),
  ).toBeNull();
  expect(item).toHaveTextContent(
    'Too big to train on this graphics card. Choose Hugging Face in Where to train to train it there.',
  );
});

it('the model training uses says so and can’t be removed meanwhile', () => {
  show([checked()], {
    starting_model: {
      id: 'hf:Qwen/Qwen3-14B',
      name: 'Qwen3-14B',
      licence: 'Apache 2.0',
      download_bytes: 29_500_000_000,
    },
  });

  const item = screen.getByRole('listitem', { name: 'Qwen3-14B' });
  expect(item).toHaveTextContent('In use for training');
  expect(within(item).queryByRole('button', { name: /^Remove/ })).toBeNull();
  expect(
    within(item).queryByRole('button', { name: 'Use for training…' }),
  ).toBeNull();
});

it('a check that didn’t pass or was stopped can be checked again', async () => {
  const io = show([
    checked({
      state: 'failed',
      failure: 'tools',
      items: [
        { id: 'fits', state: 'passed', words: 'Trains on this card.' },
        { id: 'steps', state: 'passed', words: 'Loss 2.10 → 1.60.' },
        { id: 'export', state: 'passed', words: 'Converts.' },
        {
          id: 'tools',
          state: 'failed',
          words: 'Right 1 of 3 times; it needs at least 2.',
        },
      ],
    }),
  ]);
  const item = screen.getByRole('listitem', { name: 'Qwen3-14B' });
  expect(item).toHaveTextContent('Didn’t pass');
  expect(item).toHaveTextContent(
    'Calls Row-Bot’s tools: Didn’t pass. Right 1 of 3 times; it needs at least 2.',
  );

  fireEvent.click(
    within(item).getByRole('button', { name: 'Check Qwen3-14B again' }),
  );
  await act(async () => undefined);

  expect(io.run).toHaveBeenCalledWith('check_model', {
    model: 'hf:Qwen/Qwen3-14B',
  });
});
