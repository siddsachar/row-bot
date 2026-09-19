import { ProviderSettingsSession } from '../settings/provider-settings-sessions';
import { useEffect, useSyncExternalStore } from 'react';
import BuddyControls, {
  type BuddyControlsProps,
  type BuddyPack,
  type BuddyPackPage,
  type BuddyPreferences,
  type BuddySnapshot,
} from '../shell/BuddyControls';
import BuddyHatch, {
  type HatchRequest,
  type HatchReview,
  type HatchResult,
  type HatchRemoval,
} from '../shell/BuddyHatch';
import { Button, ErrorState } from '../../ui/primitives';

export type BuddyCommand = {
  command_id: string;
  type: 'buddy.update' | 'buddy.hatch' | 'buddy.remove' | 'buddy.cancel';
  payload: Record<string, unknown>;
};
export type BuddyReceipt = {
  command_id: string;
  status: string;
  code?: string;
  buddy_revision?: string;
  hatch?: HatchResult;
  removal?: HatchRemoval;
  cancel_requested?: boolean;
};
export type BuddyPanelTransport = {
  snapshot(signal?: AbortSignal): Promise<BuddySnapshot>;
  packs(cursor?: string): Promise<BuddyPackPage>;
  pack(id: string): Promise<BuddyPack>;
  review(request: HatchRequest): Promise<HatchReview>;
  execute(command: BuddyCommand): Promise<BuddyReceipt>;
  receipt(commandId: string): Promise<BuddyReceipt>;
};
type Attempt = {
  command: BuddyCommand;
  receipt: BuddyReceipt | null;
  uncertain: boolean;
};
type State = {
  snapshot: BuddySnapshot | null;
  packs: BuddyPackPage | null;
  selectedPack: BuddyPack | null;
  result: HatchResult | null;
  busy: boolean;
  pending: boolean;
  revoked: boolean;
  error: string;
  statusError: string;
};

/** Private controller/auth-lifetime buffers, never browser persistence.
 * Root owns the session across panel mounts, wires pending to beforeunload and
 * calls purge on authentication loss. The guard runs before and after I/O.
 */
