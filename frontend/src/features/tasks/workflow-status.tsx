import {
  Bell,
  Braces,
  CalendarClock,
  Database,
  FileDown,
  FileUp,
  Mail,
  RefreshCw,
  Zap,
} from 'lucide-react';
import type { TaskSummary } from '../../api/types';
import { parseTimestamp } from '../../ui/format';
import { FAILED_RUN_STATUSES, When, scheduleWords } from '../home/home-format';

const workflowIcons = {
  notifications: Bell,
  notifications_active: Bell,
  alarm: Bell,
  schedule: CalendarClock,
  event: CalendarClock,
  code: Braces,
  terminal: Braces,
  email: Mail,
  mail: Mail,
  database: Database,
  storage: Database,
  upload: FileUp,
  download: FileDown,
  sync: RefreshCw,
  refresh: RefreshCw,
} as const;

function workflowIcon(icon: string, reminder: boolean) {
  const key = icon.trim().toLowerCase() as keyof typeof workflowIcons;
  return workflowIcons[key] ?? (reminder ? Bell : Zap);
}

/** Emoji icons are shown as they are; named icons map to glyphs. */
export function WorkflowGlyph({
  task,
  size = 16,
}: {
  task: TaskSummary;
  size?: number;
}) {
  const icon = task.icon.trim();
  if (icon && !/^[a-z_]+$/i.test(icon))
    return <span className="workflow-emoji">{icon}</span>;
  const Icon = workflowIcon(icon, task.notify_only);
  return <Icon size={size} />;
}

/** A one-off ("Once") workflow that has run, with nothing left to run: done, not unscheduled (U17). */
export function ranOnce(task: TaskSummary) {
  return Boolean(
    task.at &&
    !task.schedule &&
    task.last_run &&
    !parseTimestamp(task.next_run),
  );
}

export function isRunning(task: TaskSummary) {
  return Boolean(
    task.active_run || task.last_status?.toLowerCase() === 'running',
  );
}

export function lastRunFailed(task: TaskSummary) {
  return FAILED_RUN_STATUSES.has(String(task.last_status ?? '').toLowerCase());
}

/** A workflow Row-Bot switched off after failed runs in a row (D5). */
export const OFF_AFTER_FAILURES = 'Off: failed 5 times in a row';

/** Its schedule as a phone row says it: a manual one runs when asked. */
export function phoneSchedule(task: TaskSummary) {
  const words = scheduleWords(task.schedule, task.at);
  return words === 'Manual' ? 'Run when asked' : words;
}

export type WorkflowState = 'running' | 'failed' | 'off' | 'done' | 'on';

/** Which of the phone's filters a workflow is under, first match wins. */
export function workflowState(task: TaskSummary): WorkflowState {
  if (isRunning(task)) return 'running';
  if (ranOnce(task)) return 'done';
  if (!task.enabled) return 'off';
  if (lastRunFailed(task)) return 'failed';
  return 'on';
}

/**
 * A phone row's one status line (C2): running and how far, failed and when,
 * off, or its schedule and next run.
 */
export function WorkflowLine({
  task,
  schedule: withSchedule = true,
}: {
  task: TaskSummary;
  /** False where the schedule is said already (the sheet's header). */
  schedule?: boolean;
}) {
  const schedule = phoneSchedule(task);
  const run = task.active_run;
  switch (workflowState(task)) {
    case 'running':
      return run && run.steps_total > 1 ? (
        <>
          Running · message {Math.min(run.steps_done + 1, run.steps_total)} of{' '}
          {run.steps_total}
        </>
      ) : (
        <>
          Running · started{' '}
          <When value={run?.started_at ?? task.last_run} fallback="just now" />
        </>
      );
    case 'done':
      return (
        <>
          Done · ran <When value={task.last_run} />
        </>
      );
    case 'off':
      return task.disabled_reason === 'failures' ? (
        <>{OFF_AFTER_FAILURES}</>
      ) : withSchedule ? (
        <>Off · {schedule}</>
      ) : (
        <>Off</>
      );
    case 'failed':
      return withSchedule ? (
        <>
          Failed <When value={task.last_run} /> · {schedule}
        </>
      ) : (
        <>
          Failed <When value={task.last_run} />
        </>
      );
    default:
      if (!withSchedule)
        return parseTimestamp(task.next_run) ? (
          <>
            Next run <When value={task.next_run} />
          </>
        ) : task.last_run ? (
          <>
            Ran <When value={task.last_run} />
          </>
        ) : (
          <>Hasn’t run yet</>
        );
      return parseTimestamp(task.next_run) ? (
        <>
          {schedule} · next <When value={task.next_run} />
        </>
      ) : (
        <>{schedule}</>
      );
  }
}
