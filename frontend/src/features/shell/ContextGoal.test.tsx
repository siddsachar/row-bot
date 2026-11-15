import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import type { GoalPage, GoalSummary } from '../../api/types';
import ContextGoal, {
  goalState,
  goalTurn,
  goalUsage,
  resetContextGoals,
  type ContextGoalIO,
} from './ContextGoal';

afterEach(() => resetContextGoals());

const goal: GoalSummary = {
  id: 'goal-1',
  scope: 'conversation',
  conversation_id: 'conversation-a',
  objective: 'Draft the launch checklist',
  status: 'active',
  revision: 'goal-r1',
  turns_used: 3,
  max_turns: 10,
  token_budget: 0,
  tokens_used: 0,
  started_at: '',
  window_started_at: '',
  max_minutes: 0,
  last_progress: 'Outlined three sections',
  last_reason: 'Two sections are still empty.',
  evidence: [],
  blockers: [],
  active_profile_id: '',
  done_means: '',
  check: null,
};

function page(
  items: GoalSummary[] = [goal],
  defaultTurns = 0,
  place: GoalPage['check_place'] = {
    available: true,
    runs_in: "Row-Bot's workspace",
    suggestions: [],
  },
): GoalPage {
  return {
    schema_version: 1,
    scope: 'conversation',
    conversation_id: 'conversation-a',
    revision: 'page-r1',
    current_goal_id: items[0]?.id ?? null,
    current_revision: items[0]?.revision ?? 'none',
    items,
    total: items.length,
    next_cursor: null,
    default_max_turns: defaultTurns,
    check_place: place,
  };
}

function io(first: GoalPage = page()) {
  const pages = [first];
  const api = {
    load: vi.fn(async () => pages.at(-1)!),
    review: vi.fn(async (_conversation: string, payload) => ({
      ...payload,
      schema_version: 1 as const,
      action_digest: 'digest',
      disclosures: [],
      review_id: 'review-1',
    })),
    execute: vi.fn(async (_conversation: string, command) => ({
      command_id: command.command_id,
      status: 'completed' as const,
    })),
    push: (next: GoalPage) => pages.push(next),
  };
  return api as ContextGoalIO & typeof api;
}

function show(
  api: ContextGoalIO,
  {
    compose = false,
    running = false,
    activity = 'a',
    onCompose = vi.fn(),
    onComposeDone = vi.fn(),
    onStopTurn = vi.fn(),
  } = {},
) {
  const element = (next: { running: boolean; activity: string }) => (
    <ContextGoal
      conversationId="conversation-a"
      activity={next.activity}
      running={next.running}
      ready
      io={api}
      compose={compose}
      onCompose={onCompose}
      onComposeDone={onComposeDone}
      onStopTurn={onStopTurn}
    />
  );
  const view = render(element({ running, activity }));
  return {
    ...view,
    update: (next: { running: boolean; activity: string }) =>
      view.rerender(element(next)),
  };
}

it('says Working only while a turn runs, never while idle (B123)', () => {
  expect(goalState(goal, true)).toMatchObject({ label: 'Working' });
  expect(goalState(goal, false).label).toBe('Continuing');
  expect(goalState({ status: 'waiting_approval' }, true).label).toBe(
    'Waiting for your approval',
  );
  expect(goalState({ status: 'blocked' }, false).label).toBe('Needs you');
  expect(goalState({ status: 'completed' }, false).label).toBe('Done');
  expect(goalTurn(goal, true)).toBe('Turn 4 of 10');
  expect(goalTurn(goal, false)).toBe('Turn 3 of 10');
  expect(goalTurn({ ...goal, turns_used: 10 }, true)).toBe('Turn 10 of 10');
  // No limit: just the turn (B243).
  expect(goalTurn({ ...goal, max_turns: 0 }, true)).toBe('Turn 4');
  expect(goalTurn({ ...goal, max_turns: 0 }, false)).toBe('Turn 3');
});

