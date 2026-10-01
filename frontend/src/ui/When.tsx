import { absoluteTime, parseTimestamp, relativeTime } from './format';

/** A relative time ("3 hours ago", "in 2 days") with the full date on hover. */
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
  if (!date) return <>{fallback}</>;
  return (
    <time dateTime={date.toISOString()} title={absoluteTime(date)}>
      {relativeTime(date, now)}
    </time>
  );
}
