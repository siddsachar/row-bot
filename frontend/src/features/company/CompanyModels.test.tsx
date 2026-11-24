import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  CachedModelPage,
  CompanyView,
  ModelCatalogSummary,
} from '../../api/types';
import CompanyModels from './CompanyModels';

const COMPANY = 'rowbot.example.test';

const joined: CompanyView = {
  schema_version: 1,
  state: 'joined',
  company: COMPANY,
  origin: `https://${COMPANY}`,
  name: 'Ada Lovelace',
  email: 'ada@example.test',
  joined_at: '2026-11-23T09:00:00+00:00',
  computer_name: 'Fixture laptop',
  rejoining: false,
  failure: null,
  notice: null,
  on_this_computer: true,
  model_count: 1,
  suggested_model_ref: 'model:company:qwen3-32b',
  suggested_model_name: 'Qwen3 32B',
};

const qwen: CachedModelPage['items'][number] = {
  provider_id: 'company',
  model_id: 'qwen3-32b',
  selection_ref: 'model:company:qwen3-32b',
  display_name: 'Qwen3 32B',
  provider_display_name: COMPANY,
  categories: ['chat'],
  input_modalities: ['text'],
  output_modalities: ['text'],
  tool_calling: true,
  reasoning: null,
  context_window: 131072,
  installed: true,
  configured: true,
  runtime_ready: true,
  status_reason: '',
  runtime_mode: 'agent',
  source: 'company_catalog',
  runtime_state: 'ready',
  favourite_rank: null,
};

const summary: ModelCatalogSummary = {
  schema_version: 1,
  revision: 'one',
  surface: 'chat',
  providers: [
    {
      provider_id: 'company',
      display_name: COMPANY,
      total: 1,
      ready: 1,
      pinned: 0,
    },
  ],
};

function show(view: CompanyView | null, row = qwen) {
  const company = vi.fn(async (_action: string) => {
    if (!view) throw new Error('capability_unavailable');
    return view;
  });
  const modelCatalogPage = vi.fn(async (): Promise<CachedModelPage> => ({
    schema_version: 1,
    revision: 'one',
    generated_at: 1000,
    freshness: 'fresh',
    total: 1,
    next_cursor: null,
    items: [row],
  }));
  const controller = {
    company,
    modelCatalogSummary: vi.fn(async () => summary),
    modelCatalogPage,
  } as unknown as ClientController;
  const onDefault = vi.fn(async () => {});
  render(
    <MemoryRouter>
      <CompanyModels
        controller={controller}
        defaults={{ chat: '' }}
        onDefault={onDefault}
        onChanged={vi.fn(async () => {})}
      />
    </MemoryRouter>,
  );
  return { company, modelCatalogPage, onDefault };
}

it('joined: the company’s models are their own group, named after it, and one can be the main model', async () => {
  const { company, modelCatalogPage, onDefault } = show(joined);

  expect(await screen.findByRole('heading', { name: COMPANY })).toBeVisible();
  expect(await screen.findByText('Qwen3 32B')).toBeVisible();
  expect(modelCatalogPage).toHaveBeenCalledWith(
    'chat',
    'company',
    '',
    undefined,
    expect.anything(),
  );
  expect(company.mock.calls.map(([action]) => action)).toEqual(['status']);

  fireEvent.click(
    screen.getByRole('button', { name: 'Set Qwen3 32B as chat default' }),
  );
  await waitFor(() =>
    expect(onDefault).toHaveBeenCalledWith(
      'chat',
      expect.objectContaining({ selection_ref: 'model:company:qwen3-32b' }),
    ),
  );
});

it('disconnected: the models stay listed, unavailable, with the way back to Settings › Company', async () => {
  show(
    { ...joined, state: 'disconnected' },
    {
      ...qwen,
      runtime_ready: false,
      runtime_state: 'unavailable',
      status_reason: 'Sign in again in Settings › Company.',
    },
  );

  expect(
    await screen.findByText('Your company disconnected this Row-Bot'),
  ).toBeVisible();
  expect(screen.getByRole('link', { name: 'Sign in again' })).toHaveAttribute(
    'href',
    '/settings/company',
  );
  expect(await screen.findByText('Qwen3 32B')).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Set Qwen3 32B as chat default' }),
  ).toBeDisabled();
});

it('not joined, or no company state at all: no group and no model list asked for', async () => {
  for (const view of [{ ...joined, state: 'not_joined' as const }, null]) {
    const { company, modelCatalogPage } = show(view);
    await waitFor(() => expect(company).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('heading', { name: COMPANY })).toBeNull();
    expect(modelCatalogPage).not.toHaveBeenCalled();
  }
});

it('the kinds of model the company turned off get one quiet line each, set by the company', async () => {
  show({
    ...joined,
    locked: [
      {
        setting: 'models.personal_api_keys',
        title: 'Allow personal API keys',
        value: 'Off',
        words: 'Your company turned off personal API keys',
      },
      {
        setting: 'models.consumer_sign_ins',
        title: 'Allow consumer sign-ins',
        value: 'Off',
        words: 'Your company turned off personal subscriptions',
      },
    ],
  });

  expect(
    await screen.findByText('Your company turned off personal API keys'),
  ).toBeVisible();
  expect(
    screen.getByText('Your company turned off personal subscriptions'),
  ).toBeVisible();
  expect(screen.getAllByText('Set by your company')).toHaveLength(2);
  expect(
    screen.getByText(': Allow personal API keys: Off'),
  ).toBeInTheDocument();
  // One line, not a list of the providers it hides.
  expect(screen.queryByText(/OpenAI|Anthropic/)).toBeNull();
});
