import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { BuddyCard } from '../buddy/BuddyCard';
import type { BuddySnapshot } from '../shell/BuddyControls';
import { resetBuddyMoments } from './BuddyMoment';
import { provideProactive } from './proactive-state';
import { SidebarMoment } from './SidebarMoment';
import { fakeIO, moment } from './test-data';

const snapshot = {
  schema_version: 1,
  revision: 'r1',
  preferences: {
    visible: true,
    collapsed: false,
    display_name: 'Pip',
    personality: 'warm_mystical',
    personality_description: '',
    bubble_verbosity: 'normal',
    animation_intensity: 'normal',
    pack_id: 'pack',
  },
  status: {
    mood: 'calm',
    animation: 'idle',
    energy: 0.5,
    focus: 0.5,
    alert: 0,
    event_id: 1,
    label: 'Idle',
  },
  placement: 'docked',
  native_placement_retained: false,
  activity: 'idle',
} as unknown as BuddySnapshot;

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
}

function renderBuddy() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route
          path="*"
          element={
            <>
              <BuddyCard
                snapshot={snapshot}
                avatar={<span>avatar</span>}
                portrait={<span>portrait</span>}
                onSettings={() => undefined}
              />
              <Where />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

const delivered = (fields = {}) =>
  moment({
    state: 'shown',
    surface: 'buddy',
    shown_at: new Date().toISOString(),
    ...fields,
  });

// Radix measures the popover's anchor; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

beforeEach(() => resetBuddyMoments());
afterEach(() => provideProactive(null));

describe('Buddy’s proactive states', () => {
  it('wears the glance while a moment waits, with no words', async () => {
    provideProactive(fakeIO({ moments: [moment({ state: 'pending' })] }));
    renderBuddy();
    const button = await screen.findByRole('button', { name: 'Open Pip' });
    await waitFor(() =>
      expect(button).toHaveAttribute('aria-description', 'Something for you'),
    );
    // A glance is a pose, never a bubble.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('opens one bubble for a delivered moment; its primary action reports act and opens the chat', async () => {
    const io = fakeIO({
      moments: [
        delivered({
          kind: 'workflow_offer',
          title: 'Make the Monday sales summary a workflow?',
          actions: [{ id: 'continue_in_chat', label: 'Set it up' }],
          prepared_ref: '',
        }),
      ],
    });
    io.feedback.mockResolvedValueOnce({
      ...(await io.moment('0123456789abcdef')),
      conversation_id: 'chat-1',
    });
    provideProactive(io);
    renderBuddy();
    const bubble = await screen.findByRole('dialog', {
      name: 'Make the Monday sales summary a workflow?',
    });
    // It never takes focus.
    expect(bubble).not.toContainElement(document.activeElement as HTMLElement);
    await userEvent.click(screen.getByRole('button', { name: /Set it up/ }));
    expect(io.feedback).toHaveBeenCalledWith('0123456789abcdef', {
      action: 'act',
      action_id: 'continue_in_chat',
    });
    await waitFor(() =>
      expect(screen.getByTestId('where')).toHaveTextContent(
        '/conversations/chat-1',
      ),
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('Less like this asks one question in place and writes the rule', async () => {
    const io = fakeIO({ moments: [delivered()] });
    provideProactive(io);
    renderBuddy();
    await screen.findByRole('dialog', { name: /No reply from Priya/ });
    await userEvent.click(
      screen.getByRole('button', { name: /More for No reply/ }),
    );
    await userEvent.click(
      await screen.findByRole('menuitem', { name: /Less like this/ }),
    );
    await userEvent.click(
      await screen.findByRole('radio', { name: 'Follow-ups' }),
    );
    expect(io.feedback).toHaveBeenCalledWith('0123456789abcdef', {
      action: 'less_like_this',
      scope: 'kind',
    });
    // The rule is read back to say it in its own words.
    await waitFor(() =>
      expect(io.settings.mock.results.at(-1)?.value).resolves.toMatchObject({
        rules: [{ words: 'At most one follow-up a day' }],
      }),
    );
  });

  it('never stacks: two moments become one batch line', async () => {
    provideProactive(
      fakeIO({
        moments: [
          delivered({ moment_id: '0000000000000001', kind: 'brief' }),
          delivered({ moment_id: '0000000000000002' }),
        ],
      }),
    );
    renderBuddy();
    expect(await screen.findAllByRole('dialog')).toHaveLength(1);
    expect(
      screen.getByText('2 things for you: a morning brief and a follow-up.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Open For you/ })).toBeVisible();
  });

  it('Esc closes the bubble; the moment stays for For you', async () => {
    const io = fakeIO({ moments: [delivered()] });
    provideProactive(io);
    renderBuddy();
    const bubble = await screen.findByRole('dialog', { name: /No reply/ });
    fireEvent.keyDown(bubble, { key: 'Escape' });
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
    );
    expect(io.feedback).not.toHaveBeenCalled();
  });

  it('waits while the person is typing', async () => {
    provideProactive(fakeIO({ moments: [delivered()] }));
    fireEvent.keyDown(window, { key: 'a' });
    renderBuddy();
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows nothing outside the app', () => {
    render(
      <MemoryRouter>
        <SidebarMoment />
      </MemoryRouter>,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
