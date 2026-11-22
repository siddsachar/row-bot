import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import { placeWords } from '../../api/place';
import type { SpeechModelsView } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import SpeechModels from './SpeechModels';

const MB = 1_000_000;

function view(
  extra: Partial<SpeechModelsView> = {},
  installed: string[] = [],
): SpeechModelsView {
  const row = (
    id: string,
    name: string,
    size: number,
    unpacked: number,
    best: 'gpu' | 'cpu',
    licence: string,
  ) => ({
    id,
    name,
    engine_id: `local-${id}`,
    size_bytes: size,
    installed_bytes: unpacked,
    download_bytes: installed.includes(id) ? 0 : size,
    installed: installed.includes(id),
    language: 'en',
    licence,
    source: 'https://example.invalid/model',
    runs_best_on: best,
  });
  return {
    schema_version: 1,
    state: installed.length ? 'installed' : 'not_installed',
    computer: 'NVIDIA GeForce RTX 5090 (32 GB)',
    recommended: 'moonshine-base-en',
    reason: 'no_gpu_runtime',
    models: [
      row(
        'nemotron-streaming-en',
        'Nemotron Speech Streaming',
        464 * MB,
        662 * MB,
        'gpu',
        'NVIDIA Open Model License',
      ),
      row(
        'moonshine-base-en',
        'Moonshine Base',
        111 * MB,
        141 * MB,
        'cpu',
        'MIT',
      ),
    ],
    download: null,
    failure: null,
    talk_engine: installed.length ? `local-${installed[0]}` : 'local-whisper',
    disk_bytes: 0,
    ...extra,
  };
}

function show(
  current: SpeechModelsView,
  controller: Partial<ClientController> = {},
) {
  const update = vi.fn();
  render(
    <OverlayProvider>
      <SpeechModels
        controller={controller as ClientController}
        place={placeWords({ kind: 'this_computer' })}
        view={current}
        speed={null}
        update={update}
      />
    </OverlayProvider>,
  );
  return update;
}

it('recommends the model for this computer first and downloads it only with the size shown', async () => {
  const next = view({ state: 'downloading' });
  const installSpeechModel = vi.fn(async () => next);
  const update = show(view(), { installSpeechModel });

  expect(
    screen.getByText(
      /Moonshine Base is recommended for this computer: fast on the processor/,
    ),
  ).toBeVisible();
  const rows = within(
    screen.getByRole('list', { name: 'Streaming speech models' }),
  ).getAllByRole('listitem');
  expect(rows[0]).toHaveTextContent('Moonshine Base');
  expect(within(rows[0]).getByText('Recommended')).toBeVisible();
  expect(rows[0]).toHaveTextContent('111 MB download · English · MIT');
  fireEvent.click(
    within(rows[0]).getByRole('button', {
      name: 'Download Moonshine Base (111 MB) and listen with it',
    }),
  );
  await waitFor(() =>
    expect(installSpeechModel).toHaveBeenCalledWith(
      'moonshine-base-en',
      111 * MB,
    ),
  );
  expect(update).toHaveBeenCalledWith(next);
});

it('shows a download in progress with a Stop that keeps what arrived', async () => {
  const stopSpeechModelDownload = vi.fn(async () => view());
  show(
    view({
      state: 'downloading',
      download: {
        model_id: 'moonshine-base-en',
        total_bytes: 111 * MB,
        done_bytes: 50 * MB,
        phase: 'downloading',
      },
    }),
    { stopSpeechModelDownload },
  );

  expect(
    screen.getByRole('progressbar', { name: 'Downloading Moonshine Base' }),
  ).toBeVisible();
  expect(screen.getByRole('status')).toHaveTextContent('45% of 111 MB');
  // Another download can't start meanwhile.
  expect(
    screen.getByRole('button', { name: /Download Nemotron Speech Streaming/ }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Stop the download; it resumes later' }),
  );
  await waitFor(() => expect(stopSpeechModelDownload).toHaveBeenCalled());
});

it('marks the model in use and asks before removing it', async () => {
  const removeSpeechModel = vi.fn(async () => ({
    ...view(),
    freed_bytes: 141 * MB,
  }));
  show(view({}, ['moonshine-base-en']), { removeSpeechModel });

  const row = screen.getByText('Moonshine Base').closest('li')!;
  expect(within(row).getByText('In use')).toBeVisible();
  expect(row).toHaveTextContent('141 MB on this computer · English');
  fireEvent.click(
    within(row).getByRole('button', { name: 'Remove Moonshine Base' }),
  );
  const dialog = await screen.findByRole('alertdialog');
  expect(dialog).toHaveTextContent('Talk goes back to listening with Whisper.');
  expect(removeSpeechModel).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
  await waitFor(() =>
    expect(removeSpeechModel).toHaveBeenCalledWith('moonshine-base-en'),
  );
  expect(await screen.findByText('Removed. 141 MB freed.')).toBeVisible();
});

it('says plainly when a download failed or the speech runtime is missing', () => {
  show(view({ state: 'failed', failure: { reason: 'mismatch' } }));
  expect(screen.getByText(/didn’t match its fingerprint/)).toBeVisible();
});

it('offers nothing to download without the speech runtime', () => {
  show(view({ state: 'unavailable' }));
  expect(
    screen.getByText(
      'Streaming speech models need Row-Bot’s voice components.',
    ),
  ).toBeVisible();
  for (const button of screen.getAllByRole('button', { name: /^Download/ }))
    expect(button).toBeDisabled();
});
