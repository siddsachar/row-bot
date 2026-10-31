import {
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  BadgeCheck,
  Check,
  Copy,
  Cpu,
  Download,
  HardDrive,
  KeyRound,
  Loader2,
  LogIn,
  Server,
  Sparkles,
  type LucideIcon,
} from 'lucide-react';
import type {
  CachedModelRow,
  LocalRuntimeModel,
  LocalRuntimeSnapshot,
  OnboardingSnapshot,
  ProviderLiveCard,
  SubscriptionFlowSnapshot,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { usePlace } from '../../api/place';
import { RuntimeContext, useRuntime } from '../../runtime';
import { copyTextLater, writeClipboardText } from '../../platform/clipboard';
import {
  Button,
  Disclosure,
  Input,
  Segmented,
  StatusDot,
} from '../../ui/primitives';
import SubscriptionAccounts from '../settings/SubscriptionAccounts';
import ClaudePlanSetup, {
  CLAUDE_PLAN_SUMMARY,
  USE_MY_CLAUDE_PLAN,
} from '../settings/ClaudePlanSetup';
import ProviderSettingsPanel from '../settings/ProviderSettingsPanel';
import {
  MORE_KEY_PROVIDERS,
  RECOMMENDED_KEY_PROVIDERS,
} from '../settings/provider-keys';
import { BillingTag } from './ModelList';
import { useWorkspaceActions } from './workspace-actions';
import { markFirstChat } from './first-chat';
import { DESKTOP_APP_URL, OWN_MODEL_SERVER_GUIDE } from './hosted-links';

/** "Set up later" for this window: Home stops opening Setup until next time. */
export const SETUP_LATER_KEY = 'row-bot:setup-later:v1';

export function setupDeferred(): boolean {
  try {
    return sessionStorage.getItem(SETUP_LATER_KEY) === '1';
  } catch {
    return false;
  }
}

type Path = 'local' | 'subscription' | 'key' | 'grok';
type Step =
  | { kind: 'pick' }
  | { kind: 'testing'; label: string; connected?: string }
  | { kind: 'failed'; label: string; detail: string; onlyLooks?: boolean }
  | { kind: 'import' };

export type FirstRunActions = {
  /** Save the person's pick as the default (decision 9). */
  choose: (modelRef: string) => Promise<OnboardingSnapshot>;
  /**
   * A hosted first run (U3): the provider the person just signed in to starts
   * on its own first model, so a sign-in goes straight to a chat.
   */
  chooseSuggested?: (providerId: string) => Promise<OnboardingSnapshot>;
  /** Finish the first run once the pick answered. */
  finish: () => Promise<OnboardingSnapshot>;
};

/** What a hosted first run's sign-ins are called. */
const SIGN_IN_LABELS: Record<string, string> = {
  codex: 'ChatGPT',
  claude_subscription: 'Claude',
  xai_oauth: 'Grok',
};

/** U3's line while ChatGPT's code waits. */
export const CODE_COPIED =
  'Paste the code (it’s copied) and approve. Row-Bot notices when you’re done.';
export const CODE_NOT_COPIED =
  'Enter this code on OpenAI’s page and approve. Row-Bot notices when you’re done.';

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
function useLocalRuntime(enabled: boolean) {
  const { controller } = useRuntime();
  const [runtime, setRuntime] = useState<LocalRuntimeSnapshot | null>(null);
  useEffect(() => {
    // A hosted Row-Bot has no local runtime to look for (U3).
    if (!enabled) return;
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
  }, [controller, enabled]);
  return runtime;
}

/**
 * Whether this install downloads its updates by itself (D2: on by default and
 * said once in the first run). Development checkouts and installs whose host
 * applies updates don't, so they show nothing.
 */
function useBackgroundUpdates() {
  const { controller } = useRuntime();
  const [on, setOn] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    void (async () => {
      try {
        const updates = await controller.updates(abort.signal);
        if (!abort.signal.aborted)
          setOn(updates?.mode === 'self' && updates.auto_download === true);
      } catch {
        /* Settings › Updates says the same; this line is only a pointer. */
      }
    })();
    return () => abort.abort();
  }, [controller]);
  return on;
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
  const place = usePlace();
  if (!runtime)
    return (
      <p className="first-run-waiting" role="status">
        <Loader2 size={14} className="spin" aria-hidden /> Looking for Ollama{' '}
        {place.on}…
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
          {place.kind === 'this_computer'
            ? 'Ollama runs models on this computer: private, free and offline once a model is downloaded.'
            : `Ollama runs models ${place.onAgain}: free, and your chats go to no AI provider.`}
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
      <LocalModels models={runtime.models} onPick={onPick} />
    </div>
  );
}

