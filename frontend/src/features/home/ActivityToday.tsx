import { useEffect, useEffectEvent, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronRight, History } from 'lucide-react';
import type { ActivitySummary } from '../../api/types';
import { useLiveUpdate } from '../../live';
import { IconButton } from '../../ui/primitives';
import { plural } from './home-format';

/** Today's actions in words: "Today: 42 actions · 3 asked you · 1 you said no to". */
export function todayWords(summary: ActivitySummary) {
  if (!summary.total) return 'Today: no actions yet';
  return [
    `Today: ${plural(summary.total, 'action')}`,
    summary.asked ? `${summary.asked.toLocaleString()} asked you` : '',
    summary.denied ? `${summary.denied.toLocaleString()} you said no to` : '',
  ]
    .filter(Boolean)
    .join(' · ');
}

/**
 * Monitor's one line about the activity log (D1), with the way to the whole
 * list in Settings › System › Activity. Quiet when it can't be read.
 */
export default function ActivityToday({
  load,
  now,
}: {
  load: (since: number, signal?: AbortSignal) => Promise<ActivitySummary>;
  now?: Date;
}) {
  const navigate = useNavigate();
  const [summary, setSummary] = useState<ActivitySummary | null>(null);
  const [reload, setReload] = useState(0);
  useLiveUpdate(['approvals', 'runs'], () => setReload((value) => value + 1));
  const day = (now ?? new Date()).toDateString();
  // Monitor re-renders every minute: the line reads again on a new day or a change, not on each render.
  const read = useEffectEvent(load);
  useEffect(() => {
    const request = new AbortController();
    const midnight = new Date(day);
    read(Math.floor(midnight.getTime() / 1000), request.signal).then(
      setSummary,
      () => {
        if (!request.signal.aborted) setSummary(null);
      },
    );
    return () => request.abort();
  }, [day, reload]);
  if (!summary) return null;
  return (
    <div className="monitor-activity-line">
      <History size={15} aria-hidden />
      <span role="status">{todayWords(summary)}</span>
      <IconButton
        size="sm"
        label="See all activity"
        onClick={() => navigate('/settings/activity')}
      >
        <ChevronRight size={15} aria-hidden />
      </IconButton>
    </div>
  );
}
