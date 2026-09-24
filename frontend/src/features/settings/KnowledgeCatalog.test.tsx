import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import type {
  DocumentSummaryPage,
  EntitySummaryPage,
  KnowledgeEntityDetail,
  KnowledgeSettingsSnapshot,
  SettingsSnapshot,
} from '../../api/types';
import DocumentsCatalog from './DocumentsCatalog';
import KnowledgeCatalog, {
  type KnowledgeMaintenanceIO,
  type KnowledgePageLoader,
} from './KnowledgeCatalog';
import {
  SettingsDraftOwner,
  type SettingsMutationIO,
} from './SettingsSnapshotPanels';

const revision = 'a'.repeat(64);
const catalogRevision = 'b'.repeat(64);
afterEach(() => vi.useRealTimers());

function item(subject = 'Saved thought') {
  return {
    id: subject.replaceAll(' ', '_'),
    entity_type: 'fact',
    subject,
    description: 'Saved description',
    updated_at: '2026-09-19T10:00:00Z',
    truncated: false,
    saved_state: 'saved' as const,
    semantic_state: 'unknown' as const,
  };
}

function page(
  subject = 'Saved thought',
  nextCursor: string | null = null,
): EntitySummaryPage {
  return {
    schema_version: 1,
    revision: catalogRevision,
    availability: 'available',
    total: nextCursor ? 2 : 1,
    next_cursor: nextCursor,
    items: [item(subject)],
  };
}

function detail(subject = 'Saved thought'): KnowledgeEntityDetail {
  return {
    schema_version: 1,
    availability: 'available',
    id: subject.replaceAll(' ', '_'),
    revision,
    entity_type: 'fact',
    subject,
    description: '<img src=x onerror=sentinel()>',
    status: 'needs_review',
    tier: 'core',
    source: 'manual',
    source_bucket: 'manual',
    confidence: 0.82,
    aliases: ['Memory alias'],
    alias_count: 1,
    tags: ['important'],
    tag_count: 1,
    created_at: '2026-09-18T10:00:00Z',
    updated_at: '2026-09-19T10:00:00Z',
    last_user_modified_at: '2026-09-19T09:00:00Z',
    last_evolved_at: '',
    last_recalled_at: '2026-09-19T09:30:00Z',
    recall_count: 2,
    review_reason: 'Conflicting sources',
    superseded_by: '',
    supersedes: [],
    source_context: ['actor: manual'],
    evidence: ['Reviewed evidence'],
    evidence_count: 1,
    relations: [
      {
        relation_type: 'supports',
        direction: 'outgoing',
        peer_id: 'peer',
        peer_subject: 'Related fact',
      },
    ],
    relation_count: 1,
    can_archive: true,
    can_restore: false,
    can_resolve: true,
  };
}

const snapshot: KnowledgeSettingsSnapshot = {
  availability: 'available',
  memory_available: true,
  memory_enabled: true,
  entities: 1,
  relations: 1,
  entity_types: [{ kind: 'fact', count: 1 }],
  connected_components: 1,
  largest_component: 1,
  isolated_entities: 0,
  status_counts: { active: 0, needs_review: 1, superseded: 0, archived: 0 },
};

function pending<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

