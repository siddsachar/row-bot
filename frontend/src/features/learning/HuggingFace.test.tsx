import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import type { LearningHuggingFace } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import { HuggingFaceCard, HuggingFaceQuoteSheet } from './HuggingFace';
import { fakeIO, learningSnapshot, personalModel } from './test-data';

function hugging(
  overrides: Partial<LearningHuggingFace> = {},
): LearningHuggingFace {
  return {
    available: true,
    sign_in: {
      state: 'signed_out',
      user: '',
      code: null,
      url: null,
      failure: null,
    },
    models: [
      {
        id: 'qwen3.8-27b',
        name: 'Qwen3.8 27B',
        licence: 'Apache 2.0',
        params_b: 27,
        certified: true,
        recommended: true,
        runs_here_gb: 20,
        download_bytes: 16_600_000_000,
      },
    ],
    model: 'qwen3.8-27b',
    card_gb: 32,
    quote: {
      model_id: 'qwen3.8-27b',
      model_name: 'Qwen3.8 27B',
      flavor: 'l40sx1',
      flavor_label: 'Nvidia L40S',
      gpu_memory_gb: 48,
      price_per_hour: 1.8,
      seconds: 5_580,
      cost_usd: 12.4,
      timeout_seconds: 9_570,
      max_cost_usd: 18,
      examples: 412,
      upload_bytes: 18_000_000,
      download_bytes: 16_600_000_000,
      dataset_repo: 'ada/row-bot-training-…',
      measured: true,
      month_spent_usd: 0,
      cap_usd: 0,
      fits_cap: false,
    },
    planned: true,
    cap_usd: 0,
    cap_choices: [10, 20, 50, 100],
    month_spent_usd: 0,
    runs_confirmed: false,
    recent_runs: [],
    last_run: null,
    ...overrides,
  };
}

function snapshotWith(found: LearningHuggingFace) {
  return learningSnapshot({
    state: 'on',
    personal_model: personalModel({
      train_where: 'hugging_face',
      hugging_face: found,
    }),
  });
}

it('someone new to Hugging Face reads what it is, what Jobs need and a typical price before signing in', () => {
  const io = fakeIO(snapshotWith(hugging()));
  render(
    <OverlayProvider>
      <HuggingFaceCard io={io} hugging={hugging()} />
    </OverlayProvider>,
  );

  const card = screen.getByRole('region', { name: 'Hugging Face' });
  expect(card).toHaveTextContent(
    'You need a Hugging Face account with credit for Jobs.',
  );
  expect(card).toHaveTextContent(
    'A typical run of Qwen3.8 27B costs about $12.40 (about 1 h 33 min on Nvidia L40S).',
  );
  expect(within(card).getByRole('link', { name: 'Billing' })).toHaveAttribute(
    'href',
    'https://huggingface.co/settings/billing',
  );
  expect(
    within(card).getByRole('button', { name: 'Sign in with Hugging Face' }),
  ).toBeVisible();
});

it('while signing in it shows the code and Hugging Face’s own page, and Cancel', () => {
  const found = hugging({
    sign_in: {
      state: 'signing_in',
      user: '',
      code: 'ABCD-EFGH',
      url: 'https://huggingface.co/oauth/device?user_code=ABCD-EFGH',
      failure: null,
    },
  });
  render(
    <OverlayProvider>
      <HuggingFaceCard io={fakeIO(snapshotWith(found))} hugging={found} />
    </OverlayProvider>,
  );

  expect(screen.getByText('ABCD-EFGH')).toBeVisible();
  expect(
    screen.getByRole('link', { name: 'Open Hugging Face' }),
  ).toHaveAttribute(
    'href',
    'https://huggingface.co/oauth/device?user_code=ABCD-EFGH',
  );
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeVisible();
});

it('the quote asks for a cap the run fits and Start carries the price the person saw', async () => {
  const found = hugging({
    sign_in: {
      state: 'signed_in',
      user: 'ada',
      code: null,
      url: null,
      failure: null,
    },
  });
  const io = fakeIO(snapshotWith(found));
  render(
    <OverlayProvider>
      <HuggingFaceQuoteSheet
        open
        onOpenChange={() => undefined}
        io={io}
        jobId="job_1"
        version={4}
      />
    </OverlayProvider>,
  );

  const sheet = await screen.findByRole('dialog', {
    name: 'Train v4 on Hugging Face?',
  });
  expect(sheet).toHaveTextContent('Nvidia L40S (48 GB) · $1.80 an hour');
  expect(sheet).toHaveTextContent(
    'about $12.40 · at most $18.00 (Hugging Face stops the run there)',
  );
  expect(sheet).toHaveTextContent(
    '412 examples (18 MB) to a private dataset in your account. Deleted once your model is downloaded and checked.',
  );
  const cap = within(sheet).getByRole('combobox', { name: 'Spend at most' });
  fireEvent.change(cap, { target: { value: '10' } });
  expect(within(sheet).getByRole('alert')).toHaveTextContent(
    'This run doesn’t fit the cap.',
  );
  fireEvent.change(cap, { target: { value: '20' } });
  fireEvent.click(
    within(sheet).getByRole('button', { name: 'Start · about $12.40' }),
  );
  await act(async () => undefined);

  expect(io.run).toHaveBeenCalledWith('hf_start', {
    job_id: 'job_1',
    cap_usd: 20,
    quote_cost_cents: 1240,
  });
});

it('the quote lets the person pick another card, each with its own time and price', async () => {
  const base = hugging().quote!;
  const found = hugging({
    quote: {
      ...base,
      choices: [
        {
          flavor: 'l40sx1',
          label: 'Nvidia L40S',
          gpu_memory_gb: 48,
          price_per_hour: 1.8,
          seconds: 3_180,
          cost_usd: 1.59,
        },
        {
          flavor: 'a100-large',
          label: 'Nvidia A100',
          gpu_memory_gb: 80,
          price_per_hour: 2.5,
          seconds: 3_180,
          cost_usd: 2.21,
        },
      ],
    },
  });
  const io = fakeIO(snapshotWith(found));
  render(
    <OverlayProvider>
      <HuggingFaceQuoteSheet
        open
        onOpenChange={() => undefined}
        io={io}
        jobId="job_1"
        version={4}
      />
    </OverlayProvider>,
  );

  const sheet = await screen.findByRole('dialog', {
    name: 'Train v4 on Hugging Face?',
  });
  const cards = within(sheet).getByRole('group', {
    name: 'Graphics card on Hugging Face',
  });
  expect(
    within(cards).getByRole('radio', { name: /Nvidia L40S \(48 GB\)/ }),
  ).toBeChecked();
  expect(cards).toHaveTextContent(
    'Nvidia A100 (80 GB)about 53 min · about $2.21 · $2.50 an hour',
  );
  fireEvent.click(within(cards).getByRole('radio', { name: /Nvidia A100/ }));
  await act(async () => undefined);

  expect(io.run).toHaveBeenCalledWith('hf_choose_flavor', {
    flavor: 'a100-large',
  });
});
