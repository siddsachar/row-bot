import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { OverlayProvider } from '../../ui/overlays';
import BackgroundProcesses, {
  type BackgroundProcessesIo,
} from '../home/BackgroundProcesses';
import type { LearningIO } from './learning-state';
import { DownloadProgress, downloadView } from './PersonalModelDownload';
import {
  downloadState,
  fakeIO,
  installedModel,
  learningSnapshot,
  personalModel,
} from './test-data';
import { DownloadAsk } from './LearningSetup';

const GB = 1_000_000_000;

function card(io: LearningIO) {
  return render(
    <MemoryRouter>
      <OverlayProvider>
        <DownloadProgress io={io} snapshot={io.snapshot!} />
      </OverlayProvider>
    </MemoryRouter>,
  );
}

const downloading = (overrides = {}) =>
  personalModel({
    download: 'waiting',
    download_state: downloadState({
      state: 'downloading',
      phase: 'engine',
      done_bytes: 1.2 * GB,
      total_bytes: 6.8 * GB,
      ...overrides,
    }),
  });

it('downloading shows bytes and percent of the whole download, with a real bar and Stop', async () => {
  const io = fakeIO(
    learningSnapshot({ state: 'on', personal_model: downloading() }),
  );
  card(io);

  // O49: the title names both parts while the total holds both, with the bytes.
  expect(
    screen.getByText(
      'Downloading the training engine and Qwen3.5 2B · 1.2 of 6.8 GB',
    ),
  ).toBeVisible();
  expect(screen.getByText('17% · now the training engine')).toBeVisible();
  const bar = screen.getByRole('progressbar', { name: 'Download progress' });
  expect(bar).toHaveAttribute('max', String(6.8 * GB));
  expect(bar).toHaveAttribute('value', String(1.2 * GB));
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  await act(async () => undefined);
  expect(io.run).toHaveBeenCalledWith('stop_download');
});

it('installing and checking the engine have no bar (no denominator) but can stop', () => {
  for (const [phase, title] of [
    ['installing', 'Installing the training engine…'],
    ['checking', 'Checking the training engine…'],
  ] as const) {
    const view = downloadView(downloading({ phase }))!;
    expect(view.title).toBe(title);
    expect(view.progress).toBeUndefined();
    expect(view.actions.map((item) => item.label)).toEqual(['Stop']);
  }
  expect(downloadView(downloading({ phase: 'model' }))!).toMatchObject({
    title: 'Downloading the training engine and Qwen3.5 2B · 1.2 of 6.8 GB',
    detail: '17% · now Qwen3.5 2B',
  });
  // Only the starting model left to download (the engine is installed): its own name.
  expect(
    downloadView(
      installedModel({
        starting_model_state: 'not_downloaded',
        download_state: downloadState({
          state: 'downloading',
          phase: 'model',
          done_bytes: 2.3 * GB,
          total_bytes: 4_571_272_453,
        }),
      }),
    )!.title,
  ).toBe('Downloading Qwen3.5 2B · 2.3 of 4.6 GB');
});

it('a paused download says where it stopped, with Resume and a Cancel that asks first', async () => {
  const io = fakeIO(
    learningSnapshot({
      state: 'on',
      personal_model: downloading({ state: 'paused', done_bytes: 3.1 * GB }),
    }),
  );
  card(io);

  expect(screen.getByText('Download paused at 3.1 of 6.8 GB.')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  const confirm = await screen.findByRole('dialog', {
    name: 'Cancel the download?',
  });
  expect(confirm).toHaveTextContent(
    'What it downloaded so far (3.1 GB) is deleted.',
  );
  expect(io.run).not.toHaveBeenCalled();
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel download' }),
  );
  await act(async () => undefined);
  expect(io.run).toHaveBeenCalledWith('cancel_download');

  fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
  await act(async () => undefined);
  expect(io.run).toHaveBeenCalledWith('start_download');
});

