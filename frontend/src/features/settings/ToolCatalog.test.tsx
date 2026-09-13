import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import type { ToolCatalogPage } from '../../api/types';
import ToolCatalog from './ToolCatalog';

type Tool = ToolCatalogPage['items'][number];
function tool(id: string, source: Tool['source'] = 'core'): Tool {
  return {
    id: id.replaceAll(' ', '_'),
    label: id,
    source,
    parent_id: null,
    plugin_id: null,
    server_name: null,
    enabled: null,
    configured: null,
    destructive: null,
    requires_approval: null,
    runtime_state: 'unknown',
  };
}
function page(
  items = [tool('First tool')],
  next_cursor: string | null = null,
): ToolCatalogPage {
  return {
    schema_version: 1,
    revision: 'one',
    generated_at: null,
    freshness: 'cached',
    sources: [
      { source: 'core', state: 'cached', total: 1 },
      { source: 'mcp', state: 'cached', total: 0 },
      { source: 'plugin', state: 'unavailable', total: null },
      { source: 'custom', state: 'unavailable', total: null },
    ],
    items,
    total: next_cursor ? items.length + 1 : items.length,
    next_cursor,
    truncated: false,
  };
}
function pending<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

it('keeps at most 200 tool rows while forward paging reaches the entire catalog', async () => {
  const chunk = (offset: number) => ({
    ...page(
      Array.from({ length: 100 }, (_, i) =>
        tool(`Tool ${String(offset + i).padStart(3, '0')}`),
      ),
      offset < 300 ? String(offset + 100) : null,
    ),
    total: 400,
  });
  const load = vi.fn(
    async (_source?: Tool['source'], _query?: string, cursor?: string) =>
      chunk(Number(cursor ?? 0)),
  );
  const view = render(<ToolCatalog load={load} />);
  await screen.findByText('Tool 099 · Core');
  for (const end of [199, 299, 399]) {
    fireEvent.click(screen.getByRole('button', { name: 'Load more tools' }));
    await screen.findByText(`Tool ${end} · Core`);
    expect(
      view.container.querySelectorAll('.settings-results > li'),
    ).toHaveLength(200);
  }
  expect(screen.queryByText('Tool 000 · Core')).not.toBeInTheDocument();
  expect(screen.getByText('Tool 200 · Core')).toBeVisible();
  expect(screen.getByText(/Showing entries 201–400/)).toBeVisible();
  expect(load.mock.calls.map((call) => call[2])).toEqual([
    undefined,
    '100',
    '200',
    '300',
  ]);
  fireEvent.click(screen.getByRole('button', { name: 'Reload cached tools' }));
  await screen.findByText('Tool 000 · Core');
  expect(
    view.container.querySelectorAll('.settings-results > li'),
  ).toHaveLength(100);
  expect(screen.queryByText(/Showing entries/)).not.toBeInTheDocument();
});

it('distinguishes cached zero counts from unavailable sources and unknown runtime readiness', async () => {
  const load = vi.fn(async () => page());
  render(<ToolCatalog load={load} />);
  expect(await screen.findByText('First tool · Core')).toBeVisible();
  expect(screen.getByText(/Collection time is unknown/)).toHaveTextContent(
    'Showing cached tool information',
  );
  expect(screen.getByText('Cached · 0 recorded entries')).not.toBeVisible();
  fireEvent.click(screen.getByText('Catalog sources and coverage'));
  expect(
    within(screen.getByLabelText('Catalog sources')).getAllByRole('definition'),
  ).toHaveLength(4);
  expect(screen.getByText('Cached · 0 recorded entries')).toBeVisible();
  expect(screen.getAllByText('Unavailable · Count unknown')).toHaveLength(2);
  expect(
    screen.getByText(
      /Runtime readiness and account access have not been checked/,
    ),
  ).toBeVisible();
  fireEvent.click(screen.getByText('First tool · Core'));
  expect(screen.getAllByText('Unknown')).toHaveLength(5);
  expect(
    screen.queryByRole('button', { name: /execute|install|enable|connect/i }),
  ).not.toBeInTheDocument();
  expect(load).toHaveBeenCalledTimes(1);
});

