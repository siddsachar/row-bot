import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, expect, it, onTestFinished, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import type { TaskSummary, TaskSummaryPage } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import { absoluteTime, relativeTime } from '../../ui/format';
import { clockTime } from '../home/home-format';
import { SavedTasks } from './TaskLibrary';

afterEach(() => {
  vi.useRealTimers();
});

function task(
  name = 'Saved task',
  overrides: Partial<TaskSummary> = {},
): TaskSummary {
  return {
    id: name,
    name,
    description: 'Saved description',
    icon: '',
    enabled: false,
    notify_only: true,
    step_count: 0,
    schedule: null,
    at: null,
    last_run: null,
    last_status: null,
    conversation_id: 'conversation-one',
    ...overrides,
  };
}
function page(
  name = 'Saved task',
  cursor: string | null = null,
): TaskSummaryPage {
  return {
    schema_version: 1,
    revision: 'one',
    total: 2,
    next_cursor: cursor,
    items: [task(name)],
  };
}
function pageOf(
  items: TaskSummary[],
  overrides: Partial<TaskSummaryPage> = {},
): TaskSummaryPage {
  return {
    schema_version: 1,
    revision: 'one',
    total: items.length,
    next_cursor: null,
    items,
    ...overrides,
  };
}
function pending<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}
/** Advance fake timers and flush the resulting renders and resolved loads. */
async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}
function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
    </output>
  );
}
function show(
  load: React.ComponentProps<typeof SavedTasks>['load'],
  props: Omit<React.ComponentProps<typeof SavedTasks>, 'load'> = {},
) {
  return render(
    <MemoryRouter initialEntries={['/workflows']}>
      <OverlayProvider>
        <SavedTasks load={load} {...props} />
        <LocationProbe />
      </OverlayProvider>
    </MemoryRouter>,
  );
}
/**
 * Browsers compute the absolutely positioned `.visually-hidden` as a block, so
 * its words stay apart from the text beside them in an accessible name. jsdom
 * has no layout, so this models that for the rest of the current test.
 */
function blockVisuallyHidden() {
  const style = document.createElement('style');
  style.textContent = '.visually-hidden { display: block; }';
  document.head.append(style);
  onTestFinished(() => style.remove());
}
function row(name: string) {
  const element = screen
    .getAllByText(name, { selector: '.workflow-row-name' })[0]
    ?.closest('li');
  if (!element) throw new Error(`No workflow row for ${name}`);
  return element;
}

