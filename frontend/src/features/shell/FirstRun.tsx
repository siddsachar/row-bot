import {
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  BadgeCheck,
  Check,
  Copy,
  Download,
  HardDrive,
  KeyRound,
  Loader2,
  type LucideIcon,
} from 'lucide-react';
import type {
  CachedModelRow,
  LocalRuntimeSnapshot,
  OnboardingSnapshot,
  ProviderLiveCard,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { RuntimeContext, useRuntime } from '../../runtime';
import { writeClipboardText } from '../../platform/clipboard';
import { Button, Disclosure, Input, Segmented } from '../../ui/primitives';
import SubscriptionAccounts from '../settings/SubscriptionAccounts';
import ProviderSettingsPanel from '../settings/ProviderSettingsPanel';
import {
  MORE_KEY_PROVIDERS,
  RECOMMENDED_KEY_PROVIDERS,
} from '../settings/provider-keys';
import { BillingTag } from './ModelList';

/** "Set up later" for this window: Home stops opening Setup until next time. */
export const SETUP_LATER_KEY = 'row-bot:setup-later:v1';

export function setupDeferred(): boolean {
  try {
    return sessionStorage.getItem(SETUP_LATER_KEY) === '1';
  } catch {
    return false;
  }
}

type Path = 'local' | 'subscription' | 'key';
type Step =
  | { kind: 'pick' }
  | { kind: 'testing'; label: string }
  | { kind: 'failed'; label: string; detail: string }
  | { kind: 'import' };

export type FirstRunActions = {
  /** Save the person's pick as the default (decision 9). */
  choose: (modelRef: string) => Promise<OnboardingSnapshot>;
  /** Finish the first run once the pick answered. */
  finish: () => Promise<OnboardingSnapshot>;
};

const SUBSCRIPTIONS = [
  { value: 'codex', label: 'ChatGPT' },
  { value: 'claude_subscription', label: 'Claude' },
  { value: 'xai_oauth', label: 'Grok' },
] as const;
type Subscription = (typeof SUBSCRIPTIONS)[number]['value'];

const KEY_LABELS: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  google: 'Google Gemini',
  openrouter: 'OpenRouter',
  xai: 'xAI',
  minimax: 'MiniMax',
  requesty: 'Requesty',
  opencode_zen: 'OpenCode Zen',
  opencode_go: 'OpenCode Go',
  atlascloud: 'Atlas Cloud',
  ollama_cloud: 'Ollama Cloud',
};

/** Re-detects the local runtime every 3 s while Setup is open and visible. */
function useLocalRuntime() {
  const { controller } = useRuntime();
  const [runtime, setRuntime] = useState<LocalRuntimeSnapshot | null>(null);
  useEffect(() => {
    let stopped = false;
    let timer = 0;
    const read = async () => {
      if (stopped) return;
      if (document.visibilityState !== 'hidden') {
        try {
          const next = await controller.localRuntime();
          if (!stopped && next) setRuntime(next);
        } catch {
          /* Detection is best effort; the next read tries again. */
        }
      }
      if (!stopped) timer = window.setTimeout(() => void read(), 3000);
    };
    void read();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [controller]);
  return runtime;
}

function useProviderCards() {
  const { controller } = useRuntime();
  const [cards, setCards] = useState<ProviderLiveCard[]>([]);
  const reload = useCallback(async () => {
    try {
      const snapshot = await controller.liveProviderStatus();
      if (snapshot) setCards(snapshot.providers);
    } catch {
      /* Connection states are shown once they can be read. */
    }
  }, [controller]);
  useEffect(() => {
    void reload();
  }, [reload]);
  return { cards, reload };
}

function ChoiceCard({
  icon: Icon,
  title,
  detail,
  status,
  pressed,
  onClick,
}: {
  icon: LucideIcon;
  title: string;
  detail: string;
  status?: string;
  pressed: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="first-run-choice"
      aria-pressed={pressed}
      onClick={onClick}
    >
      <span className="first-run-choice-icon" aria-hidden>
        <Icon size={20} />
      </span>
      <span className="first-run-choice-text">
        <strong>{title}</strong>
        <span>{detail}</span>
        {status && <small>{status}</small>}
      </span>
    </button>
  );
}

function CopyCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  const writer = useContext(RuntimeContext)?.platform.writeClipboard;
  return (
    <span className="first-run-command">
      <code>{command}</code>
      <Button
        variant="ghost"
        className="small"
        aria-label={`Copy ${command}`}
        onClick={() => void writeClipboardText(command, writer).then(setCopied)}
      >
        {copied ? (
          <Check size={14} aria-hidden />
        ) : (
          <Copy size={14} aria-hidden />
        )}
      </Button>
    </span>
  );
}

