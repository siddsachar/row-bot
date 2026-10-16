import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  LocalRuntimeModel,
  LocalRuntimeSnapshot,
  ModelPickerOption,
  ModelVisibilityMutation,
} from '../../api/types';
import { applyVisibility } from '../../api/model-visibility';
import LocalModelSettings from './LocalModelSettings';

const local = (
  name: string,
  extra: Partial<LocalRuntimeModel> = {},
): LocalRuntimeModel => ({
  model_ref: `model:ollama:${name}`,
  name,
  agent_ready: true,
  ...extra,
});
const runtime = (models: LocalRuntimeModel[]): LocalRuntimeSnapshot => ({
  schema_version: 1,
  state: 'running',
  platform: 'windows',
  download_url: 'https://ollama.com/download',
  models,
});
const cloudHidden: ModelPickerOption = {
  selection_ref: 'model:codex:gpt-5.6-luna',
  provider_id: 'codex',
  label: 'GPT-5.6 Luna - ChatGPT',
  source: 'catalog',
  available: true,
  hidden: true,
};

function show(
  models: LocalRuntimeModel[],
  pickerOptions: ModelPickerOption[] = [],
) {
  const saves: ModelVisibilityMutation[] = [];
  let listed = models;
  const controller = {
    updateModelVisibility: vi.fn(async (body: ModelVisibilityMutation) => {
      saves.push(body);
      const state = applyVisibility(listed, body);
      listed = listed.map((model) => ({
        ...model,
        favourite_rank: state.favourites.indexOf(model.model_ref) + 1 || null,
        hidden: state.hidden.includes(model.model_ref),
      }));
      return state;
    }),
  } as unknown as ClientController;
  const onChanged = vi.fn();
  render(
    <LocalModelSettings
      controller={controller}
      runtime={runtime(models)}
      pickerOptions={pickerOptions}
      onChanged={onChanged}
    />,
  );
  return { saves, onChanged };
}

const names = (list: HTMLElement) =>
  within(list)
    .getAllByRole('listitem')
    .map((item) => item.querySelector('strong')?.textContent);

it('lists the local models, favourites first, with their state in words', () => {
  show([
    local('gemma3:4b', { hidden: true }),
    local('llama3.1:8b'),
    local('qwen3:8b', { favourite_rank: 1 }),
    local('tiny-chat', { agent_ready: false }),
  ]);
  const group = screen.getByRole('region', { name: 'On this computer' });
  expect(group).toHaveTextContent(
    'Favourites come first in every model list. Hidden models wait under More models and still work.',
  );
  expect(group).toHaveTextContent('1 favourite · 1 hidden');
  const list = screen.getByRole('list', { name: 'Models on this computer' });
  expect(names(list)).toEqual(['qwen3:8b', 'llama3.1:8b', 'tiny-chat']);
  expect(list).toHaveTextContent('Listed first');
  expect(list).toHaveTextContent('Chat only');
  expect(
    screen.getByRole('button', { name: 'Remove qwen3:8b from favourites' }),
  ).toHaveAttribute('aria-pressed', 'true');
  // Hidden models wait in their own collapsed list, still listed.
  expect(screen.getByText('Hidden (1)')).toBeVisible();
  expect(
    screen.getByRole('list', { name: 'Hidden models', hidden: true }),
  ).not.toBeVisible();
});

it('favourites, hides and shows from the row, and shows every hidden model', async () => {
  const { saves, onChanged } = show(
    [local('gemma3:4b'), local('llama3.1:8b'), local('qwen3:8b')],
    [cloudHidden],
  );
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Add llama3.1:8b to favourites' }),
    ),
  );
  const list = screen.getByRole('list', { name: 'Models on this computer' });
  expect(names(list)).toEqual(['llama3.1:8b', 'gemma3:4b', 'qwen3:8b']);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Hide gemma3:4b' })),
  );
  expect(names(list)).toEqual(['llama3.1:8b', 'qwen3:8b']);
  // One local and one cloud model are hidden: both count.
  expect(
    screen.getByRole('region', { name: 'On this computer' }),
  ).toHaveTextContent('1 favourite · 2 hidden');
  fireEvent.click(screen.getByText('Hidden (1)'));
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Show gemma3:4b' })),
  );
  expect(names(list)).toEqual(['llama3.1:8b', 'gemma3:4b', 'qwen3:8b']);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Show all hidden' })),
  );
  expect(saves).toEqual([
    { action: 'favourite', model_ref: 'model:ollama:llama3.1:8b' },
    { action: 'hide', model_ref: 'model:ollama:gemma3:4b' },
    { action: 'show', model_ref: 'model:ollama:gemma3:4b' },
    { action: 'show_all', model_ref: null },
  ]);
  expect(onChanged).toHaveBeenCalledTimes(4);
});

it('says why nothing is listed, and stays away when there is nothing to manage', () => {
  const { rerender } = render(
    <LocalModelSettings
      controller={{} as ClientController}
      runtime={{ ...runtime([]), state: 'installed' }}
      pickerOptions={[]}
      onChanged={vi.fn()}
    />,
  );
  expect(
    screen.getByText(
      'Ollama isn’t running. Start it to see the models on this computer.',
    ),
  ).toBeVisible();
  rerender(
    <LocalModelSettings
      controller={{} as ClientController}
      runtime={{ ...runtime([]), state: 'not_installed' }}
      pickerOptions={[]}
      onChanged={vi.fn()}
    />,
  );
  expect(screen.queryByRole('region', { name: 'On this computer' })).toBeNull();
  // A hidden cloud model can still be shown again from here.
  rerender(
    <LocalModelSettings
      controller={{} as ClientController}
      runtime={{ ...runtime([]), state: 'not_installed' }}
      pickerOptions={[cloudHidden]}
      onChanged={vi.fn()}
    />,
  );
  expect(screen.getByRole('button', { name: 'Show all hidden' })).toBeVisible();
});
