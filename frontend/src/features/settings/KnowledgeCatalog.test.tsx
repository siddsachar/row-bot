import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import type { EntitySummaryPage, DocumentSummaryPage } from '../../api/types';
import KnowledgeCatalog from './KnowledgeCatalog';
import DocumentsCatalog from './DocumentsCatalog';

function page(
  subject = 'Saved thought',
  next_cursor: string | null = null,
): EntitySummaryPage {
  return {
    schema_version: 1,
    revision: 'one',
    availability: 'available',
    total: next_cursor ? 2 : 1,
    next_cursor,
    items: [
      {
        id: subject.replaceAll(' ', '_'),
        entity_type: 'fact',
        subject,
        description: 'Saved description',
        updated_at: '',
        truncated: false,
        saved_state: 'saved',
        semantic_state: 'unknown',
      },
    ],
  };
}
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
function pending<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

it('renders saved knowledge as plain text with explicit shortened summaries and unknown readiness', async () => {
  const value = page('<script>sentinel()</script>');
  value.items[0].description = '<img src=x onerror=sentinel()>';
  value.items[0].truncated = true;
  const { container } = render(<KnowledgeCatalog load={async () => value} />);
  fireEvent.click(
    await screen.findByText('<script>sentinel()</script> · fact'),
  );
  expect(screen.getByText('<img src=x onerror=sentinel()>')).toBeVisible();
  expect(container.querySelector('script,img')).toBeNull();
  expect(screen.getByText('This saved summary is shortened.')).toBeVisible();
  expect(screen.getByText('Semantic search readiness')).toBeVisible();
  expect(screen.getAllByText('Unknown')).toHaveLength(2);
  expect(
    screen.queryByRole('button', { name: /delete|sync|rebuild|index/i }),
  ).toBeNull();
});

it.each(['missing', 'unavailable', 'available'] as const)(
  'distinguishes %s store from empty results',
  async (availability) => {
    render(
      <KnowledgeCatalog
        load={async () => ({
          ...page(),
          items: [],
          total: availability === 'available' ? 0 : null,
          availability,
        })}
      />,
    );
    const title =
      availability === 'missing'
        ? 'No saved knowledge store'
        : availability === 'unavailable'
          ? 'Saved knowledge unavailable'
          : 'No matching knowledge';
    expect(await screen.findByText(title)).toBeVisible();
    if (availability !== 'available')
      expect(screen.queryByText(/0 matching/)).toBeNull();
  },
);

it('applies full-library search and exact type only on submit', async () => {
  const user = userEvent.setup();
  const load = vi.fn(async () => page());
  render(<KnowledgeCatalog load={load} />);
  await screen.findByText('Saved thought · fact');
  await user.type(screen.getByRole('searchbox'), '  needle  ');
  await user.type(
    screen.getByLabelText('Entity type', { exact: false }),
    'project',
  );
  expect(load).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole('button', { name: 'Search' }));
  expect(load).toHaveBeenLastCalledWith(
    'needle',
    'project',
    undefined,
    expect.any(AbortSignal),
  );
  expect(screen.getByRole('searchbox')).toHaveAttribute('maxlength', '256');
});

it('appends matching revision pages once despite duplicate load-more clicks', async () => {
  const next = pending<EntitySummaryPage>();
  const load = vi
    .fn()
    .mockResolvedValueOnce(page('First', 'cursor'))
    .mockReturnValueOnce(next.promise);
  render(<KnowledgeCatalog load={load} />);
  const more = await screen.findByRole('button', {
    name: 'Load more knowledge',
  });
  fireEvent.click(more);
  fireEvent.click(more);
  expect(load).toHaveBeenCalledTimes(2);
  expect(load).toHaveBeenLastCalledWith(
    '',
    undefined,
    'cursor',
    expect.any(AbortSignal),
  );
  await act(async () => next.resolve(page('Second')));
  expect(screen.getByText('First · fact')).toBeVisible();
  expect(screen.getByText('Second · fact')).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Load more knowledge' }),
  ).toBeNull();
});

