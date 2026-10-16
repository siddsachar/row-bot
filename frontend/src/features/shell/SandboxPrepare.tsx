import { useCallback, useEffect, useRef, useState } from 'react';
import { Download, FlaskConical, PackageOpen } from 'lucide-react';
import type {
  SandboxJobView,
  SandboxPlanView,
  SandboxStatusView,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Button, Disclosure, Skeleton } from '../../ui/primitives';
import { SandboxJobCard } from './SandboxJobCard';
import { jobLive } from './sandbox-jobs';

/**
 * Prepare sandbox (F18), asked first: the sandbox network is on only while
 * the project's test tools install, then off again. What it runs is one tap
 * away. The person's confirmation here is their approval; Block refuses it.
 */
export function PrepareConfirm({
  plan,
  busy,
  onCancel,
  onConfirm,
}: {
  plan: SandboxPlanView;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div
      className="sandbox-confirm"
      role="group"
      aria-label="Confirm Prepare sandbox"
    >
      <p>
        <strong>Prepare the sandbox?</strong> Row-Bot turns the sandbox network
        on for the install only, installs {plan.summary} (from{' '}
        {plan.sources.join(', ')}), then turns the network off again.
      </p>
      <Disclosure summary="What it runs" className="sandbox-confirm-commands">
        <code>{plan.commands.join('\n')}</code>
      </Disclosure>
      <div className="action-cluster">
        <Button disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="primary" disabled={busy} onClick={onConfirm}>
          Prepare sandbox
        </Button>
      </div>
    </div>
  );
}

/** The sandbox's state for a conversation's code folder, read on demand. */
function useSandboxStatus(conversationId: string | null | undefined) {
  const { controller } = useRuntime();
  const [status, setStatus] = useState<SandboxStatusView | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!conversationId) return;
    const abort = new AbortController();
    controller
      .conversationSandbox(conversationId, abort.signal)
      .then((value) => {
        if (abort.signal.aborted) return;
        setStatus(value);
        setError('');
      })
      .catch((cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      });
    return () => abort.abort();
  }, [controller, conversationId, attempt]);
  const refresh = useCallback(() => setAttempt((value) => value + 1), []);
  return { status, error, refresh };
}

