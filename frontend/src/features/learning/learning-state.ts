import { useEffect, useMemo, useSyncExternalStore } from 'react';
import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type {
  LearningCommand,
  LearningConversation,
  LearningDownload,
  LearningHardware,
  LearningLessonPage,
  LearningReceipt,
  LearningSnapshot,
  LearningTraining,
  LearningVersionDetails,
} from '../../api/types';
import { useRuntime } from '../../runtime';

/** The handshake capability of a build started with ROW_BOT_LEARNING_PREVIEW=1 (O34). */
export const LEARNING_CAPABILITY = 'learning:preview';

export type LearningAction = LearningCommand['action'];
export type LearningFields = Omit<
  LearningCommand,
  'command_id' | 'client_session_id' | 'revision' | 'action'
>;

/** What the Learning surfaces read and do; tests pass their own. */
export type LearningIO = {
  snapshot: LearningSnapshot | null;
  /** A read failed (in words); null snapshot with no error means loading. */
  error: string;
  reload: () => Promise<LearningSnapshot | null>;
  run: (
    action: LearningAction,
    fields?: Partial<LearningFields>,
  ) => Promise<LearningReceipt>;
  hardware: (refresh?: boolean) => Promise<LearningHardware>;
  lessons: () => Promise<LearningLessonPage>;
  conversation: (id: string) => Promise<LearningConversation>;
  /** Experiment details of one personal-model version (absent in some tests). */
  version?: (version: number) => Promise<LearningVersionDetails>;
  /**
   * Keep the training run fresh while a surface shows it (every 2 s while it
   * runs, every 15 s otherwise); returns the stop. Absent in some tests.
   */
  watchTraining?: () => () => void;
  /** The same for the personal model's download (every second while it runs). */
  watchDownload?: () => () => void;
};

// Commands that don't depend on the saved choices: after a change from another
// device they are sent once more with the new revision.
const RETRY_ON_CHANGE = new Set<LearningAction>([
  'teach',
  'undo_teach',
  'opt_out',
  'opt_in',
  'answer_download',
  'train_now',
  'stop_training',
  'continue_training',
  'discard_training',
  'use_recommended',
  'start_download',
  'stop_download',
  'cancel_download',
  'remove_engine',
  'delete_starting_model',
  'undo_use',
  'dismiss',
  'review_version',
  'export_version',
  'stop_compare',
]);

/** How often the training run is read while it runs, and otherwise. */
export const TRAINING_POLL_MS = 2000;
export const TRAINING_IDLE_POLL_MS = 15000;
/** How often the download is read while it runs. */
export const DOWNLOAD_POLL_MS = 1000;

/** The personal model's download is under way (Monitor's row, the progress bar). */
export function downloadLive(download: LearningDownload | null | undefined) {
  return download?.state === 'downloading';
}

/** A run whose worker is (or may be) going: Monitor's row, Buddy's line, the progress bar. */
export function trainingLive(training: LearningTraining | null | undefined) {
  return (
    training?.state === 'preparing' ||
    training?.state === 'running' ||
    training?.state === 'pausing'
  );
}

function pageVisible() {
  return (
    typeof document === 'undefined' || document.visibilityState !== 'hidden'
  );
}

type View = { snapshot: LearningSnapshot | null; error: string };

/** One shared Learning state per connection: Settings, Home and chats see the same. */
class LearningStore {
  private view: View = { snapshot: null, error: '' };
  private listeners = new Set<() => void>();
  private identity = '';
  private reading: Promise<LearningSnapshot | null> | null = null;
  private readonly controller: ClientController;