it.each(['revision', 'expired'])(
  'requires reload after %s change and preserves existing rows',
  async (kind) => {
    const load = vi.fn().mockResolvedValueOnce(page('First', 'cursor'));
    if (kind === 'revision')
      load.mockResolvedValueOnce({ ...page('Wrong'), revision: 'two' });
    else
      load.mockRejectedValueOnce({
        code: 'cursor_expired',
        message: 'Expired',
      });
    load.mockResolvedValueOnce(page('Reloaded'));
    render(<KnowledgeCatalog load={load} />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Load more knowledge' }),
    );
    expect(
      await screen.findByText(
        'The saved knowledge changed. Reload to continue.',
      ),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Load more knowledge' }),
    ).toBeDisabled();
    expect(screen.getByText('First · fact')).toBeVisible();
    expect(screen.queryByText('Wrong · fact')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Reload knowledge' }));
    expect(await screen.findByText('Reloaded · fact')).toBeVisible();
    expect(screen.queryByText('First · fact')).toBeNull();
  },
);

it('aborts and fences initial and pagination responses after search or unmount', async () => {
  const old = pending<EntitySummaryPage>();
  const more = pending<EntitySummaryPage>();
  const load = vi
    .fn()
    .mockReturnValueOnce(old.promise)
    .mockResolvedValueOnce(page('Current', 'next'))
    .mockReturnValueOnce(more.promise)
    .mockResolvedValueOnce(page('Newest'));
  const { unmount } = render(<KnowledgeCatalog load={load} />);
  fireEvent.change(screen.getByRole('searchbox'), {
    target: { value: 'current' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await screen.findByText('Current · fact');
  expect(load.mock.calls[0][3].aborted).toBe(true);
  await act(async () => old.resolve(page('Old')));
  expect(screen.queryByText('Old · fact')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Load more knowledge' }));
  fireEvent.change(screen.getByRole('searchbox'), {
    target: { value: 'newest' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await screen.findByText('Newest · fact');
  expect(load.mock.calls[2][3].aborted).toBe(true);
  unmount();
  await act(async () => more.resolve(page('Late')));
  expect(load.mock.calls[3][3].aborted).toBe(true);
});

it('retains rows and permits retry after a transient pagination error', async () => {
  const load = vi
    .fn()
    .mockResolvedValueOnce(page('First', 'cursor'))
    .mockRejectedValueOnce(new Error('Unavailable'))
    .mockResolvedValueOnce(page('Second'));
  render(<KnowledgeCatalog load={load} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Load more knowledge' }),
  );
  await screen.findByRole('alert');
  expect(screen.getByText('First · fact')).toBeVisible();
  const retry = screen.getByRole('button', { name: 'Load more knowledge' });
  expect(retry).toBeEnabled();
  fireEvent.click(retry);
  expect(await screen.findByText('Second · fact')).toBeVisible();
});

it('shows completed document status alongside partial records and unknown current searchability', async () => {
  render(<DocumentsCatalog load={async () => documents()} />);
  fireEvent.click(await screen.findByText('report.txt'));
  const row = within(screen.getByText('report.txt').closest('li')!);
  expect(row.getByText('Completed')).toBeVisible();
  expect(row.getByText(/Partial — completion records/)).toBeVisible();
  expect(row.getByText('0 / unknown')).toBeVisible();
  expect(row.getByText('Unknown / 4')).toBeVisible();
  expect(row.getByText('Current searchability')).toBeVisible();
  expect(row.getByText('This saved name is shortened.')).toBeVisible();
  expect(
    screen.queryByRole('button', { name: /retry ingestion|remove|rebuild/i }),
  ).toBeNull();
});

it('submits document search and saved status through the agreed read contract', async () => {
  const user = userEvent.setup();
  const load = vi.fn(async () => documents());
  render(<DocumentsCatalog load={load} />);
  await screen.findByText('report.txt');
  await user.selectOptions(
    screen.getByLabelText('Saved document status'),
    'unknown',
  );
  await user.type(screen.getByRole('searchbox'), 'report');
  expect(load).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole('button', { name: 'Search' }));
  expect(load).toHaveBeenLastCalledWith(
    'report',
    'unknown',
    undefined,
    expect.any(AbortSignal),
  );
});

it('keeps a 200-entry window while every forward page remains reachable and reload returns to the start', async () => {
  const snapshots = Array.from({ length: 4 }, (_, index) => ({
    ...page(),
    total: 400,
    next_cursor: index < 3 ? `cursor-${index + 1}` : null,
    items: Array.from(
      { length: 100 },
      (_, row) => page(`Entry ${index * 100 + row}`).items[0],
    ),
  }));
  const original = structuredClone(snapshots);
  const load = vi
    .fn()
    .mockResolvedValueOnce(snapshots[0])
    .mockResolvedValueOnce(snapshots[1])
    .mockResolvedValueOnce(snapshots[2])
    .mockResolvedValueOnce(snapshots[3])
    .mockResolvedValueOnce(snapshots[0]);
  render(<KnowledgeCatalog load={load} />);
  await screen.findByText('Entry 0 · fact');
  expect(screen.getAllByRole('listitem')).toHaveLength(100);
  for (const index of [1, 2, 3]) {
    fireEvent.click(
      screen.getByRole('button', { name: 'Load more knowledge' }),
    );
    await screen.findByText(`Entry ${index * 100 + 99} · fact`);
    expect(screen.getAllByRole('listitem')).toHaveLength(200);
    expect(load).toHaveBeenLastCalledWith(
      '',
      undefined,
      `cursor-${index}`,
      expect.any(AbortSignal),
    );
  }
  const entries = screen
    .getAllByRole('listitem')
    .map((item) => item.querySelector('summary')?.textContent);
  expect(entries).toEqual(
    Array.from({ length: 200 }, (_, i) => `Entry ${i + 200} · fact`),
  );
  expect(screen.getByText(/200 earlier loaded entries/)).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Load more knowledge' }),
  ).toBeNull();
  expect(snapshots).toEqual(original);
  fireEvent.click(screen.getByRole('button', { name: 'Reload knowledge' }));
  await screen.findByText('Entry 0 · fact');
  expect(screen.getAllByRole('listitem')).toHaveLength(100);
  expect(screen.queryByText(/earlier loaded entries/)).toBeNull();
});

it('does not evict rows or advance the window on stale pages and clears its notice on a new search', async () => {
  const value = {
    ...page('First', 'cursor'),
    items: Array.from({ length: 100 }, (_, i) => page(`Entry ${i}`).items[0]),
  };
  const next = {
    ...value,
    items: Array.from({ length: 100 }, (_, i) => page(`Next ${i}`).items[0]),
  };
  const third = {
    ...value,
    items: Array.from({ length: 100 }, (_, i) => page(`Third ${i}`).items[0]),
  };
  const load = vi
    .fn()
    .mockResolvedValueOnce(value)
    .mockResolvedValueOnce(next)
    .mockResolvedValueOnce(third)
    .mockResolvedValueOnce({ ...page('Stale'), revision: 'changed' })
    .mockResolvedValueOnce(page('Filtered'));
  render(<KnowledgeCatalog load={load} />);
  await screen.findByText('Entry 0 · fact');
  fireEvent.click(screen.getByRole('button', { name: 'Load more knowledge' }));
  await screen.findByText('Next 99 · fact');
  fireEvent.click(screen.getByRole('button', { name: 'Load more knowledge' }));
  await screen.findByText('Third 99 · fact');
  fireEvent.click(screen.getByRole('button', { name: 'Load more knowledge' }));
  await screen.findByText('The saved knowledge changed. Reload to continue.');
  expect(screen.getAllByRole('listitem')).toHaveLength(200);
  expect(screen.getByText(/100 earlier loaded entries/)).toBeVisible();
  expect(screen.getByText('Next 0 · fact')).toBeVisible();
  expect(screen.queryByText('Stale · fact')).toBeNull();
  fireEvent.change(screen.getByRole('searchbox'), {
    target: { value: 'filtered' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await screen.findByText('Filtered · fact');
  expect(screen.queryByText(/earlier loaded entries/)).toBeNull();
  expect(screen.getAllByRole('listitem')).toHaveLength(1);
});
