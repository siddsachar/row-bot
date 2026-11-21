import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { LocalModelRuntimeView } from '../../api/types';
import RunOnThisComputer from './RunOnThisComputer';

const mock = vi.hoisted(() => ({
  controller: {
    installLocalModel: vi.fn(),
    stopLocalModelRuntime: vi.fn(),
  },
}));

vi.mock('../../runtime', async () => {
  const { createContext } = await import('react');
  return {
    RuntimeContext: createContext(null),
    useRuntime: () => ({ controller: mock.controller }),
  };
});

const GB = 1_000_000_000;

function localView(
  extra: Partial<LocalModelRuntimeView> = {},
): LocalModelRuntimeView {
  const build = 0.6 * GB;
  const row = (
    id: string,
    name: string,
    quantization: string,
    size: number,
    fit: 'gpu' | 'partial' | 'cpu' | 'too_big',
    older = false,
  ) => ({
    id,
    name,
    model_ref: `model:custom_openai_llamacpp:${id}`,
    size_bytes: size,
    fit,
    context_tokens: fit === 'gpu' && size < 6 * GB ? 131_072 : 32_768,
    everyday: size < 5 * GB,
    quantization,
    older,
    installed: false,
    download_bytes: size + build,
  });
  return {
    schema_version: 1,
    state: 'not_installed',
    computer: 'NVIDIA GeForce RTX 4070 (12 GB)',
    acceleration: 'cuda-13',
    acceleration_label: 'NVIDIA (CUDA 13)',
    note: null,
    release: 'b12359',
    build_installed: false,
    update_bytes: 0,
    recommended: 'qwen3.8-27b-q2',
    models: [
      row('qwen3.8-27b-q4', 'Qwen3.8 27B', '4-bit', 16.6 * GB, 'too_big'),
      row('qwen3.8-27b-q3', 'Qwen3.8 27B', '3-bit', 13.1 * GB, 'partial'),
      row('qwen3.8-27b-q2', 'Qwen3.8 27B', '2-bit', 9.9 * GB, 'gpu'),
      row('qwen3.5-9b', 'Qwen3.5 9B', '4-bit', 5.7 * GB, 'gpu', true),
      row('qwen3.5-4b', 'Qwen3.5 4B', '4-bit', 2.7 * GB, 'gpu', true),
    ],
    download: null,
    failure: null,
    disk_bytes: 0,
    free_bytes: 200 * GB,
    on_this_computer: true,
    ...extra,
  };
}

function show(view: LocalModelRuntimeView | null, onPick = vi.fn()) {
  const update = vi.fn();
  render(
    <RunOnThisComputer
      view={view}
      speed={null}
      update={update}
      onPick={onPick}
    />,
  );
  return { update, onPick };
}

beforeEach(() => {
  for (const fn of Object.values(mock.controller)) fn.mockReset();
});

it('offers Qwen3.8 27B at the build that fits, names the size and downloads nothing until pressed', async () => {
  mock.controller.installLocalModel.mockResolvedValue(
    localView({ state: 'downloading' }),
  );
  const { update } = show(localView());

  expect(
    screen.getByText(
      'Qwen3.8 27B · 2-bit · fits your NVIDIA GeForce RTX 4070 (12 GB) · reads 32K · 10.5 GB download',
    ),
  ).toBeVisible();
  expect(mock.controller.installLocalModel).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'Download 10.5 GB' }));

  await waitFor(() =>
    expect(mock.controller.installLocalModel).toHaveBeenCalledWith(
      'qwen3.8-27b-q2',
      10.5 * GB,
    ),
  );
  await waitFor(() => expect(update).toHaveBeenCalled());
});

function choices() {
  fireEvent.click(screen.getByRole('button', { name: 'Choose another model' }));
  const list = screen.getByRole('list', { name: 'Models for this computer' });
  return within(list).getAllByRole('button');
}

function names(rows: HTMLElement[]) {
  return rows.map((row) => row.querySelector('strong')?.textContent);
}

it('the sliders offer Qwen3.8 27B at the build sized to the card and the smaller models, each with how it would run here', () => {
  show(localView());

  const rows = choices();

  expect(names(rows)).toEqual([
    'Qwen3.8 27B · 2-bit',
    'Qwen3.5 9B',
    'Qwen3.5 4B',
  ]);
  expect(rows[0]).toHaveTextContent('Recommended');
  expect(within(rows[1]).getByText('Fits your graphics card')).toHaveAttribute(
    'data-fit',
    'gpu',
  );
  expect(rows[1]).toHaveTextContent(
    '6.3 GB download · smaller, older generation',
  );

  fireEvent.click(rows[2]);
  expect(screen.getByRole('button', { name: 'Download 3.3 GB' })).toBeVisible();
  expect(
    screen.getByText(/A smaller model for this computer \(older generation\)/),
  ).toBeVisible();
});

