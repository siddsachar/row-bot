export type PwaPhase =
  | 'idle'
  | 'unsupported'
  | 'registering'
  | 'ready'
  | 'offline'
  | 'update_available'
  | 'error';

export type PwaSnapshot = {
  phase: PwaPhase;
  online: boolean;
  installAvailable: boolean;
  updateAvailable: boolean;
  error: string | null;
};

type InstallChoice = { outcome: 'accepted' | 'dismissed' };
type InstallPromptEvent = Event & {
  prompt(): Promise<void>;
  userChoice: Promise<InstallChoice>;
};

type WorkerLike = {
  state?: ServiceWorkerState;
  postMessage(message: unknown): void;
  addEventListener(type: 'statechange', listener: EventListener): void;
  removeEventListener(type: 'statechange', listener: EventListener): void;
};

type RegistrationLike = {
  waiting: WorkerLike | null;
  installing: WorkerLike | null;
  addEventListener(type: 'updatefound', listener: EventListener): void;
  removeEventListener(type: 'updatefound', listener: EventListener): void;
};

type WorkerContainerLike = {
  controller: unknown;
  register(
    scriptURL: string,
    options: RegistrationOptions,
  ): Promise<RegistrationLike>;
  addEventListener(type: 'controllerchange', listener: EventListener): void;
  removeEventListener(type: 'controllerchange', listener: EventListener): void;
};

export type PwaEnvironment = {
  origin: string;
  online(): boolean;
  userActivation(): boolean;
  reload(): void;
  serviceWorker?: WorkerContainerLike;
  addEventListener(type: string, listener: EventListener): void;
  removeEventListener(type: string, listener: EventListener): void;
};

const INITIAL: PwaSnapshot = {
  phase: 'idle',
  online: true,
  installAvailable: false,
  updateAvailable: false,
  error: null,
};

export function browserPwaEnvironment(): PwaEnvironment {
  let serviceWorker: WorkerContainerLike | undefined;
  try {
    serviceWorker = navigator.serviceWorker;
  } catch {
    // Opaque/restricted documents still retain the normal browser client.
  }
  return {
    origin: window.location.origin,
    online: () => navigator.onLine,
    userActivation: () => navigator.userActivation?.isActive ?? true,
    reload: () => window.location.reload(),
    serviceWorker,
    addEventListener: (type, listener) =>
      window.addEventListener(type, listener),
    removeEventListener: (type, listener) =>
      window.removeEventListener(type, listener),
  };
}

/**
 * Owns install/update presentation state only. It never opens CacheStorage,
 * persists application data, or fetches an API/private route.
 */
export class PwaClient {
  private snapshot: PwaSnapshot;
  private listeners = new Set<() => void>();
  private registration: RegistrationLike | null = null;
  private installing: WorkerLike | null = null;
  private waitingWorker: WorkerLike | null = null;
  private installPrompt: InstallPromptEvent | null = null;
  private started = false;
  private generation = 0;
  private activationRequested = false;

  constructor(
    private readonly environment: PwaEnvironment = browserPwaEnvironment(),
  ) {
    this.snapshot = { ...INITIAL, online: environment.online() };
  }

  getSnapshot = (): PwaSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private publish(change: Partial<PwaSnapshot>): void {
    const next = { ...this.snapshot, ...change };
    if (
      Object.entries(next).every(
        ([key, value]) => this.snapshot[key as keyof PwaSnapshot] === value,
      )
    )
      return;
    this.snapshot = next;
    for (const listener of this.listeners) listener();
  }

  private phaseForConnection(): PwaPhase {
    if (!this.environment.online()) return 'offline';
    if (!this.environment.serviceWorker) return 'unsupported';
    if (this.snapshot.error) return 'error';
    return this.waitingWorker || this.registration?.waiting
      ? 'update_available'
      : 'ready';
  }

  private readonly connectivity = () => {
    const online = this.environment.online();
    this.publish({ online, phase: this.phaseForConnection() });
  };

  private readonly beforeInstall = (event: Event) => {
    const prompt = event as InstallPromptEvent;
    if (typeof prompt.prompt !== 'function') return;
    event.preventDefault();
    this.installPrompt = prompt;
    this.publish({ installAvailable: true });
  };

  private readonly installed = () => {
    this.installPrompt = null;
    this.publish({ installAvailable: false });
  };