it('spends no Goal row on a chat without a goal: a small Set a goal opens the composer', async () => {
  const api = io(page([]));
  const onCompose = vi.fn();
  show(api, { onCompose });
  await waitFor(() => expect(api.load).toHaveBeenCalledOnce());
  expect(screen.queryByText('Goal', { selector: 'summary' })).toBeNull();
  expect(screen.queryByText('No goal')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Set a goal' }));
  expect(onCompose).toHaveBeenCalledOnce();
});

it('moves focus into the goal form as it opens and back to Set a goal on Cancel', async () => {
  const api = io(page([]));
  function Owner() {
    const [compose, setCompose] = useState(false);
    return (
      <ContextGoal
        conversationId="conversation-a"
        activity="a"
        running={false}
        ready
        io={api}
        compose={compose}
        onCompose={() => setCompose(true)}
        onComposeDone={() => setCompose(false)}
      />
    );
  }
  render(<Owner />);
  await waitFor(() => expect(api.load).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole('button', { name: 'Set a goal' }));
  // While it is set, the full Goal section shows with its form.
  expect(screen.getByText('Goal', { selector: 'summary' })).toBeVisible();
  expect(
    screen.getByRole('textbox', {
      name: 'What should this conversation achieve?',
    }),
  ).toHaveFocus();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.queryByText('Goal', { selector: 'summary' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Set a goal' })).toHaveFocus();
});

it('waits for the conversation before a goal can be set', () => {
  render(
    <ContextGoal
      conversationId="conversation-a"
      activity="a"
      running={false}
      ready={false}
      io={io(page([]))}
      compose={false}
      onCompose={vi.fn()}
      onComposeDone={vi.fn()}
    />,
  );
  expect(screen.getByRole('button', { name: 'Set a goal' })).toBeDisabled();
});

it('shows the turn, the latest reason, and pauses in one reviewed step', async () => {
  const api = io();
  show(api, { running: true });
  expect(await screen.findByText('Draft the launch checklist')).toBeVisible();
  expect(screen.getByText('Working')).toBeVisible();
  expect(screen.getByText('Turn 4 of 10')).toBeVisible();
  expect(screen.getByText('Two sections are still empty.')).toBeVisible();
  expect(
    screen.getByRole('progressbar', { name: 'Goal turns' }),
  ).toHaveAttribute('aria-valuenow', '3');
  api.push(page([{ ...goal, status: 'paused', revision: 'goal-r2' }]));
  fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
  await waitFor(() => expect(api.execute).toHaveBeenCalledOnce());
  expect(api.review).toHaveBeenCalledWith(
    'conversation-a',
    expect.objectContaining({
      operation: 'pause',
      goal_id: 'goal-1',
      revision: 'goal-r1',
    }),
  );
  expect(await screen.findByRole('button', { name: 'Resume' })).toBeVisible();
});

it('Stop ends the goal and the turn it is running, without a dialog', async () => {
  const api = io();
  const onStopTurn = vi.fn();
  show(api, { running: true, onStopTurn });
  fireEvent.click(await screen.findByRole('button', { name: 'Stop' }));
  expect(onStopTurn).toHaveBeenCalledOnce();
  await waitFor(() => expect(api.execute).toHaveBeenCalledOnce());
  expect(api.review).toHaveBeenCalledWith(
    'conversation-a',
    expect.objectContaining({ operation: 'clear' }),
  );
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('re-reads when a turn starts or ends', async () => {
  const api = io();
  const view = show(api, { running: false, activity: 'g1:completed' });
  await screen.findByText('Turn 3 of 10');
  api.push(page([{ ...goal, turns_used: 4, last_reason: 'One left.' }]));
  view.update({ running: true, activity: 'g2:running' });
  expect(await screen.findByText('One left.')).toBeVisible();
  expect(screen.getByText('Turn 5 of 10')).toBeVisible();
});

it('never counts backwards while the goal is re-read after a turn', async () => {
  const api = io();
  const view = show(api, { running: true, activity: 'g4:running' });
  // Read while turn 4 runs: 3 turns done, the 4th under way.
  await screen.findByText('Turn 4 of 10');
  let release = () => {};
  api.load.mockImplementationOnce(
    () =>
      new Promise<GoalPage>((resolve) => {
        release = () => resolve(page([{ ...goal, turns_used: 4 }]));
      }),
  );
  view.update({ running: false, activity: 'g4:completed' });
  expect(screen.getByText('Continuing')).toBeVisible();
  expect(screen.getByText('Turn 4 of 10')).toBeVisible();
  await waitFor(() => expect(api.load).toHaveBeenCalledTimes(2));
  release();
  // The server counted turn 4: still "Turn 4 of 10", now as turns done.
  await waitFor(() => expect(screen.getByText('Turn 4 of 10')).toBeVisible());
  expect(screen.queryByText('Turn 3 of 10')).toBeNull();
});

it('starts a goal with no turn limit by default (B243)', async () => {
  const api = io(page([]));
  const done = vi.fn();
  show(api, { compose: true, onComposeDone: done });
  await waitFor(() => expect(api.load).toHaveBeenCalled());
  const start = screen.getByRole('button', { name: 'Start goal' });
  expect(start).toBeDisabled();
  const limit = screen.getByLabelText('Turn limit');
  expect(limit).toHaveValue(null);
  expect(limit).toHaveAttribute('placeholder', 'No limit');
  fireEvent.change(
    screen.getByRole('textbox', {
      name: 'What should this conversation achieve?',
    }),
    { target: { value: 'Ship the settings pass' } },
  );
  api.push(page([{ ...goal, objective: 'Ship the settings pass' }]));
  fireEvent.click(start);
  await waitFor(() => expect(done).toHaveBeenCalledOnce());
  expect(api.review).toHaveBeenCalledWith(
    'conversation-a',
    expect.objectContaining({
      operation: 'start',
      objective: 'Ship the settings pass',
      max_turns: null,
    }),
  );
});

it('starts with the Agent runtime default, which can be changed or cleared', async () => {
  const api = io(page([], 30));
  show(api, { compose: true });
  const limit = await screen.findByLabelText('Turn limit');
  await waitFor(() => expect(limit).toHaveValue(30));
  const objective = screen.getByRole('textbox', {
    name: 'What should this conversation achieve?',
  });
  fireEvent.change(objective, { target: { value: 'Overnight research' } });
  fireEvent.change(limit, { target: { value: '0' } });
  expect(screen.getByRole('button', { name: 'Start goal' })).toBeDisabled();
  fireEvent.change(limit, { target: { value: '' } });
  api.push(page([{ ...goal, objective: 'Overnight research', max_turns: 0 }]));
  fireEvent.click(screen.getByRole('button', { name: 'Start goal' }));
  await waitFor(() => expect(api.execute).toHaveBeenCalledOnce());
  expect(api.review).toHaveBeenCalledWith(
    'conversation-a',
    expect.objectContaining({ operation: 'start', max_turns: null }),
  );
});

it('reads "Turn 7" without a limit and draws no progress bar', async () => {
  const unlimited = { ...goal, turns_used: 7, max_turns: 0 };
  show(io(page([unlimited])));
  expect(await screen.findByText('Turn 7')).toBeVisible();
  expect(screen.queryByRole('progressbar')).toBeNull();
});

it('keeps earlier goals in the thread after the current one ends', async () => {
  const done = { ...goal, id: 'goal-0', status: 'completed' as const };
  const api = io({
    ...page([done]),
    current_goal_id: null,
    current_revision: 'none',
  });
  show(api);
  // The section stays for its history, with Set a goal and no "No goal".
  expect(await screen.findByText('Earlier goals')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Set a goal' })).toBeVisible();
  expect(screen.queryByText('No goal')).toBeNull();
  fireEvent.click(screen.getByText('Earlier goals'));
  expect(screen.getByText('Draft the launch checklist')).toBeVisible();
  expect(screen.getByText(/Done · 3 of 10 turns/)).toBeVisible();
});

it('lists an earlier goal without a limit by its turns alone', async () => {
  const done = {
    ...goal,
    id: 'goal-0',
    status: 'completed' as const,
    max_turns: 0,
  };
  show(
    io({ ...page([done]), current_goal_id: null, current_revision: 'none' }),
  );
  fireEvent.click(await screen.findByText('Earlier goals'));
  expect(screen.getByText(/Done · 3 turns/)).toBeVisible();
});

it('shows the time running and the tokens used, with the time limit (B244)', async () => {
  const now = new Date('2026-09-30T23:10:00');
  const running = {
    ...goal,
    status: 'active' as const,
    started_at: '2026-09-30T21:00:00',
    window_started_at: '2026-09-30T21:00:00',
    tokens_used: 12400,
  };
  expect(goalUsage(running, now)).toEqual(['2 h 10 min', '12.4k tokens']);
  expect(goalUsage({ ...running, max_minutes: 480 }, now)).toEqual([
    '2 h 10 min of 8 h',
    '12.4k tokens',
  ]);
  // A paused goal is not running; its tokens still show.
  expect(
    goalUsage({ ...running, status: 'paused', tokens_used: 950 }, now),
  ).toEqual(['950 tokens']);
  render(
    <ContextGoal
      conversationId="conversation-a"
      activity="a"
      running={false}
      ready
      io={io(page([{ ...running, turns_used: 7, max_turns: 0 }]))}
      compose={false}
      onCompose={vi.fn()}
      onComposeDone={vi.fn()}
      now={now}
    />,
  );
  expect(
    await screen.findByText('Turn 7 · 2 h 10 min · 12.4k tokens'),
  ).toBeVisible();
});

it('starts a goal with an optional time limit in hours', async () => {
  const api = io(page([]));
  show(api, { compose: true });
  const hours = await screen.findByLabelText('Time limit (hours)');
  expect(hours).toHaveAttribute('placeholder', 'No limit');
  fireEvent.change(
    screen.getByRole('textbox', {
      name: 'What should this conversation achieve?',
    }),
    { target: { value: 'Research overnight' } },
  );
  fireEvent.change(hours, { target: { value: '200' } });
  expect(screen.getByRole('button', { name: 'Start goal' })).toBeDisabled();
  fireEvent.change(hours, { target: { value: '8' } });
  api.push(page([{ ...goal, objective: 'Research overnight' }]));
  fireEvent.click(screen.getByRole('button', { name: 'Start goal' }));
  await waitFor(() => expect(api.execute).toHaveBeenCalledOnce());
  expect(api.review).toHaveBeenCalledWith(
    'conversation-a',
    expect.objectContaining({ max_turns: null, max_minutes: 480 }),
  );
});

// B3: "Done means…" and a check that must pass.

const objectiveBox = () =>
  screen.getByRole('textbox', {
    name: 'What should this conversation achieve?',
  });

it('reviews a goal with a check first, and Start goal confirms that review', async () => {
  const api = io(
    page([], 0, {
      available: true,
      runs_in: 'row-bot (Docker sandbox, network off)',
      suggestions: ['npm test', 'npm run lint'],
    }),
  );
  api.review.mockImplementationOnce(async (_conversation, payload) => ({
    ...payload,
    schema_version: 1 as const,
    action_digest: 'digest',
    disclosures: [
      'Each time Row-Bot thinks the goal is done, it runs `npm test` in row-bot (Docker sandbox, network off). The goal counts as done only when it exits 0.',
    ],
    review_id: 'review-check',
  }));
  const done = vi.fn();
  show(api, { compose: true, onComposeDone: done });
  await waitFor(() => expect(api.load).toHaveBeenCalled());
  fireEvent.change(objectiveBox(), { target: { value: 'Fix the docs' } });
  // Collapsed until opened: most goals need neither.
  const disclosure = screen.getByText('Done means and check', {
    selector: 'summary',
  });
  expect(screen.getByLabelText('Check with')).not.toBeVisible();
  fireEvent.click(disclosure);
  fireEvent.change(screen.getByLabelText('Done means…'), {
    target: { value: 'Every link works' },
  });
  expect(
    screen.getByText(/Runs in row-bot \(Docker sandbox, network off\)/),
  ).toBeVisible();
  // The project's own commands fill the field.
  fireEvent.click(screen.getByRole('button', { name: 'npm test' }));
  expect(screen.getByLabelText('Check with')).toHaveValue('npm test');
  expect(screen.getByRole('button', { name: 'npm test' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );

  fireEvent.click(screen.getByRole('button', { name: 'Start goal' }));
  const review = await screen.findByRole('group', { name: 'Before it starts' });
  expect(review).toHaveTextContent(
    'it runs npm test in row-bot (Docker sandbox, network off)',
  );
  expect(review.querySelector('code')).toHaveTextContent('npm test');
  expect(api.execute).not.toHaveBeenCalled();
  expect(api.review).toHaveBeenCalledWith(
    'conversation-a',
    expect.objectContaining({
      done_means: 'Every link works',
      check_command: 'npm test',
    }),
  );

  api.push(page([{ ...goal, objective: 'Fix the docs' }]));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm and start' }));
  await waitFor(() => expect(done).toHaveBeenCalledOnce());
  expect(api.review).toHaveBeenCalledOnce();
  expect(api.execute).toHaveBeenCalledWith(
    'conversation-a',
    expect.objectContaining({
      payload: expect.objectContaining({
        check_command: 'npm test',
        review_id: 'review-check',
      }),
    }),
  );
});

it('asks for a fresh review after the form changes', async () => {
  const api = io(page([]));
  show(api, { compose: true });
  await waitFor(() => expect(api.load).toHaveBeenCalled());
  fireEvent.change(objectiveBox(), { target: { value: 'Fix the docs' } });
  fireEvent.click(
    screen.getByText('Done means and check', { selector: 'summary' }),
  );
  fireEvent.change(screen.getByLabelText('Check with'), {
    target: { value: 'npm test' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Start goal' }));
  await screen.findByRole('button', { name: 'Confirm and start' });
  fireEvent.change(screen.getByLabelText('Check with'), {
    target: { value: 'npm run build' },
  });
  expect(screen.getByRole('button', { name: 'Start goal' })).toBeVisible();
  expect(screen.queryByRole('group', { name: 'Before it starts' })).toBeNull();
});

it('shows a refused check inline, in the field', async () => {
  const api = io(page([]));
  api.review.mockRejectedValueOnce({ code: 'goal_check_read_only' });
  show(api, { compose: true });
  await waitFor(() => expect(api.load).toHaveBeenCalled());
  fireEvent.change(objectiveBox(), { target: { value: 'Fix the docs' } });
  fireEvent.click(
    screen.getByText('Done means and check', { selector: 'summary' }),
  );
  fireEvent.change(screen.getByLabelText('Check with'), {
    target: { value: 'npm test' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Start goal' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'This chat is on Block, which only allows checks that just read.',
  );
  expect(screen.getByLabelText('Check with')).toHaveAttribute(
    'aria-invalid',
    'true',
  );
  expect(api.execute).not.toHaveBeenCalled();
});

function checked(
  state: NonNullable<GoalSummary['check']>['state'],
  extra: Partial<GoalSummary> = {},
): GoalSummary {
  return {
    ...goal,
    done_means: 'Every link works',
    check: {
      command: 'npm test',
      runs_in: "Row-Bot's workspace",
      state,
      result:
        state === 'passed' || state === 'failed' || state === 'error'
          ? {
              exit_code: state === 'passed' ? 0 : state === 'failed' ? 1 : null,
              started_at: '2026-11-15T14:02:00',
              finished_at: '2026-11-15T14:05:00',
              duration_seconds: 180,
              output_tail: state === 'failed' ? 'FAIL docs/index.md' : '',
            }
          : null,
    },
    ...extra,
  };
}

it.each([
  ['', 'active', 'Checks with npm test'],
  ['running', 'active', 'Checking with npm test…'],
  ['passed', 'completed', /^Verified by npm test · /],
  ['failed', 'active', 'npm test failed · Row-Bot is fixing it'],
  ['failed', 'paused', 'npm test failed'],
  ['error', 'paused', "Couldn't run npm test"],
] as const)(
  'shows the check line for a %s check on a %s goal',
  async (state, status, line) => {
    show(io(page([checked(state, { status })])));
    expect(
      await screen.findByText('Done means: Every link works'),
    ).toBeVisible();
    const status_ = screen
      .getAllByRole('status')
      .find((node) => node.classList.contains('context-goal-check'))!;
    expect(status_.textContent).toMatch(line);
  },
);

it('keeps the reason off the card while the check says it, but shows why it paused', async () => {
  show(
    io(
      page([checked('running', { last_reason: 'Checking with `npm test`…' })]),
    ),
  );
  await screen.findByText('Checking with', { exact: false });
  expect(screen.queryByText('Checking with `npm test`…')).toBeNull();
  resetContextGoals();
  show(
    io(
      page([
        checked('error', {
          status: 'paused',
          last_reason: "`npm test` didn't finish in 10 minutes.",
        }),
      ]),
    ),
  );
  expect(
    await screen.findByText("`npm test` didn't finish in 10 minutes."),
  ).toBeVisible();
});

it('opens the check output by keyboard, with Copy', async () => {
  show(io(page([checked('failed')])));
  const info = await screen.findByRole('button', {
    name: "Show the check's output",
  });
  info.focus();
  await userEvent.keyboard('{Enter}');
  const output = await screen.findByRole('dialog', {
    name: "The check's output",
  });
  expect(output).toHaveTextContent('FAIL docs/index.md');
  expect(output).toHaveTextContent('exit 1');
  expect(screen.getByRole('button', { name: 'Copy the output' })).toBeVisible();
});

it('says an earlier goal was verified by its check', async () => {
  const done = { ...checked('passed', { status: 'completed' }), id: 'goal-0' };
  show(
    io({ ...page([done]), current_goal_id: null, current_revision: 'none' }),
  );
  fireEvent.click(await screen.findByText('Earlier goals'));
  expect(screen.getByText(/Done · 3 of 10 turns · Verified by/)).toBeVisible();
});