it('keeps declarations separate from access and renders labels as text', async () => {
  const entry = {
    ...tool('same', 'plugin'),
    label: '<script>privateMarkup()</script>',
    enabled: true,
    configured: true,
    destructive: false,
    requires_approval: false,
    parent_id: 'parent',
    plugin_id: 'plugin-id',
    server_name: 'Synthetic server',
  };
  const { container } = render(
    <ToolCatalog load={async () => page([entry, tool('same', 'core')])} />,
  );
  const summary = await screen.findByText(
    '<script>privateMarkup()</script> · Plugins',
  );
  expect(container.querySelector('script')).toBeNull();
  fireEvent.click(summary);
  const row = within(summary.closest('li')!);
  expect(row.getByText('Enabled')).toBeVisible();
  expect(row.getByText('Recorded')).toBeVisible();
  expect(row.getAllByText('Not declared')).toHaveLength(2);
  expect(row.getByText('Unknown')).toBeVisible();
  expect(row.getByText('plugin-id')).toBeVisible();
  expect(screen.getByText('same · Core')).toBeVisible();
  expect(
    screen.queryByText(/ready to run|credentials verified|safe to execute/i),
  ).not.toBeInTheDocument();
});

it('makes unavailable and truncated outcomes explicit', async () => {
  const load = vi.fn(async () => ({
    ...page([]),
    freshness: 'unavailable' as const,
    truncated: true,
    sources: page().sources.map((source) => ({
      ...source,
      state: 'unavailable' as const,
      total: null,
    })),
  }));
  render(<ToolCatalog load={load} />);
  expect(await screen.findByText('No matching cached tools')).toBeVisible();
  expect(
    screen.getByText(/Cached tool information is unavailable/),
  ).toBeVisible();
  expect(
    screen.getByText(/results and counts may be incomplete/),
  ).toBeVisible();
  expect(screen.getAllByText('Unavailable · Count unknown')).toHaveLength(4);
  expect(
    screen.queryByRole('button', { name: 'Load more tools' }),
  ).not.toBeInTheDocument();
});

it('searches explicitly with keyboard submission and applies the source filter', async () => {
  const user = userEvent.setup();
  const load = vi.fn(async () => page());
  render(<ToolCatalog load={load} />);
  await screen.findByText('First tool · Core');
  await user.click(screen.getByRole('searchbox', { name: 'Search tools' }));
  await user.type(
    screen.getByRole('searchbox', { name: 'Search tools' }),
    '  lookup  ',
  );
  expect(load).toHaveBeenCalledTimes(1);
  await user.keyboard('{Enter}');
  await screen.findByText('First tool · Core');
  expect(load.mock.calls.at(-1)?.slice(0, 3)).toEqual([
    undefined,
    'lookup',
    undefined,
  ]);
  await user.selectOptions(
    screen.getByRole('combobox', { name: 'Tool source' }),
    'mcp',
  );
  await screen.findByText('First tool · Core');
  expect(load.mock.calls.at(-1)?.slice(0, 3)).toEqual([
    'mcp',
    'lookup',
    undefined,
  ]);
});

it('loads one additional page on request and ignores duplicate load-more clicks', async () => {
  const next = pending<ToolCatalogPage>();
  const load = vi
    .fn()
    .mockResolvedValueOnce(page([tool('First')], 'next'))
    .mockImplementationOnce(() => next.promise);
  render(<ToolCatalog load={load} />);
  await screen.findByText('First · Core');
  expect(load).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Load more tools' }));
  const loading = screen.getByRole('button', { name: 'Loading more tools…' });
  expect(loading).toBeDisabled();
  fireEvent.click(loading);
  expect(load).toHaveBeenCalledTimes(2);
  await act(async () => next.resolve(page([tool('Second')])));
  expect(screen.getByText('First · Core')).toBeVisible();
  expect(screen.getByText('Second · Core')).toBeVisible();
  expect(load.mock.calls[1].slice(0, 3)).toEqual([undefined, '', 'next']);
  expect(
    screen.queryByRole('button', { name: 'Load more tools' }),
  ).not.toBeInTheDocument();
});

it('aborts pending pagination on a source change and excludes its late response', async () => {
  const late = pending<ToolCatalogPage>();
  const load = vi
    .fn()
    .mockResolvedValueOnce(page([tool('Old')], 'next'))
    .mockImplementationOnce(() => late.promise)
    .mockResolvedValueOnce(page([tool('Filtered', 'mcp')]));
  render(<ToolCatalog load={load} />);
  await screen.findByText('Old · Core');
  fireEvent.click(screen.getByRole('button', { name: 'Load more tools' }));
  fireEvent.change(screen.getByRole('combobox', { name: 'Tool source' }), {
    target: { value: 'mcp' },
  });
  expect(await screen.findByText('Filtered · MCP')).toBeVisible();
  expect(load.mock.calls[1][3].aborted).toBe(true);
  await act(async () => late.resolve(page([tool('Obsolete')])));
  expect(screen.queryByText('Obsolete · Core')).not.toBeInTheDocument();
  expect(screen.queryByText('Old · Core')).not.toBeInTheDocument();
});

