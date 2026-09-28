import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  BadgeCheck,
  BookOpen,
  Brain,
  Check,
  Code2,
  Compass,
  Cpu,
  HardDrive,
  MessageSquare,
  MessagesSquare,
  Mic,
  MoreHorizontal,
  Palette,
  Puzzle,
  SkipForward,
  UsersRound,
  Workflow,
  Wrench,
  type LucideIcon,
} from 'lucide-react';
import type {
  OnboardingCommand,
  OnboardingReceipt,
  OnboardingSnapshot,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import {
  Button,
  EmptyState,
  ErrorState,
  Menu,
  ProgressRing,
  Skeleton,
} from '../../ui/primitives';
import { useWorkspaceActions } from './workspace-actions';
import FirstRun from './FirstRun';

type Owner = {
  load: (signal?: AbortSignal) => Promise<OnboardingSnapshot>;
  send: (command: OnboardingCommand) => Promise<OnboardingReceipt>;
};

type Area = {
  icon: LucideIcon;
  /** The one primary action: a link, or a chat for areas that start in one. */
  action: string;
  to?: string;
  draft?: string;
};

/** Where each area is set up. Designs start in a chat (no studio gallery). */
const areas: Record<string, Area> = {
  models: { icon: Cpu, action: 'Choose models', to: '/settings/models' },
  knowledge: {
    icon: Brain,
    action: 'Open Knowledge',
    to: '/settings/knowledge',
  },
  workflows: {
    icon: Workflow,
    action: 'Open Workflows',
    to: '/?tab=workflows',
  },
  designer: {
    icon: Palette,
    action: 'Start a design',
    draft: 'Create a design: ',
  },
  developer: {
    icon: Code2,
    action: 'Developer tools',
    to: '/settings/tools#built-in-tools',
  },
  channels: {
    icon: MessagesSquare,
    action: 'Open Channels',
    to: '/settings/channels',
  },
  accounts: {
    icon: UsersRound,
    action: 'Open Accounts',
    to: '/settings/accounts',
  },
  tools: { icon: Wrench, action: 'Review tools', to: '/settings/tools' },
  extensions: {
    icon: Puzzle,
    action: 'Open MCP & Plugins',
    to: '/settings/mcp',
  },
  voice: { icon: Mic, action: 'Open Voice', to: '/settings/voice' },
  final: { icon: BadgeCheck, action: 'Run diagnosis', to: '/?tab=monitor' },
};

const intentIcons: Record<string, LucideIcon> = {
  chat: MessageSquare,
  research: BookOpen,
  workflows: Workflow,
  designer: Palette,
  developer: Code2,
  channels: MessagesSquare,
  local: HardDrive,
};

/** Areas that matter most for each use, in the order they are recommended. */
const intentPriority: Record<string, readonly string[]> = {
  chat: ['models', 'tools'],
  research: ['knowledge', 'tools', 'extensions'],
  workflows: ['workflows', 'channels', 'accounts'],
  designer: ['designer', 'knowledge'],
  developer: ['developer', 'tools'],
  channels: ['channels', 'accounts'],
  local: ['models', 'knowledge'],
};

/** Recommended areas first (from the chosen uses), then the rest in order. */
export function orderSetupSteps<T extends { id: string }>(
  steps: readonly T[],
  profile: readonly string[],
): { ordered: T[]; recommended: Set<string> } {
  const recommended = new Set<string>();
  for (const intent of profile)
    for (const step of intentPriority[intent] ?? [])
      if (steps.some((item) => item.id === step)) recommended.add(step);
  const priority = [...recommended];
  const ordered = [
    ...priority.map((id) => steps.find((step) => step.id === id)!),
    ...steps.filter((step) => !recommended.has(step.id)),
  ];
  return { ordered, recommended };
}

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

function StatusChip({
  status,
}: {
  status: 'done' | 'skipped' | 'recommended' | 'todo';
}) {
  const label = {
    done: 'Done',
    skipped: 'Skipped',
    recommended: 'Recommended',
    todo: 'To do',
  }[status];
  return (
    <span className="setup-chip" data-status={status}>
      {status === 'done' ? (
        <Check size={12} aria-hidden />
      ) : (
        <span className="setup-chip-dot" aria-hidden />
      )}
      {label}
    </span>
  );
}

export function OnboardingCenter({ owner }: { owner: Owner }) {
  const workspace = useWorkspaceActions();
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

  /** First-run commands: the result or the error goes back to the step. */
  async function firstRun(
    action: 'choose_model' | 'finish_models',
    modelRef = '',
  ): Promise<OnboardingSnapshot> {
    const current = snapshot;
    if (!current) throw new Error('Setup is still loading');
    const command: OnboardingCommand = {
      command_id: crypto.randomUUID(),
      expected_revision: current.revision,
      action,
      profile: [],
      step: '',
      ...(modelRef ? { model_ref: modelRef } : {}),
    };
    const receipt = await owner.send(command);
    if (
      receipt.command_id !== command.command_id ||
      receipt.status !== 'completed'
    )
      throw new Error('Setup receipt did not match the requested action');
    setSnapshot(receipt.snapshot);
    return receipt.snapshot;
  }

  const locked = busy || !!pending;
  // Setup shows the real state (decision 13): done and skipped are counted
  // separately, and an area that is really done never reads as skipped.
  const done = snapshot ? new Set(snapshot.completed_steps).size : 0;
  const skipped = snapshot
    ? snapshot.skipped_steps.filter(
        (step) => !snapshot.completed_steps.includes(step),
      ).length
    : 0;
  const total = snapshot?.steps.length ?? 0;
  const ringLabel = `${done} of ${total} done${skipped ? ` · ${skipped} skipped` : ''}`;
  const { ordered, recommended } = orderSetupSteps(
    snapshot?.steps ?? [],
    snapshot?.profile ?? [],
  );

  if (snapshot?.needs_model)
    return (
      <FirstRun
        snapshot={snapshot}
        actions={{
          choose: (modelRef) => firstRun('choose_model', modelRef),
          finish: () => firstRun('finish_models'),
        }}
      />
    );

  return (
    <section className="setup-center" aria-label="Setup Center">
      <header className="setup-header">
        <span className="setup-header-icon" aria-hidden>
          <Compass size={18} />
        </span>
        <div className="setup-header-text">
          <h1>Setup Center</h1>
          <p>
            Finish or skip the areas you want. Each area shows its real state,
            and your progress is saved.
          </p>
        </div>
        {snapshot && (
          <ProgressRing value={done} total={total} label={ringLabel} />
        )}
      </header>
      {loading && !snapshot && <Skeleton label="Loading setup progress" />}
      {error && (
        <ErrorState
          title="Setup needs attention"
          action={<Button onClick={() => void load()}>Refresh setup</Button>}
        >
          {error}
        </ErrorState>
      )}
      {pending && (
        <div className="setup-callout" role="status">
          <p>
            An earlier setup action may have completed. Check its original
            result before making another change.
          </p>
          <Button disabled={busy} onClick={() => void execute(pending)}>
            Check setup action
          </Button>
        </div>
      )}
      <p role="status" className={notice ? 'setup-notice' : 'visually-hidden'}>
        {notice}
      </p>
      {!loading && !snapshot && !error && (
        <EmptyState title="Setup unavailable">Refresh to try again.</EmptyState>
      )}
      {snapshot && (
        <>
          <section className="setup-section" aria-label="Your goals">
            <div className="setup-section-head">
              <h2>What would you like to use?</h2>
              <p>Pick any. Recommended areas move to the top.</p>
            </div>
            <div className="setup-intents">
              {snapshot.intents.map((intent) => {
                const Icon = intentIcons[intent.id] ?? Compass;
                const checked = snapshot.profile.includes(intent.id);
                return (
                  <label
                    key={intent.id}
                    className="setup-intent"
                    data-checked={checked ? 'true' : undefined}
                  >
                    <input
                      type="checkbox"
                      className="setup-intent-input"
                      checked={checked}
                      disabled={locked}
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
                    <span className="setup-intent-icon" aria-hidden>
                      <Icon size={18} />
                    </span>
                    <span className="setup-intent-label">{intent.label}</span>
                    <span className="setup-intent-check" aria-hidden>
                      <Check size={14} />
                    </span>
                  </label>
                );
              })}
            </div>
          </section>
          {snapshot.setup_complete && (
            <section className="setup-section" aria-label="Setup checklist">
              <div className="setup-section-head">
                <h2>Continue setup</h2>
                <p>
                  {ringLabel}.
                  {recommended.size > 0 &&
                    ' Recommended areas come first; every area stays available.'}
                </p>
              </div>
              <ul className="setup-areas">
                {ordered.map((step) => {
                  const area = areas[step.id] ?? {
                    icon: Compass,
                    action: `Open ${step.title}`,
                    to: '/',
                  };
                  const Icon = area.icon;
                  const areaDone = snapshot.completed_steps.includes(step.id);
                  const live = (snapshot.live_done ?? []).includes(step.id);
                  const areaSkipped =
                    !areaDone && snapshot.skipped_steps.includes(step.id);
                  const status = areaDone
                    ? 'done'
                    : areaSkipped
                      ? 'skipped'
                      : recommended.has(step.id)
                        ? 'recommended'
                        : 'todo';
                  const starters =
                    step.id === 'workflows' &&
                    snapshot.starter_workflows_missing > 0;
                  return (
                    <li
                      className="setup-area"
                      key={step.id}
                      data-status={status}
                    >
                      <span className="setup-area-icon" aria-hidden>
                        <Icon size={18} />
                      </span>
                      <div className="setup-area-text">
                        <h3>{step.title}</h3>
                        <p>{step.description}</p>
                      </div>
                      <StatusChip status={status} />
                      <div className="setup-area-actions">
                        {starters ? (
                          <Button
                            className="small"
                            disabled={locked}
                            onClick={() => void send('add_starters')}
                          >
                            Add missing starter workflows
                          </Button>
                        ) : area.draft && workspace?.newChat ? (
                          <Button
                            className="small"
                            onClick={() => workspace.newChat?.(area.draft)}
                          >
                            {area.action}
                          </Button>
                        ) : (
                          <Link
                            className="button small"
                            to={area.to ?? '/library'}
                          >
                            {area.action}
                          </Link>
                        )}
                        <Menu
                          label={`More actions for ${step.title}`}
                          iconOnly
                          variant="ghost"
                          className="setup-area-more"
                          disabled={locked}
                          actions={[
                            {
                              label: `Mark ${step.title} done`,
                              icon: <Check size={16} />,
                              disabled: areaDone,
                              onSelect: () =>
                                void send('mark_done', [], step.id),
                            },
                            {
                              label: `Skip ${step.title}`,
                              icon: <SkipForward size={16} />,
                              // Live areas follow their real state.
                              disabled: areaSkipped || live,
                              onSelect: () =>
                                void send('skip_step', [], step.id),
                            },
                          ]}
                        >
                          <MoreHorizontal size={16} aria-hidden />
                        </Menu>
                      </div>
                    </li>
                  );
                })}
              </ul>
              <p className="setup-more">
                <Link to="/settings/system">
                  Import from Hermes or OpenClaw
                </Link>
                <span aria-hidden>·</span>
                <Link to="/settings/documents">
                  Set up private knowledge embeddings
                </Link>
              </p>
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
