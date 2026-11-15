import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import ForYou from './ForYou';
import { fakeIO, moment, settings } from './test-data';

const now = new Date(2026, 10, 16, 9, 30);
const at = (hour: number, minute = 0) =>
  new Date(2026, 10, 16, hour, minute).toISOString();

function show(io: ReturnType<typeof fakeIO>, today: React.ReactNode[] = []) {
  return render(
    <MemoryRouter>
      <ForYou
        io={io}
        now={now}
        today={today}
        footer="12 memories saved this week"
      />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  // The introduction was answered on this device.
  window.localStorage.setItem('row-bot:proactive-introduced:v1', '1');
});

describe('Home › For you', () => {
  it('groups moments as Now, Later today and Ready when you are, and ends', async () => {
    show(
      fakeIO({
        moments: [
          moment({
            moment_id: '0000000000000001',
            kind: 'meeting_prep',
            title: 'Design review with Priya and Tom',
            due_at: at(9, 40),
            actions: [{ id: 'open_brief', label: 'Open brief' }],
          }),
          moment({
            moment_id: '0000000000000002',
            kind: 'change',
            title: '1:1 with Ana moved to 15:30',
            due_at: at(15, 30),
            actions: [],
            prepared_ref: '',
          }),
          moment({ moment_id: '0000000000000003' }),
        ],
      }),
      [<li key="run">Weekly report runs at 17:00</li>],
    );
    const now = await screen.findByRole('group', { name: 'Now' });
    expect(
      within(now).getByText('Design review with Priya and Tom'),
    ).toBeVisible();
    const later = screen.getByRole('group', { name: 'Later today' });
    expect(
      within(later).getByText('1:1 with Ana moved to 15:30'),
    ).toBeVisible();
    // Today's fact rows join Later today.
    expect(
      within(later).getByText('Weekly report runs at 17:00'),
    ).toBeVisible();
    const ready = screen.getByRole('group', { name: 'Ready when you are' });
    expect(within(ready).getByText(/No reply from Priya/)).toBeVisible();
    expect(within(ready).getByText('Reply drafted, not sent')).toBeVisible();
    expect(screen.getByText('That’s everything for today.')).toBeVisible();
    expect(screen.getByText('12 memories saved this week')).toBeVisible();
  });

  it('lets expired moments leave quietly, and renders nothing on a day with nothing', async () => {
    const io = fakeIO({
      moments: [moment({ expires_at: at(9, 0) })],
    });
    const { container } = show(io);
    await waitFor(() => expect(io.settings).toHaveBeenCalled());
    await waitFor(() =>
      expect(container.querySelector('#for-you')).not.toBeInTheDocument(),
    );
    expect(screen.queryByText(/No reply from Priya/)).not.toBeInTheDocument();
  });

  it('shows the morning brief first, its lines and its end', async () => {
    show(
      fakeIO({
        moments: [
          moment({
            moment_id: '0000000000000009',
            kind: 'brief',
            title: 'Good morning. 3 meetings, 2 follow-ups.',
            body: '- 10:00 Design review\n- Priya hasn’t replied\nThat’s everything for today.',
            actions: [],
            prepared_ref: '',
          }),
        ],
      }),
    );
    const brief = await screen.findByRole('region', {
      name: 'Good morning. 3 meetings, 2 follow-ups.',
    });
    expect(within(brief).getByText('10:00 Design review')).toBeVisible();
    expect(
      within(brief).getByText('That’s everything for today.'),
    ).toBeVisible();
  });

  it('the row does the primary action and reports it', async () => {
    const io = fakeIO({
      moments: [
        moment({
          kind: 'change',
          actions: [{ id: 'open_fix', label: 'Sign in' }],
          prepared_ref: '',
          title: 'Gmail needs you to sign in again.',
        }),
      ],
    });
    show(io);
    await userEvent.click(
      await screen.findByRole('button', {
        name: /Gmail needs you to sign in again.*Sign in/,
      }),
    );
    expect(io.feedback).toHaveBeenCalledWith('0123456789abcdef', {
      action: 'act',
      action_id: 'open_fix',
    });
  });

  it('stays away while Proactive is off (Home keeps Today)', async () => {
    const io = fakeIO({
      moments: [moment()],
      settings: settings({ level: 'off' }),
    });
    const { container } = show(io);
    await waitFor(() => expect(io.settings).toHaveBeenCalled());
    expect(container.querySelector('#for-you')).not.toBeInTheDocument();
  });

  it('keeps each moment in the group the server decided, whatever its times say', async () => {
    show(
      fakeIO({
        moments: [
          // Carried in the brief and due later today: the server says Later today.
          moment({
            moment_id: '0000000000000004',
            title: 'Send Ana the deck',
            surface: '',
            due_at: at(9, 45),
            group: 'later_today',
          }),
          moment({
            moment_id: '0000000000000005',
            kind: 'meeting_prep',
            title: 'Design review',
            urgency: 'urgent',
            starts_at: at(9, 40),
            group: 'now',
            actions: [{ id: 'open_brief', label: 'Open brief' }],
          }),
        ],
      }),
    );
    const later = await screen.findByRole('group', { name: 'Later today' });
    expect(within(later).getByText('Send Ana the deck')).toBeVisible();
    const soon = screen.getByRole('group', { name: 'Now' });
    // The meeting's own start is its time: "in 10 minutes", in the row and,
    // for a narrow Home, under the title with what was prepared.
    const row = within(soon).getByRole('button', { name: /^Design review. / });
    expect(row).toHaveAccessibleName(/in 10 min/);
    expect(row.querySelector('.overview-row-meta')).toHaveTextContent(
      /Brief ready · in 10 min/,
    );
  });

  it('introduces itself once per person: the server remembers, on any device', async () => {
    window.localStorage.removeItem('row-bot:proactive-introduced:v1');
    const io = fakeIO({
      moments: [],
      settings: settings({ introduced: true }),
    });
    show(io);
    await waitFor(() => expect(io.settings).toHaveBeenCalled());
    expect(
      screen.queryByRole('region', { name: 'Row-Bot can speak first' }),
    ).not.toBeInTheDocument();

    const fresh = fakeIO({ moments: [] });
    window.localStorage.removeItem('row-bot:proactive-introduced:v1');
    const { unmount } = show(fresh);
    const intro = await screen.findByRole('region', {
      name: 'Row-Bot can speak first',
    });
    await userEvent.click(within(intro).getByRole('button', { name: 'Done' }));
    expect(fresh.changeSettings).toHaveBeenCalledWith({ introduced: true });
    unmount();
  });

  it('tells the server when the introduction was answered here before it kept that', async () => {
    const io = fakeIO({
      moments: [],
      settings: settings({ introduced: false }),
    });
    show(io);
    await waitFor(() =>
      expect(io.changeSettings).toHaveBeenCalledWith({ introduced: true }),
    );
  });

  it('introduces itself once, with the level and the privacy line', async () => {
    window.localStorage.removeItem('row-bot:proactive-introduced:v1');
    const io = fakeIO({ moments: [] });
    show(io);
    const intro = await screen.findByRole('region', {
      name: 'Row-Bot can speak first',
    });
    expect(
      within(intro).getByText(/short summaries go to your main model/),
    ).toBeVisible();
    await userEvent.click(
      within(intro).getByRole('radio', { name: 'Balanced' }),
    );
    expect(io.changeSettings).toHaveBeenCalledWith({ level: 'balanced' });
    await userEvent.click(within(intro).getByRole('button', { name: 'Done' }));
    expect(
      screen.queryByRole('region', { name: 'Row-Bot can speak first' }),
    ).not.toBeInTheDocument();
    expect(window.localStorage.getItem('row-bot:proactive-introduced:v1')).toBe(
      '1',
    );
  });
});
