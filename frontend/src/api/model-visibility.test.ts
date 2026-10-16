import { afterEach, expect, it, vi } from 'vitest';
import { ClientController } from './controller';
import { FixtureTransport } from './fixtures';
import { applyVisibility, markModels, visibilityOf } from './model-visibility';
import type {
  HandshakeView,
  ModelChoice,
  ModelVisibilityMutation,
  ModelVisibilityState,
} from './types';

const model = (
  name: string,
  extra: Partial<ModelChoice> = {},
): ModelChoice => ({
  provider_id: 'ollama',
  model_ref: `model:ollama:${name}`,
  label: `${name} - Ollama Local`,
  available: true,
  billing: 'local',
  ...extra,
});
const listed = [
  model('gemma3:4b', { hidden: true }),
  model('llama3.1:8b', { favourite_rank: 2 }),
  model('qwen3:8b', { favourite_rank: 1 }),
  model('mistral:7b'),
];
const marks = (models: readonly ModelChoice[] | undefined) =>
  (models ?? []).map((item) => [
    item.model_ref.split(':').slice(2).join(':'),
    item.favourite_rank ?? (item.hidden ? 'hidden' : '-'),
  ]);

class ModelsTransport extends FixtureTransport {
  saves: ModelVisibilityMutation[] = [];
  answer: (body: ModelVisibilityMutation) => Promise<ModelVisibilityState> =
    async (body) => applyVisibility(listed, body);
  async connect(signal?: AbortSignal): Promise<HandshakeView> {
    return { ...(await super.connect(signal)), models: listed };
  }
  updateModelVisibility(body: ModelVisibilityMutation) {
    this.saves.push(body);
    return this.answer(body);
  }
}
const clients: ClientController[] = [];
afterEach(() => clients.splice(0).forEach((value) => value.dispose()));

it('reads favourites in order and hidden models from a marked list', () => {
  expect(visibilityOf(listed)).toEqual({
    schema_version: 1,
    favourites: ['model:ollama:qwen3:8b', 'model:ollama:llama3.1:8b'],
    hidden: ['model:ollama:gemma3:4b'],
  });
  const hidden = applyVisibility(listed, {
    action: 'hide',
    model_ref: 'model:ollama:qwen3:8b',
  });
  expect(marks(markModels(listed, hidden))).toEqual([
    ['gemma3:4b', 'hidden'],
    ['llama3.1:8b', 1],
    ['qwen3:8b', 'hidden'],
    ['mistral:7b', '-'],
  ]);
  expect(
    applyVisibility(listed, { action: 'show_all', model_ref: null }).hidden,
  ).toEqual([]);
});

it('changes the composer list at once and settles on what was saved', async () => {
  const transport = new ModelsTransport();
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  let settle!: (state: ModelVisibilityState) => void;
  transport.answer = () => new Promise((resolve) => (settle = resolve));
  const saving = controller.updateModelVisibility({
    action: 'favourite',
    model_ref: 'model:ollama:mistral:7b',
  });
  // Shown before the save settles: the model is the newest favourite.
  expect(marks(controller.getSnapshot().handshake?.models)).toEqual([
    ['gemma3:4b', 'hidden'],
    ['llama3.1:8b', 2],
    ['qwen3:8b', 1],
    ['mistral:7b', 3],
  ]);
  settle({
    schema_version: 1,
    favourites: ['model:ollama:mistral:7b'],
    hidden: [],
  });
  await saving;
  expect(marks(controller.getSnapshot().handshake?.models)).toEqual([
    ['gemma3:4b', '-'],
    ['llama3.1:8b', '-'],
    ['qwen3:8b', '-'],
    ['mistral:7b', 1],
  ]);
  expect(transport.saves).toEqual([
    { action: 'favourite', model_ref: 'model:ollama:mistral:7b' },
  ]);
});

it('puts the list back when a change does not save', async () => {
  const transport = new ModelsTransport();
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  transport.answer = vi.fn(async () => {
    throw { code: 'model_settings_unavailable' };
  });
  await expect(
    controller.updateModelVisibility({
      action: 'hide',
      model_ref: 'model:ollama:qwen3:8b',
    }),
  ).rejects.toMatchObject({ code: 'model_settings_unavailable' });
  expect(marks(controller.getSnapshot().handshake?.models)).toEqual(
    marks(listed),
  );
});
