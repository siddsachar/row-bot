import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { CachedModelPage, ProviderStatusSnapshot } from '../../api/types';
import ModelCatalog from './ModelCatalog';
import ProviderStatus from './ProviderStatus';

const snapshot: ProviderStatusSnapshot = {
  schema_version: 1,
  revision: 'one',
  generated_at: null,
  freshness: 'unavailable',
  refresh_running: false,
  total_models: 1,
  providers: [
    {
      provider_id: 'local',
      display_name: 'Local engine',
      group: 'local',
      auth_methods: [],
      catalog_state: 'cached',
      model_count: 1,
      runtime_state: 'unknown',
      enabled: null,
    },
  ],
};
const providers = vi.fn(async () => snapshot);
function page(
  name = 'First model',
  cursor: string | null = null,
): CachedModelPage {
  return {
    schema_version: 1,
    revision: 'one',
    generated_at: null,
    freshness: 'unavailable',
    total: 2,
    next_cursor: cursor,
    items: [
      {
        provider_id: 'local',
        model_id: name,
        selection_ref: `local:${name}`,
        display_name: name,
        provider_display_name: 'Local engine',
        categories: [],
        input_modalities: [],
        output_modalities: [],
        tool_calling: null,
        reasoning: null,
        context_window: null,
        installed: null,
        pinned_surfaces: [],
        runtime_state: 'unknown',
      },
    ],
  };
}
function pending<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

it('bounds rendered models while keeping later pages reachable and reload returning to start', async () => {
  const chunk = (offset: number): CachedModelPage => ({
    ...page(),
    total: 400,
    next_cursor: offset < 300 ? String(offset + 100) : null,
    items: Array.from(
      { length: 100 },
      (_, i) => page(`Model ${String(offset + i).padStart(3, '0')}`).items[0],
    ),
  });
  const load = vi.fn(
    async (_provider?: string, _query?: string, cursor?: string) =>
      chunk(Number(cursor ?? 0)),
  );
  const view = render(<ModelCatalog load={load} loadProviders={providers} />);
  await screen.findByText('Model 099 · Local engine');
  for (const end of [199, 299, 399]) {
    fireEvent.click(screen.getByRole('button', { name: 'Load more models' }));
    await screen.findByText(`Model ${end} · Local engine`);
    expect(
      view.container.querySelectorAll('.settings-results > li'),
    ).toHaveLength(200);
  }
  expect(
    screen.queryByText('Model 000 · Local engine'),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/Showing entries 201–400/)).toBeVisible();
  expect(load.mock.calls.map((call) => call[2])).toEqual([
    undefined,
    '100',
    '200',
    '300',
  ]);
  fireEvent.click(screen.getByRole('button', { name: 'Reload saved models' }));
  await screen.findByText('Model 000 · Local engine');
  expect(
    view.container.querySelectorAll('.settings-results > li'),
  ).toHaveLength(100);
  expect(screen.queryByText(/Showing entries/)).not.toBeInTheDocument();
});

it('shows saved provider facts without inventing runtime readiness or timestamps', async () => {
  const load = vi.fn(async () => snapshot);
  render(
    <MemoryRouter>
      <ProviderStatus load={load} />
    </MemoryRouter>,
  );
  expect(
    await screen.findByRole('link', { name: /Local engine/ }),
  ).toHaveAttribute('href', '/settings/models?provider=local');
  expect(
    screen.getByText(
      /Account access and runtime readiness have not been checked/,
    ),
  ).toHaveTextContent('No dated catalog snapshot');
  expect(load).toHaveBeenCalledTimes(1);
});

