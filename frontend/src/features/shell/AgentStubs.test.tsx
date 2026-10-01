import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { DelegatedRun } from '../../api/types';
import { AgentStubs, DelegatedRunsContext } from './AgentStubs';

const agent = (runId: string, name: string, status = 'queued') => ({
  run_id: runId,
  name,
  status,
  profile_id: '',
});
const run = (runId: string, status: string, summary = ''): DelegatedRun => ({
  run_id: runId,
  parent_conversation_id: 'parent-a',
  child_conversation_id: `child-${runId}`,
  name: `Agent ${runId}`,
  status,
  summary,
});

function stubs(runs: DelegatedRun[] | null, open = vi.fn()) {
  const agents = [
    agent('1', 'Agent 1'),
    agent('2', 'Agent 2'),
    agent('3', 'Agent 3'),
    agent('4', 'Agent 4'),
    agent('5', 'Agent 5'),
  ];
  return render(
    <DelegatedRunsContext.Provider
      value={{
        runs: new Map((runs ?? []).map((item) => [item.run_id, item])),
        settled: runs !== null,
        open,
      }}
    >
      <AgentStubs agents={agents} />
    </DelegatedRunsContext.Provider>,
  );
}

it('shows each agent’s status in words, with a shape for each (B241)', () => {
  stubs([
    run('1', 'running'),
    run('2', 'waiting_user'),
    run('3', 'completed'),
    run('4', 'timed_out'),
    run('5', 'cancelled'),
  ]);
  const list = screen.getByRole('list', { name: 'Agents started' });
  const expectations = [
    ['Agent 1, Working', 'working'],
    ['Agent 2, Waiting for you', 'waiting'],
    ['Agent 3, Done', 'done'],
    ['Agent 4, Failed', 'failed'],
    ['Agent 5, Stopped', 'stopped'],
  ];
  for (const [name, state] of expectations) {
    const stub = within(list).getByRole('button', { name });
    expect(stub).toHaveAttribute('data-state', state);
    expect(within(stub).getByText(name.split(', ')[1])).toBeVisible();
  }
  // Working pulses; Done and Failed draw a check and a cross.
  expect(
    within(list)
      .getByRole('button', { name: 'Agent 1, Working' })
      .querySelector('[data-pulse="true"]'),
  ).not.toBeNull();
  expect(
    within(list)
      .getByRole('button', { name: 'Agent 3, Done' })
      .querySelector('svg.lucide-check'),
  ).not.toBeNull();
  expect(
    within(list)
      .getByRole('button', { name: 'Agent 4, Failed' })
      .querySelector('svg.lucide-x'),
  ).not.toBeNull();
  // A failure says why, in words.
  expect(
    within(list).getByRole('button', { name: 'Agent 4, Failed' }),
  ).toHaveAttribute('aria-description', 'It ran out of time.');
});

it('shows no status until the feed answers, then the stored one for agents it lacks', () => {
  const { unmount } = stubs(null);
  expect(screen.getByRole('button', { name: 'Agent 1' })).not.toHaveAttribute(
    'data-state',
  );
  unmount();
  stubs([run('1', 'completed')]);
  expect(screen.getByRole('button', { name: 'Agent 1, Done' })).toBeVisible();
  // Agent 2 is not on the feed's page: the turn's own record stands.
  expect(
    screen.getByRole('button', { name: 'Agent 2, Working' }),
  ).toBeVisible();
});

it('opens an agent’s conversation from its stub', () => {
  const open = vi.fn();
  stubs([run('1', 'running')], open);
  fireEvent.click(screen.getByRole('button', { name: 'Agent 1, Working' }));
  expect(open).toHaveBeenCalledWith('1');
});