it('aborts initial requests on unmount and ignores their late failure', async () => {
  const old = pending<ToolCatalogPage>();
  const load = vi.fn(
    (
      _source?: Tool['source'],
      _query?: string,
      _cursor?: string,
      _signal?: AbortSignal,
    ) => old.promise,
  );
  const { unmount } = render(<ToolCatalog load={load} />);
  unmount();
  expect(load.mock.calls[0][3]?.aborted).toBe(true);
  await act(async () => old.reject(new Error('private late failure')));
  expect(screen.queryByText(/private late failure/)).not.toBeInTheDocument();
});

it.each(['revision', 'cursor'] as const)(
  'requires reload after a stale %s instead of combining catalogs',
  async (failure) => {
    const load = vi
      .fn()
      .mockResolvedValueOnce(page([tool('Original')], 'next'));
    if (failure === 'revision')
      load.mockResolvedValueOnce({
        ...page([tool('Changed')]),
        revision: 'two',
      });
    else load.mockRejectedValueOnce({ code: 'cursor_expired' });
    load.mockResolvedValueOnce({
      ...page([tool('Reloaded')]),
      revision: 'two',
    });
    render(<ToolCatalog load={load} />);
    await screen.findByText('Original · Core');
    fireEvent.click(screen.getByRole('button', { name: 'Load more tools' }));
    expect(
      await screen.findByText(
        'The tool catalog changed. Reload cached tools to continue.',
      ),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Load more tools' }),
    ).toBeDisabled();
    expect(screen.queryByText('Changed · Core')).not.toBeInTheDocument();
    expect(screen.getByText('Original · Core')).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Reload cached tools' }),
    );
    expect(await screen.findByText('Reloaded · Core')).toBeVisible();
    expect(screen.queryByText('Original · Core')).not.toBeInTheDocument();
    expect(load.mock.calls[2].slice(0, 3)).toEqual([undefined, '', undefined]);
  },
);

it('redacts errors and supports an explicit first-page retry', async () => {
  const load = vi
    .fn()
    .mockRejectedValueOnce(new Error('private token/path'))
    .mockResolvedValueOnce(page());
  render(<ToolCatalog load={load} />);
  expect(
    await screen.findByText('Row-Bot could not complete this request.'),
  ).toBeVisible();
  expect(screen.queryByText(/private token/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Reload cached tools' }));
  expect(await screen.findByText('First tool · Core')).toBeVisible();
});

it('fences an obsolete first page when its loader changes', async () => {
  const old = pending<ToolCatalogPage>();
  const first = vi.fn(
    (
      _source?: Tool['source'],
      _query?: string,
      _cursor?: string,
      _signal?: AbortSignal,
    ) => old.promise,
  );
  const { rerender } = render(<ToolCatalog load={first} />);
  rerender(<ToolCatalog load={async () => page([tool('Current')])} />);
  expect(await screen.findByText('Current · Core')).toBeVisible();
  expect(first.mock.calls[0][3]?.aborted).toBe(true);
  await act(async () => old.resolve(page([tool('Obsolete')])));
  expect(screen.queryByText('Obsolete · Core')).not.toBeInTheDocument();
});

it('keeps confirmed entries on a pagination failure and retries only on request', async () => {
  const load = vi
    .fn()
    .mockResolvedValueOnce(page([tool('Confirmed')], 'next'))
    .mockRejectedValueOnce(new TypeError('private network detail'))
    .mockResolvedValueOnce(page([tool('Recovered')]));
  render(<ToolCatalog load={load} />);
  await screen.findByText('Confirmed · Core');
  fireEvent.click(screen.getByRole('button', { name: 'Load more tools' }));
  expect(
    await screen.findByText(
      'Disconnected. Your last confirmed view is preserved.',
    ),
  ).toBeVisible();
  expect(screen.getByText('Confirmed · Core')).toBeVisible();
  expect(load).toHaveBeenCalledTimes(2);
  expect(screen.queryByText('private network detail')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Load more tools' }));
  expect(await screen.findByText('Recovered · Core')).toBeVisible();
  expect(screen.getByText('Confirmed · Core')).toBeVisible();
  expect(load.mock.calls[2].slice(0, 3)).toEqual([undefined, '', 'next']);
});