it('not enough disk says what it needs and what is free', () => {
  const view = downloadView(
    downloading({
      state: 'not_enough_disk',
      done_bytes: 0,
      needs_bytes: 9.4 * GB,
      free_bytes: 6 * GB,
    }),
  )!;
  expect(view.title).toBe('Needs 9.4\u00a0GB free; 6\u00a0GB free.');
  expect(view.detail).toBe('Free some space on this computer, then try again.');
  expect(view.actions.map((item) => item.label)).toEqual(['Try again']);
});

it('a failed download says why, with Try again', () => {
  expect(
    downloadView(downloading({ state: 'failed', failure: 'mismatch' }))!,
  ).toMatchObject({
    title:
      'The download stopped: a file didn’t match the one Row-Bot expects, so it wasn’t used. Try again.',
    actions: [
      { label: 'Try again', action: 'start_download', primary: true },
      { label: 'Cancel', action: 'cancel_download' },
    ],
  });
  expect(
    downloadView(downloading({ state: 'failed', failure: 'no_cuda' }))!.title,
  ).toContain('Update your NVIDIA driver');
});

it('a damaged or outdated engine offers Repair or Update, and nothing shows when all is well', () => {
  expect(
    downloadView(installedModel({ engine: 'damaged' }))!.actions[0].label,
  ).toBe('Repair');
  expect(
    downloadView(installedModel({ engine: 'outdated' }))!.actions[0].label,
  ).toBe('Update');
  expect(downloadView(installedModel())).toBeNull();
  expect(downloadView(personalModel())).toBeNull();
});

it('the ask names where it comes from and says usage reporting is off, and checks the free space', async () => {
  const io = fakeIO(
    learningSnapshot({
      state: 'on',
      personal_model: personalModel({
        download_state: downloadState({ needs_bytes: 9 * GB }),
      }),
    }),
  );
  render(
    <OverlayProvider>
      <DownloadAsk
        open
        onOpenChange={vi.fn()}
        io={io}
        hardware={{
          state: 'gpu',
          gpu: null,
          tier: null,
          larger_tiers: [],
          starting_models: [],
          engine_download_bytes: 2.2 * GB,
          free_bytes: 6 * GB,
        }}
        onAnswered={vi.fn()}
      />
    </OverlayProvider>,
  );
  const ask = await screen.findByRole('dialog', {
    name: 'Download the training engine and Qwen3.5 2B?',
  });
  expect(
    within(ask).getByText(
      'From PyPI, PyTorch, Hugging Face and GitHub (llama.cpp). The training engine’s own usage reporting is switched off.',
    ),
  ).toBeVisible();
  expect(within(ask).getByRole('alert')).toHaveTextContent(
    'Needs 9 GB free; 6 GB free.',
  );
  expect(within(ask).getByRole('button', { name: 'Download' })).toBeDisabled();
});

it('Monitor shows one row for the download, with Stop', async () => {
  const learning = fakeIO(
    learningSnapshot({ state: 'on', personal_model: downloading() }),
  );
  const io = {
    load: vi.fn(async () => ({
      items: [],
      running: 0,
      limit_per_conversation: 5,
      limit_total: 12,
    })),
    output: vi.fn(),
    stop: vi.fn(),
  } as unknown as BackgroundProcessesIo;
  render(
    <OverlayProvider>
      <BackgroundProcesses io={io} learning={learning} />
    </OverlayProvider>,
  );
  const row = await screen.findByRole('button', {
    name: 'Stop downloading Training engine',
  });
  expect(screen.getByText('Downloading · 17%')).toBeVisible();
  expect(
    screen.getByText('Personal model download · 1.2 of 6.8 GB'),
  ).toBeVisible();
  fireEvent.click(row);
  await act(async () => undefined);
  expect(learning.run).toHaveBeenCalledWith('stop_download');
  // O49: Stop says what stopped and where it continues.
  expect(
    await screen.findByText(
      'Done: stop_download Resume it in Learning › Personal model.',
    ),
  ).toBeInTheDocument();
});
