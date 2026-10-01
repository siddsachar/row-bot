import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type {
  EntitySummaryPage,
  KnowledgeSettingsSnapshot,
  SettingsSnapshot,
} from '../../api/types';
import MemorySettings, { type KnowledgeMaintenanceIO } from './MemorySettings';
import {
  SettingsDraftOwner,
  type SettingsMutationIO,
} from './SettingsSnapshotPanels';

const catalogRevision = 'b'.repeat(64);

const snapshot: KnowledgeSettingsSnapshot = {
  availability: 'available',
  memory_available: true,
  memory_enabled: true,
  entities: 599,
  relations: 956,
  entity_types: [
    { kind: 'fact', count: 411 },
    { kind: 'person', count: 23 },
  ],
  connected_components: 60,
  largest_component: 538,
  isolated_entities: 57,
  status_counts: { active: 597, needs_review: 0, superseded: 0, archived: 2 },
};

function catalog(
  availability: EntitySummaryPage['availability'] = 'available',
): EntitySummaryPage {
  return {
    schema_version: 1,
    revision: catalogRevision,
    availability,
    total: availability === 'available' ? 599 : null,
    next_cursor: null,
    items: [],
  };
}

function pending<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

it('keeps only settings: the memory switch, graph health and a way to Knowledge', async () => {
  render(
    <MemorySettings snapshot={snapshot} loadCatalog={async () => catalog()} />,
  );
  expect(screen.getByRole('switch', { name: 'Enable Memory' })).toBeChecked();
  expect(screen.getByRole('link', { name: 'Open Knowledge' })).toHaveAttribute(
    'href',
    '/app-v2/?tab=knowledge',
  );
  // Graph health: totals, types and how connected the graph is.
  expect(screen.getByText('599 memories · 956 links')).toBeVisible();
  expect(screen.getByText('Fact 411 · Person 23')).toBeVisible();
  expect(screen.getByText('The largest holds 538 memories.')).toBeVisible();
  // Browsing, search, filters, review, bulk actions and the logs live in
  // Knowledge now (B264).
  await act(async () => undefined);
  expect(screen.queryByRole('searchbox')).toBeNull();
  expect(screen.queryByRole('combobox')).toBeNull();
  expect(screen.queryByRole('checkbox')).toBeNull();
  expect(screen.queryByRole('list')).toBeNull();
  expect(
    screen.queryByRole('heading', { name: 'Stored Knowledge' }),
  ).toBeNull();
  expect(screen.queryByText('Recent recall decisions')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Add memory' })).toBeNull();
});

it('saves the Memory switch on one click with its exact reviewed request', async () => {
  const saved = { revision: 'saved-b' } as SettingsSnapshot;
  const mutation = {
    revision: 'saved-a',
    page: 'knowledge',
    review: vi.fn<SettingsMutationIO['review']>(async (request) => ({
      schema_version: 1,
      operation: 'settings.update',
      settings_revision: request.settings_revision,
      page: request.page,
      field: request.field,
      value_summary: 'disabled',
      secret: false,
      action_digest: 'd'.repeat(64),
      review_id: 'review-memory',
    })),
    execute: vi.fn<SettingsMutationIO['execute']>(
      async (_request, _review, commandId) => ({
        command_id: commandId,
        status: 'completed',
        settings_revision: 'saved-b',
        snapshot: saved,
      }),
    ),
    receipt: vi.fn<SettingsMutationIO['receipt']>(),
    drafts: new SettingsDraftOwner(),
    onSnapshot: vi.fn(),
  } satisfies SettingsMutationIO;
  render(<MemorySettings snapshot={snapshot} settingsMutation={mutation} />);
  fireEvent.click(screen.getByRole('switch', { name: 'Enable Memory' }));
  await act(async () => undefined);
  expect(mutation.review).toHaveBeenCalledWith({
    settings_revision: 'saved-a',
    page: 'knowledge',
    field: 'memory_enabled',
    value: false,
  });
  expect(mutation.execute).toHaveBeenCalledTimes(1);
  expect(mutation.execute.mock.calls[0][0]).toEqual(
    mutation.review.mock.calls[0][0],
  );
  expect(mutation.onSnapshot).toHaveBeenCalledWith(saved);
});

it('Delete all reads the saved catalog itself and runs the reviewed deletion', async () => {
  const read = pending<EntitySummaryPage>();
  const review = {
    schema_version: 1 as const,
    action: 'knowledge.delete_all' as const,
    catalog_revision: catalogRevision,
    targets: [],
    entity_count: 599,
    side_effects: ['entities' as const],
    action_digest: 'c'.repeat(64),
    review_id: 'd'.repeat(64),
  };
  const maintenance: KnowledgeMaintenanceIO = {
    review: vi.fn(async () => review),
    execute: vi.fn(async (_review, commandId) => ({
      command_id: commandId,
      status: 'completed' as const,
      action: 'knowledge.delete_all' as const,
      deleted: [],
      stale: [],
      missing: [],
      cleanup: {},
      code: null,
    })),
    receipt: vi.fn(),
  };
  const onMutation = vi.fn();
  render(
    <MemorySettings
      snapshot={snapshot}
      loadCatalog={() => read.promise}
      maintenance={maintenance}
      onMutation={onMutation}
    />,
  );
  const deleteAll = screen.getByRole('button', {
    name: 'Delete all knowledge (599)',
  });
  // It is bound to the exact saved catalog, so it waits for that read.
  expect(deleteAll).toBeDisabled();
  await act(async () => read.resolve(catalog()));
  expect(deleteAll).toBeEnabled();
  fireEvent.click(deleteAll);
  await act(async () => undefined);
  expect(maintenance.review).toHaveBeenCalledWith(
    'knowledge.delete_all',
    catalogRevision,
    [],
  );
  const confirm = screen.getByRole('region', {
    name: 'Reviewed knowledge deletion',
  });
  expect(confirm).toHaveTextContent('599 entries will be removed.');
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Confirm permanent deletion' }),
  );
  await act(async () => undefined);
  expect(maintenance.execute).toHaveBeenCalledWith(review, expect.any(String));
  expect(onMutation).toHaveBeenCalledOnce();
});

it.each(['missing', 'unavailable'] as const)(
  'keeps Delete all off while the saved catalog is %s',
  async (availability) => {
    const maintenance: KnowledgeMaintenanceIO = {
      review: vi.fn(),
      execute: vi.fn(),
      receipt: vi.fn(),
    };
    render(
      <MemorySettings
        snapshot={snapshot}
        loadCatalog={async () => catalog(availability)}
        maintenance={maintenance}
      />,
    );
    await act(async () => undefined);
    expect(
      screen.getByRole('button', { name: 'Delete all knowledge (599)' }),
    ).toBeDisabled();
  },
);
