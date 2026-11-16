import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import type {
  ActivityEntry,
  ActivityFilters,
  ActivityPage,
} from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import ActivitySettings, { type ActivityIO } from './ActivitySettings';

const today = new Date();
today.setHours(10, 30, 0, 0);

function entry(overrides: Partial<ActivityEntry> = {}): ActivityEntry {
  return {
    entry_id: 1,
    at: today.toISOString(),
    conversation_id: 'chat-1',
    conversation_title: 'Tidy my files',
    surface: 'chat',
    source_id: '',
    tool: 'delete_file',
    title: 'Delete a file',
    app_id: '',
    app_name: '',
    summary: 'Delete old.txt',
    level: 'auto',
    outcome: 'approved_by_reviewer',
    decided_by: 'reviewer',
    reason: 'You asked to delete old.txt.',
    risk: 'unknown',
    pending: false,
    ...overrides,
  };
}

function page(
  items: ActivityEntry[],
  next_before: number | null = null,
): ActivityPage {
  return { schema_version: 1, items, next_before };
}

function setup(
  pages: ActivityPage[] = [page([entry()])],
  { any = true, failures = 0 } = {},
) {
  const queue = [...pages];
  let failing = failures;
  const io = {
    list: vi.fn(async (filters: ActivityFilters, _signal?: AbortSignal) => {
      // The page's check for whether the log holds anything at all.
      if (filters.limit === 1 && !filters.outcome)
        return page(any ? [entry()] : []);
      if (failing-- > 0) throw new Error('offline');
      return queue.shift() ?? page([]);
    }),
    exportCsv: vi.fn(async () => ({
      reference: `save:${'a'.repeat(32)}`,
      file_name: 'row-bot-activity-2026-11-15.csv',
    })),
    save: vi.fn(async () => ({
      status: 'ok' as const,
      value: { kind: 'download' as const },
    })),
    clear: vi.fn(async () => ({ schema_version: 1 as const, removed: 3 })),
  } satisfies ActivityIO;
  render(
    <MemoryRouter>
      <OverlayProvider>
        <ActivitySettings io={io} />
      </OverlayProvider>
    </MemoryRouter>,
  );
  return io;
}

