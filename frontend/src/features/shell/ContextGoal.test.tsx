import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { GoalPage, GoalSummary } from '../../api/types';
import ContextGoal, {
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
  max_turns: 12,
  token_budget: 0,
  tokens_used: 0,
  last_progress: 'Outlined three sections',
  last_reason: '',
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

function show(api: ContextGoalIO, compose = false, onComposeDone = vi.fn()) {
  return render(
    <ContextGoal
      conversationId="conversation-a"
      revision="r1"
      ready
      io={api}
      compose={compose}
      onComposeDone={onComposeDone}
    />,
  );
}

it('shows nothing without a goal until one is being set', async () => {
  const api = io(page([]));
  const { container } = show(api);
  await waitFor(() => expect(api.load).toHaveBeenCalledOnce());
  expect(container).toBeEmptyDOMElement();
});

it('shows the goal with its progress and pauses it in one reviewed step', async () => {
  const api = io();
  show(api);
  expect(await screen.findByText('Draft the launch checklist')).toBeVisible();
  expect(screen.getByText('Working')).toBeVisible();
  expect(
    screen.getByRole('progressbar', { name: 'Goal turns used' }),
  ).toHaveAttribute('aria-valuenow', '3');
  expect(
    screen.getByText(/3 of 12 turns · Outlined three sections/),
  ).toBeVisible();
  api.push(page([{ ...goal, status: 'paused', revision: 'goal-r2' }]));
  fireEvent.click(screen.getByRole('button', { name: 'Pause goal' }));
  await waitFor(() => expect(api.execute).toHaveBeenCalledOnce());
  expect(api.review).toHaveBeenCalledWith(
    'conversation-a',
    expect.objectContaining({
      operation: 'pause',
      goal_id: 'goal-1',
      revision: 'goal-r1',
    }),
  );
  expect(api.execute.mock.calls[0][1]).toMatchObject({
    type: 'goal.control',
    payload: { operation: 'pause', review_id: 'review-1' },
  });
  expect(
    await screen.findByRole('button', { name: 'Resume goal' }),
  ).toBeVisible();
});

it('asks before clearing a goal', async () => {
  const api = io();
  show(api);
  const more = await screen.findByRole('button', { name: 'More goal actions' });
  await act(async () => fireEvent.keyDown(more, { key: 'Enter' }));
  await act(async () =>
    fireEvent.click(screen.getByRole('menuitem', { name: 'Clear goal' })),
  );
  expect(api.review).not.toHaveBeenCalled();
  expect(await screen.findByText('Clear this goal?')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(api.review).not.toHaveBeenCalled();
});

it('starts a goal from the inline form', async () => {
  const api = io(page([]));
  const done = vi.fn();
  show(api, true, done);
  await waitFor(() => expect(api.load).toHaveBeenCalled());
  const start = screen.getByRole('button', { name: 'Start goal' });
  expect(start).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Goal objective'), {
    target: { value: 'Ship the settings pass' },
  });
  fireEvent.change(screen.getByLabelText('Turn limit'), {
    target: { value: '8' },
  });
  api.push(page([{ ...goal, objective: 'Ship the settings pass' }]));
  fireEvent.click(start);
  await waitFor(() => expect(done).toHaveBeenCalledOnce());
  expect(api.review).toHaveBeenCalledWith(
    'conversation-a',
    expect.objectContaining({
      operation: 'start',
      objective: 'Ship the settings pass',
      max_turns: 8,
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
  expect(screen.getByText(/Completed · 3 of 12 turns/)).toBeVisible();
});
