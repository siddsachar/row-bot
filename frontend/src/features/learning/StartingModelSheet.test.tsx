import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import { OverlayProvider } from '../../ui/overlays';
import type { LearningIO } from './learning-state';
import { StartingModelSheet } from './StartingModelSheet';
import { StrongerModelNotice } from './StrongerModelNotice';
import {
  fakeIO,
  GPU_32GB,
  installedModel,
  learningSnapshot,
  NO_GPU,
} from './test-data';

const NINE_B = GPU_32GB.starting_models[0];

/** A setup made on a smaller certified model (a stand-in now the 2B is gone, O124) on a 32 GB card. */
function onTwoB(hardware = GPU_32GB) {
  return fakeIO(
    learningSnapshot({
      state: 'on',
      personal_model: installedModel({
        starting_model: GPU_32GB.starting_models[1],
      }),
    }),
    { hardware },
  );
}

function sheet(io: LearningIO, offer = false) {
  return render(
    <OverlayProvider>
      <StartingModelSheet
        open
        onOpenChange={() => undefined}
        io={io}
        offer={offer ? NINE_B : null}
      />
    </OverlayProvider>,
  );
}

it('lists the certified models this card trains, the recommended one marked, and asks before switching', async () => {
  const io = onTwoB();
  sheet(io);

  const dialog = await screen.findByRole('dialog', { name: 'Starting model' });
  const nine = await within(dialog).findByRole('radio', {
    name: /Qwen3\.5 9B · recommended/,
  });
  const two = within(dialog).getByRole('radio', {
    name: /A small model · in use/,
  });
  expect(two).toBeChecked();
  expect(dialog).toHaveTextContent('Apache 2.0 · 19.3 GB download');
  expect(dialog).toHaveTextContent(
    'Versions you have stay as they are, each compared with the model it started from.',
  );
  expect(within(dialog).getByRole('button', { name: 'Use…' })).toBeDisabled();

  fireEvent.click(nine);
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Use Qwen3.5 9B…' }),
  );

  const ask = await screen.findByRole('dialog', {
    name: 'Switch to Qwen3.5 9B?',
  });
  expect(ask).toHaveTextContent(
    'Row-Bot downloads Qwen3.5 9B (19.3 GB) from Hugging Face, then trains your next version from it in quiet hours. 700 GB free on this computer.',
  );
  expect(ask).toHaveTextContent(
    'A small model stays on this computer until you delete it',
  );
  expect(io.run).not.toHaveBeenCalled();
  fireEvent.click(
    within(ask).getByRole('button', { name: 'Download and switch' }),
  );
  await act(async () => undefined);
  expect(io.run).toHaveBeenCalledWith('change_starting_model', {
    model: 'qwen3.5-9b',
  });
});

it('says when there isn’t room for the download and switches nothing', async () => {
  const io = onTwoB({ ...GPU_32GB, free_bytes: 8_000_000_000 });
  sheet(io, true);

  const ask = await screen.findByRole('dialog', {
    name: 'Switch to Qwen3.5 9B?',
  });
  expect(await within(ask).findByRole('alert')).toHaveTextContent(
    'Needs 19.3 GB free; 8 GB free. Free some space, then try again.',
  );
  expect(
    within(ask).getByRole('button', { name: 'Download and switch' }),
  ).toBeDisabled();
});

it('keeps the sheet open with the reason when the switch is refused, so it can be tried again', async () => {
  const io = onTwoB();
  io.run = Object.assign(async () => {
    throw Object.assign(new Error('busy'), { code: 'learning_download_busy' });
  }, {}) as unknown as LearningIO['run'];
  sheet(io, true);

  const ask = await screen.findByRole('dialog', {
    name: 'Switch to Qwen3.5 9B?',
  });
  const go = await within(ask).findByRole('button', {
    name: 'Download and switch',
  });
  fireEvent.click(go);
  expect(await within(ask).findByRole('alert')).toBeVisible();
  expect(
    within(ask).getByRole('button', { name: 'Download and switch' }),
  ).toBeEnabled();
});

it('a computer that can’t train says so', async () => {
  sheet(onTwoB(NO_GPU));

  expect(
    await screen.findByText('This computer can’t train a personal model.'),
  ).toBeVisible();
});

it('the stronger model is offered once: Switch… asks, Dismiss hides it for good', async () => {
  const io = onTwoB();
  render(
    <OverlayProvider>
      <StrongerModelNotice io={io} model={NINE_B} />
    </OverlayProvider>,
  );

  const notice = screen.getByRole('status', {
    name: 'A stronger starting model',
  });
  expect(notice).toHaveTextContent(
    'Your graphics card can now train Qwen3.5 9B, a stronger starting model. Your next versions would start from it; the ones you have stay as they are.',
  );
  fireEvent.click(
    within(notice).getByRole('button', {
      name: 'Don’t suggest Qwen3.5 9B again',
    }),
  );
  await act(async () => undefined);
  expect(io.run).toHaveBeenCalledWith('dismiss', {
    item_id: 'stronger_model:qwen3.5-9b',
  });

  fireEvent.click(
    within(notice).getByRole('button', {
      name: /^Switch… \(downloads 19\.3\sGB\)$/,
    }),
  );
  expect(
    await screen.findByRole('dialog', { name: 'Switch to Qwen3.5 9B?' }),
  ).toBeVisible();
});
