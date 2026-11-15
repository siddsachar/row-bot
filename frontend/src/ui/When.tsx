import { useEffect, useState } from 'react';
import { absoluteTime, parseTimestamp, relativeTime } from './format';

/**
 * A relative time ("3 hours ago", "in 2 days") with the full date on hover.
 * Without a `now` from its parent it keeps itself current: every 15 seconds
 * within an hour ("in 2 minutes" reaches "in under a minute"), every five
 * minutes beyond.
 */
export function When({
  value,
  fallback = 'Unknown',
  now,
}: {
  value: string | null | undefined;
  fallback?: string;
  now?: Date;
}) {
  const date = parseTimestamp(value);
  const time = date?.getTime();
  const [, tick] = useState(0);
  useEffect(() => {
    if (now || time === undefined) return;
    const near = Math.abs(time - Date.now()) < 3_600_000;
    const timer = setInterval(
      () => tick((count) => count + 1),
      near ? 15_000 : 300_000,
    );
    return () => clearInterval(timer);
  }, [now, time]);
  if (!date) return <>{fallback}</>;
  return (
    <time dateTime={date.toISOString()} title={absoluteTime(date)}>
      {relativeTime(date, now)}
    </time>
  );
}