describe('Settings › System › Activity', () => {
  it('opens on what needed approval, by day, with plain outcomes and who decided', async () => {
    const io = setup([
      page([
        entry(),
        entry({
          entry_id: 2,
          outcome: 'denied',
          decided_by: 'person',
          reason: '',
          summary: 'Send the report to Sam',
          title: 'Send an email',
          app_name: 'Gmail',
        }),
        entry({ entry_id: 3, outcome: 'asked', pending: true }),
      ]),
    ]);

    expect(await screen.findByText('Today')).toBeVisible();
    expect(io.list).toHaveBeenCalledWith(
      expect.objectContaining({ outcome: 'needed', surface: '', q: '' }),
      expect.any(AbortSignal),
    );
    expect(
      screen.getByRole('radio', { name: 'Needed approval' }),
    ).toBeChecked();
    const list = within(screen.getByRole('list'));
    expect(list.getByText('Reviewer approved')).toBeVisible();
    expect(list.getByText('You said no')).toBeVisible();
    expect(list.getByText('Waiting for you')).toBeVisible();
    expect(list.getByText('Gmail › Send an email')).toBeVisible();

    const row = screen.getAllByRole('button', { name: /Delete a file/ })[0];
    await userEvent.click(row);
    expect(row).toHaveAttribute('aria-expanded', 'true');
    const detail = document.getElementById(row.getAttribute('aria-controls')!)!;
    expect(within(detail).getByText('The reviewer (Auto)')).toBeVisible();
    expect(
      within(detail).getByText('You asked to delete old.txt.'),
    ).toBeVisible();
    expect(within(detail).getByText('Auto')).toBeVisible();
    expect(
      within(detail).getByRole('link', { name: 'Open in chat' }),
    ).toHaveAttribute('href', '/conversations/chat-1');
    expect(
      screen.getByText('That’s everything from the last 90 days.'),
    ).toBeVisible();
  });

  it('says a call nobody was asked about in the words chat steps use, never its tool name (W19)', async () => {
    setup([
      page([
        entry({
          entry_id: 4,
          tool: 'agent_wait',
          title: '',
          summary: '',
          outcome: 'ran',
          decided_by: 'none',
        }),
        entry({
          entry_id: 5,
          tool: 'read_url',
          title: '',
          summary: '',
          outcome: 'ran',
        }),
        entry({
          entry_id: 6,
          tool: 'duckduckgo_search',
          title: '',
          summary: '',
          outcome: 'ran',
        }),
      ]),
    ]);

    const list = within(await screen.findByRole('list'));
    expect(list.getByText('Wait for a helper')).toBeVisible();
    expect(list.getByText('Read page')).toBeVisible();
    expect(list.getByText('Search the web')).toBeVisible();
    expect(list.queryByText(/agent_wait|Agent wait|read_url/)).toBeNull();
  });

  it('filters by outcome, by where it ran and by words', async () => {
    const io = setup();
    await screen.findByText('Reviewer approved');

    await userEvent.click(screen.getByRole('radio', { name: 'You said no' }));
    await waitFor(() =>
      expect(io.list).toHaveBeenCalledWith(
        expect.objectContaining({ outcome: 'denied' }),
        expect.any(AbortSignal),
      ),
    );
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: 'Where' }),
      'workflow',
    );
    await waitFor(() =>
      expect(io.list).toHaveBeenCalledWith(
        expect.objectContaining({ outcome: 'denied', surface: 'workflow' }),
        expect.any(AbortSignal),
      ),
    );
    await userEvent.type(
      screen.getByRole('searchbox', { name: 'Search activity' }),
      'report',
    );
    await waitFor(() =>
      expect(io.list).toHaveBeenCalledWith(
        expect.objectContaining({ q: 'report', surface: 'workflow' }),
        expect.any(AbortSignal),
      ),
    );
    expect(
      await screen.findByText('Nothing matches these filters.'),
    ).toBeVisible();
  });

  it('says what will show here while there is nothing yet', async () => {
    setup([page([])], { any: false });
    expect(await screen.findByText('Nothing yet')).toBeVisible();
    expect(
      screen.getByText('Every action Row-Bot takes will show here.'),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Clear activity' }),
    ).toBeDisabled();
  });

  it('offers Retry when the log cannot be read', async () => {
    setup(undefined, { failures: 1 });
    expect(await screen.findByText('Activity couldn’t be read')).toBeVisible();

    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Reviewer approved')).toBeVisible();
  });

  it('reads earlier pages', async () => {
    const io = setup([
      page([entry({ entry_id: 9 })], 9),
      page([
        entry({ entry_id: 4, outcome: 'hands_off', decided_by: 'hands_off' }),
      ]),
    ]);
    await userEvent.click(
      await screen.findByRole('button', { name: 'Show earlier' }),
    );
    expect(await screen.findByText('Hands-off')).toBeVisible();
    expect(io.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ before: 9, outcome: 'needed' }),
    );
  });

  it('exports what the filters show as a CSV through the save path', async () => {
    const io = setup();
    await screen.findByText('Reviewer approved');
    await userEvent.click(screen.getByRole('radio', { name: 'Blocked' }));

    await userEvent.click(
      screen.getByRole('button', { name: 'Export as CSV' }),
    );

    await waitFor(() =>
      expect(io.save).toHaveBeenCalledWith(
        `save:${'a'.repeat(32)}`,
        'row-bot-activity-2026-11-15.csv',
      ),
    );
    expect(io.exportCsv).toHaveBeenCalledWith({
      outcome: 'blocked',
      surface: '',
      q: '',
    });
  });

  it('says an action ran because of your rule, and points to it (D1)', async () => {
    setup([
      page([
        entry({
          outcome: 'allowed_by_rule',
          decided_by: 'rule',
          reason: '',
          rule_id: '0123456789abcdef',
        }),
      ]),
    ]);
    const pill = await screen.findByText('Allowed by your rule');
    fireEvent.click(pill.closest('button')!);
    expect(screen.getByText(/^Your rule ·/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'See the rule' })).toBeVisible();
  });

  it('clears everything only after you confirm', async () => {
    const io = setup();
    await screen.findByText('Reviewer approved');
    fireEvent.click(screen.getByRole('button', { name: 'Clear activity' }));
    const dialog = await screen.findByRole('alertdialog', {
      name: 'Clear all activity?',
    });
    expect(io.clear).not.toHaveBeenCalled();

    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Clear activity' }),
    );

    await waitFor(() => expect(io.clear).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('Nothing yet')).toBeVisible();
  });
});