type Option = {
  ref: string;
  name: string;
  chatOnly: boolean;
  hidden?: boolean;
};

const localOption = (model: LocalRuntimeModel): Option => ({
  ref: model.model_ref,
  name: model.name,
  chatOnly: model.agent_ready === false,
  ...(model.hidden ? { hidden: true } : {}),
});

/**
 * The local models, short list first (F10): the person's favourites, then
 * Row-Bot's suggested picks, with every other model one "Show all models"
 * away (hidden ones last). Without either, every model is listed as before.
 */
function LocalModels({
  models,
  onPick,
}: {
  models: readonly LocalRuntimeModel[];
  onPick: (modelRef: string, label: string) => void;
}) {
  const place = usePlace();
  const favourites = models
    .filter((model) => model.favourite_rank != null)
    .sort((a, b) => (a.favourite_rank ?? 0) - (b.favourite_rank ?? 0));
  const picks = models.filter(
    (model) => model.recommended && model.favourite_rank == null,
  );
  const short = new Set([...favourites, ...picks]);
  const rest = models.filter((model) => !short.has(model));
  const others = [
    ...rest.filter((model) => !model.hidden),
    ...rest.filter((model) => model.hidden),
  ];
  if (!short.size)
    return (
      <ModelOptions
        label={`Models on ${place.name}`}
        options={others.map(localOption)}
        onPick={onPick}
      />
    );
  return (
    <div className="first-run-model-groups">
      {favourites.length > 0 && (
        <ModelOptions
          label="Your favourites"
          title="Your favourites"
          options={favourites.map(localOption)}
          onPick={onPick}
        />
      )}
      {picks.length > 0 && (
        <ModelOptions
          label="Recommended"
          title="Recommended"
          options={picks.map(localOption)}
          onPick={onPick}
        />
      )}
      {others.length > 0 && (
        <Disclosure
          className="first-run-all-models"
          summary={`Show all models (${others.length})`}
        >
          <ModelOptions
            label={`All models on ${place.name}`}
            options={others.map(localOption)}
            onPick={onPick}
          />
        </Disclosure>
      )}
    </div>
  );
}

