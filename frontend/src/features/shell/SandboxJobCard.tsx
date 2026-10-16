import { useEffect, useRef, useState } from 'react';
import { Download, PackageCheck, PackageOpen, Square } from 'lucide-react';
import type { SandboxJobView } from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Button, Disclosure, Skeleton, StatusDot } from '../../ui/primitives';
import { useNow } from './background-processes';
import { clockWords } from './recordings';
import {
  clockTime,
  jobLive,
  jobStatus,
  jobTitle,
  networkWords,
  problemWords,
  useSandboxJob,
} from './sandbox-jobs';

/**
 * Prepare sandbox, or the sandbox image's download (F18), as a card: what it
 * does with its progress and Stop while it runs, the sandbox network's state
 * (on only for the install, in the warning tone; then off again), and what
 * happened. What it ran and when the network was on are one tap away.
 */
export function SandboxJobCard({
  jobId,
  initial,
  onSettled,
}: {
  jobId: string;
  /** The view the start returned, shown until the first read. */
  initial?: SandboxJobView | null;
  /** It has just finished (ready, failed or stopped). */
  onSettled?: (view: SandboxJobView) => void;
}) {
  const { controller } = useRuntime();
  const job = useSandboxJob(jobId);
  const view = job.view ?? initial ?? null;
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState('');
  const live = jobLive(view);
  const now = useNow(live);
  const wasLive = useRef(live);
  const settled = useRef(onSettled);
  settled.current = onSettled;
  useEffect(() => {
    if (wasLive.current && !live && view) settled.current?.(view);
    wasLive.current = live;
  }, [live, view]);

  async function stop() {
    if (!view || stopping) return;
    setStopping(true);
    setError('');
    try {
      job.merge(await controller.stopSandboxJob(view.job_id));
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setStopping(false);
    }
  }

  if (!view)
    return (
      <div
        className="transcript-card sandbox-job-card"
        data-kind="sandbox_job"
        data-state={job.error ? 'unavailable' : 'loading'}
        role="group"
        aria-label="Prepare sandbox"
      >
        <PackageOpen className="transcript-card-icon" aria-hidden />
        <div className="transcript-card-text">
          {job.error ? (
            <span>Couldn&apos;t check the sandbox.</span>
          ) : (
            <Skeleton label="Checking the sandbox" />
          )}
        </div>
        {job.error && (
          <div className="transcript-card-actions">
            <Button variant="ghost" onClick={job.refresh}>
              Try again
            </Button>
          </div>
        )}
      </div>
    );

  const title = jobTitle(view);
  const status = jobStatus(view);
  const network = networkWords(view);
  const why = problemWords(view);
  const started = Date.parse(view.started_at);
  const seconds =
    live && Number.isFinite(started)
      ? Math.max(view.seconds, (now - started) / 1000)
      : view.seconds;
  const Icon =
    view.kind === 'pull' || view.status === 'pulling'
      ? Download
      : view.status === 'ready'
        ? PackageCheck
        : PackageOpen;
  return (
    <div
      className="transcript-card recording-card sandbox-job-card"
      data-kind="sandbox_job"
      data-state={view.status}
      data-network={view.network}
      role="group"
      aria-label={`${title}: ${status.label}`}
    >
      <Icon className="transcript-card-icon" aria-hidden />
      <div className="transcript-card-text recording-card-text">
        <strong>{title}</strong>
        <span className="recording-card-meta">
          <StatusDot
            tone={status.tone}
            pulse={status.pulse}
            label={status.label}
            showLabel
          />
          {live && <span>{clockWords(seconds)}</span>}
          <span
            className="sandbox-job-network"
            data-warning={network.warning || undefined}
          >
            {network.text}
          </span>
        </span>
        {view.kind === 'prepare' && view.status !== 'failed' && (
          <span className="sandbox-job-summary">
            {view.status === 'ready'
              ? `Installed ${view.summary}. The tests can run now.`
              : `Installing ${view.summary}`}
          </span>
        )}
        {live && view.progress && (
          <code className="sandbox-job-progress" title={view.progress}>
            {view.progress}
          </code>
        )}
        {why && <span className="video-edit-why">{why}</span>}
      </div>
      {live && (
        <div className="transcript-card-actions">
          <Button
            disabled={stopping || view.status === 'finishing'}
            onClick={() => void stop()}
          >
            <Square size={12} aria-hidden />
            Stop
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="transcript-card-error">
          {error}
        </p>
      )}
      {!live && (
        <Disclosure summary="Details" className="video-edit-details">
          <dl>
            {view.sources.length > 0 && (
              <>
                <dt>Read from</dt>
                <dd>{view.sources.join(', ')}</dd>
              </>
            )}
            <dt>{view.kind === 'pull' ? 'Image' : 'Ran'}</dt>
            <dd>
              {view.kind === 'pull' ? (
                view.image
              ) : (
                <code className="sandbox-job-commands">
                  {view.commands.join('\n')}
                </code>
              )}
            </dd>
            {view.network_on_at && (
              <>
                <dt>Network</dt>
                <dd>
                  On at {clockTime(view.network_on_at)}
                  {view.network_off_at
                    ? `, off again at ${clockTime(view.network_off_at)}`
                    : ''}{' '}
                  (for the install only)
                </dd>
              </>
            )}
            <dt>Started by</dt>
            <dd>{view.by === 'agent' ? 'Row-Bot' : 'You'}</dd>
          </dl>
        </Disclosure>
      )}
    </div>
  );
}