export function createBuddyPanelSession(
  transport: BuddyPanelTransport,
  guard: () => void,
  newId: () => string = () => crypto.randomUUID(),
) {
  const preferencesEditor = new ProviderSettingsSession('buddy-preferences');
  const hatchEditor = new ProviderSettingsSession('buddy-hatch');
  let state: State = {
    snapshot: null,
    packs: null,
    selectedPack: null,
    result: null,
    busy: false,
    pending: false,
    revoked: false,
    error: '',
    statusError: '',
  };
  const listeners = new Set<() => void>();
  const attempts = new Map<string, Attempt>();
  let reviewed: {
    request: HatchRequest;
    review: HatchReview;
    command: BuddyCommand;
  } | null = null;
  let currentAttempt: Attempt | null = null;
  let operation: Promise<unknown> | null = null;
  let cancellation: Promise<void> | null = null;
  let cancellationJob: string | null = null;
  let epoch = 0;
  let viewVersion = 0;
  let observers = 0;
  let timer: ReturnType<typeof setInterval> | null = null;
  let observation: AbortController | null = null;
  const emit = (patch: Partial<State>) => {
    state = { ...state, ...patch };
    listeners.forEach((notify) => notify());
  };
  function authority(captured = epoch) {
    if (state.revoked || captured !== epoch)
      throw new Error('authentication_required');
    guard();
  }
  async function owned<T>(read: () => Promise<T>): Promise<T> {
    const captured = epoch;
    authority(captured);
    const value = await read();
    authority(captured);
    return value;
  }
  const running = (value?: HatchResult) =>
    !!value && ['queued', 'running', 'cancelling'].includes(value.status);
  function accept(attempt: Attempt, receipt: BuddyReceipt) {
    if (
      receipt.command_id !== attempt.command.command_id ||
      (receipt.hatch && receipt.hatch.command_id !== receipt.command_id)
    )
      throw new Error('buddy_receipt_changed');
    attempt.receipt = receipt;
    if (
      receipt.status === 'completed' &&
      reviewed?.command.command_id === receipt.command_id
    )
      reviewed = null;
    attempt.uncertain = receipt.status !== 'completed' && !receipt.hatch;
    emit({
      result: receipt.hatch ?? state.result,
      pending: admitted(),
      error: '',
    });
  }
  function admitted(): boolean {
    return [...attempts.values()].some(
      (attempt) => attempt.uncertain || running(attempt.receipt?.hatch),
    );
  }
  function makeRoom() {
    if (attempts.size < 32) return;
    const settled = [...attempts].find(
      ([, attempt]) =>
        attempt !== currentAttempt &&
        !attempt.uncertain &&
        attempt.receipt?.status === 'completed' &&
        !running(attempt.receipt.hatch) &&
        attempt.command.command_id !== reviewed?.command.command_id &&
        !(
          attempt.command.type === 'buddy.cancel' &&
          attempt.command.payload.source_command_id ===
            currentAttempt?.command.command_id
        ),
    );
    if (!settled) throw new Error('buddy_session_full');
    attempts.delete(settled[0]);
  }
  function reserve(command: BuddyCommand) {
    if (admitted()) throw new Error('buddy_outcome_uncertain');
    makeRoom();
    const attempt: Attempt = {
      command: structuredClone(command),
      receipt: null,
      uncertain: true,
    };
    attempts.set(command.command_id, attempt);
    currentAttempt = attempt;
    emit({ pending: true });
    return attempt;
  }
  async function dispatch(attempt: Attempt) {
    // This function is called exactly once for each reserved intent. Lost
    // acknowledgements retain the original command and use receipt() only.
    try {
      const receipt = await owned(() =>
        transport.execute(structuredClone(attempt.command)),
      );
      accept(attempt, receipt);
      return receipt;
    } catch (error) {
      if (!state.revoked)
        emit({
          pending: true,
          error:
            'Buddy could not confirm the original command. Refresh its saved outcome before another action.',
        });
      throw error;
    }
  }
  async function exclusive<T>(action: () => Promise<T>): Promise<T> {
    authority();
    if (operation) throw new Error('buddy_busy');
    viewVersion += 1;
    emit({ busy: true });
    const captured = epoch;
    const pending = Promise.resolve().then(action);
    operation = pending;
    try {
      return await pending;
    } finally {
      if (operation === pending) operation = null;
      if (captured === epoch) emit({ busy: false });
    }
  }
  async function load() {
    const snapshot = await owned(() => transport.snapshot());
    const [packs, pack] = await Promise.all([
      owned(() => transport.packs()),
      owned(() => transport.pack(snapshot.preferences.pack_id)),
    ]);
    authority();
    if (pack.id !== snapshot.preferences.pack_id)
      throw new Error('buddy_pack_changed');
    emit({ snapshot, packs, selectedPack: pack, error: '' });
    return snapshot;
  }
  const session = {
    preferencesEditor,
    hatchEditor,
    observe() {
      authority();
      observers += 1;
      if (!timer)
        timer = setInterval(() => {
          if (
            state.revoked ||
            !state.snapshot ||
            state.busy ||
            observation ||
            document.hidden
          )
            return;
          const version = viewVersion;
          const abort = new AbortController();
          observation = abort;
          void owned(() => transport.snapshot(abort.signal))
            .then(async (snapshot) => {
              if (abort.signal.aborted) return;
              let pack = state.selectedPack;
              if (pack?.id !== snapshot.preferences.pack_id)
                pack = await owned(() =>
                  transport.pack(snapshot.preferences.pack_id),
                );
              if (abort.signal.aborted || state.busy || version !== viewVersion)
                return;
              if (pack?.id !== snapshot.preferences.pack_id)
                throw new Error('buddy_pack_changed');
              if (JSON.stringify(snapshot) !== JSON.stringify(state.snapshot))
                emit({ snapshot, selectedPack: pack, statusError: '' });
              else if (state.statusError) emit({ statusError: '' });
            })
            .catch(() => {
              if (!abort.signal.aborted && !state.revoked)
                emit({
                  statusError:
                    'Buddy status could not be refreshed. Reload the saved view.',
                });
            })
            .finally(() => {
              if (observation === abort) observation = null;
            });
        }, 2000);
      let released = false;
      return () => {
        if (released) return;
        released = true;
        observers -= 1;
        if (observers === 0) {
          if (timer) clearInterval(timer);
          timer = null;
          observation?.abort();
        }
      };
    },
    hasRetained: () =>
      state.pending ||
      state.busy ||
      !!reviewed ||
      preferencesEditor.get('dirty', false) ||
      preferencesEditor.get('busy', false) ||
      !!hatchEditor.get('prompt', '') ||
      !!hatchEditor.get('review', null) ||
      hatchEditor.get('busy', false),

    getSnapshot: () => state,
    subscribe(notify: () => void) {
      listeners.add(notify);
      return () => {
        listeners.delete(notify);
      };
    },
    async load() {
      try {
        return await exclusive(load);
      } catch (error) {
        if (!state.revoked)
          emit({
            error: 'Buddy settings could not be read. Retry the saved view.',
          });
        throw error;
      }
    },
    loadPacks: (cursor: string) => owned(() => transport.packs(cursor)),
    async save(changes: Partial<BuddyPreferences>, revision: string) {
      return exclusive(async () => {
        const attempt = reserve({
          command_id: newId(),
          type: 'buddy.update',
          payload: { changes, config_revision: revision },
        });
        const receipt = await dispatch(attempt);
        if (receipt.status !== 'completed')
          throw new Error('buddy_outcome_uncertain');
        return load();
      });
    },
    async review(request: HatchRequest) {
      return exclusive(async () => {
        if (admitted()) throw new Error('buddy_outcome_uncertain');
        reviewed = null;
        const captured = structuredClone(request);
        const review = await owned(() => transport.review(captured));
        if (
          review.action !== captured.action ||
          review.config_revision !== captured.config_revision ||
          !review.review_id
        )
          throw new Error('buddy_review_changed');
        reviewed = {
          request: captured,
          review,
          command: {
            command_id: newId(),
            type: captured.action === 'remove' ? 'buddy.remove' : 'buddy.hatch',
            payload: {
              request: captured,
              review_id: review.review_id,
              ...(review.nonce ? { nonce: review.nonce } : {}),
            },
          },
        };
        return review;
      });
    },
    dismissReview() {
      authority();
      if (operation || admitted()) throw new Error('buddy_busy');
      reviewed = null;
    },
    async confirm(reviewId: string): Promise<HatchResult | HatchRemoval> {
      return exclusive(async () => {
        if (!reviewed || reviewed.review.review_id !== reviewId)
          throw new Error('buddy_review_changed');
        const existing = attempts.get(reviewed.command.command_id);
        const attempt = existing ?? reserve(reviewed.command);
        const receipt = existing
          ? await owned(() => transport.receipt(attempt.command.command_id))
          : await dispatch(attempt);
        accept(attempt, receipt);
        if (receipt.hatch) return receipt.hatch;
        if (receipt.removal) {
          emit({ result: null });
          await load();
          return receipt.removal;
        }
        throw new Error('buddy_outcome_uncertain');
      });
    },
    async refresh(commandId: string) {
      return exclusive(async () => {
        const attempt = attempts.get(commandId);
        if (!attempt) throw new Error('buddy_command_unavailable');
        const receipt = await owned(() => transport.receipt(commandId));
        accept(attempt, receipt);
        if (!receipt.hatch) throw new Error('buddy_outcome_uncertain');
        if (!running(receipt.hatch)) await load();
        return receipt.hatch;
      });
    },
    async recover() {
      return exclusive(async () => {
        const attempt = currentAttempt;
        if (!attempt) return;
        const receipt = await owned(() =>
          transport.receipt(attempt.command.command_id),
        );
        accept(attempt, receipt);
        // An uncertain Stop has its own identity even after generation ends.
        // Reconcile it read-only before allowing the retained session to clear.
        for (const pending of [...attempts.values()]) {
          if (pending.command.type === 'buddy.cancel' && pending.uncertain) {
            accept(
              pending,
              await owned(() => transport.receipt(pending.command.command_id)),
            );
          }
        }
        if (
          receipt.status === 'completed' ||
          (receipt.hatch && !running(receipt.hatch))
        )
          await load();
      });
    },
    async cancel(jobId: string) {
      authority();
      if (cancellation) {
        if (cancellationJob !== jobId) throw new Error('hatch_job_unavailable');
        return cancellation;
      }
      // Stop is independent of slow passive reads and preference operations.
      // Reserve synchronously below; concurrent Stop calls share this promise.
      const pending = Promise.resolve().then(async () => {
        authority();
        const prior = currentAttempt;
        const result = prior?.receipt?.hatch;
        if (!prior || !result || result.job_id !== jobId || !running(result))
          throw new Error('hatch_job_unavailable');
        const previous = [...attempts.values()].find(
          (item) =>
            item.command.type === 'buddy.cancel' &&
            item.command.payload.job_id === jobId,
        );
        if (previous) {
          const receipt =
            previous.receipt?.status === 'completed'
              ? previous.receipt
              : await owned(() =>
                  transport.receipt(previous.command.command_id),
                );
          accept(previous, receipt);
          emit({ pending: admitted() });
          return;
        }
        // Preserve the generation as the recovery target. Stop has its own
        // durable identity and may never be substituted for another worker.
        const command: BuddyCommand = {
          command_id: newId(),
          type: 'buddy.cancel',
          payload: { source_command_id: result.command_id, job_id: jobId },
        };
        makeRoom();
        const attempt: Attempt = { command, receipt: null, uncertain: true };
        attempts.set(command.command_id, attempt);
        await dispatch(attempt);
        emit({ pending: admitted() });
      });
      cancellation = pending;
      cancellationJob = jobId;
      try {
        await pending;
      } finally {
        if (cancellation === pending) {
          cancellation = null;
          cancellationJob = null;
        }
      }
    },
    purge() {
      epoch += 1;
      if (timer) clearInterval(timer);
      timer = null;
      observation?.abort();
      preferencesEditor.dispose();
      hatchEditor.dispose();
      reviewed = null;
      currentAttempt = null;
      cancellation = null;
      cancellationJob = null;
      attempts.clear();
      emit({
        snapshot: null,
        packs: null,
        selectedPack: null,
        result: null,
        pending: false,
        busy: false,
        revoked: true,
        error: '',
        statusError: '',
      });
    },
  };
  return session;
}
export type BuddyPanelSession = ReturnType<typeof createBuddyPanelSession>;
export type BuddyPanelProps = Pick<
  BuddyControlsProps,
  | 'scopeKey'
  | 'currentRunId'
  | 'renderAvatar'
  | 'renderPackPreview'
  | 'previewUrl'
  | 'companionVisible'
  | 'stop'
