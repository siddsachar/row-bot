import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import type { ProactiveActivityEntry } from '../../api/types';
import ProactiveSettings from './ProactiveSettings';
import { fakeIO, moment, settings } from '../proactive/test-data';

function show(io: ReturnType<typeof fakeIO>) {
  return render(
    <MemoryRouter>
      <ProactiveSettings io={io} />
    </MemoryRouter>,
  );
}

describe('Settings › Proactive', () => {
  it('changes the level at once and shows what each level brings up', async () => {
    const io = fakeIO();
    show(io);
    expect(
      await screen.findByText(/Things wait on Home\. Buddy speaks only/),
    ).toBeVisible();
    await userEvent.click(screen.getByRole('radio', { name: 'Balanced' }));
    expect(io.changeSettings).toHaveBeenCalledWith({ level: 'balanced' });
    expect(await screen.findByText('An example day at Balanced')).toBeVisible();
    expect(screen.getByText(/Buddy speaks at good moments/)).toBeVisible();
  });

  it("shows what each level would have brought up today, from today's moments", async () => {
    const item = (
      moment_id: string,
      title: string,
      where: 'buddy' | 'notification' | 'home',
      in_brief = false,
    ) => ({
      moment_id,
      kind: 'follow_up' as const,
      title,
      at: where !== 'home' ? new Date(2026, 10, 16, 9, 50).toISOString() : '',
      where,
      in_brief,
    });
    const io = fakeIO({
      preview: {
        schema_version: 1,
        example: false,
        levels: [
          { level: 'off', items: [] },
          {
            level: 'quiet',
            items: [
              item('0000000000000001', 'Design review brief', 'buddy'),
              item('0000000000000002', 'Priya hasn’t replied', 'home', true),
              item('0000000000000003', 'Venue walkthrough', 'home'),
              item('0000000000000004', 'Menu tasting call', 'notification'),
            ],
          },
          {
            level: 'balanced',
            items: [
              item('0000000000000001', 'Design review brief', 'buddy'),
              item('0000000000000002', 'Priya hasn’t replied', 'buddy'),
            ],
          },
          { level: 'eager', items: [] },
        ],
      },
    });
    show(io);
    expect(
      await screen.findByText('Today at Quiet you’d have seen'),
    ).toBeVisible();
    expect(screen.getByText('Home only (in the brief)')).toBeVisible();
    // Where each really went (R5): one stayed on Home, one was a notification.
    const rows = screen.getAllByRole('listitem');
    expect(rows[2]).toHaveTextContent('Venue walkthroughHome only');
    expect(rows[3]).toHaveTextContent('Menu tasting callA notification');
    expect(screen.queryByText(/An example day/)).toBeNull();
    await userEvent.click(screen.getByRole('radio', { name: 'Balanced' }));
    expect(
      await screen.findByText('Today at Balanced you’d have seen'),
    ).toBeVisible();
    expect(screen.getAllByText('Buddy’s bubble')).toHaveLength(2);
  });

  it('lists only connected sources, each kind’s last 30 days, and the end-of-day wrap', async () => {
    const base = settings();
    const io = fakeIO({
      settings: settings({
        sources: [
          { ...base.sources[0], connected: true },
          { ...base.sources[1], connected: false },
        ],
        kinds: base.kinds.map((kind) =>
          kind.id === 'follow_up'
            ? {
                ...kind,
                last_30_days: { shown: 9, acted: 7, dismissed: 1, ignored: 0 },
              }
            : kind,
        ),
        wrap: false,
      }),
    });
    show(io);
    expect(await screen.findByRole('switch', { name: 'Mail' })).toBeVisible();
    expect(screen.queryByRole('switch', { name: 'Calendar' })).toBeNull();
    expect(
      screen.getByText('Last 30 days: 9 shown · 7 opened · 1 dismissed'),
    ).toBeVisible();
    await userEvent.click(
      screen.getByRole('switch', { name: 'End-of-day wrap' }),
    );
    expect(io.changeSettings).toHaveBeenCalledWith({ wrap: true });
  });

  it('turns a source off, and brings a quieted kind back', async () => {
    const io = fakeIO();
    show(io);
    expect(
      await screen.findByText(/Quieted: you left the last few alone/),
    ).toBeVisible();
    await userEvent.click(screen.getByRole('switch', { name: 'Mail' }));
    expect(io.changeSettings).toHaveBeenCalledWith({
      sources: { mail: false },
    });
    await userEvent.click(screen.getByRole('button', { name: 'Bring back' }));
    await waitFor(() =>
      expect(io.changeSettings).toHaveBeenCalledWith({ kinds: { date: true } }),
    );
    expect(io.changeSettings).toHaveBeenCalledWith({ kinds: { date: false } });
  });

  it('lists your rules to edit or delete', async () => {
    const io = fakeIO({
      settings: settings({
        rules: [
          {
            rule_id: 'bbbbbbbbbbbb',
            type: 'never_about',
            kind: '',
            topic: 'the offsite',
            hour: -1,
            words: 'Never about the offsite',
          },
        ],
      }),
    });
    show(io);
    const rules = await screen.findByRole('list', { name: 'Your rules' });
    await userEvent.click(
      within(rules).getByRole('button', {
        name: 'Delete: Never about the offsite',
      }),
    );
    expect(io.deleteRule).toHaveBeenCalledWith('bbbbbbbbbbbb');
    expect(await screen.findByText(/No rules yet/)).toBeVisible();
  });

  it('shows every check in the Activity log, and planned moments with Cancel', async () => {
    const io = fakeIO({
      moments: [moment({ state: 'pending', title: 'Brief for 1:1 with Ana' })],
    });
    show(io);
    expect(
      await screen.findByText('Checked mail, calendar: nothing new.'),
    ).toBeVisible();
    expect(screen.getByText('Brief for 1:1 with Ana · planned')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(io.feedback).toHaveBeenCalledWith('0123456789abcdef', {
      action: 'dismiss',
    });
  });

  it('folds checks in a row into one Activity line, and lists each under Silent', async () => {
    const io = fakeIO();
    const at = (minute: number) => {
      const when = new Date();
      when.setHours(14, minute, 0, 0);
      return when.toISOString();
    };
    const check = (
      entry_id: number,
      minute: number,
    ): ProactiveActivityEntry => ({
      entry_id,
      at: at(minute),
      kind: 'pass',
      summary: 'Checked workflows: nothing new.',
      moment_id: '',
    });
    io.activity.mockResolvedValue({
      schema_version: 1,
      // Newest first.
      items: [
        check(6, 20),
        {
          entry_id: 5,
          at: at(16),
          kind: 'moment',
          summary: 'Shown on Home: Priya hasn’t replied',
          moment_id: '0123456789abcdef',
        },
        check(4, 15),
        check(3, 10),
        check(2, 5),
        check(1, 0),
      ],
      next_before: null,
    });
    show(io);
    const shown = await screen.findByText(
      'Shown on Home: Priya hasn’t replied',
    );
    const log = shown.closest('ul')!;
    const lines = within(log).getAllByRole('listitem');
    // What was shown stands out between the checks; the four in a row are one line.
    expect(lines).toHaveLength(3);
    expect(lines[1]).toHaveTextContent('Shown on Home: Priya hasn’t replied');
    const clock = (minute: number) =>
      new Date(at(minute)).toLocaleTimeString(undefined, {
        hour: 'numeric',
        minute: '2-digit',
      });
    expect(lines[2].textContent).toBe(
      `${clock(15)}Checked workflows: nothing new. 4 checks since ${clock(0)}`,
    );
    await userEvent.click(screen.getByRole('radio', { name: 'Silent' }));
    expect(
      await screen.findAllByText('Checked workflows: nothing new.'),
    ).toHaveLength(5);
    expect(io.activity).toHaveBeenCalledWith(undefined, 100);
  });
});