function LocalPanel({
  runtime,
  onPick,
}: {
  runtime: LocalRuntimeSnapshot | null;
  onPick: (modelRef: string, label: string) => void;
}) {
  if (!runtime)
    return (
      <p className="first-run-waiting" role="status">
        <Loader2 size={14} className="spin" aria-hidden /> Looking for Ollama on
        this computer…
      </p>
    );
  const watching = (
    <p className="first-run-waiting" role="status">
      <Loader2 size={14} className="spin" aria-hidden /> Row-Bot notices by
      itself when Ollama is running.
    </p>
  );
  if (runtime.state === 'not_installed')
    return (
      <div className="first-run-panel-body">
        <h3>Install Ollama</h3>
        <p>
          Ollama runs models on this computer: private, free and offline once a
          model is downloaded.
        </p>
        {runtime.platform === 'linux' ? (
          <ol className="first-run-steps">
            <li>
              In a terminal, run{' '}
              <CopyCommand command="curl -fsSL https://ollama.com/install.sh | sh" />
            </li>
            <li>
              Start it with <CopyCommand command="ollama serve" />
            </li>
          </ol>
        ) : (
          <ol className="first-run-steps">
            <li>
              Download Ollama for{' '}
              {runtime.platform === 'macos' ? 'Mac' : 'Windows'} and run the
              installer.
            </li>
            <li>
              {runtime.platform === 'macos'
                ? 'Open Ollama from Applications.'
                : 'Ollama starts by itself when the installer finishes.'}
            </li>
          </ol>
        )}
        {runtime.platform !== 'linux' && (
          <a
            className="button primary first-run-download"
            href={runtime.download_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            <Download size={16} aria-hidden /> Download Ollama
          </a>
        )}
        {watching}
      </div>
    );
  if (runtime.state === 'installed')
    return (
      <div className="first-run-panel-body">
        <h3>Start Ollama</h3>
        <p>
          Ollama is installed but not running.{' '}
          {runtime.platform === 'windows'
            ? 'Open Ollama from the Start menu.'
            : runtime.platform === 'macos'
              ? 'Open Ollama from Applications.'
              : 'Start it in a terminal:'}
        </p>
        {runtime.platform === 'linux' && <CopyCommand command="ollama serve" />}
        {watching}
      </div>
    );
  if (!runtime.models.length)
    return (
      <div className="first-run-panel-body">
        <h3>Download a model</h3>
        <p>
          Ollama is running but has no models yet. Download one in a terminal,
          then pick it here (a few gigabytes).
        </p>
        <CopyCommand command="ollama pull qwen3" />
        {watching}
      </div>
    );
  return (
    <div className="first-run-panel-body">
      <h3>Pick a model</h3>
      <p>
        Ollama is running with {runtime.models.length} model
        {runtime.models.length === 1 ? '' : 's'}. The one you pick becomes the
        default; you can change it any time.
      </p>
      <ModelOptions
        label="Models on this computer"
        options={runtime.models.map((model) => ({
          ref: model.model_ref,
          name: model.name,
          chatOnly: model.agent_ready === false,
        }))}
        onPick={onPick}
      />
    </div>
  );
}

type Option = { ref: string; name: string; chatOnly: boolean };

function ModelOptions({
  label,
  options,
  onPick,
}: {
  label: string;
  options: Option[];
  onPick: (modelRef: string, label: string) => void;
}) {
  const [query, setQuery] = useState('');
  const shown = options.filter((option) =>
    option.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
  );
  return (
    <div className="first-run-models">
      {options.length > 8 && (
        <Input
          type="search"
          aria-label="Search models"
          placeholder="Search models"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      )}
      <ul aria-label={label}>
        {shown.map((option) => (
          <li key={option.ref}>
            <button
              type="button"
              className="first-run-model"
              onClick={() => onPick(option.ref, option.name)}
            >
              <span>{option.name}</span>
              {option.chatOnly && (
                <span className="first-run-model-tag">Chat only</span>
              )}
              <span className="first-run-model-use" aria-hidden>
                Use
              </span>
            </button>
          </li>
        ))}
      </ul>
      {!shown.length && <p role="status">No models match.</p>}
    </div>
  );
}

/** A connected provider's chat models, after its catalog is read. */
function ProviderModels({
  providerId,
  label,
  onPick,
}: {
  providerId: string;
  label: string;
  onPick: (modelRef: string, label: string) => void;
}) {
  const { controller } = useRuntime();
  const [rows, setRows] = useState<CachedModelRow[] | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let stopped = false;
    setRows(null);
    setError('');
    void (async () => {
      try {
        // Load the provider's models (this contacts the provider).
        await controller.refreshLiveProvider(providerId).catch(() => null);
        for (let tries = 0; tries < 90 && !stopped; tries += 1) {
          const state = await controller.liveProviderRefresh();
          if (!state?.running) break;
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
        }
        // The catalog read that assesses readiness (as Settings › Models does).
        const page = await controller.modelCatalogPage(
          'chat',
          providerId,
          '',
          undefined,
        );
        if (stopped) return;
        setRows(
          (page?.items ?? []).filter(
            (row) =>
              row.categories.includes('chat') &&
              row.configured &&
              row.runtime_ready &&
              row.installed !== false,
          ),
        );
      } catch (cause) {
        if (!stopped) setError(clientError(cause).message);
      }
    })();
    return () => {
      stopped = true;
    };
  }, [controller, providerId, attempt]);
  if (error)
    return (
      <div className="first-run-panel-body" role="alert">
        <p>
          Row-Bot couldn't load {label}'s models. {error}
        </p>
        <Button onClick={() => setAttempt((value) => value + 1)}>
          Try again
        </Button>
      </div>
    );
  if (!rows)
    return (
      <p className="first-run-waiting" role="status">
        <Loader2 size={14} className="spin" aria-hidden /> Loading {label}'s
        models…
      </p>
    );
  if (!rows.length)
    return (
      <div className="first-run-panel-body">
        <p role="status">No chat models are available from {label} yet.</p>
        <Button onClick={() => setAttempt((value) => value + 1)}>
          Try again
        </Button>
      </div>
    );
  return (
    <div className="first-run-panel-body">
      <h3>Pick a model from {label}</h3>
      <ModelOptions
        label={`${label} models`}
        options={rows.map((row) => ({
          ref: row.selection_ref,
          name: row.display_name || row.model_id,
          chatOnly:
            row.runtime_mode === 'chat_only' || row.tool_calling === false,
        }))}
        onPick={onPick}
      />
    </div>
  );
}