  private readonly controllerChanged = () => {
    if (!this.activationRequested) return;
    this.activationRequested = false;
    this.environment.reload();
  };

  private readonly workerStateChanged = () => {
    if (!this.installing || this.installing.state !== 'installed') return;
    const updateAvailable = Boolean(this.environment.serviceWorker?.controller);
    this.waitingWorker = updateAvailable ? this.installing : null;
    this.publish({
      phase: !this.environment.online()
        ? 'offline'
        : updateAvailable
          ? 'update_available'
          : 'ready',
      updateAvailable,
      error: null,
    });
  };

  private readonly updateFound = () => {
    if (this.installing)
      this.installing.removeEventListener(
        'statechange',
        this.workerStateChanged,
      );
    this.installing = this.registration?.installing ?? null;
    this.installing?.addEventListener('statechange', this.workerStateChanged);
    this.workerStateChanged();
  };

  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    const generation = ++this.generation;
    const workers = this.environment.serviceWorker;
    this.environment.addEventListener('online', this.connectivity);
    this.environment.addEventListener('offline', this.connectivity);
    this.environment.addEventListener(
      'beforeinstallprompt',
      this.beforeInstall,
    );
    this.environment.addEventListener('appinstalled', this.installed);
    if (!workers) {
      this.publish({ phase: 'unsupported', online: this.environment.online() });
      return;
    }
    workers.addEventListener('controllerchange', this.controllerChanged);
    this.publish({
      phase: this.environment.online() ? 'registering' : 'offline',
      online: this.environment.online(),
      error: null,
    });
    try {
      const script = new URL(
        '/app-v2/service-worker.js',
        this.environment.origin,
      );
      if (script.origin !== this.environment.origin)
        throw new Error('service_worker_origin');
      const registration = await workers.register(script.pathname, {
        scope: '/app-v2/',
        updateViaCache: 'none',
      });
      if (!this.started || generation !== this.generation) return;
      this.registration = registration;
      this.registration.addEventListener('updatefound', this.updateFound);
      this.updateFound();
      const updateAvailable = Boolean(this.registration.waiting);
      this.waitingWorker = this.registration.waiting;
      this.publish({
        phase: !this.environment.online()
          ? 'offline'
          : updateAvailable
            ? 'update_available'
            : 'ready',
        online: this.environment.online(),
        updateAvailable,
        error: null,
      });
    } catch {
      if (!this.started || generation !== this.generation) return;
      // Registration denial or unavailable storage must not block the web app.
      this.publish({
        phase: this.environment.online() ? 'error' : 'offline',
        online: this.environment.online(),
        error: 'pwa_registration_unavailable',
      });
    }
  }

  async requestInstall(): Promise<
    'accepted' | 'dismissed' | 'unavailable' | 'user_activation_required'
  > {
    const prompt = this.installPrompt;
    if (!prompt) return 'unavailable';
    if (!this.environment.userActivation()) return 'user_activation_required';
    await prompt.prompt();
    const choice = await prompt.userChoice;
    this.installPrompt = null;
    this.publish({ installAvailable: false });
    return choice.outcome;
  }

  applyUpdate(): 'requested' | 'unavailable' | 'user_activation_required' {
    const waiting = this.waitingWorker ?? this.registration?.waiting;
    if (!waiting) return 'unavailable';
    if (!this.environment.userActivation()) return 'user_activation_required';
    this.activationRequested = true;
    waiting.postMessage({ type: 'SKIP_WAITING' });
    return 'requested';
  }

  dispose(): void {
    if (!this.started) return;
    this.started = false;
    this.generation += 1;
    this.environment.removeEventListener('online', this.connectivity);
    this.environment.removeEventListener('offline', this.connectivity);
    this.environment.removeEventListener(
      'beforeinstallprompt',
      this.beforeInstall,
    );
    this.environment.removeEventListener('appinstalled', this.installed);
    this.environment.serviceWorker?.removeEventListener(
      'controllerchange',
      this.controllerChanged,
    );
    this.registration?.removeEventListener('updatefound', this.updateFound);
    this.installing?.removeEventListener(
      'statechange',
      this.workerStateChanged,
    );
    this.installing = null;
    this.waitingWorker = null;
    this.registration = null;
    this.installPrompt = null;
    this.activationRequested = false;
  }
}
