import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import type { TaskSummaryPage } from '../../api/types';
import { SavedTasks } from './TaskLibrary';

function page(
  name = 'Saved task',
  cursor: string | null = null,
): TaskSummaryPage {
  return {
    schema_version: 1,
    revision: 'one',
    total: 2,
    next_cursor: cursor,
    items: [
      {
        id: name,
        name,
        description: 'Saved description',
        icon: '',
        enabled: false,
        notify_only: true,
        schedule: null,
        at: null,
        last_run: null,
        last_status: null,
        conversation_id: 'conversation-one',
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

it('bounds rendered task rows while preserving full forward traversal and reload', async () => {
  const chunk = (offset: number): TaskSummaryPage => ({
    ...page(),
    total: 240,
    next_cursor: offset < 180 ? String(offset + 60) : null,
    items: Array.from(
      { length: 60 },
      (_, i) => page(`Task ${String(offset + i).padStart(3, '0')}`).items[0],
    ),
  });
  const load = vi.fn(
    async (_query?: string, _enabled?: boolean, cursor?: string) =>
      chunk(Number(cursor ?? 0)),
  );
  const view = show(load);
  await screen.findByText('Task 059');
  for (const [end, expectedRows] of [
    [119, 120],
    [179, 180],
    [239, 200],
  ] as const) {
    fireEvent.click(screen.getByRole('button', { name: 'Load more tasks' }));
    await screen.findByText(`Task ${end}`);
    expect(view.container.querySelectorAll('.workflow-grid > li')).toHaveLength(
      expectedRows,
    );
  }
  expect(screen.queryByText('Task 000')).not.toBeInTheDocument();
  expect(screen.getByText('Task 040')).toBeVisible();
  expect(screen.getByText(/Showing entries 41–240/)).toBeVisible();
  expect(load.mock.calls.map((call) => call[2])).toEqual([
    undefined,
    '60',
    '120',
    '180',
  ]);
  fireEvent.click(screen.getByRole('button', { name: 'Refresh workflows' }));
  await screen.findByText('Task 000');
  expect(view.container.querySelectorAll('.workflow-grid > li')).toHaveLength(
    60,
  );
  expect(screen.queryByText(/Showing entries/)).not.toBeInTheDocument();
}, 15_000);
function show(load: React.ComponentProps<typeof SavedTasks>['load']) {
  return render(
    <MemoryRouter>
      <SavedTasks load={load} />
    </MemoryRouter>,
  );
}

it('shows recorded task facts and opens its existing conversation', async () => {
  const user = userEvent.setup();
  const load = vi.fn(async () => page());
  show(load);
  await screen.findByText('Saved task');
  expect(screen.getByText('Reminder')).toBeVisible();
  expect(screen.getByText('No recorded status')).toBeVisible();
  await user.click(
    screen.getByRole('button', { name: 'More actions for Saved task' }),
  );
  await user.click(screen.getByRole('menuitem', { name: 'Open conversation' }));
  expect(load).toHaveBeenCalledTimes(1);
});

it('loads another bounded page once even when clicked repeatedly', async () => {
  const next = pending<TaskSummaryPage>();
  const load = vi
    .fn()
    .mockResolvedValueOnce(page('First', 'next'))
    .mockReturnValueOnce(next.promise);
  show(load);
  await screen.findByText('First');
  const button = screen.getByRole('button', { name: 'Load more tasks' });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(load).toHaveBeenCalledTimes(2);
  expect(load.mock.calls[1].slice(0, 3)).toEqual(['', undefined, 'next']);
  await act(async () => next.resolve(page('Second')));
  expect(screen.getByText('First')).toBeVisible();
  expect(screen.getByText('Second')).toBeVisible();
});

it('searches on submit and discards a late page after the search changes', async () => {
  const late = pending<TaskSummaryPage>();
  const load = vi
    .fn()
    .mockResolvedValueOnce(page('First', 'next'))
    .mockReturnValueOnce(late.promise)
    .mockResolvedValueOnce(page('Filtered'));
  show(load);
  await screen.findByText('First');
  fireEvent.click(screen.getByRole('button', { name: 'Load more tasks' }));
  fireEvent.change(
    screen.getByRole('searchbox', { name: 'Search workflows' }),
    {
      target: { value: ' filtered ' },
    },
  );
  expect(load).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole('button', { name: 'Search workflows' }));
  expect(await screen.findByText('Filtered')).toBeVisible();
  expect(load.mock.calls[1][3].aborted).toBe(true);
  await act(async () => late.resolve(page('Obsolete')));
  expect(screen.queryByText('Obsolete')).not.toBeInTheDocument();
  expect(load.mock.calls[2].slice(0, 3)).toEqual([
    'filtered',
    undefined,
    undefined,
  ]);
});

it('applies the enabled filter and keeps empty results explicit', async () => {
  const load = vi
    .fn()
    .mockResolvedValueOnce(page())
    .mockResolvedValueOnce({ ...page(), items: [], total: 0 });
  show(load);
  await screen.findByText('Saved task');
  fireEvent.change(screen.getByRole('combobox', { name: 'Workflow status' }), {
    target: { value: 'enabled' },
  });
  expect(await screen.findByText('No matching saved tasks')).toBeVisible();
  expect(load.mock.calls[1].slice(0, 3)).toEqual(['', true, undefined]);
});

it('does not merge a changed snapshot and allows an explicit reload', async () => {
  const load = vi
    .fn()
    .mockResolvedValueOnce(page('First', 'next'))
    .mockResolvedValueOnce({ ...page('Changed'), revision: 'two' })
    .mockResolvedValueOnce(page('Reloaded'));
  show(load);
  await screen.findByText('First');
  fireEvent.click(screen.getByRole('button', { name: 'Load more tasks' }));
  expect(
    await screen.findByText(
      'Saved tasks changed. Reload the list to continue.',
    ),
  ).toBeVisible();
  expect(screen.queryByText('Changed')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh workflows' }));
  expect(await screen.findByText('Reloaded')).toBeVisible();
  expect(screen.queryByText('First')).not.toBeInTheDocument();
});

it('redacts unexpected errors and retries explicitly', async () => {
  const load = vi
    .fn()
    .mockRejectedValueOnce(new Error('private path and secret'))
    .mockResolvedValueOnce(page());
  show(load);
  expect(
    await screen.findByText('Row-Bot could not complete this request.'),
  ).toBeVisible();
  expect(screen.queryByText(/private path/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh workflows' }));
  expect(await screen.findByText('Saved task')).toBeVisible();
});

it('aborts the initial read when unmounted', async () => {
  const late = pending<TaskSummaryPage>();
  const load = vi.fn(
    (
      _query?: string,
      _enabled?: boolean,
      _cursor?: string,
      _signal?: AbortSignal,
    ) => late.promise,
  );
  const view = show(load);
  view.unmount();
  expect(load.mock.calls[0][3]?.aborted).toBe(true);
  await act(async () => late.resolve(page()));
});
