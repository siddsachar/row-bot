import type { TaskRunDigest } from '../../api/types';
import { absoluteTime } from '../../ui/format';
import { runStatus } from '../home/home-format';

/**
 * The last ten saved runs, oldest to newest, as small status ticks. Colour is
 * backed by words: every tick has a title and the group has a summary label.
 */
export function RunSparkline({
  runs,
  name,
}: {
  runs: readonly TaskRunDigest[] | undefined;
  name: string;
}) {
  const recent = [...(runs ?? [])].slice(0, 10).reverse();
  if (!recent.length)
    return <span className="run-sparkline-empty">No runs yet</span>;
  const counts = new Map<string, number>();
  for (const run of recent) {
    const label = runStatus(run.status).label.toLowerCase();
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  const summary = [...counts]
    .map(([label, count]) => `${count} ${label}`)
    .join(', ');
  return (
    <span
      className="run-sparkline"
      role="img"
      aria-label={`Last ${recent.length} ${recent.length === 1 ? 'run' : 'runs'} of ${name}: ${summary}`}
    >
      {Array.from({ length: 10 - recent.length }, (_, index) => (
        <span
          key={`empty-${index}`}
          className="run-sparkline-tick"
          data-empty
        />
      ))}
      {recent.map((run, index) => {
        const view = runStatus(run.status);
        return (
          <span
            key={`${run.started_at}-${index}`}
            className="run-sparkline-tick"
            data-tone={view.tone}
            title={`${view.label} · ${absoluteTime(run.started_at) || 'unknown time'}`}
          />
        );
      })}
    </span>
  );
}
