import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { GoalPage, GoalSummary } from '../../api/types';
import ContextGoal, {
  goalState,
  goalTurn,
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
  last_progress: 'Outlined three sections',
  last_reason: 'Two sections are still empty.',
  evidence: [],
  blockers: [],
  active_profile_id: '',
};

function page(items: GoalSummary[] = [goal]): GoalPage {
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
});

it('shows nothing without a goal until one is being set', async () => {
  const api = io(page([]));
  const { container } = show(api);
  await waitFor(() => expect(api.load).toHaveBeenCalledOnce());
  expect(container).toBeEmptyDOMElement();
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

it('starts a goal from a labelled field with a limit of 10 by default', async () => {
  const api = io(page([]));
  const done = vi.fn();
  show(api, { compose: true, onComposeDone: done });
  await waitFor(() => expect(api.load).toHaveBeenCalled());
  const start = screen.getByRole('button', { name: 'Start goal' });
  expect(start).toBeDisabled();
  expect(screen.getByLabelText('Turn limit')).toHaveValue(10);
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
      max_turns: 10,
    }),
  );
});

it('keeps earlier goals in the thread after the current one ends', async () => {
  const done = { ...goal, id: 'goal-0', status: 'completed' as const };
  const api = io({
    ...page([done]),
    current_goal_id: null,
    current_revision: 'none',
  });
  show(api);
  expect(await screen.findByText('No goal is running.')).toBeVisible();
  fireEvent.click(screen.getByText('Earlier goals'));
  expect(screen.getByText('Draft the launch checklist')).toBeVisible();
  expect(screen.getByText(/Done · 3 of 10 turns/)).toBeVisible();
});
