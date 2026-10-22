import { useEffect, useMemo, useSyncExternalStore } from 'react';
import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type {
  LearningCommand,
  LearningConversation,
  LearningHardware,
  LearningLessonPage,
  LearningReceipt,
  LearningSnapshot,
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
};

// Commands that don't depend on the saved choices: after a change from another
// device they are sent once more with the new revision.
const RETRY_ON_CHANGE = new Set<LearningAction>([
  'teach',
  'undo_teach',
  'opt_out',
  'opt_in',
  'answer_download',
]);

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
    return receipt;
  };
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
          }
        : null,
    [controller, preview, store, view],
  );
}

/** Learning is on, paused or not: the Learning tab and the chat controls exist. */
export function learningIsOn(io: LearningIO | null) {
  return io?.snapshot?.state === 'on' || io?.snapshot?.state === 'paused';
}
