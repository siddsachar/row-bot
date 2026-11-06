import { useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { Download, LogIn, RefreshCw } from 'lucide-react';
import { clientError } from '../../api/errors';
import type { ClaudeCodeInstallState, ClaudePlanStatus } from '../../api/types';
import { RuntimeContext } from '../../runtime';
import {
  Button,
  Progress,
  Skeleton,
  StatusDot,
  type Tone,
} from '../../ui/primitives';
import ClaudeSignInPanel, { type SignInEnd } from './ClaudeSignInPanel';

/** The provider's name, and the one line that says what it does (U11). */
export const CLAUDE_PLAN_TITLE = 'Claude (your Claude plan)';
export const CLAUDE_PLAN_SUMMARY =
  'Row-Bot runs Claude Code, Anthropic’s program, with your own Claude plan. Its limits apply.';
export const USE_MY_CLAUDE_PLAN = 'Use my Claude plan';
export const SIGNED_OUT_BY_CLAUDE =
  'Claude signed you out. Sign in again to keep using your plan.';
export const SIGN_IN_CLOSED_EARLY =
  'Claude’s sign-in was closed before it finished.';

type State = ClaudePlanStatus['state'];

/** Each state in words: the status pill, and the row's short detail. */
export const CLAUDE_PLAN_STATES: Record<
  State,
  { label: string; tone: Tone; detail: string }
> = {
  not_installed: {
    label: 'Not set up',
    tone: 'neutral',
    detail: 'Use your Claude plan in Row-Bot',
  },
  signed_out: {
    label: 'Not signed in',
    tone: 'warning',
    detail: 'Sign in to Claude to use your plan',
  },
  too_old: {
    label: 'Update needed',
    tone: 'warning',
    detail: 'Claude Code needs an update',
  },
  ready: {
    label: 'Ready',
    tone: 'success',
    detail: 'Runs Claude Code with your Claude plan',
  },
  error: {
    label: 'Couldn’t check',
    tone: 'warning',
    detail: 'Row-Bot couldn’t check Claude Code',
  },
};

/** Why Claude Code couldn't be installed, in a few plain words. */
export const INSTALL_FAILURES: Record<
  Exclude<ClaudeCodeInstallState['reason'], ''>,
  string
> = {
  no_internet: 'no internet connection',
  download_failed: 'the download from Anthropic didn’t work',
  checksum_mismatch:
    'the download didn’t match Anthropic’s checksum, so it was deleted',
  disk_full: 'there isn’t enough disk space',
  unsupported_platform: 'it doesn’t run on this system',
  install_failed: 'its installer stopped with an error',
};

/** The status pill and detail for a Claude plan row. */
export function claudePlanSummary(plan: ClaudePlanStatus) {
  if (plan.set_up_again)
    return {
      label: 'Set up again',
      tone: 'warning' as Tone,
      detail: 'Claude now works through Claude Code. Set it up again',
    };
  if (plan.state === 'signed_out' && plan.signed_in_before)
    return {
      ...CLAUDE_PLAN_STATES.signed_out,
      detail: 'Claude signed you out',
    };
  return CLAUDE_PLAN_STATES[plan.state];
}

type Step =
  | { kind: 'idle' }
  | {
      kind: 'installing';
      update: boolean;
      progress: ClaudeCodeInstallState | null;
    }
  | { kind: 'install_failed'; update: boolean; reason: string }
  | { kind: 'signing_in'; installed: string }
  | { kind: 'closed_early'; detail: string }
  | { kind: 'connected' };

const POLL_MS = 500;

/** How far the install is (the download is most of it), and what it is doing, in words. */
function installing(progress: ClaudeCodeInstallState | null): {
  value: number | undefined;
  words: string;
} {
  if (progress?.step === 'check')
    return { value: 92, words: 'Checking Anthropic’s checksum…' };
  if (progress?.step === 'install')
    return { value: 96, words: 'Setting up Claude Code…' };
  if (!progress?.total) return { value: undefined, words: 'Downloading…' };
  const share = Math.min(1, progress.received / progress.total);
  return {
    value: Math.round(share * 90),
    words: `${Math.round(share * 100)}% downloaded`,
  };
}

/**
 * Claude (your Claude plan), set up in one place (U11, U12): "Use my Claude
 * plan" installs Claude Code from Anthropic when it's missing, then opens
 * Claude's own sign-in. Row-Bot offers no Claude login of its own and never
 * sees the sign-in.
 */
export default function ClaudePlanSetup({
  plan,
  checking,
  onCheck,
  onDone,
  autoStart = false,
}: {
  plan: ClaudePlanStatus | null | undefined;
  checking: boolean;
  onCheck: () => void;
  /** Ready: close the dialog (Settings) or carry on (first run). */
  onDone?: () => void;
  /**
   * The person already chose "Use my Claude plan" (a hosted first run's big
   * button, U3): install or sign in at once instead of asking again.
   */
  autoStart?: boolean;
}) {
  const controller = useContext(RuntimeContext)?.controller;
  const [step, setStep] = useState<Step>({ kind: 'idle' });
  const polling = useRef<AbortController | null>(null);
  useEffect(() => () => polling.current?.abort(), []);
  const started = useRef(false);
  useEffect(() => {
    if (!autoStart || started.current || !plan) return;
    started.current = true;
    // An install already running (a reload) is followed by the effect below.
    if (plan.state === 'not_installed')
      void (controller?.claudeCodeInstall?.() ?? Promise.resolve(null))
        .catch(() => null)
        .then((progress) => {
          if (progress?.state !== 'running') void install(false);
        });
    else if (plan.state === 'signed_out')
      setStep({ kind: 'signing_in', installed: '' });
    // Once, when the plan is first read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStart, plan]);

  async function follow(update: boolean, signal: AbortSignal) {
    if (!controller) return;
    let progress = await controller.claudeCodeInstall(signal);
    while (progress.state === 'running' && !signal.aborted) {
      setStep({ kind: 'installing', update, progress });
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      progress = await controller.claudeCodeInstall(signal);
    }
    if (signal.aborted) return;
    if (progress.state === 'failed') {
      setStep({
        kind: 'install_failed',
        update,
        reason: progress.reason || 'install_failed',
      });
      return;
    }
    onCheck();
    // A fresh install goes straight on to Claude's sign-in; an update only checks again.
    setStep(
      update
        ? { kind: 'idle' }
        : { kind: 'signing_in', installed: progress.version },
    );
  }

  // An install already running (the dialog was closed and opened again) shows its progress.
  useEffect(() => {
    if (plan?.state !== 'not_installed' || !controller?.claudeCodeInstall)
      return;
    const abort = new AbortController();
    void controller
      .claudeCodeInstall(abort.signal)
      .then((progress) => {
        if (progress.state !== 'running' || abort.signal.aborted) return;
        polling.current?.abort();
        polling.current = abort;
        return follow(false, abort.signal);
      })
      .catch(() => undefined);
    return () => abort.abort();
    // Only when the dialog opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function install(update: boolean) {
    if (!controller) return;
    polling.current?.abort();
    const abort = new AbortController();
    polling.current = abort;
    setStep({ kind: 'installing', update, progress: null });
    try {
      await controller.installClaudeCode(update);
      await follow(update, abort.signal);
    } catch (cause) {
      if (abort.signal.aborted) return;
      const error = clientError(cause);
      if (error.code === 'claude_code_present') {
        onCheck();
        setStep({ kind: 'signing_in', installed: '' });
        return;
      }
      setStep({ kind: 'install_failed', update, reason: error.message });
    }
  }

  function signedIn(end: SignInEnd) {
    if (end.plan?.state === 'ready') {
      setStep({ kind: 'connected' });
      onCheck();
      return;
    }
    setStep({
      kind: 'closed_early',
      detail:
        end.plan?.state === 'signed_out' && end.plan.detail
          ? end.plan.detail
          : end.error,
    });
  }

  if (!plan) return <Skeleton label="Checking your Claude plan" />;
  const ready = step.kind === 'connected' || plan.state === 'ready';
  const update = (variant: 'primary' | 'secondary' | 'ghost') => (
    <Button variant={variant} onClick={() => void install(true)}>
      <Download size={16} aria-hidden /> Update Claude Code
    </Button>
  );
  const signIn = (label: string) => (
    <Button
      variant="primary"
      onClick={() => setStep({ kind: 'signing_in', installed: '' })}
    >
      <LogIn size={16} aria-hidden /> {label}
    </Button>
  );

  let body: ReactNode;
  if (step.kind === 'installing') {
    const { value, words } = installing(step.progress);
    body = (
      <div className="claude-plan-progress" role="status" aria-live="polite">
        <Progress
          label={
            step.update
              ? 'Updating Claude Code from Anthropic…'
              : 'Installing Claude Code from Anthropic…'
          }
          value={value}
        />
        <small>{words}</small>
      </div>
    );
  } else if (step.kind === 'install_failed') {
    const reason =
      INSTALL_FAILURES[step.reason as keyof typeof INSTALL_FAILURES];
    body = (
      <>
        <p role="alert">
          {reason
            ? `Claude Code couldn’t be installed: ${reason}.`
            : `Claude Code couldn’t be installed. ${step.reason}`}
        </p>
        <div className="actions">
          <Button variant="primary" onClick={() => void install(step.update)}>
            <RefreshCw size={16} aria-hidden /> Try again
          </Button>
        </div>
      </>
    );
  } else if (step.kind === 'signing_in') {
    body = (
      <>
        {step.installed && (
          <p className="claude-plan-state" role="status">
            <StatusDot
              tone="success"
              label={`Installed (version ${step.installed})`}
              showLabel
            />
          </p>
        )}
        <ClaudeSignInPanel onEnd={signedIn} />
      </>
    );
  } else if (step.kind === 'closed_early' && !ready) {
    body = (
      <>
        <p role="alert">
          {SIGN_IN_CLOSED_EARLY}
          {step.detail ? ` ${step.detail}` : ''}
        </p>
        <div className="actions">{signIn('Sign in again')}</div>
      </>
    );
  } else if (ready) {
    body = (
      <>
        <p className="claude-plan-state" role="status">
          <StatusDot tone="success" label="Claude plan connected" showLabel />
          {(plan.plan || plan.version) && (
            <span className="claude-plan-version">
              {[plan.plan, plan.version ? `Claude Code ${plan.version}` : '']
                .filter(Boolean)
                .join(' · ')}
            </span>
          )}
        </p>
        <p>Claude models are in the model picker.</p>
        <div className="actions">
          {onDone && (
            <Button variant="primary" onClick={onDone}>
              Done
            </Button>
          )}
          {update('ghost')}
        </div>
      </>
    );
  } else if (plan.state === 'not_installed') {
    body = (
      <div className="actions">
        <Button variant="primary" onClick={() => void install(false)}>
          <LogIn size={16} aria-hidden /> {USE_MY_CLAUDE_PLAN}
        </Button>
      </div>
    );
  } else if (plan.state === 'signed_out') {
    body = (
      <>
        {plan.signed_in_before && !plan.detail && (
          <p role="status">{SIGNED_OUT_BY_CLAUDE}</p>
        )}
        {plan.detail && <p role="status">{plan.detail}</p>}
        <div className="actions">
          {signIn(
            plan.signed_in_before || plan.detail
              ? 'Sign in again'
              : USE_MY_CLAUDE_PLAN,
          )}
        </div>
      </>
    );
  } else if (plan.state === 'too_old') {
    body = (
      <>
        <p role="status">
          Row-Bot needs Claude Code {plan.minimum_version} or newer.
        </p>
        <div className="actions">{update('primary')}</div>
      </>
    );
  } else {
    body = (
      <>
        <p role="status">
          {plan.detail || 'Claude Code didn’t answer.'} Check again, or update
          Claude Code.
        </p>
        <div className="actions">
          <Button variant="primary" disabled={checking} onClick={onCheck}>
            <RefreshCw
              size={16}
              aria-hidden
              data-spinning={checking || undefined}
            />{' '}
            {checking ? 'Checking…' : 'Check again'}
          </Button>
          {update('secondary')}
        </div>
      </>
    );
  }

  return (
    <div className="stack claude-plan-setup" aria-busy={checking}>
      {plan.set_up_again && !ready && (
        <p className="settings-help" role="status">
          Claude now works through Claude Code. Set it up again.
        </p>
      )}
      {body}
      <p className="settings-help claude-plan-note">
        Row-Bot never sees your Claude sign-in. It starts Claude Code with its
        non-essential traffic off: no telemetry, error reports or automatic
        updates from these runs.
      </p>
    </div>
  );
}
