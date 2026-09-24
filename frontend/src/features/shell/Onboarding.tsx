import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Check, ExternalLink, SkipForward } from 'lucide-react';
import type {
  OnboardingCommand,
  OnboardingReceipt,
  OnboardingSnapshot,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import {
  Button,
  CompactAction,
  EmptyState,
  ErrorState,
  Skeleton,
  Toggle,
} from '../../ui/primitives';

type Owner = {
  load: (signal?: AbortSignal) => Promise<OnboardingSnapshot>;
  send: (command: OnboardingCommand) => Promise<OnboardingReceipt>;
};

const destinations: Record<string, string> = {
  models: '/settings/models',
  knowledge: '/settings/knowledge',
  workflows: '/?tab=workflows',
  designer: '/',
  developer: '/',
  channels: '/settings/channels',
  accounts: '/settings/accounts',
  tools: '/settings/tools',
  extensions: '/settings/mcp',
  voice: '/settings/voice',
  final: '/settings/system',
};
const pendingKey = 'row-bot:onboarding:pending:v1';

function savedPending(): OnboardingCommand | null {
  try {
    const raw = sessionStorage.getItem(pendingKey);
    if (!raw || raw.length > 4096) return null;
    const value = JSON.parse(raw) as OnboardingCommand;
    return typeof value.command_id === 'string' &&
      typeof value.action === 'string'
      ? value
      : null;
  } catch {
    return null;
  }
}

export function OnboardingCenter({ owner }: { owner: Owner }) {
  const [snapshot, setSnapshot] = useState<OnboardingSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [pending, setPending] = useState<OnboardingCommand | null>(
    savedPending,
  );
  const running = useRef(false);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      try {
        setSnapshot(await owner.load(signal));
        setError('');
      } catch (cause) {
        if (!signal?.aborted) setError(clientError(cause).message);
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [owner],
  );
  useEffect(() => {
    const abort = new AbortController();
    void load(abort.signal);
    return () => abort.abort();
  }, [load]);

  async function send(
    action: OnboardingCommand['action'],
    profile: string[] = [],
    step = '',
  ) {
    if (!snapshot || running.current || pending) return;
    const command: OnboardingCommand = {
      command_id: crypto.randomUUID(),
      expected_revision: snapshot.revision,
      action,
      profile,
      step,
    };
    await execute(command);
  }

  async function execute(command: OnboardingCommand) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    sessionStorage.setItem(pendingKey, JSON.stringify(command));
    setPending(command);
    try {
      const receipt = await owner.send(command);
      if (
        receipt.command_id !== command.command_id ||
        receipt.status !== 'completed'
      )
        throw new Error('Setup receipt did not match the requested action');
      sessionStorage.removeItem(pendingKey);
      setPending(null);
      setSnapshot(receipt.snapshot);
      setNotice('Setup choice saved.');
    } catch (cause) {
      const error = clientError(cause);
      const rejected = [
        'onboarding_model_required',
        'onboarding_changed',
        'invalid_onboarding_command',
        'onboarding_command_conflict',
      ].includes(error.code);
      if (rejected) {
        sessionStorage.removeItem(pendingKey);
        setPending(null);
      }
      setError(
        rejected
          ? error.message
          : `${error.message} Check the original setup action before sending another.`,
      );
    } finally {
      running.current = false;
      setBusy(false);
    }
  }

  return (
    <section className="stack capability-page" aria-label="Setup Center">
      <header className="capability-header">
        <div>
          <h1>Setup Center</h1>
          <p>
            Connect one model, then finish or skip the areas you want. Your
            progress is saved.
          </p>
        </div>
        <Link className="button ghost" to="/">
          Return home
        </Link>
      </header>
      {loading && <Skeleton label="Loading setup progress" />}
      {error && (
        <ErrorState
          title="Setup needs attention"
          action={<Button onClick={() => void load()}>Refresh setup</Button>}
        >
          {error}
        </ErrorState>
      )}
      {pending && (
        <div className="surface stack" role="status">
          <p>
            An earlier setup action may have completed. Check its original
            result before making another change.
          </p>
          <Button disabled={busy} onClick={() => void execute(pending)}>
            Check setup action
          </Button>
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      {!loading && !snapshot && !error && (
        <EmptyState title="Setup unavailable">Refresh to try again.</EmptyState>
      )}
      {snapshot && (
        <>
          {!snapshot.setup_complete && (
            <section
              className="capability-section stack"
              aria-label="First model setup"
            >
              <h2>Connect your first model</h2>
              <p>
                Choose a local, cloud, or self-hosted model in Settings. Return
                here when a working model is selected.
              </p>
              <div className="actions">
                <Link className="button secondary" to="/settings/models">
                  Local models <ExternalLink size={16} aria-hidden />
                </Link>
                <Link className="button secondary" to="/settings/providers">
                  Cloud or self-hosted providers{' '}
                  <ExternalLink size={16} aria-hidden />
                </Link>
              </div>
              <Button
                disabled={busy || !!pending}
                onClick={() => void send('finish_models')}
              >
                Use selected model and continue
              </Button>
              <p className="muted">
                Optional migration and private knowledge model setup remain
                available after this step.
              </p>
            </section>
          )}
          <section className="capability-section stack" aria-label="Your goals">
            <h2>What would you like to use?</h2>
            <div className="settings-choice-grid">
              {snapshot.intents.map((intent) => (
                <label key={intent.id} className="check-field">
                  <Toggle
                    label={intent.label}
                    checked={snapshot.profile.includes(intent.id)}
                    disabled={busy || !!pending}
                    onChange={(event) =>
                      void send(
                        'save_profile',
                        event.target.checked
                          ? [...snapshot.profile, intent.id]
                          : snapshot.profile.filter(
                              (value) => value !== intent.id,
                            ),
                      )
                    }
                  />
                  {intent.label}
                </label>
              ))}
            </div>
          </section>
          {snapshot.setup_complete && (
            <section
              className="capability-section stack"
              aria-label="Setup checklist"
            >
              <h2>Continue setup</h2>
              <p>
                {
                  new Set([
                    ...snapshot.completed_steps,
                    ...snapshot.skipped_steps,
                  ]).size
                }{' '}
                of {snapshot.steps.length} areas handled.
              </p>
              <div className="setup-grid">
                {snapshot.steps.map((step) => {
                  const done = snapshot.completed_steps.includes(step.id);
                  const skipped = snapshot.skipped_steps.includes(step.id);
                  return (
                    <article className="surface stack" key={step.id}>
                      <h3>{step.title}</h3>
                      <p>{step.description}</p>
                      <p>{done ? 'Done' : skipped ? 'Skipped' : 'Open'}</p>
                      <div className="actions">
                        <Link
                          className="button ghost"
                          to={destinations[step.id] ?? '/'}
                        >
                          Open {step.title}
                        </Link>
                        <CompactAction
                          label={`Mark ${step.title} done`}
                          disabled={busy || !!pending}
                          onClick={() => void send('mark_done', [], step.id)}
                        >
                          <Check size={17} aria-hidden />
                        </CompactAction>
                        <CompactAction
                          label={`Skip ${step.title}`}
                          disabled={busy || !!pending}
                          onClick={() => void send('skip_step', [], step.id)}
                        >
                          <SkipForward size={17} aria-hidden />
                        </CompactAction>
                        {step.id === 'workflows' &&
                          snapshot.starter_workflows_missing > 0 && (
                            <Button
                              disabled={busy || !!pending}
                              onClick={() => void send('add_starters')}
                            >
                              Add missing starter workflows
                            </Button>
                          )}
                      </div>
                    </article>
                  );
                })}
              </div>
              <div className="actions">
                <Link className="button ghost" to="/settings/system">
                  Import from Hermes or OpenClaw
                </Link>
                <Link className="button ghost" to="/settings/documents">
                  Set up private knowledge embeddings
                </Link>
              </div>
            </section>
          )}
        </>
      )}
    </section>
  );
}

export default function Onboarding() {
  const { controller } = useRuntime();
  const owner = useRef<Owner>({
    load: (signal) => controller.onboarding(signal),
    send: (command) => controller.onboardingCommand(command),
  });
  return <OnboardingCenter owner={owner.current} />;
}