it('redacts provider failures and supports explicit retry', async () => {
  const load = vi
    .fn()
    .mockRejectedValueOnce(new Error('private secret'))
    .mockResolvedValue(snapshot);
  render(
    <MemoryRouter>
      <ProviderStatus load={load} />
    </MemoryRouter>,
  );
  expect(
    await screen.findByText('Row-Bot could not complete this request.'),
  ).toBeVisible();
  expect(screen.queryByText(/private secret/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Reload saved status' }));
  expect(
    await screen.findByRole('link', { name: /Local engine/ }),
  ).toBeVisible();
  expect(load).toHaveBeenCalledTimes(2);
});

it('aborts old provider reads and excludes their late response', async () => {
  const old = pending<ProviderStatusSnapshot>();
  const load = vi.fn((_signal?: AbortSignal) => old.promise);
  const { rerender } = render(
    <MemoryRouter>
      <ProviderStatus load={load} />
    </MemoryRouter>,
  );
  const next = vi.fn(async () => ({ ...snapshot, providers: [] }));
  rerender(
    <MemoryRouter>
      <ProviderStatus load={next} />
    </MemoryRouter>,
  );
  expect(await screen.findByText('No saved providers')).toBeVisible();
  expect(load.mock.calls[0][0]?.aborted).toBe(true);
  await act(async () => old.resolve(snapshot));
  expect(
    screen.queryByRole('link', { name: /Local engine/ }),
  ).not.toBeInTheDocument();
});

it('loads bounded pages only on request and preserves unknown model capabilities', async () => {
  const load = vi
    .fn()
    .mockResolvedValueOnce(page('First model', 'next'))
    .mockResolvedValueOnce(page('Second model'));
  render(<ModelCatalog load={load} loadProviders={providers} />);
  expect(await screen.findByText('First model · Local engine')).toBeVisible();
  fireEvent.click(screen.getByText('First model · Local engine'));
  expect(screen.getAllByText('Unknown')).toHaveLength(6);
  expect(load).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Load more models' }));
  expect(await screen.findByText('Second model · Local engine')).toBeVisible();
  expect(screen.getByText('First model · Local engine')).toBeVisible();
  expect(load.mock.calls[1].slice(0, 3)).toEqual([undefined, '', 'next']);
});

it('searches explicitly and rejects late pages after filters change', async () => {
  const late = pending<CachedModelPage>();
  const load = vi
    .fn()
    .mockResolvedValueOnce(page('First model', 'next'))
    .mockImplementationOnce(() => late.promise)
    .mockResolvedValueOnce(page('Filtered model'));
  render(<ModelCatalog load={load} loadProviders={providers} />);
  await screen.findByText('First model · Local engine');
  fireEvent.click(screen.getByRole('button', { name: 'Load more models' }));
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search models' }), {
    target: { value: 'filtered' },
  });
  expect(load).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  expect(
    await screen.findByText('Filtered model · Local engine'),
  ).toBeVisible();
  expect(load.mock.calls[1][3].aborted).toBe(true);
  await act(async () => late.resolve(page('Obsolete model')));
  expect(
    screen.queryByText('Obsolete model · Local engine'),
  ).not.toBeInTheDocument();
  expect(load.mock.calls[2].slice(0, 3)).toEqual([
    undefined,
    'filtered',
    undefined,
  ]);
});

it.each(['revision', 'expired'])(
  'requires reload after a %s model cursor change',
  async (cause) => {
    const load = vi.fn().mockResolvedValueOnce(page('First model', 'next'));
    if (cause === 'revision')
      load.mockResolvedValueOnce({ ...page('Changed model'), revision: 'two' });
    else load.mockRejectedValueOnce({ code: 'cursor_expired' });
    load.mockResolvedValueOnce(page('Reloaded model', 'new-next'));
    render(<ModelCatalog load={load} loadProviders={providers} />);
    await screen.findByText('First model · Local engine');
    fireEvent.click(screen.getByRole('button', { name: 'Load more models' }));
    expect(
      await screen.findByText(
        'The catalog changed. Reload saved models to continue.',
      ),
    ).toBeVisible();
    expect(
      screen.queryByText('Changed model · Local engine'),
    ).not.toBeInTheDocument();
    const more = screen.getByRole('button', { name: 'Load more models' });
    expect(more).toBeDisabled();
    fireEvent.click(more);
    expect(load).toHaveBeenCalledTimes(2);
    fireEvent.click(
      screen.getByRole('button', { name: 'Reload saved models' }),
    );
    await screen.findByText('Reloaded model · Local engine');
    expect(
      screen.getByRole('button', { name: 'Load more models' }),
    ).toBeEnabled();
    expect(load.mock.calls[2][2]).toBeUndefined();
  },
);

it('keeps a deep-linked provider filter when its separate metadata read fails', async () => {
  const load = vi.fn(async () => ({ ...page(), items: [], total: 0 }));
  render(
    <ModelCatalog
      load={load}
      loadProviders={async () => {
        throw new Error('unavailable');
      }}
      initialProvider="saved-provider"
    />,
  );
  expect(await screen.findByText('No matching saved models')).toBeVisible();
  expect(screen.getByRole('combobox', { name: 'Provider' })).toHaveValue(
    'saved-provider',
  );
  await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
  expect(load.mock.calls[0]?.length).toBe(4);
});
