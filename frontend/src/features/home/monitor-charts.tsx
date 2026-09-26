import { absoluteTime } from '../../ui/format';

/**
 * One series of values as a thin 2px line with its latest point marked.
 * The title names the series, so there is no legend; the label states the
 * range in words for assistive tech.
 */
export function Sparkline({
  values,
  label,
  width = 72,
  height = 22,
}: {
  values: readonly number[];
  label: string;
  width?: number;
  height?: number;
}) {
  if (values.length < 2)
    return <span className="sparkline-empty" aria-hidden />;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const step = (width - 4) / (values.length - 1);
  const points = values.map((value, index) => [
    2 + index * step,
    height - 3 - ((value - min) / span) * (height - 6),
  ]);
  const [lastX, lastY] = points[points.length - 1];
  return (
    <svg
      className="sparkline"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`${label}: last ${values.length} values from ${values[0]} to ${values[values.length - 1]}, highest ${max}`}
    >
      <polyline
        points={points
          .map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`)
          .join(' ')}
        fill="none"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx={lastX} cy={lastY} r={2.5} />
    </svg>
  );
}

export type LaneEvent = {
  at: Date;
  failed?: boolean;
  label: string;
};

export type Lane = {
  key: string;
  label: string;
  events: readonly LaneEvent[];
  /** Shown after the counts, e.g. where the data comes from. */
  note?: string;
};

/**
 * A time strip per activity: each event is a dot at its time, failures in
 * the danger tone with a ring. The counts are always written out beside it.
 */
export function Swimlane({
  lanes,
  hours,
  now,
}: {
  lanes: readonly Lane[];
  hours: number;
  now: Date;
}) {
  const end = now.getTime();
  const start = end - hours * 3_600_000;
  const position = (date: Date) =>
    Math.min(
      100,
      Math.max(0, ((date.getTime() - start) / (end - start)) * 100),
    );
  const ticks: { at: number; label: string }[] = [];
  const tickHours = hours <= 24 ? 6 : 24;
  const first = new Date(start);
  first.setMinutes(0, 0, 0);
  if (tickHours === 24) first.setHours(0);
  else first.setHours(Math.ceil(first.getHours() / 6) * 6);
  for (let time = first.getTime(); time <= end; time += tickHours * 3_600_000) {
    if (time < start) continue;
    const date = new Date(time);
    ticks.push({
      at: position(date),
      label:
        tickHours === 24
          ? new Intl.DateTimeFormat(undefined, { weekday: 'short' }).format(
              date,
            )
          : new Intl.DateTimeFormat(undefined, { hour: 'numeric' }).format(
              date,
            ),
    });
  }
  return (
    <div className="swimlane">
      {lanes.map((lane) => {
        const inRange = lane.events.filter(
          (event) => event.at.getTime() >= start && event.at.getTime() <= end,
        );
        const failed = inRange.filter((event) => event.failed).length;
        return (
          <div className="swimlane-row" key={lane.key}>
            <span className="swimlane-label">{lane.label}</span>
            <div
              className="swimlane-track"
              role="img"
              aria-label={`${lane.label}: ${inRange.length} in the last ${hours <= 24 ? `${hours} hours` : `${Math.round(hours / 24)} days`}${failed ? `, ${failed} failed` : ''}`}
            >
              {ticks.map((tick) => (
                <span
                  key={tick.at}
                  className="swimlane-grid"
                  style={{ left: `${tick.at}%` }}
                  aria-hidden
                />
              ))}
              {inRange.map((event, index) => (
                <span
                  key={`${event.at.getTime()}-${index}`}
                  className="swimlane-mark"
                  data-failed={event.failed ? 'true' : undefined}
                  style={{ left: `${position(event.at)}%` }}
                  title={`${event.label} · ${absoluteTime(event.at)}`}
                  aria-hidden
                />
              ))}
            </div>
            <span className="swimlane-count">
              {inRange.length}
              {failed ? (
                <span className="swimlane-failed"> · {failed} failed</span>
              ) : null}
              {lane.note && inRange.length === 0 ? (
                <span className="swimlane-note"> {lane.note}</span>
              ) : null}
            </span>
          </div>
        );
      })}
      <div className="swimlane-axis" aria-hidden>
        <span className="swimlane-label" />
        <div className="swimlane-axis-track">
          {ticks.map((tick) => (
            <span key={tick.at} style={{ left: `${tick.at}%` }}>
              {tick.label}
            </span>
          ))}
        </div>
        <span className="swimlane-count" />
      </div>
    </div>
  );
}
