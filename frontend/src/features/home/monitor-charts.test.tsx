import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { Sparkline, Swimlane, type Lane } from './monitor-charts';

const NOW = new Date('2026-09-20T12:00:00Z');
const hoursAgo = (hours: number) => new Date(NOW.getTime() - hours * 3_600_000);

it('draws a sparkline only when there is a trend to show', () => {
  const { container, rerender } = render(
    <Sparkline values={[4]} label="Memories saved" />,
  );
  expect(screen.queryByRole('img')).toBeNull();
  expect(container.querySelector('svg')).toBeNull();

  rerender(<Sparkline values={[3, 9, 6]} label="Memories saved" />);
  const chart = screen.getByRole('img', {
    name: 'Memories saved: last 3 values from 3 to 6, highest 9',
  });
  const points = chart
    .querySelector('polyline')!
    .getAttribute('points')!
    .split(' ');
  expect(points).toHaveLength(3);

  // A flat series still draws finite coordinates.
  rerender(<Sparkline values={[5, 5]} label="Links inferred" />);
  const flat = screen.getByRole('img', {
    name: 'Links inferred: last 2 values from 5 to 5, highest 5',
  });
  expect(flat.querySelector('polyline')!.getAttribute('points')).not.toMatch(
    /NaN|Infinity/,
  );
  expect(flat.querySelector('circle')!.getAttribute('cy')).not.toMatch(
    /NaN|Infinity/,
  );
});

it('counts only events inside the range and writes failures and notes in words', () => {
  const lanes: Lane[] = [
    {
      key: 'extraction',
      label: 'Extraction',
      events: [
        { at: hoursAgo(2), label: 'Extraction: ran' },
        { at: hoursAgo(5), failed: true, label: 'Extraction: failed' },
        { at: hoursAgo(30), label: 'Extraction: older' },
        { at: hoursAgo(-1), label: 'Extraction: in the future' },
      ],
    },
    {
      key: 'channels',
      label: 'Channel events',
      events: [{ at: hoursAgo(100), label: 'Channel event (info)' }],
      note: 'from recent log lines',
    },
  ];
  const { container, rerender } = render(
    <Swimlane lanes={lanes} hours={24} now={NOW} />,
  );

  expect(
    screen.getByRole('img', {
      name: 'Extraction: 2 in the last 24 hours, 1 failed',
    }),
  ).toBeVisible();
  expect(
    screen.getByRole('img', { name: 'Channel events: 0 in the last 24 hours' }),
  ).toBeVisible();
  expect(screen.getByText('from recent log lines')).toBeVisible();
  const marks = container.querySelectorAll('.swimlane-mark');
  expect(marks).toHaveLength(2);
  expect(
    [...marks].filter((mark) => mark.hasAttribute('data-failed')),
  ).toHaveLength(1);
  for (const mark of marks) expect(mark).toHaveAttribute('aria-hidden');
  expect(marks[0].getAttribute('title')).toMatch(/^Extraction: ran · /);

  rerender(<Swimlane lanes={lanes} hours={24 * 7} now={NOW} />);
  expect(
    screen.getByRole('img', {
      name: 'Extraction: 3 in the last 7 days, 1 failed',
    }),
  ).toBeVisible();
  expect(
    screen.getByRole('img', { name: 'Channel events: 1 in the last 7 days' }),
  ).toBeVisible();
  // The source note is only needed while a lane is empty.
  expect(screen.queryByText('from recent log lines')).toBeNull();
});
