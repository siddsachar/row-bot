import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import type { LearningCheckedModel, LearningOpenModel } from '../../api/types';
import { OtherModels } from './OtherModels';
import { fakeIO, learningSnapshot, personalModel } from './test-data';

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

function show(checked: LearningCheckedModel[] = []) {
  const io = fakeIO(
    learningSnapshot({
      state: 'on',
      personal_model: personalModel({ other_models: checked }),
    }),
    { models: { query: 'qwen', models: RESULTS, failure: null } },
  );
  render(<OtherModels io={io} />);
  return io;
}

async function search(text: string) {
  fireEvent.change(
    screen.getByRole('searchbox', { name: 'Search Hugging Face' }),
    {
      target: { value: text },
    },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await act(async () => undefined);
}

it('search says plainly what Row-Bot can try, never offering a model it can’t run after', async () => {
  const io = show();

  await search('qwen');

  expect(io.models).toHaveBeenCalledWith('qwen');
  const results = screen.getByRole('list', { name: 'Search results' });
  expect(results).toHaveTextContent(
    'packs its attention layers in a way Row-Bot’s training doesn’t adapt yet.',
  );
  expect(results).toHaveTextContent(
    'Gated on Hugging Face: request access there.',
  );
  expect(results).toHaveTextContent(
    'Too big to run on this computer afterwards (about 140 GB of the graphics card).',
  );
  expect(
    within(results).getAllByRole('button', { name: 'Check…' }),
  ).toHaveLength(2);
});

it('a licence with terms shows its key terms and needs the person’s OK before the check', async () => {
  const io = show();
  await search('llama');

  fireEvent.click(
    within(screen.getByRole('list', { name: 'Search results' })).getAllByRole(
      'button',
      {
        name: 'Check…',
      },
    )[1],
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
      name: /I agree to Llama Community License/,
    }),
  );
  fireEvent.click(check);
  await act(async () => undefined);

  expect(io.run).toHaveBeenCalledWith('check_model', {
    model: 'hf:unsloth/Llama-3.1-8B-Instruct',
    licence_ok: true,
  });
});

it('a checked model says what was checked, the tool sample as it ran, and where it trains', () => {
  show([
    {
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
          words: 'Converts to run on Row-Bot’s own server (9.0 GB at Q4_K_M).',
        },
        {
          id: 'tools',
          state: 'passed',
          words: 'It called Row-Bot’s tools right 2 of 3 times.',
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
      hugging_face_flavor: 'a100-large',
    },
  ]);

  const item = screen.getByRole('listitem', { name: 'Qwen3-14B' });
  expect(item).toHaveTextContent('Qwen3-14B passed its check');
  expect(item).toHaveTextContent(
    'not certified. It trains on this computer and trains on Hugging Face.',
  );
  expect(
    within(item).getByRole('list', { name: 'What was checked' }),
  ).toHaveTextContent(
    'Fits the graphics card: Passed. Trains on this card: peak 21.2 GB of the 25.5 GB it may use.',
  );
  expect(item).toHaveTextContent('The tool sample: 2 of 3 tasks right');
  expect(
    within(item).getByRole('button', { name: 'Remove' }),
  ).toBeInTheDocument();
});