/**
 * The first run (decision 10): one question, "How should Row-Bot think?".
 * The pick becomes the default, a quick test runs, then Home opens. The only
 * extra step is an import offer when another assistant's data is found.
 */
export default function FirstRun({
  snapshot,
  actions,
}: {
  snapshot: OnboardingSnapshot;
  actions: FirstRunActions;
}) {
  const { controller, providerSettingsSessions, subscriptionAccountsOwner } =
    useRuntime();
  const navigate = useNavigate();
  const runtime = useLocalRuntime();
  const { cards, reload } = useProviderCards();
  const [path, setPath] = useState<Path | null>(null);
  const [subscription, setSubscription] = useState<Subscription>('codex');
  const [keyProvider, setKeyProvider] = useState<string | null>(null);
  const [step, setStep] = useState<Step>({ kind: 'pick' });
  const importSources = useRef(snapshot.import_sources ?? []);
  const connected = useMemo(
    () =>
      new Set(
        cards.filter((card) => card.configured).map((card) => card.provider_id),
      ),
    [cards],
  );
  const billing = (id: string) =>
    cards.find((card) => card.provider_id === id)?.billing ?? null;
  const endpoints = cards.filter(
    (card) => card.group === 'custom' && card.configured,
  );
  const localStatus = !runtime
    ? 'Looking for Ollama…'
    : runtime.state === 'running'
      ? `Ollama is running · ${runtime.models.length} model${runtime.models.length === 1 ? '' : 's'}`
      : runtime.state === 'installed'
        ? 'Ollama is installed · not running'
        : "Ollama isn't installed yet";

  async function pick(modelRef: string, label: string) {
    setStep({ kind: 'testing', label });
    try {
      await actions.choose(modelRef);
    } catch (cause) {
      setStep({ kind: 'failed', label, detail: clientError(cause).message });
      return;
    }
    await verify(label);
  }

  /** The quick test, then Home (or the import offer). */
  async function verify(label: string) {
    setStep({ kind: 'testing', label });
    let result: { ok: boolean; detail: string } | null;
    try {
      result = (await controller.testChosenModel()) ?? null;
    } catch (cause) {
      result = { ok: false, detail: clientError(cause).message };
    }
    if (!result?.ok) {
      setStep({
        kind: 'failed',
        label,
        detail: result?.detail || `${label} didn't answer.`,
      });
      return;
    }
    try {
      await actions.finish();
    } catch (cause) {
      setStep({ kind: 'failed', label, detail: clientError(cause).message });
      return;
    }
    void controller.refreshChoices().catch(() => undefined);
    if (importSources.current.length) setStep({ kind: 'import' });
    else navigate('/', { replace: true });
  }

  if (step.kind === 'testing' || step.kind === 'failed')
    return (
      <section className="first-run" aria-label="First run">
        <header className="first-run-header">
          <h1>
            {step.kind === 'testing'
              ? 'Checking the model'
              : 'That model didn’t answer'}
          </h1>
        </header>
        {step.kind === 'testing' ? (
          <p className="first-run-waiting" role="status">
            <Loader2 size={16} className="spin" aria-hidden /> Sending{' '}
            {step.label} a short test message. A local model can take a minute
            to load the first time.
          </p>
        ) : (
          <div className="first-run-panel-body" role="alert">
            <p>{step.detail}</p>
            <div className="actions">
              <Button variant="primary" onClick={() => void verify(step.label)}>
                Try again
              </Button>
              <Button onClick={() => setStep({ kind: 'pick' })}>
                Choose another model
              </Button>
            </div>
          </div>
        )}
      </section>
    );

  if (step.kind === 'import') {
    const source = importSources.current[0];
    return (
      <section className="first-run" aria-label="First run">
        <header className="first-run-header">
          <h1>Bring your {source.label} data along?</h1>
          <p>
            Row-Bot found {source.label} on this computer. Import its memories,
            persona and skills now, or later from Settings › Data.
          </p>
        </header>
        <div className="actions">
          <Button
            variant="primary"
            onClick={() => navigate('/settings/data#migration')}
          >
            Import from {source.label}
          </Button>
          <Button onClick={() => navigate('/', { replace: true })}>
            Not now
          </Button>
        </div>
      </section>
    );
  }

  return (
    <section className="first-run" aria-label="First run">
      <header className="first-run-header">
        <h1>How should Row-Bot think?</h1>
        <p>
          Pick one to get started. The model you choose becomes the default; you
          can add others later.
        </p>
      </header>
      <div
        className="first-run-choices"
        role="group"
        aria-label="How should Row-Bot think?"
      >
        <ChoiceCard
          icon={HardDrive}
          title="On this computer"
          detail="Private and free, with Ollama."
          status={localStatus}
          pressed={path === 'local'}
          onClick={() => setPath('local')}
        />
        <ChoiceCard
          icon={BadgeCheck}
          title="With my subscription"
          detail="ChatGPT, Claude or Grok."
          pressed={path === 'subscription'}
          onClick={() => setPath('subscription')}
        />
        <ChoiceCard
          icon={KeyRound}
          title="With an API key"
          detail="OpenAI, Anthropic, Google, OpenRouter and more."
          pressed={path === 'key'}
          onClick={() => setPath('key')}
        />
      </div>
      {path && (
        <section
          className="first-run-panel"
          aria-label={
            path === 'local'
              ? 'On this computer'
              : path === 'subscription'
                ? 'With my subscription'
                : 'With an API key'
          }
        >
          {path === 'local' && <LocalPanel runtime={runtime} onPick={pick} />}
          {path === 'subscription' && (
            <div className="first-run-panel-body">
              <Segmented
                label="Subscription"
                value={subscription}
                onChange={setSubscription}
                options={SUBSCRIPTIONS.map((item) => ({ ...item }))}
              />
              {connected.has(subscription) ? (
                <ProviderModels
                  key={subscription}
                  providerId={subscription}
                  label={
                    SUBSCRIPTIONS.find((item) => item.value === subscription)!
                      .label
                  }
                  onPick={pick}
                />
              ) : subscriptionAccountsOwner?.get() ? (
                <SubscriptionAccounts
                  key={subscription}
                  compact
                  initialProvider={subscription}
                  initialAction="connect"
                  session={subscriptionAccountsOwner.get()!}
                  load={controller.subscriptionAccounts}
                  review={controller.reviewSubscriptionAction}
                  apply={controller.applySubscriptionAction}
                  readFlow={controller.subscriptionFlow}
                  cancel={controller.cancelSubscriptionFlow}
                  cancelStart={controller.cancelSubscriptionStart}
                  receipt={controller.subscriptionReceipt}
                  onSaved={() => void reload()}
                  onClose={() => setPath(null)}
                />
              ) : (
                <p role="status">
                  Signing in is available once Row-Bot is connected.
                </p>
              )}
            </div>
          )}
          {path === 'key' &&
            (keyProvider ? (
              <div className="first-run-panel-body">
                <Button
                  variant="ghost"
                  className="small first-run-back"
                  onClick={() => setKeyProvider(null)}
                >
                  <ArrowLeft size={14} aria-hidden /> All providers
                </Button>
                {connected.has(keyProvider) ? (
                  <ProviderModels
                    key={keyProvider}
                    providerId={keyProvider}
                    label={KEY_LABELS[keyProvider] ?? keyProvider}
                    onPick={pick}
                  />
                ) : providerSettingsSessions ? (
                  <ProviderSettingsPanel
                    compact
                    owner={providerSettingsSessions}
                    providerId={keyProvider}
                    onSaved={(saved) => {
                      if (saved.configured) void reload();
                    }}
                    onCancel={() => setKeyProvider(null)}
                  />
                ) : (
                  <p role="status">
                    Keys can be added once Row-Bot is connected.
                  </p>
                )}
              </div>
            ) : (
              <div className="first-run-panel-body">
                <ul className="first-run-providers" aria-label="Providers">
                  {RECOMMENDED_KEY_PROVIDERS.map((id) => (
                    <li key={id}>
                      <button
                        type="button"
                        className="first-run-provider"
                        onClick={() => setKeyProvider(id)}
                      >
                        <span>{KEY_LABELS[id]}</span>
                        {connected.has(id) && (
                          <span className="first-run-model-tag">Connected</span>
                        )}
                        <BillingTag billing={billing(id) ?? 'pay_per_use'} />
                      </button>
                    </li>
                  ))}
                </ul>
                <Disclosure summary="More providers">
                  <ul
                    className="first-run-providers"
                    aria-label="More providers"
                  >
                    {MORE_KEY_PROVIDERS.map((id) => (
                      <li key={id}>
                        <button
                          type="button"
                          className="first-run-provider"
                          onClick={() => setKeyProvider(id)}
                        >
                          <span>{KEY_LABELS[id]}</span>
                          {connected.has(id) && (
                            <span className="first-run-model-tag">
                              Connected
                            </span>
                          )}
                          <BillingTag billing={billing(id)} />
                        </button>
                      </li>
                    ))}
                  </ul>
                </Disclosure>
              </div>
            ))}
        </section>
      )}
      {endpoints.length > 0 && (
        <section className="first-run-panel" aria-label="Your endpoints">
          {endpoints.map((card) => (
            <ProviderModels
              key={card.provider_id}
              providerId={card.provider_id}
              label={card.display_name}
              onPick={pick}
            />
          ))}
        </section>
      )}
      <footer className="first-run-footer">
        <Link to="/settings/providers?add=custom-endpoint">
          Other (custom endpoint)
        </Link>
        <span aria-hidden>·</span>
        <button
          type="button"
          className="first-run-later"
          onClick={() => {
            try {
              sessionStorage.setItem(SETUP_LATER_KEY, '1');
            } catch {
              /* Private windows simply ask again next time. */
            }
            navigate('/');
          }}
        >
          Set up later
        </button>
      </footer>
    </section>
  );
}