/** Start Prepare or the image's download; the job it started, or the reason it couldn't. */
function useStart(conversationId: string | null | undefined) {
  const { controller } = useRuntime();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [started, setStarted] = useState<SandboxJobView | null>(null);
  const start = async (kind: 'prepare' | 'pull') => {
    if (!conversationId || busy) return;
    setBusy(true);
    setError('');
    try {
      setStarted(
        kind === 'pull'
          ? await controller.pullSandboxImage(conversationId)
          : await controller.prepareSandbox(conversationId),
      );
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, started, start, setError };
}

const REASONS = {
  pytest_missing: "pytest isn't installed in the sandbox",
  packages_missing: "the project's packages aren't installed in the sandbox",
} as const;

/**
 * Under a sandbox test run that couldn't start for want of its tools: what
 * happened and Prepare sandbox, asked first. Once started, the job follows.
 */
export function SandboxPrepareOffer({
  conversationId,
  reason,
}: {
  conversationId?: string | null;
  reason: string;
}) {
  const sandbox = useSandboxStatus(conversationId);
  const run = useStart(conversationId);
  const [asking, setAsking] = useState(false);
  const why =
    REASONS[reason as keyof typeof REASONS] ?? REASONS.packages_missing;
  // The person (or Row-Bot) already prepared it since: show that, not the offer.
  const earlier =
    sandbox.status?.job?.kind === 'prepare' &&
    (jobLive(sandbox.status.job) || sandbox.status.job.status === 'ready')
      ? sandbox.status.job
      : null;
  const job = run.started ?? earlier;
  if (job) return <SandboxJobCard jobId={job.job_id} initial={job} />;
  const plan = sandbox.status?.prepare ?? null;
  return (
    <div
      className="transcript-card sandbox-offer"
      data-kind="sandbox_unprepared"
      role="group"
      aria-label="Tests couldn't run"
    >
      <FlaskConical className="transcript-card-icon" aria-hidden />
      <div className="transcript-card-text recording-card-text">
        <strong>Tests couldn&apos;t run: {why}</strong>
        <span className="sandbox-offer-advice">
          Prepare sandbox installs the project&apos;s test tools. The
          sandbox&apos;s network is off; it&apos;s on only while they install,
          then off again.
        </span>
      </div>
      {!asking && (
        <div className="transcript-card-actions">
          <Button
            variant="primary"
            disabled={!conversationId || (!plan && !sandbox.error)}
            onClick={() => setAsking(true)}
          >
            Prepare sandbox
          </Button>
        </div>
      )}
      {asking &&
        (plan ? (
          <PrepareConfirm
            plan={plan}
            busy={run.busy}
            onCancel={() => setAsking(false)}
            onConfirm={() => void run.start('prepare')}
          />
        ) : (
          <p className="transcript-card-error" role="alert">
            {sandbox.error ||
              'Row-Bot found nothing to install in this code folder.'}
          </p>
        ))}
      {run.error && (
        <p role="alert" className="transcript-card-error">
          {run.error}
        </p>
      )}
    </div>
  );
}

/**
 * The sandbox's own controls (Git › Sandbox): Docker's state, the image (with
 * the offer to download it when Docker mode is chosen and it isn't here yet),
 * Prepare sandbox, and the latest job.
 */
export function SandboxReadiness({
  conversationId,
  revisionKey,
}: {
  conversationId: string;
  /** Re-read when this changes (the sandbox settings were saved). */
  revisionKey?: string;
}) {
  const sandbox = useSandboxStatus(conversationId);
  const run = useStart(conversationId);
  const [asking, setAsking] = useState(false);
  const { refresh } = sandbox;
  const seenKey = useRef(revisionKey);
  useEffect(() => {
    if (seenKey.current === revisionKey) return;
    seenKey.current = revisionKey;
    refresh();
  }, [refresh, revisionKey]);
  // A job that was over by the time its start answered (an image already
  // downloading elsewhere, say) changes what the sandbox shows at once.
  const started = run.started;
  useEffect(() => {
    if (started && !jobLive(started)) refresh();
  }, [refresh, started]);
  const status = sandbox.status;
  if (!status)
    return sandbox.error ? (
      <p className="sandbox-ready-line" role="alert">
        {sandbox.error}{' '}
        <Button variant="ghost" onClick={sandbox.refresh}>
          Check again
        </Button>
      </p>
    ) : (
      <Skeleton label="Checking the sandbox" />
    );
  if (status.execution_mode !== 'docker') return null;
  // The job just started, until the sandbox's own record of it is fresher.
  const job =
    run.started && status.job?.job_id !== run.started.job_id
      ? run.started
      : status.job;
  const busy = run.busy || jobLive(job);
  return (
    <div className="sandbox-ready" role="group" aria-label="Sandbox readiness">
      {status.runtime === 'unavailable' ? (
        <p className="sandbox-ready-line">
          {status.runtime_detail}{' '}
          <Button variant="ghost" onClick={sandbox.refresh}>
            Check again
          </Button>
        </p>
      ) : status.image_present === false && !jobLive(job) ? (
        <div
          className="sandbox-image-offer"
          role="group"
          aria-label="Sandbox image"
        >
          <Download className="sandbox-ready-icon" aria-hidden />
          <div>
            <strong>Download the sandbox image?</strong>
            <span className="sandbox-image-name">{status.image}</span>
            <p>
              It isn&apos;t on this computer yet. Docker downloads it once
              {status.image_download
                ? ` (${status.image_download} from Docker Hub)`
                : ''}
              ; every sandbox command then uses it. The sandbox network stays
              off.
            </p>
          </div>
          <Button
            variant="primary"
            disabled={busy}
            onClick={() => void run.start('pull')}
          >
            Download image
          </Button>
        </div>
      ) : null}
      {status.runtime !== 'unavailable' && (
        <div className="sandbox-prepare-row">
          <PackageOpen className="sandbox-ready-icon" aria-hidden />
          <p>
            {status.prepare ? (
              <>
                <strong>Prepare for tests.</strong> Installs{' '}
                {status.prepare.summary} with the network on only for the
                install, then off again.
              </>
            ) : (
              'Nothing to prepare: no pyproject.toml, requirements file or package.json here.'
            )}
          </p>
          {status.prepare && !asking && (
            <Button disabled={busy} onClick={() => setAsking(true)}>
              Prepare sandbox
            </Button>
          )}
        </div>
      )}
      {asking && status.prepare && (
        <PrepareConfirm
          plan={status.prepare}
          busy={run.busy}
          onCancel={() => setAsking(false)}
          onConfirm={() => {
            setAsking(false);
            void run.start('prepare');
          }}
        />
      )}
      {run.error && (
        <p role="alert" className="sandbox-ready-line">
          {run.error}
        </p>
      )}
      {job && (
        <SandboxJobCard
          key={job.job_id}
          jobId={job.job_id}
          initial={job}
          onSettled={sandbox.refresh}
        />
      )}
    </div>
  );
}