function ModelOptions({
  label,
  title,
  options,
  onPick,
}: {
  label: string;
  /** A short heading over the list (a short list among others). */
  title?: string;
  options: Option[];
  onPick: (modelRef: string, label: string) => void;
}) {
  const [query, setQuery] = useState('');
  const shown = options.filter((option) =>
    option.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
  );
  return (
    <div className="first-run-models">
      {title && <h4 className="first-run-models-title">{title}</h4>}
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
              {option.hidden && (
                <span className="first-run-model-tag">Hidden</span>
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

/** "With an API key": the providers, then the chosen one's key and models. */
function KeyPanel({
  keyProvider,
  setKeyProvider,
  connected,
  billing,
  onPick,
  onSaved,
}: {
  keyProvider: string | null;
  setKeyProvider: (id: string | null) => void;
  connected: Set<string>;
  billing: (id: string) => ProviderLiveCard['billing'] | null;
  onPick: (modelRef: string, label: string) => void;
  onSaved: () => void;
}) {
  const { providerSettingsSessions } = useRuntime();
  if (keyProvider)
    return (
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
            onPick={onPick}
          />
        ) : providerSettingsSessions ? (
          <ProviderSettingsPanel
            compact
            owner={providerSettingsSessions}
            providerId={keyProvider}
            onSaved={(saved) => {
              if (saved.configured) onSaved();
            }}
            onCancel={() => setKeyProvider(null)}
          />
        ) : (
          <p role="status">Keys can be added once Row-Bot is connected.</p>
        )}
      </div>
    );
  return (
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
        <ul className="first-run-providers" aria-label="More providers">
          {MORE_KEY_PROVIDERS.map((id) => (
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
                <BillingTag billing={billing(id)} />
              </button>
            </li>
          ))}
        </ul>
      </Disclosure>
    </div>
  );
}

type Hosted = 'chatgpt' | 'claude' | 'key' | 'grok';

/** A promise the click that starts ChatGPT's sign-in copies from (U3). */
function deferred() {
  let resolve: (value: string) => void = () => undefined;
  let reject: () => void = () => undefined;
  const promise = new Promise<string>((done, fail) => {
    resolve = done;
    reject = () => fail(new Error('not started'));
  });
  promise.catch(() => undefined);
  return { promise, resolve, reject };
}

/** Opens a sign-in page in a new tab; false when the browser blocked it. */
function openSignInPage(url: string | null | undefined): boolean {
  if (!url) return false;
  try {
    const opened = window.open(url, '_blank');
    if (!opened) return false;
    opened.opener = null;
    return true;
  } catch {
    return false;
  }
}

/**
 * U3, a hosted first run: two big buttons, "Sign in with ChatGPT" and "Use my
 * Claude plan"; "Other ways" holds an API key, your own model server and Grok.
 * Models can't run on the server itself, so there is no local choice: one
 * quiet line says so and points to the desktop app.
 */
function HostedChoices({
  cards,
  connected,
  listed,
  onListedClose,
  reload,
  signedIn,
  pick,
  keyPanel,
  endpointModels,
  setUpLater,
}: {
  cards: ProviderLiveCard[];
  connected: Set<string>;
  listed: string | null;
  onListedClose: () => void;
  reload: () => Promise<void>;
  signedIn: (providerId: string) => void;
  pick: (modelRef: string, label: string) => void;
  keyPanel: ReactNode;
  endpointModels: ReactNode;
  setUpLater: ReactNode;
}) {
  const { controller, subscriptionAccountsOwner } = useRuntime();
  const writer = useContext(RuntimeContext)?.platform.writeClipboard;
  const [path, setPath] = useState<Hosted | null>(null);
  const [checkingClaude, setCheckingClaude] = useState(false);
  const [copied, setCopied] = useState(false);
  const code = useRef<ReturnType<typeof deferred> | null>(null);
  const copying = useRef<Promise<boolean>>(Promise.resolve(false));
  const continued = useRef(new Set<string>());
  const provider =
    path === 'chatgpt'
      ? 'codex'
      : path === 'claude'
        ? 'claude_subscription'
        : path === 'grok'
          ? 'xai_oauth'
          : null;

  // A sign-in that connects (here, or already before) carries straight on.
  useEffect(() => {
    if (!provider || !connected.has(provider)) return;
    if (continued.current.has(provider)) return;
    continued.current.add(provider);
    signedIn(provider);
  }, [provider, connected, signedIn]);

  /** Inside the click: copy ChatGPT's code the moment the server has it. */
  function prepareCopy() {
    code.current?.reject();
    const next = deferred();
    code.current = next;
    setCopied(false);
    copying.current = copyTextLater(next.promise, writer);
  }

  function started(flow: SubscriptionFlowSnapshot | null) {
    if (!flow) {
      code.current?.reject();
      return;
    }
    if (flow.device_code) {
      code.current?.resolve(flow.device_code);
      // Copy first: the new tab takes the focus a copy needs.
      void copying.current.then((done) => {
        setCopied(done);
        openSignInPage(flow.authorization_url);
      });
    } else openSignInPage(flow.authorization_url);
  }

  function choose(next: Hosted) {
    if (next === 'chatgpt' && path !== 'chatgpt' && !connected.has('codex'))
      prepareCopy();
    setPath(next);
  }

  const session = subscriptionAccountsOwner?.get();
  const signIn = (id: 'codex' | 'xai_oauth') =>
    session ? (
      <SubscriptionAccounts
        key={id}
        compact
        autoStart
        quiet
        remote
        initialProvider={id}
        initialAction="connect"
        guide={id === 'codex' ? (copied ? CODE_COPIED : CODE_NOT_COPIED) : null}
        beforeStart={id === 'codex' ? prepareCopy : undefined}
        onStarted={started}
        session={session}
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
      <p role="status">Signing in is available once Row-Bot is connected.</p>
    );

  const panel = (body: ReactNode, label: string) => (
    <section className="first-run-panel" aria-label={label}>
      {body}
    </section>
  );
  const listedLabel = listed ? (SIGN_IN_LABELS[listed] ?? listed) : '';

  return (
    <section className="first-run first-run-hosted" aria-label="First run">
      <header className="first-run-header">
        <h1>How should Row-Bot think?</h1>
        <p>Sign in with a plan you already have. You can add others later.</p>
      </header>
      <div
        className="first-run-big"
        role="group"
        aria-label="How should Row-Bot think?"
      >
        <button
          type="button"
          className="first-run-big-choice"
          aria-pressed={path === 'chatgpt'}
          onClick={() => choose('chatgpt')}
        >
          <LogIn size={20} aria-hidden />
          <span>Sign in with ChatGPT</span>
        </button>
        <button
          type="button"
          className="first-run-big-choice"
          aria-pressed={path === 'claude'}
          onClick={() => choose('claude')}
        >
          <BadgeCheck size={20} aria-hidden />
          <span>{USE_MY_CLAUDE_PLAN}</span>
        </button>
      </div>
      {listed &&
        panel(
          <div className="first-run-panel-body">
            <ProviderModels
              key={listed}
              providerId={listed}
              label={listedLabel}
              onPick={pick}
            />
            <Button
              variant="ghost"
              className="small first-run-back"
              onClick={() => {
                // Back to the two buttons; the same one carries on again.
                if (listed) continued.current.delete(listed);
                setPath(null);
                onListedClose();
              }}
            >
              <ArrowLeft size={14} aria-hidden /> Back
            </Button>
          </div>,
          `${listedLabel} models`,
        )}
      {!listed &&
        path === 'chatgpt' &&
        !connected.has('codex') &&
        panel(signIn('codex'), 'Sign in with ChatGPT')}
      {!listed &&
        path === 'claude' &&
        !connected.has('claude_subscription') &&
        panel(
          <div className="stack">
            <p>{CLAUDE_PLAN_SUMMARY}</p>
            <ClaudePlanSetup
              autoStart
              plan={
                cards.find((card) => card.provider_id === 'claude_subscription')
                  ?.claude_plan
              }
              checking={checkingClaude}
              onCheck={() => {
                setCheckingClaude(true);
                void reload().finally(() => setCheckingClaude(false));
              }}
            />
          </div>,
          USE_MY_CLAUDE_PLAN,
        )}
      <Disclosure
        className="first-run-other"
        summary="Other ways"
        defaultOpen={path === 'key' || path === 'grok'}
      >
        <ul className="first-run-other-list" aria-label="Other ways">
          <li>
            <button
              type="button"
              className="first-run-other-choice"
              aria-pressed={path === 'key'}
              onClick={() => choose('key')}
            >
              <KeyRound size={18} aria-hidden />
              <span>
                <strong>Use an API key</strong>
                <small>OpenAI, Anthropic, Google, OpenRouter and more</small>
              </span>
            </button>
          </li>
          <li>
            <Link
              className="first-run-other-choice"
              to="/settings/providers?add=custom-endpoint&type=ollama&from=setup"
            >
              <Server size={18} aria-hidden />
              <span>
                <strong>Use my own model server</strong>
                <small>Ollama, llama.cpp, vLLM, LM Studio and more</small>
              </span>
            </Link>
            <a
              className="first-run-other-guide"
              href={OWN_MODEL_SERVER_GUIDE}
              target="_blank"
              rel="noopener noreferrer"
            >
              How to reach it safely
            </a>
          </li>
          <li>
            <button
              type="button"
              className="first-run-other-choice"
              aria-pressed={path === 'grok'}
              onClick={() => choose('grok')}
            >
              <Sparkles size={18} aria-hidden />
              <span>
                <strong>Sign in with Grok</strong>
                <small>Your xAI account</small>
              </span>
            </button>
          </li>
        </ul>
        {!listed && path === 'key' && panel(keyPanel, 'Use an API key')}
        {!listed &&
          path === 'grok' &&
          !connected.has('xai_oauth') &&
          panel(signIn('xai_oauth'), 'Sign in with Grok')}
      </Disclosure>
      {endpointModels}
      <p className="first-run-note">
        <Cpu size={14} aria-hidden />
        <span>
          Running models on this server needs a GPU. Use your own model server,
          or{' '}
          <a href={DESKTOP_APP_URL} target="_blank" rel="noopener noreferrer">
            get the desktop app →
          </a>
        </span>
      </p>
      <footer className="first-run-footer">{setUpLater}</footer>
    </section>
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
  const { controller, subscriptionAccountsOwner } = useRuntime();
  const navigate = useNavigate();
  const workspace = useWorkspaceActions();
  const place = usePlace();
  // A known host runs this Row-Bot (U3): two big buttons, and no local runtime.
  const host = snapshot.host_name ?? null;
  const runtime = useLocalRuntime(!host);
  const backgroundUpdates = useBackgroundUpdates();
  const { cards, reload } = useProviderCards();
  const [path, setPath] = useState<Path | null>(null);
  const [subscription, setSubscription] = useState<Subscription>('codex');
  const [keyProvider, setKeyProvider] = useState<string | null>(null);
  const [step, setStep] = useState<Step>({ kind: 'pick' });
  const [checkingClaude, setCheckingClaude] = useState(false);
  // A hosted sign-in whose provider had no model ready to start on: its list.
  const [listed, setListed] = useState<string | null>(null);
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

  /** A refusal, not the model: a window that isn't signed in can only look. */
  function refused(label: string, cause: unknown) {
    const error = clientError(cause);
    setStep({
      kind: 'failed',
      label,
      detail: error.message,
      onlyLooks: error.code === 'sign_in_required',
    });
  }

  async function pick(modelRef: string, label: string) {
    setStep({ kind: 'testing', label });
    try {
      await actions.choose(modelRef);
    } catch (cause) {
      refused(label, cause);
      return;
    }
    await verify(label);
  }

  /** The quick test, then Home (or the import offer). */
  async function verify(label: string, connected?: string) {
    setStep({ kind: 'testing', label, connected });
    let result: { ok: boolean; detail: string } | null;
    try {
      result = (await controller.testChosenModel()) ?? null;
    } catch (cause) {
      refused(label, cause);
      return;
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
      refused(label, cause);
      return;
    }
    void controller.refreshChoices().catch(() => undefined);
    if (importSources.current.length) setStep({ kind: 'import' });
    else if (host && workspace?.newChat) {
      // Straight into a chat with one suggested first message (U3).
      markFirstChat();
      workspace.newChat();
    } else navigate('/', { replace: true });
  }

  /**
   * A hosted sign-in finished: load the provider's models, start on its own
   * first pick and test it. With none ready yet, its models are listed.
   */
  async function signedIn(providerId: string) {
    const label = SIGN_IN_LABELS[providerId] ?? providerId;
    setStep({ kind: 'testing', label, connected: label });
    try {
      await controller.refreshLiveProvider(providerId).catch(() => null);
      for (let tries = 0; tries < 90; tries += 1) {
        const state = await controller.liveProviderRefresh();
        if (!state?.running) break;
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
      }
      if (!actions.chooseSuggested) throw new Error('unavailable');
      await actions.chooseSuggested(providerId);
    } catch {
      setListed(providerId);
      setStep({ kind: 'pick' });
      return;
    }
    await verify(label, label);
  }

  if (step.kind === 'testing' || step.kind === 'failed')
    return (
      <section className="first-run" aria-label="First run">
        <header className="first-run-header">
          <h1>
            {step.kind === 'testing'
              ? 'Checking the model'
              : step.onlyLooks
                ? 'This window can only look'
                : 'That model didn’t answer'}
          </h1>
        </header>
        {step.kind === 'testing' && step.connected && (
          <p className="first-run-connected" role="status">
            <StatusDot
              tone="success"
              label={`Connected to ${step.connected}`}
              showLabel
            />
          </p>
        )}
        {step.kind === 'testing' ? (
          <p className="first-run-waiting" role="status">
            <Loader2 size={16} className="spin" aria-hidden /> Sending{' '}
            {step.label} a short test message.
            {!host &&
              ' A local model can take a minute to load the first time.'}
          </p>
        ) : (
          <div className="first-run-panel-body" role="alert">
            <p>{step.detail}</p>
            {/* Trying again here would be refused the same way. */}
            {step.onlyLooks ? null : (
              <div className="actions">
                <Button
                  variant="primary"
                  onClick={() => void verify(step.label)}
                >
                  Try again
                </Button>
                <Button onClick={() => setStep({ kind: 'pick' })}>
                  Choose another model
                </Button>
              </div>
            )}
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
            Row-Bot found {source.label} {place.on}. Import its memories,
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

  const setUpLater = (
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
  );
  const endpointModels = endpoints.length > 0 && (
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
  );
  const keyPanel = (
    <KeyPanel
      keyProvider={keyProvider}
      setKeyProvider={setKeyProvider}
      connected={connected}
      billing={billing}
      onPick={pick}
      onSaved={() => void reload()}
    />
  );

  if (host)
    return (
      <HostedChoices
        cards={cards}
        connected={connected}
        listed={listed}
        onListedClose={() => setListed(null)}
        reload={reload}
        signedIn={(providerId) => void signedIn(providerId)}
        pick={pick}
        keyPanel={keyPanel}
        endpointModels={endpointModels}
        setUpLater={setUpLater}
      />
    );

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
          title={`On ${place.name}`}
          detail={
            place.kind === 'this_computer'
              ? 'Private and free, with Ollama.'
              : 'Free, with Ollama.'
          }
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
              ? `On ${place.name}`
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
              ) : subscription === 'claude_subscription' ? (
                <div className="stack">
                  <p>{CLAUDE_PLAN_SUMMARY}</p>
                  <ClaudePlanSetup
                    plan={
                      cards.find(
                        (card) => card.provider_id === 'claude_subscription',
                      )?.claude_plan
                    }
                    checking={checkingClaude}
                    onCheck={() => {
                      setCheckingClaude(true);
                      void reload().finally(() => setCheckingClaude(false));
                    }}
                  />
                </div>
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
          {path === 'key' && keyPanel}
        </section>
      )}
      {endpointModels}
      <footer className="first-run-footer">
        <Link to="/settings/providers?add=custom-endpoint">
          Other (custom endpoint)
        </Link>
        <span aria-hidden>·</span>
        {setUpLater}
      </footer>
      {backgroundUpdates && (
        <p className="first-run-note">
          <Download size={14} aria-hidden />
          <span>
            Row-Bot downloads updates in the background. You can turn this off
            in <Link to="/settings/updates">Settings › Updates</Link>.
          </span>
        </p>
      )}
    </section>
  );
}
