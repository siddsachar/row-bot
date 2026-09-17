import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { CachedModelPage, ModelCatalogSummary } from '../../api/types';
import ModelCatalog from './ModelCatalog';

const row: CachedModelPage['items'][number] = {
  provider_id: 'codex',
  model_id: 'gpt-6-astra',
  selection_ref: 'model:codex:gpt-6-astra',
  display_name: 'GPT-6-Astra',
  provider_display_name: 'ChatGPT / Codex',
  categories: ['chat', 'vision'],
  input_modalities: ['text', 'image'],
  output_modalities: ['text'],
  tool_calling: true,
  reasoning: null,
  context_window: 272000,
  installed: true,
  pinned_surfaces: ['chat'],
  configured: true,
  runtime_ready: true,
  status_reason: '',
  runtime_mode: 'agent',
  source: 'saved_catalog',
  runtime_state: 'unknown',
};
const summary: ModelCatalogSummary = {
  schema_version: 1,
  revision: 'one',
  surface: 'chat',
  providers: [
    {
      provider_id: 'codex',
      display_name: 'ChatGPT / Codex',
      total: 81,
      ready: 80,
      pinned: 1,
    },
  ],
};
function page(
  items: CachedModelPage['items'],
  next: string | null = null,
): CachedModelPage {
  return {
    schema_version: 1,
    revision: 'one',
    generated_at: 1000,
    freshness: 'fresh',
    total: 81,
    next_cursor: next,
    items,
  };
}
function fixture(
  first = page([row], 'next'),
  second = page([
    {
      ...row,
      model_id: 'gpt-5.5',
      selection_ref: 'model:codex:gpt-5.5',
      display_name: 'GPT-5.5',
    },
  ]),
) {
  const controller = {
    modelCatalogSummary: vi.fn(async (surface: string) => ({
      ...summary,
      surface,
    })),
    modelCatalogPage: vi.fn(
      async (
        _surface: string,
        _provider?: string,
        _query?: string,
        cursor?: string,
      ) => (cursor ? second : first),
    ),
  } as unknown as ClientController;
  const onDefault = vi.fn(async () => {});
  const onPin = vi.fn(async () => {});
  const onChanged = vi.fn(async () => {});
  return { controller, onDefault, onPin, onChanged };
}
function show(props = fixture()) {
  render(<ModelCatalog {...props} defaults={{ chat: '' }} />);
  return props;
}

it('shows provider counts first and loads 80-row pages only after a provider opens', async () => {
  const eighty = Array.from({ length: 80 }, (_, index) => ({
    ...row,
    model_id: `model-${index}`,
    selection_ref: `model:codex:model-${index}`,
    display_name: `Model ${index}`,
  }));
  const props = show(fixture(page(eighty, 'next')));
  expect(
    await screen.findByText('81 chat model(s) · 80 ready · 1 pinned'),
  ).toBeVisible();
  expect(props.controller.modelCatalogPage).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Open' }));
  expect(await screen.findByText('Model 79')).toBeVisible();
  expect(screen.getAllByRole('button', { name: /Unpin Model/ })).toHaveLength(
    80,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Show more models' }));
  expect(await screen.findByText('GPT-5.5')).toBeVisible();
  expect(props.controller.modelCatalogPage).toHaveBeenCalledTimes(2);
});

it('filters category and search before mounting rows', async () => {
  const props = show();
  await screen.findByText('Providers');
  fireEvent.click(screen.getByRole('tab', { name: 'VISION' }));
  await waitFor(() =>
    expect(props.controller.modelCatalogSummary).toHaveBeenCalledWith(
      'vision',
      expect.anything(),
    ),
  );
  expect(props.controller.modelCatalogPage).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search models' }), {
    target: { value: 'astra' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search models' }));
  await screen.findByText('GPT-6-Astra');
  expect(props.controller.modelCatalogPage).toHaveBeenCalledWith(
    'vision',
    undefined,
    'astra',
    undefined,
    expect.anything(),
  );
});

it('pins and applies actual defaults through compact icons; unavailable rows are disabled', async () => {
  const props = show(
    fixture(
      page(
        [
          row,
          {
            ...row,
            model_id: 'offline',
            selection_ref: 'model:codex:offline',
            display_name: 'Offline',
            runtime_ready: false,
            status_reason: 'Connect the provider.',
          },
        ],
        null,
      ),
    ),
  );
  await screen.findByText('Providers');
  fireEvent.click(screen.getByRole('button', { name: 'Open' }));
  await screen.findByText('GPT-6-Astra');
  fireEvent.click(
    screen.getByRole('button', { name: 'Unpin GPT-6-Astra for chat' }),
  );
  await waitFor(() => expect(props.onPin).toHaveBeenCalledWith('chat', row));
  fireEvent.click(
    screen.getByRole('button', { name: 'Set GPT-6-Astra as chat default' }),
  );
  await waitFor(() =>
    expect(props.onDefault).toHaveBeenCalledWith('chat', row),
  );
  expect(
    screen.getByRole('button', { name: 'Unpin Offline for chat' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Set Offline as chat default' }),
  ).toBeDisabled();
  expect(screen.queryByText(/Brain draft/)).not.toBeInTheDocument();
});

it('offers a reload only after the bounded catalog cursor expires', async () => {
  const props = fixture();
  vi.spyOn(props.controller, 'modelCatalogPage').mockImplementation(
    async (_surface, _provider, _query, cursor) => {
      if (cursor) throw { code: 'cursor_expired' };
      return page([row], 'next');
    },
  );
  show(props);
  await screen.findByText('Providers');
  fireEvent.click(screen.getByRole('button', { name: 'Open' }));
  await screen.findByText('GPT-6-Astra');
  expect(
    screen.queryByRole('button', { name: 'Reload catalog results' }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Show more models' }));
  expect(
    await screen.findByRole('button', { name: 'Reload catalog results' }),
  ).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Show more models' }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Reload catalog results' }),
  );
  await waitFor(() =>
    expect(props.controller.modelCatalogPage).toHaveBeenCalledTimes(3),
  );
  expect(
    screen.queryByRole('button', { name: 'Reload catalog results' }),
  ).not.toBeInTheDocument();
});
