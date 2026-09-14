import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type {
  CachedModelPage,
  ProviderConfigurationPage,
  ProviderConfigurationReview,
} from '../../api/types';
import { DefaultModelSession } from './DefaultModelSettings';
import ModelSurfaceSettings from './ModelSurfaceSettings';

const revision = 'a'.repeat(64);
const nextRevision = 'b'.repeat(64);
const configuration: ProviderConfigurationPage = {
  schema_version: 1,
  revision,
  items: [],
  total: 0,
  next_cursor: null,
  profiles: [],
};
const models: CachedModelPage = {
  schema_version: 1,
  revision,
  generated_at: null,
  freshness: 'unavailable',
  total: 3,
  next_cursor: null,
  items: [
    {
      provider_id: 'local',
      model_id: 'vision-model',
      selection_ref: 'model:local:vision-model',
      display_name: 'Vision model',
      provider_display_name: 'Local provider',
      categories: ['vision'],
      input_modalities: ['text', 'image'],
      output_modalities: ['text'],
      tool_calling: true,
      reasoning: null,
      context_window: 32768,
      installed: true,
      pinned_surfaces: ['vision'],
      runtime_state: 'unknown',
    },
    {
      provider_id: 'image-provider',
      model_id: 'image-model',
      selection_ref: 'model:image-provider:image-model',
      display_name: 'Image model',
      provider_display_name: 'Image provider',
      categories: ['image'],
      input_modalities: ['text'],
      output_modalities: ['image'],
      tool_calling: false,
      reasoning: null,
      context_window: null,
      installed: null,
      pinned_surfaces: [],
      runtime_state: 'unknown',
    },
    {
      provider_id: 'video-provider',
      model_id: 'video-model',
      selection_ref: 'model:video-provider:video-model',
      display_name: 'Video model',
      provider_display_name: 'Video provider',
      categories: ['video'],
      input_modalities: ['text', 'image'],
      output_modalities: ['video'],
      tool_calling: false,
      reasoning: null,
      context_window: null,
      installed: null,
      pinned_surfaces: ['video'],
      runtime_state: 'unknown',
    },
  ],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function fixture(session = new DefaultModelSession()) {
  session.get('snapshot', null);
  session.set('snapshot', {
    schema_version: 1,
    revision,
    selection_ref: models.items[0].selection_ref,
    provider_id: models.items[0].provider_id,
    model_id: models.items[0].model_id,
    saved_state: 'saved',
    runtime_state: 'unknown',
  });
  return {
    session,
    loadModels: vi.fn().mockResolvedValue(models),
    loadConfiguration: vi.fn().mockResolvedValue(configuration),
    review: vi.fn(
      async (
        operation: ProviderConfigurationReview['operation'],
        configurationRevision: string,
      ): Promise<ProviderConfigurationReview> => ({
        operation,
        configuration_revision: configurationRevision,
        action_digest: 'digest',
        nonce: 'original-nonce',
      }),
    ),
    apply: vi.fn().mockResolvedValue({ configuration_revision: nextRevision }),
    receipt: vi.fn().mockResolvedValue({
      command_id: crypto.randomUUID(),
      status: 'uncertain',
      configuration_revision: revision,
    }),
    onChanged: vi.fn(),
  };
}

function view(props: ReturnType<typeof fixture>) {
  return render(
    <MemoryRouter>
      <ModelSurfaceSettings {...props} />
    </MemoryRouter>,
  );
}

it('loads only passive saved owners and renders the NiceGUI role order', async () => {
  const props = fixture();
  view(props);
  expect(
    await screen.findByRole('heading', { name: 'Vision', level: 4 }),
  ).toBeVisible();
  expect(
    screen
      .getAllByRole('heading', { level: 4 })
      .map((item) => item.textContent),
  ).toEqual(['Vision', 'Image', 'Video', 'Agent runtime & delegation']);
  expect(screen.getByLabelText('Vision pinned choice')).toHaveValue(
    'model:local:vision-model',
  );
  expect(screen.getByLabelText('Image pinned choice')).toHaveValue('');
  expect(screen.getByLabelText('Video pinned choice')).toHaveValue(
    'model:video-provider:video-model',
  );
  expect(screen.getAllByText('Runtime not checked')).toHaveLength(3);
  expect(screen.getByText(/Local provider · installed locally/)).toBeVisible();
  expect(
    screen
      .getByRole('heading', { name: 'Agent runtime & delegation' })
      .closest('details'),
  ).not.toHaveAttribute('open');
  fireEvent.click(screen.getByText('Advanced context'));
  expect(
    screen.getByText('Saved catalog context window: 32,768 tokens.'),
  ).toBeVisible();
  expect(
    screen.getByRole('link', { name: 'Provider connections' }),
  ).toHaveAttribute('href', '/settings/providers');
  expect(props.review).not.toHaveBeenCalled();
  expect(props.apply).not.toHaveBeenCalled();
});

it('reviews and saves exact provider-qualified picker membership once', async () => {
  const props = fixture();
  view(props);
  await screen.findByLabelText('Image pinned choice');
  fireEvent.change(screen.getByLabelText('Image pinned choice'), {
    target: { value: 'model:image-provider:image-model' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Review add Image picker choice' }),
  );
  await waitFor(() =>
    expect(props.review).toHaveBeenCalledWith(
      'provider.model.pin',
      revision,
      {
        provider_id: 'image-provider',
        model_id: 'image-model',
        surface: 'image',
      },
      expect.any(AbortSignal),
    ),
  );
  const save = await screen.findByRole('button', {
    name: 'Save reviewed picker change',
  });
  fireEvent.click(save);
  fireEvent.click(save);
  await screen.findByText(
    'Image picker membership saved. No provider or model was started.',
  );
  expect(props.apply).toHaveBeenCalledExactlyOnceWith(
    'provider.model.pin',
    revision,
    {
      provider_id: 'image-provider',
      model_id: 'image-model',
      surface: 'image',
    },
    expect.any(String),
    expect.objectContaining({ nonce: 'original-nonce' }),
  );
  expect(
    screen.getByRole('button', { name: 'Review remove Image picker choice' }),
  ).toBeEnabled();
  expect(props.onChanged).toHaveBeenCalledOnce();
});

it('retains an uncertain picker command across route remount and reads only its receipt', async () => {
  const session = new DefaultModelSession();
  const props = fixture(session);
  const uncertain = deferred<{ configuration_revision: string }>();
  props.apply.mockReturnValue(uncertain.promise);
  const first = view(props);
  await screen.findByLabelText('Vision pinned choice');
  fireEvent.click(
    screen.getByRole('button', { name: 'Review remove Vision picker choice' }),
  );
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Save reviewed picker change',
    }),
  );
  const commandId = props.apply.mock.calls[0][3];
  first.unmount();
  await act(async () => uncertain.reject({ code: 'operation_uncertain' }));
  props.receipt.mockResolvedValue({
    command_id: commandId,
    status: 'completed',
    configuration_revision: nextRevision,
  });
  view(props);
  expect(session.hasRetained()).toBe(true);
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original picker receipt' }),
  );
  await screen.findByText(
    'Vision picker membership saved. No provider or model was started.',
  );
  expect(props.receipt).toHaveBeenCalledWith(
    commandId,
    expect.any(AbortSignal),
  );
  expect(props.apply).toHaveBeenCalledOnce();
  expect(session.hasRetained()).toBe(false);
});

it('bounds automatic passive pagination and points to explicit catalog search', async () => {
  const props = fixture();
  const pageItems = Array.from({ length: 50 }, (_, index) => ({
    ...models.items[0],
    model_id: `vision-${index}`,
    selection_ref: `model:local:vision-${index}`,
    display_name: `Vision ${index}`,
    pinned_surfaces: [] as string[],
  }));
  props.loadModels.mockImplementation(
    async (_provider, _query, cursor?: string) => ({
      ...models,
      total: 250,
      items: pageItems.map((item) => ({
        ...item,
        model_id: `${item.model_id}-${cursor ?? 'first'}`,
        selection_ref: `${item.selection_ref}-${cursor ?? 'first'}`,
        display_name: `${item.display_name} ${cursor ?? 'first'}`,
      })),
      next_cursor:
        cursor === 'page-3'
          ? 'page-4'
          : `page-${Number(cursor?.at(-1) ?? 0) + 1}`,
    }),
  );
  view(props);
  expect(
    await screen.findByText(
      'Showing the first 200 saved models. Search the catalog below to manage additional choices.',
    ),
  ).toBeVisible();
  expect(props.loadModels).toHaveBeenCalledTimes(4);
});