> & {
  session: BuddyPanelSession;
  settingsOpen: boolean;
  initialPrompt?: string;
  onSettings(): void;
};

export default function BuddyPanel(props: BuddyPanelProps) {
  const state = useSyncExternalStore(
    props.session.subscribe,
    props.session.getSnapshot,
  );
  useEffect(() => {
    if (
      !props.session.getSnapshot().snapshot &&
      !props.session.getSnapshot().revoked
    )
      void props.session.load().catch(() => {});
  }, [props.session]);
  if (state.revoked) return null;
  return (
    <>
      {!state.snapshot && (
        <Button
          disabled={state.busy}
          onClick={() => void props.session.load().catch(() => {})}
        >
          Reload Buddy settings
        </Button>
      )}
      <BuddyControls
        {...props}
        editor={props.session.preferencesEditor}
        snapshot={state.snapshot}
        packs={state.packs}
        save={props.session.save}
        loadPacks={props.session.loadPacks}
      />
      {state.statusError && <p role="status">{state.statusError}</p>}
      {props.settingsOpen && (
        <section aria-label="Buddy appearance" aria-busy={state.busy}>
          <BuddyHatch
            editor={props.session.hatchEditor}
            scopeKey={props.scopeKey}
            configRevision={state.snapshot?.revision ?? null}
            initialPrompt={props.initialPrompt}
            selectedPack={state.selectedPack}
            result={state.result}
            review={props.session.review}
            confirm={props.session.confirm}
            dismissReview={props.session.dismissReview}
            refresh={props.session.refresh}
            cancel={props.session.cancel}
          />
          {state.pending && (
            <Button
              disabled={state.busy}
              onClick={() => void props.session.recover().catch(() => {})}
            >
              Refresh original Buddy command
            </Button>
          )}
          {state.error && (
            <ErrorState title="Buddy needs attention">{state.error}</ErrorState>
          )}
        </section>
      )}
    </>
  );
}