it('renders the memory switch, lifecycle totals, four filters, and debounced search', async () => {
  vi.useFakeTimers();
  const load = vi.fn<KnowledgePageLoader>(async (filters) =>
    filters.status === 'needs_review' ? page('Review me') : page(),
  );
  render(<KnowledgeCatalog snapshot={snapshot} loadFiltered={load} />);
  await act(async () => undefined);
  expect(screen.getByRole('switch', { name: 'Enable Memory' })).toBeChecked();
  expect(screen.getAllByRole('combobox')).toHaveLength(4);
  expect(screen.getByText('1 needs review')).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Needs Review' })).toBeVisible();
  fireEvent.change(screen.getByRole('searchbox'), {
    target: { value: '  needle  ' },
  });
  expect(load).toHaveBeenCalledTimes(2);
  await act(async () => vi.advanceTimersByTime(300));
  expect(load).toHaveBeenCalledWith(
    expect.objectContaining({ query: 'needle' }),
    undefined,
    expect.any(AbortSignal),
  );
  vi.useRealTimers();
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
  render(<KnowledgeCatalog snapshot={snapshot} settingsMutation={mutation} />);
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

it('archives a saved entity on one click with its loaded revision', async () => {
  const onLifecycle = vi.fn(async () => undefined);
  render(
    <KnowledgeCatalog
      loadFiltered={async (filters) =>
        filters.status === 'needs_review' ? { ...page(), items: [] } : page()
      }
      loadDetail={async () => detail()}
      onLifecycle={onLifecycle}
    />,
  );
  fireEvent.click(await screen.findByLabelText('Saved thought · fact'));
  fireEvent.click(await screen.findByRole('button', { name: /Archive/ }));
  await act(async () => undefined);
  expect(onLifecycle).toHaveBeenCalledExactlyOnceWith(
    'Saved_thought',
    revision,
    'knowledge.archive',
  );
  expect(
    screen.queryByText(/Confirm lifecycle change/),
  ).not.toBeInTheDocument();
});

it('loads rich details only on first expansion and renders safe provenance and actions', async () => {
  const loadDetail = vi.fn(async () => detail());
  const onOpen = vi.fn();
  const { container } = render(
    <KnowledgeCatalog
      loadFiltered={async (filters) =>
        filters.status === 'needs_review' ? { ...page(), items: [] } : page()
      }
      loadDetail={loadDetail}
      onOpen={onOpen}
    />,
  );
  const row = await screen.findByLabelText('Saved thought · fact');
  expect(loadDetail).not.toHaveBeenCalled();
  fireEvent.click(row);
  expect(
    await screen.findByText('<img src=x onerror=sentinel()>'),
  ).toBeVisible();
  expect(container.querySelector('img')).toBeNull();
  expect(screen.getByText('82% confidence')).toBeVisible();
  expect(screen.getByText('Memory alias')).toBeVisible();
  expect(screen.getByText(/supports: Related fact/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Open Related fact' }));
  expect(onOpen).toHaveBeenCalledWith('peer');
  fireEvent.click(screen.getByText('Provenance'));
  expect(screen.getByText('actor: manual')).toBeVisible();
  expect(screen.getByText('Evidence: Reviewed evidence')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: /Edit/ }));
  expect(onOpen).toHaveBeenLastCalledWith('Saved_thought');
  fireEvent.click(row);
  fireEvent.click(row);
  expect(loadDetail).toHaveBeenCalledTimes(1);
});

it('loads each bounded audit view only when first opened', async () => {
  const recalls = vi.fn(async () => ({
    schema_version: 1 as const,
    availability: 'available' as const,
    items: [
      {
        timestamp: '2026-09-19T10:00:00Z',
        outcome: 'used' as const,
        reason: 'Relevant',
        candidate_count: 2,
        selected_count: 1,
        context_characters: 120,
        candidates: [{ subject: 'Saved thought', score: 0.91 }],
        rejection_reasons: [],
      },
    ],
  }));
  const changes = vi.fn(async () => ({
    schema_version: 1 as const,
    availability: 'available' as const,
    items: [
      {
        timestamp: '2026-09-19T11:00:00Z',
        action: 'user_modified',
        actor: 'manual',
        old_status: 'needs_review',
        new_status: 'active',
        subjects: ['Saved thought'],
        additional_subjects: 0,
        reason: 'Resolved',
      },
    ],
  }));
  render(
    <KnowledgeCatalog
      snapshot={snapshot}
      load={async () => page()}
      loadRecalls={recalls}
      loadChangeLog={changes}
    />,
  );
  await screen.findByLabelText('Saved thought · fact');
  expect(recalls).not.toHaveBeenCalled();
  expect(changes).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('Recent recall decisions'));
  expect(await screen.findByText('Memory used')).toBeVisible();
  fireEvent.click(screen.getByText('Recent recall decisions'));
  fireEvent.click(screen.getByText('Recent recall decisions'));
  expect(recalls).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText('Memory change log'));
  expect(await screen.findByText('user modified')).toBeVisible();
  expect(changes).toHaveBeenCalledTimes(1);
});