  constructor(controller: ClientController) {
    this.controller = controller;
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getView = () => this.view;

  private publish(next: View) {
    this.view = next;
    this.listeners.forEach((listener) => listener());
  }

  set(snapshot: LearningSnapshot) {
    this.publish({ snapshot, error: '' });
  }

  /** Read once per connection (a new sign-in or restart reads again). */
  ensure(identity: string) {
    if (identity === this.identity && (this.view.snapshot || this.reading))
      return;
    if (identity !== this.identity) {
      this.identity = identity;
      this.publish({ snapshot: null, error: '' });
    }
    void this.reload();
  }

  reload = (): Promise<LearningSnapshot | null> => {
    if (this.reading) return this.reading;
    const reading = this.controller.learning().then(
      (snapshot) => {
        this.set(snapshot);
        return snapshot;
      },
      (cause: unknown) => {
        this.publish({
          snapshot: this.view.snapshot,
          error: clientError(cause).message,
        });
        return null;
      },
    );
    this.reading = reading.finally(() => {
      this.reading = null;
    });
    return this.reading;
  };

  run = async (
    action: LearningAction,
    fields: Partial<LearningFields> = {},
  ): Promise<LearningReceipt> => {
    let snapshot = this.view.snapshot ?? (await this.reload());
    if (!snapshot) throw clientError({ code: 'learning_unavailable' });
    const send = (revision: string) =>
      this.controller.executeLearning({
        command_id: crypto.randomUUID(),
        revision,
        action,
        ...fields,
      });
    let receipt: LearningReceipt;
    try {
      receipt = await send(snapshot.revision);
    } catch (cause) {
      if (
        clientError(cause).code !== 'learning_revision_conflict' ||
        !RETRY_ON_CHANGE.has(action)
      ) {
        void this.reload();
        throw cause;
      }
      snapshot = await this.reload();
      if (!snapshot) throw cause;
      receipt = await send(snapshot.revision);
    }
    this.set(receipt.snapshot);
    if (this.watchers) this.scheduleTraining(TRAINING_POLL_MS);
    if (this.downloadWatchers) this.scheduleDownload(DOWNLOAD_POLL_MS);
    return receipt;
  };

  // ── The download, polled while a surface shows it ──────────────────────

  private downloadWatchers = 0;
  private downloadTimer: ReturnType<typeof setTimeout> | undefined;
  private readonly onDownloadVisibility = () => {
    if (pageVisible() && this.downloadWatchers) this.scheduleDownload(0);
  };

  watchDownload = () => {
    this.downloadWatchers += 1;
    if (this.downloadWatchers === 1) {
      document.addEventListener('visibilitychange', this.onDownloadVisibility);
      this.scheduleDownload(0);
    }
    let stopped = false;
    return () => {
      if (stopped) return;
      stopped = true;
      this.downloadWatchers -= 1;
      if (!this.downloadWatchers) {
        clearTimeout(this.downloadTimer);
        document.removeEventListener(
          'visibilitychange',
          this.onDownloadVisibility,
        );
      }
    };
  };

  private scheduleDownload(delay: number) {
    clearTimeout(this.downloadTimer);
    if (!this.downloadWatchers) return;
    this.downloadTimer = setTimeout(() => void this.readDownload(), delay);
  }

  private async readDownload() {
    if (!this.downloadWatchers || !pageVisible()) return;
    const before = this.view.snapshot?.personal_model.download_state ?? null;
    let next = before;
    try {
      const download = await this.controller.learningDownload();
      const snapshot = this.view.snapshot;
      if (download && snapshot) {
        next = download;
        if (downloadLive(before) && !downloadLive(download)) {
          // It finished, paused or failed: the engine's and model's rows changed too.
          await this.reload();
        } else
          this.publish({
            snapshot: {
              ...snapshot,
              personal_model: {
                ...snapshot.personal_model,
                download_state: download,
              },
            },
            error: '',
          });
      }
    } catch {
      // The next read tries again; the last known state stays shown.
    }
    this.scheduleDownload(
      downloadLive(next) ? DOWNLOAD_POLL_MS : TRAINING_IDLE_POLL_MS,
    );
  }

  // ── The training run, polled while a surface shows it ──────────────────

  private watchers = 0;
  private trainingTimer: ReturnType<typeof setTimeout> | undefined;
  private readonly onVisibility = () => {
    if (pageVisible() && this.watchers) this.scheduleTraining(0);
  };

  watchTraining = () => {
    this.watchers += 1;
    if (this.watchers === 1) {
      document.addEventListener('visibilitychange', this.onVisibility);
      this.scheduleTraining(0);
    }
    let stopped = false;
    return () => {
      if (stopped) return;
      stopped = true;
      this.watchers -= 1;
      if (!this.watchers) {
        clearTimeout(this.trainingTimer);
        document.removeEventListener('visibilitychange', this.onVisibility);
      }
    };
  };

  private scheduleTraining(delay: number) {
    clearTimeout(this.trainingTimer);
    if (!this.watchers) return;
    this.trainingTimer = setTimeout(() => void this.readTraining(), delay);
  }

  private async readTraining() {
    if (!this.watchers) return;
    if (!pageVisible()) return; // read again when the page shows
    let next = this.view.snapshot?.training ?? null;
    try {
      const training = await this.controller.learningTraining();
      const snapshot = this.view.snapshot;
      if (training && snapshot) {
        this.publish({ snapshot: { ...snapshot, training }, error: '' });
        next = training;
      }
    } catch {
      // The next read tries again; the last known run stays shown.
    }
    this.scheduleTraining(
      trainingLive(next) || next?.state === 'waiting'
        ? TRAINING_POLL_MS
        : TRAINING_IDLE_POLL_MS,
    );
  }
}

const stores = new WeakMap<ClientController, LearningStore>();

function storeFor(controller: ClientController) {
  let store = stores.get(controller);
  if (!store) {
    store = new LearningStore(controller);
    stores.set(controller, store);
  }
  return store;
}

const NOTHING = () => () => {};
const EMPTY_VIEW: View = { snapshot: null, error: '' };

/** The controller, unless it is a stand-in without a state to follow (some tests). */
function useOptionalController(): ClientController | null {
  const controller: ClientController | undefined = useRuntime().controller;
  return controller &&
    typeof controller.subscribe === 'function' &&
    typeof controller.getSnapshot === 'function'
    ? controller
    : null;
}

function useSelected<T>(
  controller: ClientController | null,
  select: (state: ReturnType<ClientController['getSnapshot']>) => T,
  fallback: T,
): T {
  return useSyncExternalStore(
    controller ? controller.subscribe : NOTHING,
    () => (controller ? select(controller.getSnapshot()) : fallback),
  );
}

/** Whether this build has Learning (preview) at all. */
export function useLearningPreview(): boolean {
  return useSelected(
    useOptionalController(),
    (state) =>
      Boolean(
        state.handshake?.application_capabilities?.includes(
          LEARNING_CAPABILITY,
        ),
      ),
    false,
  );
}

/**
 * Learning's shared state and actions, or null without the preview flag.
 * Reading starts when a surface first asks for it.
 */
export function useLearning(): LearningIO | null {
  const controller = useOptionalController();
  const preview = useLearningPreview();
  const identity = useSelected(
    controller,
    (state) =>
      state.status === 'ready' && state.handshake
        ? `${state.handshake.instance_id}:${state.handshake.client_session_id}`
        : '',
    '',
  );
  const store = controller ? storeFor(controller) : null;
  const view = useSyncExternalStore(
    store ? store.subscribe : NOTHING,
    store ? store.getView : () => EMPTY_VIEW,
  );
  useEffect(() => {
    if (store && preview && identity) store.ensure(identity);
  }, [identity, preview, store]);
  return useMemo(
    () =>
      controller && store && preview
        ? {
            snapshot: view.snapshot,
            error: view.error,
            reload: store.reload,
            run: store.run,
            hardware: (refresh?: boolean) =>
              controller.learningHardware(refresh),
            lessons: () => controller.learningLessons(),
            conversation: (id: string) => controller.learningConversation(id),
            version: (version: number) => controller.learningVersion(version),
            watchTraining: store.watchTraining,
            watchDownload: store.watchDownload,
          }
        : null,
    [controller, preview, store, view],
  );
}

/** Keeps the training run fresh while the calling surface is shown. */
export function useTrainingWatch(io: LearningIO | null) {
  const watch = io?.watchTraining;
  const on = learningIsOn(io);
  useEffect(() => (on && watch ? watch() : undefined), [on, watch]);
}

/** Keeps the personal model's download fresh while the calling surface is shown. */
export function useDownloadWatch(io: LearningIO | null) {
  const watch = io?.watchDownload;
  const on = learningIsOn(io);
  useEffect(() => (on && watch ? watch() : undefined), [on, watch]);
}

/** Learning is on, paused or not: the Learning tab and the chat controls exist. */
export function learningIsOn(io: LearningIO | null) {
  return io?.snapshot?.state === 'on' || io?.snapshot?.state === 'paused';
}
