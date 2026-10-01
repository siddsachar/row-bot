import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import type { TaskRunDigest } from '../../api/types';
import { absoluteTime } from '../../ui/format';
import { RunSparkline } from './RunSparkline';

function digest(status: string, day: number): TaskRunDigest {
  return {
    status,
    started_at: `2026-09-${String(day).padStart(2, '0')}T09:00:00Z`,
  };
}

it.each([
  ['no saved runs', undefined],
  ['an empty run list', []],
] as const)('says "No runs yet" for %s', (_, runs) => {
  const view = render(<RunSparkline runs={runs} name="Digest" />);
  expect(screen.getByText('No runs yet')).toBeVisible();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(view.container.querySelectorAll('.run-sparkline-tick')).toHaveLength(
    0,
  );
});

it('draws the saved runs oldest to newest after empty slots, with words for every tick', () => {
  // Saved runs arrive newest first.
  const runs = [
    digest('failed', 26),
    digest('completed', 25),
    digest('completed', 24),
  ];
  const view = render(<RunSparkline runs={runs} name="Digest" />);
  const sparkline = screen.getByRole('img', {
    name: 'Last 3 runs of Digest: 2 completed, 1 failed',
  });
  expect(sparkline).toBeVisible();
  const ticks = [...view.container.querySelectorAll('.run-sparkline-tick')];
  expect(ticks).toHaveLength(10);
  expect(
    ticks.slice(0, 7).every((tick) => tick.hasAttribute('data-empty')),
  ).toBe(true);
  expect(ticks.slice(0, 7).every((tick) => !tick.hasAttribute('title'))).toBe(
    true,
  );
  const drawn = ticks.slice(7);
  expect(drawn.map((tick) => tick.getAttribute('data-tone'))).toEqual([
    'success',
    'success',
    'danger',
  ]);
  expect(drawn.map((tick) => tick.getAttribute('title'))).toEqual([
    `Completed · ${absoluteTime(runs[2].started_at)}`,
    `Completed · ${absoluteTime(runs[1].started_at)}`,
    `Failed · ${absoluteTime(runs[0].started_at)}`,
  ]);
  expect(drawn.some((tick) => tick.hasAttribute('data-empty'))).toBe(false);
  expect(screen.queryByText('No runs yet')).not.toBeInTheDocument();
});

it('shows only the ten most recent runs', () => {
  const runs = Array.from({ length: 12 }, (_, index) =>
    digest(index < 10 ? 'completed' : 'failed', 26 - index),
  );
  const view = render(<RunSparkline runs={runs} name="Digest" />);
  expect(
    screen.getByRole('img', { name: 'Last 10 runs of Digest: 10 completed' }),
  ).toBeVisible();
  const ticks = [...view.container.querySelectorAll('.run-sparkline-tick')];
  expect(ticks).toHaveLength(10);
  expect(ticks.some((tick) => tick.hasAttribute('data-empty'))).toBe(false);
  expect(
    ticks.every((tick) => tick.getAttribute('data-tone') === 'success'),
  ).toBe(true);
  // The newest run is the last tick.
  expect(ticks[9]).toHaveAttribute(
    'title',
    `Completed · ${absoluteTime(runs[0].started_at)}`,
  );
});

it('uses singular wording, readable status words and a fallback time', () => {
  const view = render(
    <RunSparkline
      runs={[{ status: 'waiting_approval', started_at: 'not a time' }]}
      name="Review"
    />,
  );
  expect(
    screen.getByRole('img', {
      name: 'Last 1 run of Review: 1 waiting for approval',
    }),
  ).toBeVisible();
  const tick = view.container.querySelector(
    '.run-sparkline-tick:not([data-empty])',
  );
  expect(tick).toHaveAttribute('title', 'Waiting for approval · unknown time');
  expect(tick).toHaveAttribute('data-tone', 'warning');
});

it('names unknown statuses in words rather than raw identifiers', () => {
  const view = render(
    <RunSparkline
      runs={[digest('partially_done', 26), digest('PAUSED', 25)]}
      name="Sync"
    />,
  );
  expect(
    screen.getByRole('img', {
      name: 'Last 2 runs of Sync: 1 waiting for approval, 1 partially done',
    }),
  ).toBeVisible();
  const drawn = [
    ...view.container.querySelectorAll('.run-sparkline-tick:not([data-empty])'),
  ];
  expect(drawn.map((tick) => tick.getAttribute('data-tone'))).toEqual([
    'warning',
    'neutral',
  ]);
  expect(drawn[1].getAttribute('title')).toMatch(/^Partially done · /);
  expect(view.container.textContent).not.toContain('partially_done');
});