it('reviews exact selected revisions and executes captured bulk deletion', async () => {
  const reviewValue = {
    schema_version: 1 as const,
    action: 'knowledge.delete.bulk' as const,
    catalog_revision: catalogRevision,
    targets: [{ entity_id: 'Saved_thought', revision }],
    entity_count: 1,
    side_effects: ['entities' as const],
    action_digest: 'c'.repeat(64),
    review_id: 'd'.repeat(64),
  };
  const maintenance: KnowledgeMaintenanceIO = {
    review: vi.fn(async () => reviewValue),
    execute: vi.fn(async (_review, commandId) => ({
      command_id: commandId,
      status: 'completed' as const,
      action: 'knowledge.delete.bulk' as const,
      deleted: ['Saved_thought'],
      stale: [],
      missing: [],
      cleanup: {},
      code: null,
    })),
    receipt: vi.fn(),
  };
  render(
    <KnowledgeCatalog
      load={async () => page()}
      loadDetail={async () => detail()}
      maintenance={maintenance}
    />,
  );
  await screen.findByLabelText('Saved thought · fact');
  fireEvent.click(screen.getByRole('button', { name: 'Select' }));
  fireEvent.click(
    screen.getByRole('checkbox', { name: 'Select Saved thought' }),
  );
  await screen.findByText('1 selected · maximum 100');
  fireEvent.click(screen.getByRole('button', { name: 'Delete selected' }));
  expect(maintenance.review).toHaveBeenCalledWith(
    'knowledge.delete.bulk',
    catalogRevision,
    [{ entity_id: 'Saved_thought', revision }],
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Confirm permanent deletion' }),
  );
  expect(maintenance.execute).toHaveBeenCalledWith(
    reviewValue,
    expect.any(String),
  );
});

it('aborts and fences a slower search response', async () => {
  vi.useFakeTimers();
  const first = pending<EntitySummaryPage>();
  let unfilteredCalls = 0;
  const load = vi.fn<KnowledgePageLoader>((filters) => {
    if (filters.status === 'needs_review')
      return Promise.resolve({ ...page(), items: [], total: 0 });
    if (filters.query) return Promise.resolve(page('Newest'));
    unfilteredCalls += 1;
    return unfilteredCalls === 1 ? first.promise : Promise.resolve(page());
  });
  render(<KnowledgeCatalog loadFiltered={load} />);
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'new' } });
  await act(async () => vi.advanceTimersByTime(300));
  vi.useRealTimers();
  expect(await screen.findByLabelText('Newest · fact')).toBeVisible();
  expect(load.mock.calls[0][2]?.aborted).toBe(true);
  await act(async () => first.resolve(page('Old')));
  expect(screen.queryByLabelText('Old · fact')).toBeNull();
});

it.each(['missing', 'unavailable', 'available'] as const)(
  'distinguishes %s store from empty results',
  async (availability) => {
    render(
      <KnowledgeCatalog
        load={async () => ({
          ...page(),
          availability,
          items: [],
          total: availability === 'available' ? 0 : null,
        })}
      />,
    );
    expect(
      await screen.findByText(
        availability === 'missing'
          ? 'No saved knowledge store'
          : availability === 'unavailable'
            ? 'Saved knowledge unavailable'
            : 'No matching knowledge',
      ),
    ).toBeVisible();
  },
);

function documents(): DocumentSummaryPage {
  return {
    schema_version: 1,
    revision: 'one',
    availability: 'available',
    total: 1,
    next_cursor: null,
    items: [
      {
        id: 'document',
        name: 'report.txt',
        status: 'completed',
        stage: 'finalize',
        record_state: 'partial',
        index_current: 0,
        index_total: null,
        extraction_current: null,
        extraction_total: 4,
        updated_at: '',
        truncated: true,
        searchability: 'unknown',
      },
    ],
  };
}

it('does not regress rich saved document details', async () => {
  render(<DocumentsCatalog load={async () => documents()} />);
  fireEvent.click(await screen.findByText('report.txt'));
  const row = within(screen.getByText('report.txt').closest('li')!);
  expect(row.getByText('Completed')).toBeVisible();
  expect(row.getByText(/Partial — completion records/)).toBeVisible();
  expect(row.getByText('Current searchability')).toBeVisible();
});

it('does not regress explicit saved document removal', async () => {
  const remove = vi.fn();
  render(<DocumentsCatalog load={async () => documents()} onRemove={remove} />);
  await screen.findByText('report.txt');
  fireEvent.click(screen.getByRole('button', { name: 'Remove report.txt' }));
  expect(remove).toHaveBeenCalledWith('document', 'report.txt');
});

it('does not regress the saved document search contract', async () => {
  const user = userEvent.setup();
  const load = vi.fn(async () => documents());
  render(<DocumentsCatalog load={load} />);
  await screen.findByText('report.txt');
  await user.selectOptions(
    screen.getByLabelText('Saved document status'),
    'unknown',
  );
  await user.type(screen.getByRole('searchbox'), 'report');
  await user.click(screen.getByRole('button', { name: 'Search' }));
  expect(load).toHaveBeenLastCalledWith(
    'report',
    'unknown',
    undefined,
    expect.any(AbortSignal),
  );
});