it('bounds rendered task rows while preserving full forward traversal and reload', async () => {
  const chunk = (offset: number): TaskSummaryPage => ({
    ...page(),
    total: 240,
    next_cursor: offset < 180 ? String(offset + 60) : null,
    items: Array.from({ length: 60 }, (_, i) =>
      task(`Task ${String(offset + i).padStart(3, '0')}`),
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
    fireEvent.click(
      screen.getByRole('button', { name: 'Load more workflows' }),
    );
    await screen.findByText(`Task ${end}`);
    expect(
      view.container.querySelectorAll('.workflow-grid > li.workflow-row'),
    ).toHaveLength(expectedRows);
  }
  expect(screen.queryByText('Task 000')).not.toBeInTheDocument();
  expect(screen.getByText('Task 040')).toBeVisible();
  expect(screen.getByText(/Showing entries 41–240/)).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Load more workflows' }),
  ).not.toBeInTheDocument();
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

it('shows recorded task facts and opens its existing conversation', async () => {
  const user = userEvent.setup();
  const load = vi.fn(async () => page());
  show(load);
  await screen.findByText('Saved task');
  const saved = row('Saved task');
  expect(saved.querySelector('.workflow-metadata')).toHaveTextContent(
    'Reminder·Never run·Run manually',
  );
  expect(within(saved).getByText('Saved description')).toHaveAttribute(
    'title',
    'Saved description',
  );
  expect(within(saved).getByText('No runs yet')).toBeVisible();
  expect(saved).toHaveAttribute('data-enabled', 'false');
  expect(saved).not.toHaveAttribute('data-running');
  expect(saved).not.toHaveAttribute('data-failed');
  await user.click(
    screen.getByRole('button', { name: 'More actions for Saved task' }),
  );
  await user.click(
    await screen.findByRole('menuitem', { name: 'Open conversation' }),
  );
  expect(screen.getByTestId('location')).toHaveTextContent(
    '/conversations/conversation-one',
  );
  expect(load).toHaveBeenCalledTimes(1);
});

it('hands the conversation to the owner when one is supplied', async () => {
  const user = userEvent.setup();
  const openConversation = vi.fn();
  const load = vi.fn(async () =>
    pageOf([
      task('Linked', { conversation_id: 'conversation/one' }),
      task('Unlinked', { conversation_id: null }),
    ]),
  );
  show(load, { onOpenConversation: openConversation });
  await screen.findByText('Linked');
  await user.click(
    screen.getByRole('button', { name: 'More actions for Linked' }),
  );
  await user.click(
    await screen.findByRole('menuitem', { name: 'Open conversation' }),
  );
  expect(openConversation).toHaveBeenCalledWith('conversation/one');
  expect(screen.getByTestId('location')).toHaveTextContent('/workflows');
  // No conversation and no other row action: no empty menu is offered.
  expect(
    screen.queryByRole('button', { name: 'More actions for Unlinked' }),
  ).not.toBeInTheDocument();
});

it('shows one readable metadata line with steps and local schedule/run times', async () => {
  vi.useFakeTimers({ toFake: ['Date'] });
  const now = new Date('2026-09-26T12:40:00Z');
  vi.setSystemTime(now);
  const saved = task('Saved task', {
    notify_only: false,
    step_count: 3,
    enabled: true,
    schedule: 'weekly:mon:09:30',
    last_run: '2026-09-25T12:40:00Z',
    next_run: '2026-09-28T09:30:00Z',
  });
  const view = show(vi.fn(async () => pageOf([saved])));
  await screen.findByText('Saved task');
  const metadata = view.container.querySelector('.workflow-metadata');
  expect(metadata).toHaveTextContent('3 steps');
  expect(metadata).toHaveTextContent(
    `Ran ${relativeTime('2026-09-25T12:40:00Z', now)}`,
  );
  expect(metadata).toHaveTextContent(`Every Monday at ${clockTime(9, 30)}`);
  expect(metadata?.textContent).not.toContain('2026-09-25T');
  expect(metadata?.textContent).not.toContain('weekly:');
  const ran = metadata?.querySelector('time');
  expect(ran).toHaveAttribute('dateTime', '2026-09-25T12:40:00.000Z');
  expect(ran).toHaveAttribute('title', absoluteTime('2026-09-25T12:40:00Z'));
  const next = view.container.querySelector('.workflow-next');
  expect(next).toHaveTextContent(
    `Next run ${relativeTime('2026-09-28T09:30:00Z', now)}`,
  );
  expect(next?.textContent).not.toContain('2026-09-28T');
  expect(
    screen.getByRole('switch', { name: 'Disable workflow: Saved task' }),
  ).toBeChecked();
});

it('uses singular step wording and omits an empty description', async () => {
  show(
    vi.fn(async () =>
      pageOf([
        task('One step', {
          notify_only: false,
          step_count: 1,
          description: '',
        }),
      ]),
    ),
  );
  await screen.findByText('One step');
  expect(row('One step').querySelector('.workflow-metadata')).toHaveTextContent(
    /^1 step·/,
  );
  expect(row('One step').querySelector('.workflow-row-description')).toBeNull();
});

it.each([
  ['daily:08:00', null, `Every day at ${clockTime(8, 0)}`],
  ['interval:2', null, 'Every 2 hours'],
  ['interval:0.5', null, 'Every 30 minutes'],
  ['interval_minutes:30', null, 'Every 30 minutes'],
  ['cron:0 9 * * mon', null, `Every Monday at ${clockTime(9, 0)}`],
  // The scheduler numbers weekdays from Monday = 0, so 1 is Tuesday.
  ['cron:0 9 * * 1', null, `Every Tuesday at ${clockTime(9, 0)}`],
  ['cron:30 7 * * mon-fri', null, `Weekdays at ${clockTime(7, 30)}`],
  ['cron:*/5 * * * *', null, 'Custom schedule'],
  ['nonsense', null, 'Custom schedule'],
  [null, '2026-10-01T10:00', `Once · ${absoluteTime('2026-10-01T10:00')}`],
  [null, 'invalid', 'Invalid one-time schedule'],
  [null, null, 'Run manually'],
] as const)('formats schedule %s / %s safely', async (schedule, at, words) => {
  const view = show(
    vi.fn(async () => pageOf([task('Saved task', { schedule, at })])),
  );
  await screen.findByText('Saved task');
  const metadata = view.container.querySelector('.workflow-metadata');
  expect(metadata).toHaveTextContent(words);
  expect(metadata?.textContent).not.toMatch(/cron:|interval|daily:/);
});

it.each([
  [
    'an upcoming run',
    { enabled: true, schedule: 'daily:08:00', next_run: '2099-01-01T08:00' },
    /^Next run /,
  ],
  [
    'an enabled schedule without a next run',
    { enabled: true, schedule: 'daily:08:00', next_run: null },
    /^Not scheduled$/,
  ],
  [
    'a disabled schedule',
    { enabled: false, schedule: 'daily:08:00', next_run: '2099-01-01T08:00' },
    /^Paused$/,
  ],
  ['a manual workflow', { enabled: true }, /^$/],
] as const)('shows the next-run column for %s', async (_, overrides, text) => {
  const view = show(vi.fn(async () => pageOf([task('Saved task', overrides)])));
  await screen.findByText('Saved task');
  expect(view.container.querySelector('.workflow-next')).toHaveTextContent(
    text,
  );
});

it('shows emoji icons as they are and named icons as glyphs', async () => {
  const view = show(
    vi.fn(async () =>
      pageOf([task('Mail', { icon: '📬' }), task('Named', { icon: 'email' })]),
    ),
  );
  await screen.findByText('Mail');
  const icons = view.container.querySelectorAll('.workflow-icon');
  expect(icons[0]).toHaveTextContent('📬');
  expect(icons[0]).toHaveAttribute('aria-hidden', 'true');
  expect(icons[1]).toHaveTextContent('');
  expect(icons[1].querySelector('svg')).not.toBeNull();
});

it('loads another bounded page once even when clicked repeatedly', async () => {
  const next = pending<TaskSummaryPage>();
  const load = vi
    .fn()
    .mockResolvedValueOnce(page('First', 'next'))
    .mockReturnValueOnce(next.promise);
  show(load);
  await screen.findByText('First');
  const button = screen.getByRole('button', { name: 'Load more workflows' });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(load).toHaveBeenCalledTimes(2);
  expect(load.mock.calls[1].slice(0, 3)).toEqual(['', undefined, 'next']);
  expect(
    screen.getByRole('button', { name: 'Loading more workflows…' }),
  ).toBeDisabled();
  await act(async () => next.resolve(page('Second')));
  expect(screen.getByText('First')).toBeVisible();
  expect(screen.getByText('Second')).toBeVisible();
  expect(load).toHaveBeenCalledTimes(2);
});

it('searches on submit and discards a late page after the search changes', async () => {
  vi.useFakeTimers();
  const late = pending<TaskSummaryPage>();
  const load = vi
    .fn()
    .mockResolvedValueOnce(page('First', 'next'))
    .mockReturnValueOnce(late.promise)
    .mockResolvedValueOnce(page('Filtered'));
  show(load);
  await flush();
  expect(screen.getByText('First')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Load more workflows' }));
  fireEvent.change(
    screen.getByRole('searchbox', { name: 'Search workflows' }),
    { target: { value: ' filtered ' } },
  );
  await flush(349);
  expect(load).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole('button', { name: 'Search workflows' }));
  await flush();
  expect(screen.getByText('Filtered')).toBeVisible();
  expect(load.mock.calls[1][3].aborted).toBe(true);
  await act(async () => late.resolve(page('Obsolete')));
  expect(screen.queryByText('Obsolete')).not.toBeInTheDocument();
  expect(load.mock.calls[2].slice(0, 3)).toEqual([
    'filtered',
    undefined,
    undefined,
  ]);
  // The pending pause does not repeat a search that was already submitted.
  await flush(1_000);
  expect(load).toHaveBeenCalledTimes(3);
  expect(screen.getByText('2 matching')).toBeVisible();
});

it('searches as you type after a 350ms pause and at once on Enter', async () => {
  vi.useFakeTimers();
  const load = vi.fn(async (query?: string) =>
    page(query ? `Match ${query}` : 'Saved task'),
  );
  show(load);
  await flush();
  expect(screen.getByText('Saved task')).toBeInTheDocument();
  const search = screen.getByRole('searchbox', { name: 'Search workflows' });
  fireEvent.change(search, { target: { value: 're' } });
  await flush(200);
  fireEvent.change(search, { target: { value: 'rep' } });
  await flush(349);
  expect(load).toHaveBeenCalledTimes(1);
  await flush(1);
  expect(load).toHaveBeenCalledTimes(2);
  expect(load.mock.calls[1].slice(0, 3)).toEqual(['rep', undefined, undefined]);
  expect(screen.getByText('Match rep')).toBeInTheDocument();
  // Surrounding spaces do not change the search.
  fireEvent.change(search, { target: { value: '  rep ' } });
  await flush(1_000);
  expect(load).toHaveBeenCalledTimes(2);

  // Enter submits the search form, which searches at once without the pause.
  // jsdom has no implicit submission and user-event cannot run under Vitest
  // fake timers, so Enter is represented by the form's submit event.
  fireEvent.change(search, { target: { value: 'daily ' } });
  fireEvent.submit(screen.getByRole('search', { name: 'Filter workflows' }));
  await flush();
  expect(load).toHaveBeenCalledTimes(3);
  expect(load.mock.calls[2].slice(0, 3)).toEqual([
    'daily',
    undefined,
    undefined,
  ]);
  expect(screen.getByText('Match daily')).toBeInTheDocument();
  await flush(1_000);
  expect(load).toHaveBeenCalledTimes(3);
});

it('applies the enabled filter and keeps empty results explicit', async () => {
  const user = userEvent.setup();
  const load = vi
    .fn()
    .mockResolvedValueOnce(page())
    .mockResolvedValueOnce({ ...page(), items: [], total: 0 });
  show(load);
  await screen.findByText('Saved task');
  expect(screen.getByText('2 workflows')).toBeVisible();
  const filters = screen.getByRole('radiogroup', { name: 'Show workflows' });
  expect(
    within(filters)
      .getAllByRole('radio')
      .map((radio) => radio.textContent),
  ).toEqual(['All', 'Enabled', 'Scheduled', 'Failed']);
  expect(within(filters).getByRole('radio', { name: 'All' })).toBeChecked();
  await user.click(within(filters).getByRole('radio', { name: 'Enabled' }));
  expect(await screen.findByText('No matching workflows')).toBeVisible();
  expect(screen.getByText('Try another search or filter.')).toBeVisible();
  expect(screen.getByText('0 matching')).toBeVisible();
  expect(within(filters).getByRole('radio', { name: 'Enabled' })).toBeChecked();
  expect(load.mock.calls[1].slice(0, 3)).toEqual(['', true, undefined]);
});

it('says there are no workflows yet when nothing is saved', async () => {
  show(vi.fn(async () => pageOf([])));
  expect(await screen.findByText('No workflows yet')).toBeVisible();
  expect(screen.getByText('0 workflows')).toBeVisible();
  expect(screen.queryByRole('list')).not.toBeInTheDocument();
});

it('counts a single workflow in the singular', async () => {
  show(vi.fn(async () => pageOf([task('Only')])));
  expect(await screen.findByText('1 workflow')).toBeVisible();
});

it('filters Scheduled and Failed across every page on the client', async () => {
  const user = userEvent.setup();
  const first = pageOf(
    [
      task('Daily digest', { enabled: true, schedule: 'daily:08:00' }),
      task('Paused digest', { enabled: false, schedule: 'daily:09:00' }),
      task('Broken sync', { enabled: true, last_status: 'FAILED' }),
    ],
    { total: 7, next_cursor: 'p2' },
  );
  const second = pageOf(
    [
      task('One-off', { enabled: true, at: '2026-10-01T10:00' }),
      task('Late mail', { last_status: 'timed_out' }),
      task('Delivered badly', { last_status: 'completed_delivery_failed' }),
      task('Fine', { enabled: true, last_status: 'completed' }),
    ],
    { total: 7 },
  );
  const load = vi.fn(
    async (_query?: string, _enabled?: boolean, cursor?: string) =>
      cursor === 'p2' ? second : first,
  );
  const view = show(load);
  await screen.findByText('Daily digest');
  expect(
    screen.getByRole('button', { name: 'Load more workflows' }),
  ).toBeVisible();
  const filters = screen.getByRole('radiogroup', { name: 'Show workflows' });
  const names = () =>
    [...view.container.querySelectorAll('.workflow-row-name')].map(
      (element) => element.textContent,
    );

  await user.click(within(filters).getByRole('radio', { name: 'Scheduled' }));
  expect(await screen.findByText('One-off')).toBeVisible();
  expect(names()).toEqual(['Daily digest', 'One-off']);
  expect(screen.getByText('2 matching')).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Load more workflows' }),
  ).not.toBeInTheDocument();
  expect(load.mock.calls.slice(1).map((call) => call.slice(0, 3))).toEqual([
    ['', true, undefined],
    ['', true, 'p2'],
  ]);

  await user.click(within(filters).getByRole('radio', { name: 'Failed' }));
  expect(await screen.findByText('Late mail')).toBeVisible();
  expect(names()).toEqual(['Broken sync', 'Late mail', 'Delivered badly']);
  expect(screen.getByText('3 matching')).toBeVisible();
  expect(row('Broken sync')).toHaveAttribute('data-failed', 'true');
  expect(load.mock.calls.slice(3).map((call) => call.slice(0, 3))).toEqual([
    ['', undefined, undefined],
    ['', undefined, 'p2'],
  ]);

  await user.click(within(filters).getByRole('radio', { name: 'All' }));
  expect(await screen.findByText('Paused digest')).toBeVisible();
  expect(names()).toEqual(['Daily digest', 'Paused digest', 'Broken sync']);
  expect(screen.getByText('7 workflows')).toBeVisible();
  expect(load).toHaveBeenCalledTimes(6);
});

it('bounds a client-side filter read and rejects a snapshot that changes mid-read', async () => {
  const user = userEvent.setup();
  let calls = 0;
  const endless = vi.fn(async () => {
    calls += 1;
    return pageOf([task(`Failed ${calls}`, { last_status: 'failed' })], {
      next_cursor: `after-${calls}`,
      total: 1_000,
    });
  });
  const view = show(endless);
  await screen.findByText('Failed 1');
  await user.click(screen.getByRole('radio', { name: 'Failed' }));
  expect(await screen.findByText('Failed 21')).toBeVisible();
  // One unfiltered read, then at most twenty pages for the client filter.
  expect(endless).toHaveBeenCalledTimes(21);
  expect(view.container.querySelectorAll('.workflow-row')).toHaveLength(20);
  expect(screen.getByText('20 matching in the first 20')).toBeVisible();
  view.unmount();

  const changing = vi.fn(
    async (_query?: string, _enabled?: boolean, cursor?: string) =>
      pageOf([task(cursor ? 'Later' : 'Earlier', { last_status: 'failed' })], {
        revision: cursor ? 'two' : 'one',
        next_cursor: cursor ? null : 'p2',
      }),
  );
  show(changing);
  await screen.findByText('Earlier');
  await user.click(screen.getByRole('radio', { name: 'Failed' }));
  expect(
    await screen.findByText('This list is out of date. Load it again.'),
  ).toBeVisible();
  expect(screen.queryByText('Earlier')).not.toBeInTheDocument();
  expect(screen.queryByText('Later')).not.toBeInTheDocument();
});

it('says how many workflows a capped Scheduled or Failed read checked', async () => {
  const user = userEvent.setup();
  // Pages of 25; one failed and one scheduled workflow on pages 0, 7 and 19.
  const library = (pages: number) =>
    vi.fn(async (_query?: string, _enabled?: boolean, cursor?: string) => {
      const index = cursor ? Number(cursor.slice(1)) : 0;
      const marked = [0, 7, 19].includes(index);
      return pageOf(
        Array.from({ length: 25 }, (_, row) =>
          task(
            `Workflow ${index}-${row}`,
            marked && row === 0
              ? { last_status: 'failed' }
              : marked && row === 1
                ? { enabled: true, schedule: 'daily:08:00' }
                : {},
          ),
        ),
        {
          total: pages * 25,
          next_cursor: index + 1 < pages ? `p${index + 1}` : null,
        },
      );
    });

  const endless = library(40);
  let view = show(endless);
  await screen.findByText('Workflow 0-0');
  await user.click(screen.getByRole('radio', { name: 'Failed' }));
  expect(await screen.findByText('Workflow 19-0')).toBeVisible();
  expect(screen.getByText('3 matching in the first 500')).toBeVisible();
  // One unfiltered read, then twenty pages; page 20 is never asked for.
  expect(endless).toHaveBeenCalledTimes(21);
  expect(endless.mock.lastCall?.[2]).toBe('p19');
  await user.click(screen.getByRole('radio', { name: 'Scheduled' }));
  expect(await screen.findByText('Workflow 19-1')).toBeVisible();
  expect(screen.getByText('3 matching in the first 500')).toBeVisible();
  await user.click(screen.getByRole('radio', { name: 'All' }));
  expect(
    await screen.findByText(`${(1_000).toLocaleString()} workflows`),
  ).toBeVisible();
  view.unmount();

  // Reading every workflow within the cap gives the plain count.
  const complete = library(20);
  view = show(complete);
  await screen.findByText('Workflow 0-0');
  await user.click(screen.getByRole('radio', { name: 'Failed' }));
  expect(await screen.findByText('Workflow 19-0')).toBeVisible();
  expect(screen.getByText('3 matching')).toBeVisible();
  expect(screen.queryByText(/in the first/)).not.toBeInTheDocument();
  expect(complete).toHaveBeenCalledTimes(21);
  view.unmount();
});

it('does not merge a changed snapshot and allows an explicit reload', async () => {
  const load = vi
    .fn()
    .mockResolvedValueOnce(page('First', 'next'))
    .mockResolvedValueOnce({ ...page('Changed'), revision: 'two' })
    .mockResolvedValueOnce(page('Reloaded'));
  show(load);
  await screen.findByText('First');
  fireEvent.click(screen.getByRole('button', { name: 'Load more workflows' }));
  expect(
    await screen.findByText(
      'Saved workflows changed. Refresh the list to continue.',
    ),
  ).toBeVisible();
  expect(screen.queryByText('Changed')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh workflows' }));
  expect(await screen.findByText('Reloaded')).toBeVisible();
  expect(screen.queryByText('First')).not.toBeInTheDocument();
  expect(
    screen.queryByText(
      'Saved workflows changed. Refresh the list to continue.',
    ),
  ).not.toBeInTheDocument();
});

it('redacts unexpected errors and retries explicitly', async () => {
  const load = vi
    .fn()
    .mockRejectedValueOnce(new Error('private path and secret'))
    .mockResolvedValueOnce(page());
  show(load);
  expect(
    await screen.findByText('Something went wrong. Try again.'),
  ).toBeVisible();
  expect(screen.getByText('Workflow list unavailable')).toBeVisible();
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

it('shows delivery defaults and keeps the web app visibly locked on', async () => {
  const user = userEvent.setup();
  const save = pending<void>();
  const setDefaults = vi.fn((_ids: readonly string[]) => save.promise);
  const load = vi.fn(async () => page());
  show(load, {
    deliveryDefaults: [{ id: 'desktop', label: 'Desktop' }],
    deliveryOptions: [
      { id: 'desktop', label: 'Desktop' },
      { id: 'email', label: 'Email' },
    ],
    onSetDeliveryDefaults: setDefaults,
  });
  await screen.findByText('Saved task');
  const trigger = screen.getByRole('button', { name: 'Delivery defaults' });
  expect(trigger).toHaveAttribute(
    'title',
    'Delivery defaults: Web app, Desktop',
  );
  expect(trigger).toHaveTextContent('1');
  await user.click(trigger);
  const popover = await screen.findByRole('dialog', {
    name: 'Default delivery channels',
  });
  expect(within(popover).getByText('Web app')).toBeVisible();
  expect(within(popover).getByText('Always on')).toBeVisible();
  expect(
    within(popover)
      .getAllByRole('checkbox')
      .map((box) => box.closest('label')?.textContent),
  ).toEqual(['Desktop', 'Email']);
  const desktop = within(popover).getByRole('checkbox', { name: 'Desktop' });
  const email = within(popover).getByRole('checkbox', { name: 'Email' });
  expect(desktop).toBeChecked();
  expect(email).not.toBeChecked();
  const saveButton = within(popover).getByRole('button', {
    name: 'Save defaults',
  });
  expect(saveButton).toBeDisabled();
  await user.click(desktop);
  await user.click(email);
  expect(saveButton).toBeEnabled();
  await user.click(saveButton);
  expect(setDefaults).toHaveBeenCalledWith(['email']);
  // One save at a time: the button stays unavailable until it settles.
  expect(saveButton).toBeDisabled();
  await user.click(saveButton);
  expect(setDefaults).toHaveBeenCalledTimes(1);
  await act(async () => save.resolve());
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Default delivery channels' }),
    ).not.toBeInTheDocument(),
  );
  expect(
    await screen.findByText('Workflow default delivery saved.'),
  ).toBeInTheDocument();
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
});

it('keeps the delivery defaults open when saving fails', async () => {
  const user = userEvent.setup();
  const setDefaults = vi.fn(async () => {
    throw new Error('private channel secret');
  });
  const load = vi.fn(async () => page());
  show(load, {
    deliveryOptions: [{ id: 'email', label: 'Email' }],
    onSetDeliveryDefaults: setDefaults,
  });
  await screen.findByText('Saved task');
  const trigger = screen.getByRole('button', { name: 'Delivery defaults' });
  expect(trigger).toHaveAttribute('title', 'Delivery defaults: Web app');
  await user.click(trigger);
  const popover = await screen.findByRole('dialog', {
    name: 'Default delivery channels',
  });
  await user.click(within(popover).getByRole('checkbox', { name: 'Email' }));
  await user.click(
    within(popover).getByRole('button', { name: 'Save defaults' }),
  );
  expect(setDefaults).toHaveBeenCalledWith(['email']);
  expect(await screen.findByText('Workflow action failed')).toBeVisible();
  expect(screen.queryByText(/private channel/)).not.toBeInTheDocument();
  expect(
    screen.getByRole('dialog', { name: 'Default delivery channels' }),
  ).toBeVisible();
  expect(
    within(popover).getByRole('button', { name: 'Save defaults' }),
  ).toBeEnabled();
  expect(load).toHaveBeenCalledTimes(1);
});

it('explains when no external delivery channels are set up', async () => {
  const user = userEvent.setup();
  show(
    vi.fn(async () => page()),
    { onSetDeliveryDefaults: vi.fn() },
  );
  await screen.findByText('Saved task');
  await user.click(screen.getByRole('button', { name: 'Delivery defaults' }));
  const popover = await screen.findByRole('dialog', {
    name: 'Default delivery channels',
  });
  expect(
    within(popover).getByText('No external channels are set up.'),
  ).toBeVisible();
  expect(within(popover).queryByRole('checkbox')).not.toBeInTheDocument();
  await user.click(within(popover).getByRole('button', { name: 'Cancel' }));
  expect(
    screen.queryByRole('dialog', { name: 'Default delivery channels' }),
  ).not.toBeInTheDocument();
});

it('selects bounded visible workflows and confirms bulk deletion', async () => {
  const user = userEvent.setup();
  const load = vi.fn(async () => ({
    ...page(),
    items: [task('First'), task('Second')],
  }));
  const bulkDelete = vi.fn(async (_ids: readonly string[]) => undefined);
  show(load, { onBulkDelete: bulkDelete });
  await screen.findByText('First');
  expect(
    screen.queryByRole('checkbox', { name: 'Select workflow: First' }),
  ).not.toBeInTheDocument();
  await user.click(
    screen.getByRole('button', { name: 'More workflow actions' }),
  );
  await user.click(
    await screen.findByRole('menuitem', { name: 'Select workflows' }),
  );
  const actions = screen.getByRole('group', {
    name: 'Selected workflow actions',
  });
  expect(within(actions).getByText('0 workflows selected')).toBeVisible();
  expect(
    within(actions).getByRole('button', { name: 'Delete selected' }),
  ).toBeDisabled();
  await user.click(
    screen.getByRole('checkbox', { name: 'Select workflow: First' }),
  );
  expect(screen.getByText('1 workflow selected')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Delete selected' }));
  expect(
    screen.getByRole('alertdialog', { name: 'Delete 1 workflow?' }),
  ).toBeVisible();
  expect(bulkDelete).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Delete 1 workflow' }));
  expect(bulkDelete).toHaveBeenCalledWith(['First']);
  expect(await screen.findByText('1 workflow deleted.')).toBeInTheDocument();
  await waitFor(() =>
    expect(
      screen.queryByRole('group', { name: 'Selected workflow actions' }),
    ).not.toBeInTheDocument(),
  );
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
});

it('leaves selection mode from the menu or the Done button', async () => {
  const user = userEvent.setup();
  const load = vi.fn(async () => pageOf([task('First'), task('Second')]));
  show(load, { onBulkDelete: vi.fn() });
  await screen.findByText('First');
  const more = screen.getByRole('button', { name: 'More workflow actions' });
  await user.click(more);
  await user.click(
    await screen.findByRole('menuitem', { name: 'Select workflows' }),
  );
  await user.click(
    screen.getByRole('checkbox', { name: 'Select workflow: Second' }),
  );
  await user.click(screen.getByRole('button', { name: 'Clear' }));
  expect(screen.getByText('0 workflows selected')).toBeVisible();
  await user.click(more);
  await user.click(
    await screen.findByRole('menuitem', { name: 'Done selecting' }),
  );
  expect(
    screen.queryByRole('checkbox', { name: 'Select workflow: First' }),
  ).not.toBeInTheDocument();
  await user.click(more);
  await user.click(
    await screen.findByRole('menuitem', { name: 'Select workflows' }),
  );
  await user.click(screen.getByRole('button', { name: 'Done' }));
  expect(
    screen.queryByRole('group', { name: 'Selected workflow actions' }),
  ).not.toBeInTheDocument();
  expect(load).toHaveBeenCalledTimes(1);
});

it('offers no bulk selection without a bulk delete owner', async () => {
  show(vi.fn(async () => page()));
  await screen.findByText('Saved task');
  expect(
    screen.queryByRole('button', { name: 'More workflow actions' }),
  ).not.toBeInTheDocument();
});

it('exposes reviewed direct enable and delete actions', async () => {
  const user = userEvent.setup();
  const load = vi.fn(async () => page());
  const toggle = vi.fn(async (_id: string, _enabled: boolean) => undefined);
  const remove = vi.fn(async (_id: string) => undefined);
  show(load, { onToggleEnabled: toggle, onDelete: remove });
  await screen.findByText('Saved task');
  await user.click(
    screen.getByRole('switch', { name: 'Enable workflow: Saved task' }),
  );
  expect(toggle).toHaveBeenCalledWith('Saved task', true);
  expect(await screen.findByText('Saved task enabled.')).toBeInTheDocument();
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  // Let the reloaded list replace the old rows before using them.
  await act(async () => {});

  const more = await screen.findByRole('button', {
    name: 'More actions for Saved task',
  });
  await user.click(more);
  const menu = await screen.findByRole('menu');
  const items = within(menu).getAllByRole('menuitem');
  expect(items.at(-1)).toHaveTextContent('Delete workflow');
  await user.click(
    within(menu).getByRole('menuitem', { name: 'Delete workflow' }),
  );
  expect(
    screen.getByRole('alertdialog', { name: "Delete 'Saved task'?" }),
  ).toBeVisible();
  expect(remove).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(
    screen.queryByRole('alertdialog', { name: "Delete 'Saved task'?" }),
  ).not.toBeInTheDocument();
  expect(remove).not.toHaveBeenCalled();
  await waitFor(() => expect(more).toHaveFocus());

  await user.click(more);
  await user.click(
    await screen.findByRole('menuitem', { name: 'Delete workflow' }),
  );
  await user.click(screen.getByRole('button', { name: 'Delete workflow' }));
  expect(remove).toHaveBeenCalledWith('Saved task');
  expect(await screen.findByText('Saved task deleted.')).toBeInTheDocument();
  await waitFor(() => expect(load).toHaveBeenCalledTimes(3));
});

it('runs one row action at a time and reports failures without details', async () => {
  const user = userEvent.setup();
  const toggling = pending<void>();
  const toggle = vi.fn((_id: string, _enabled: boolean) => toggling.promise);
  const load = vi.fn(async () =>
    pageOf([task('First', { enabled: true }), task('Second')]),
  );
  const runs = vi.fn();
  show(load, { onToggleEnabled: toggle, onRuns: runs });
  await screen.findByText('First');
  await user.click(
    screen.getByRole('switch', { name: 'Disable workflow: First' }),
  );
  expect(toggle).toHaveBeenCalledWith('First', false);
  expect(
    screen.getByRole('switch', { name: 'Disable workflow: First' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('switch', { name: 'Enable workflow: Second' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Run workflow: Second' }),
  ).toBeDisabled();
  await act(async () => toggling.reject(new Error('private toggle detail')));
  expect(await screen.findByText('Workflow action failed')).toBeVisible();
  expect(screen.queryByText(/private toggle/)).not.toBeInTheDocument();
  expect(
    screen.getByRole('switch', { name: 'Enable workflow: Second' }),
  ).toBeEnabled();
  expect(toggle).toHaveBeenCalledTimes(1);
  expect(load).toHaveBeenCalledTimes(1);
});

it('keeps the enable switch read-only without a toggle owner', async () => {
  show(vi.fn(async () => page()));
  expect(
    await screen.findByRole('switch', { name: 'Enable workflow: Saved task' }),
  ).toBeDisabled();
});

it('offers run history, step, settings and conversation actions in the row menu', async () => {
  const user = userEvent.setup();
  const runs = vi.fn();
  const graph = vi.fn();
  const settings = vi.fn();
  const conversation = vi.fn();
  const load = vi.fn(async () => pageOf([task('Digest', { enabled: true })]));
  show(load, {
    onRuns: runs,
    onGraph: graph,
    onSettings: settings,
    onOpenConversation: conversation,
    onDuplicate: vi.fn(),
    onDelete: vi.fn(),
  });
  await screen.findByText('Digest');
  const more = screen.getByRole('button', { name: 'More actions for Digest' });
  expect(more).toHaveAccessibleDescription('More workflow actions');
  await user.click(more);
  expect(
    within(await screen.findByRole('menu'))
      .getAllByRole('menuitem')
      .map((item) => item.textContent),
  ).toEqual([
    'Run history',
    'Edit workflow steps',
    'Workflow settings',
    'Open conversation',
    'Duplicate workflow',
    'Delete workflow',
  ]);
  for (const [label, callback, args] of [
    ['Run history', runs, ['Digest', 'Digest']],
    ['Edit workflow steps', graph, ['Digest', 'Digest']],
    ['Workflow settings', settings, ['Digest', 'Digest']],
    ['Open conversation', conversation, ['conversation-one']],
  ] as const) {
    if (!screen.queryByRole('menu')) await user.click(more);
    await user.click(await screen.findByRole('menuitem', { name: label }));
    expect(callback).toHaveBeenCalledWith(...args);
  }
  expect(load).toHaveBeenCalledTimes(1);
});

it('opens the run drawer from Run now, even for a disabled workflow, and edits in place', async () => {
  const user = userEvent.setup();
  const runs = vi.fn();
  const edit = vi.fn();
  const load = vi.fn(async () =>
    pageOf([task('Ready', { enabled: false, schedule: 'daily:08:00' })]),
  );
  show(load, { onRuns: runs, onEdit: edit });
  const run = await screen.findByRole('button', {
    name: 'Run workflow: Ready',
  });
  expect(run).toBeEnabled();
  await user.click(run);
  expect(runs).toHaveBeenCalledWith('Ready', 'Ready');
  await user.click(
    screen.getByRole('button', { name: 'Edit workflow: Ready' }),
  );
  expect(edit).toHaveBeenCalledWith('Ready', 'Ready');
  // Opening the drawer is not an action: nothing is reloaded or announced.
  expect(load).toHaveBeenCalledTimes(1);
  expect(screen.queryByText(/started/)).not.toBeInTheDocument();
});

it('stops running workflows without opening the run drawer', async () => {
  const user = userEvent.setup();
  const stopping = pending<void>();
  const stop = vi.fn((_id: string) => stopping.promise);
  const runs = vi.fn();
  const load = vi.fn(async () =>
    pageOf([task('Busy', { enabled: true, last_status: 'running' })]),
  );
  show(load, { onStop: stop, onRuns: runs });
  const button = await screen.findByRole('button', {
    name: 'Stop running workflow: Busy',
  });
  expect(
    screen.queryByRole('button', { name: 'Run workflow: Busy' }),
  ).not.toBeInTheDocument();
  expect(row('Busy')).toHaveAttribute('data-running', 'true');
  await user.click(button);
  expect(stop).toHaveBeenCalledWith('Busy');
  expect(button).toBeDisabled();
  await user.click(button);
  expect(stop).toHaveBeenCalledTimes(1);
  expect(runs).not.toHaveBeenCalled();
  await act(async () => stopping.resolve());
  expect(await screen.findByText('Stopping Busy.')).toBeInTheDocument();
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  expect(runs).not.toHaveBeenCalled();
});

it('shows running progress that opens the run drawer', async () => {
  blockVisuallyHidden();
  const user = userEvent.setup();
  const runs = vi.fn();
  const load = vi.fn(async () =>
    pageOf([
      task('Stepping', {
        enabled: true,
        active_run: {
          id: 'run-1',
          status: 'running',
          started_at: '2026-09-26T10:00:00Z',
          steps_done: 1,
          steps_total: 3,
        },
      }),
      task('Finishing', {
        active_run: {
          id: 'run-2',
          status: 'running',
          started_at: '2026-09-26T10:00:00Z',
          steps_done: 3,
          steps_total: 3,
        },
      }),
      task('Waiting', {
        active_run: {
          id: 'run-3',
          status: 'waiting_approval',
          started_at: '2026-09-26T10:00:00Z',
          steps_done: 0,
          steps_total: 0,
        },
      }),
      task('Legacy', { last_status: 'Running' }),
    ]),
  );
  show(load, { onRuns: runs });
  const stepping = await screen.findByRole('button', {
    name: 'Open run of Stepping: Step 2/3',
  });
  expect(stepping).toHaveTextContent('Step 2/3');
  expect(
    screen.getByRole('button', { name: 'Open run of Finishing: Step 3/3' }),
  ).toHaveTextContent('Step 3/3');
  expect(
    screen.getByRole('button', {
      name: 'Open run of Waiting: Waiting for approval',
    }),
  ).toHaveTextContent('Waiting for approval');
  expect(
    screen.getByRole('button', { name: 'Open run of Legacy: Running' }),
  ).toHaveTextContent('Running');
  for (const name of ['Stepping', 'Finishing', 'Waiting', 'Legacy']) {
    expect(row(name)).toHaveAttribute('data-running', 'true');
    expect(within(row(name)).queryByText('No runs yet')).toBeNull();
  }
  await user.click(stepping);
  expect(runs).toHaveBeenCalledWith('Stepping', 'Stepping');
  await user.click(
    screen.getByRole('button', {
      name: 'Open run of Waiting: Waiting for approval',
    }),
  );
  expect(runs).toHaveBeenLastCalledWith('Waiting', 'Waiting');
  expect(load).toHaveBeenCalledTimes(1);
});

it('summarises recent runs for idle workflows', async () => {
  show(
    vi.fn(async () =>
      pageOf([
        task('Healthy', {
          recent_runs: [
            { status: 'failed', started_at: '2026-09-26T09:00:00Z' },
            { status: 'completed', started_at: '2026-09-25T09:00:00Z' },
          ],
        }),
      ]),
    ),
  );
  expect(
    await screen.findByRole('img', {
      name: 'Last 2 runs of Healthy: 1 completed, 1 failed',
    }),
  ).toBeVisible();
});

it('duplicates a workflow from the row menu and says what it made (parity row 18)', async () => {
  const user = userEvent.setup();
  const duplicate = vi.fn(async () => 'Digest (copy)');
  const load = vi.fn(async () => pageOf([task('Digest', { enabled: true })]));
  show(load, { onDuplicate: duplicate });
  await screen.findByText('Digest');
  await user.click(
    screen.getByRole('button', { name: 'More actions for Digest' }),
  );
  await user.click(
    await screen.findByRole('menuitem', { name: 'Duplicate workflow' }),
  );
  expect(duplicate).toHaveBeenCalledWith('Digest');
  expect(
    await screen.findByText('Duplicated as “Digest (copy)”.'),
  ).toBeInTheDocument();
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
});