it('on a Mac, a model that fits says it fits the Mac’s memory, not a graphics card (M2)', () => {
  show(
    localView({
      computer: 'Apple M4 (16 GB memory)',
      acceleration: 'metal',
      acceleration_label: 'Apple silicon (Metal)',
      recommended: 'qwen3.5-9b',
    }),
  );

  const rows = choices();

  expect(within(rows[1]).getByText('Fits this Mac’s memory')).toBeVisible();
  expect(screen.queryByText('Fits your graphics card')).toBeNull();
});

it('on a card no 27B build fits, the smallest build is offered as slower, beside the smaller models', () => {
  const small = localView({ recommended: 'qwen3.5-9b' });
  small.models = small.models.map((model) =>
    model.older
      ? model
      : { ...model, fit: model.id.endsWith('q2') ? 'partial' : 'too_big' },
  );
  show(small);

  const rows = choices();

  expect(names(rows)).toEqual([
    'Qwen3.8 27B · 2-bit',
    'Qwen3.5 9B',
    'Qwen3.5 4B',
  ]);
  expect(
    within(rows[0]).getByText('Partly on the graphics card (slower)'),
  ).toBeVisible();
  expect(rows[0]).toBeEnabled();
});

it('a build already downloaded stays in the list, and a stopped download is what Download resumes', () => {
  const view = localView({
    download: {
      model_id: 'qwen3.5-4b',
      acceleration: 'cuda-13',
      total_bytes: 3.3 * GB,
      done_bytes: 0.2 * GB,
      phase: 'stopped',
    },
  });
  view.models[1] = { ...view.models[1], installed: true, download_bytes: 0 };
  show(view);

  expect(screen.getByRole('button', { name: 'Download 3.3 GB' })).toBeVisible();
  expect(names(choices())).toEqual([
    'Qwen3.8 27B · 3-bit',
    'Qwen3.8 27B · 2-bit',
    'Qwen3.5 9B',
    'Qwen3.5 4B',
  ]);
});

it('shows the download with its progress, and Stop keeps what arrived', async () => {
  mock.controller.stopLocalModelRuntime.mockResolvedValue(localView());
  show(
    localView({
      state: 'downloading',
      download: {
        model_id: 'qwen3.8-27b-q2',
        acceleration: 'cuda-13',
        total_bytes: 10.5 * GB,
        done_bytes: 4.2 * GB,
        phase: 'downloading',
      },
    }),
  );

  expect(
    screen.getByText('Downloading Qwen3.8 27B · 2-bit · 4.2 GB of 10.5 GB'),
  ).toBeVisible();
  expect(
    screen.getByRole('progressbar', {
      name: 'Downloading Qwen3.8 27B · 2-bit',
    }),
  ).toHaveAttribute('value', String((4.2 / 10.5) * 100));

  fireEvent.click(
    screen.getByRole('button', { name: 'Stop download; it resumes later' }),
  );
  await waitFor(() =>
    expect(mock.controller.stopLocalModelRuntime).toHaveBeenCalled(),
  );
});

it('a downloaded model is ready to use', () => {
  const ready = localView({ state: 'installed', build_installed: true });
  ready.models[2] = { ...ready.models[2], installed: true, download_bytes: 0 };
  const { onPick } = show(ready);

  expect(
    screen.getByText('Qwen3.8 27B · 2-bit is on this computer and ready.'),
  ).toBeVisible();
  fireEvent.click(
    screen.getByRole('button', { name: 'Use Qwen3.8 27B · 2-bit' }),
  );

  expect(onPick).toHaveBeenCalledWith(
    'model:custom_openai_llamacpp:qwen3.8-27b-q2',
    'Qwen3.8 27B · 2-bit',
  );
});

it('says plainly why a download did not start', () => {
  show(
    localView({
      state: 'failed',
      failure: {
        reason: 'out_of_disk',
        needed_bytes: 12.5 * GB,
        free_bytes: 3 * GB,
      },
    }),
  );

  expect(screen.getByRole('alert')).toHaveTextContent(
    'Not enough disk space: 12.5 GB needed, 3.0 GB free.',
  );
});

it('from a phone, never offers the download', () => {
  show(localView({ on_this_computer: false }));

  expect(screen.queryByRole('button', { name: /Download/ })).toBeNull();
  expect(
    screen.getByText(/Download models from Row-Bot on the computer it runs on/),
  ).toBeVisible();
});

it('a computer no build runs on is told so, without a dead end', () => {
  show(localView({ state: 'unavailable', recommended: null }));

  expect(screen.getByRole('status')).toHaveTextContent(
    'Use one of the other ways, or a provider.',
  );
  expect(screen.queryByRole('button', { name: /Download/ })).toBeNull();
});
