import { useEffect, useState, useSyncExternalStore } from 'react';
import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import { fallbackDue, type LiveUpdates } from '../../api/live-updates';
import type {
  ProactiveActivityPage,
  ProactiveBoundaryNoted,
  ProactiveDraftEdit,
  ProactiveDraftPage,
  ProactiveDraftSendAnswer,
  ProactiveDraftSendOutcome,
  ProactiveDraftView,
  ProactivePreview,
  ProactiveFeedback,
  ProactiveMomentDetail,
  ProactiveMomentPage,
  ProactiveMomentView,
  ProactiveRuleWrite,
  ProactiveSettingsChange,
  ProactiveSettingsView,
  TaskDeliveryChannel,
} from '../../api/types';

/**
 * What the Proactive surfaces read and do: one call each to the server's
 * `/proactive` routes (see `.local/proactive-api.md`). Tests pass their own.
 */
export type ProactiveIO = {
  moments(signal?: AbortSignal): Promise<ProactiveMomentPage>;
  moment(
    momentId: string,
    signal?: AbortSignal,
  ): Promise<ProactiveMomentDetail>;
  feedback(
    momentId: string,
    feedback: ProactiveFeedback,
  ): Promise<ProactiveMomentDetail>;
  activity(before?: number, limit?: number): Promise<ProactiveActivityPage>;
  settings(signal?: AbortSignal): Promise<ProactiveSettingsView>;
  changeSettings(
    change: ProactiveSettingsChange,
  ): Promise<ProactiveSettingsView>;
  addRule(rule: ProactiveRuleWrite): Promise<ProactiveSettingsView>;
  changeRule(
    ruleId: string,
    rule: ProactiveRuleWrite,
  ): Promise<ProactiveSettingsView>;
  deleteRule(ruleId: string): Promise<ProactiveSettingsView>;
  drafts(signal?: AbortSignal): Promise<ProactiveDraftPage>;
  draft(draftId: string, signal?: AbortSignal): Promise<ProactiveDraftView>;
  /** The person's edits to a drafted reply, saved in Row-Bot (never to the mail account). */
  saveDraft(
    draftId: string,
    edit: ProactiveDraftEdit,
  ): Promise<ProactiveDraftView>;
  /** Shows the draft's approval card (always, whatever the approval level); sends nothing. */
  askToSend(draftId: string): Promise<ProactiveDraftView>;
  /** The person's answer on that card: only an approval of exactly it sends. */
  answerSend(
    draftId: string,
    answer: ProactiveDraftSendAnswer,
  ): Promise<ProactiveDraftSendOutcome>;
  /** What each level would have brought up today. */
  preview(signal?: AbortSignal): Promise<ProactivePreview>;
  boundary(): Promise<ProactiveBoundaryNoted>;
  /** Chat apps set up to deliver (Telegram, Slack…): where urgent kinds may go. */
  channels?(signal?: AbortSignal): Promise<TaskDeliveryChannel[]>;
  /** The app-wide live stream, when there is one (topic `proactive`). */
  live?: Pick<LiveUpdates, 'subscribe' | 'connected'>;
};

const ios = new WeakMap<ClientController, ProactiveIO>();

/** The controller's Proactive calls, one object per controller. */
export function proactiveIO(controller: ClientController): ProactiveIO {
  let io = ios.get(controller);
  if (!io) {
    io = {
      moments: (signal) => controller.proactiveMoments(signal),
      moment: (id, signal) => controller.proactiveMoment(id, signal),
      feedback: (id, feedback) => controller.proactiveFeedback(id, feedback),
      activity: (before, limit) => controller.proactiveActivity(before, limit),
      settings: (signal) => controller.proactiveSettings(signal),
      changeSettings: (change) => controller.changeProactiveSettings(change),
      addRule: (rule) => controller.addProactiveRule(rule),
      changeRule: (id, rule) => controller.changeProactiveRule(id, rule),
      deleteRule: (id) => controller.deleteProactiveRule(id),
      drafts: (signal) => controller.proactiveDrafts(signal),
      draft: (id, signal) => controller.proactiveDraft(id, signal),
      saveDraft: (id, edit) => controller.saveProactiveDraft(id, edit),
      askToSend: (id) => controller.askToSendProactiveDraft(id),
      answerSend: (id, answer) =>
        controller.answerProactiveDraftSend(id, answer),
      preview: (signal) => controller.proactivePreview(signal),
      boundary: () => controller.noteProactiveBoundary({ kind: 'app_open' }),
      channels: async (signal) =>
        (await controller.taskDeliveryDefaults(signal)).channels,
      live: controller.live,
    };
    ios.set(controller, io);
  }
  return io;
}

/**
 * This window has opened: the server may count it as a break (the first one
 * of the day brings the morning brief). Sent once, after the handshake.
 */
export function noteAppOpen(controller: ClientController): () => void {
  const send = () => {
    if (!controller.getSnapshot().handshake) return false;
    void controller
      .noteProactiveBoundary({ kind: 'app_open' })
      .catch(() => undefined);
    return true;
  };
  if (send()) return () => {};
  const unsubscribe = controller.subscribe(() => {
    if (send()) unsubscribe();
  });
  return unsubscribe;
}

export type ProactiveView = {
  /** For you: open moments, urgent first (null until read). */
  moments: ProactiveMomentView[] | null;
  settings: ProactiveSettingsView | null;
  /** Read only once a surface asks for drafts. */
  drafts: ProactiveDraftView[] | null;
  /** A read failed, in words. */
  error: string;
  /** The server has no Proactive routes (an older server): nothing shows. */
  unavailable: boolean;
};

const EMPTY: ProactiveView = {
  moments: null,
  settings: null,
  drafts: null,
  error: '',
  unavailable: false,
};

const UNAVAILABLE = new Set([
  'capability_unavailable',
  'not_found',
  'unsupported_command',
]);

/** One shared reading of Proactive state per IO; every surface shows the same. */
export class ProactiveStore {
  private view: ProactiveView = EMPTY;
  private listeners = new Set<() => void>();
  private wantsDrafts = false;
  private started = false;
  private readAt = 0;
  private stopLive: (() => void) | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private reading: Promise<void> | null = null;
  private again = false;

  constructor(readonly io: ProactiveIO) {}

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    this.start();
    return () => {
      this.listeners.delete(listener);
      if (!this.listeners.size) this.stop();
    };
  };

  getView = () => this.view;

  private set(next: Partial<ProactiveView>) {
    this.view = { ...this.view, ...next };
    this.listeners.forEach((listener) => listener());
  }

  private start() {
    if (this.started) return;
    this.started = true;
    this.stopLive = this.io.live?.subscribe('proactive', this.changed) ?? null;
    // A minute's fallback for when the live stream is down; it also lets an
    // expired moment leave without a server change.
    this.timer = setInterval(() => {
      if (fallbackDue(this.io.live, this.readAt)) void this.reload();
      else this.listeners.forEach((listener) => listener());
    }, 60_000);
    void this.reload();
  }

  private stop() {
    this.started = false;
    this.stopLive?.();
    this.stopLive = null;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** The server says something changed: read again. */
  changed = () => {
    void this.reload();
  };

  /** Read moments and settings (and drafts once asked for), one read at a time. */
  reload = (): Promise<void> => {
    if (this.reading) {
      this.again = true;
      return this.reading;
    }
    this.reading = this.read().finally(() => {
      this.reading = null;
      if (this.again) {
        this.again = false;
        void this.reload();
      }
    });
    return this.reading;
  };

  private async read() {
    this.readAt = Date.now();
    try {
      const [page, settings, drafts] = await Promise.all([
        this.io.moments(),
        this.io.settings(),
        this.wantsDrafts ? this.io.drafts() : Promise.resolve(null),
      ]);
      this.set({
        moments: page.items,
        settings,
        drafts: drafts ? drafts.items : this.view.drafts,
        error: '',
        unavailable: false,
      });
    } catch (cause) {
      const error = clientError(cause);
      if (UNAVAILABLE.has(error.code))
        this.set({ unavailable: true, error: '' });
      else this.set({ error: error.message });
    }
  }

  /** A surface shows drafts: read them now and with every change. */
  watchDrafts() {
    if (this.wantsDrafts) return;
    this.wantsDrafts = true;
    void this.reload();
  }

  /** Feedback on a moment; the list drops it at once when it settled. */
  async feedback(momentId: string, feedback: ProactiveFeedback) {
    try {
      const detail = await this.io.feedback(momentId, feedback);
      this.settle(detail.moment);
      void this.reload();
      return detail;
    } catch (cause) {
      // Settled elsewhere (another window, the phone): read where things stand.
      if (clientError(cause).code === 'revision_conflict') void this.reload();
      throw cause;
    }
  }

  private settle(moment: ProactiveMomentView) {
    const open = moment.state === 'pending' || moment.state === 'shown';
    const moments = this.view.moments ?? [];
    this.set({
      moments: open
        ? moments.map((item) =>
            item.moment_id === moment.moment_id ? moment : item,
          )
        : moments.filter((item) => item.moment_id !== moment.moment_id),
    });
  }

  /** A draft as the server last answered (saved, asked to send, sent): every surface shows it. */
  draftChanged(draft: ProactiveDraftView) {
    const drafts = this.view.drafts;
    if (!drafts) return;
    this.set({
      drafts: drafts.map((item) =>
        item.draft_id === draft.draft_id ? draft : item,
      ),
    });
  }

  /** A settings change, a rule written or removed: the page shows the server's answer. */
  async settingsChanged(
    change: () => Promise<ProactiveSettingsView>,
  ): Promise<ProactiveSettingsView> {
    const settings = await change();
    this.set({ settings });
    return settings;
  }
}

const stores = new WeakMap<ProactiveIO, ProactiveStore>();

export function proactiveStore(io: ProactiveIO): ProactiveStore {
  let store = stores.get(io);
  if (!store) {
    store = new ProactiveStore(io);
    stores.set(io, store);
  }
  return store;
}

let provided: ProactiveIO | null = null;

/** This window's Proactive IO (like `provideLive`): its controller's calls. */
export function provideProactive(io: ProactiveIO | null): void {
  provided = io;
}

/** The app's Proactive IO, or null outside the app (a component on its own). */
export function useProactiveIO(): ProactiveIO | null {
  return provided;
}

const NOTHING = () => () => {};
const NO_VIEW = () => EMPTY;

/** Proactive's shared state for `io` (the app's own by default). */
export function useProactive(given?: ProactiveIO | null): {
  store: ProactiveStore | null;
  view: ProactiveView;
} {
  const fallback = useProactiveIO();
  const io = given === undefined ? fallback : given;
  const store = io ? proactiveStore(io) : null;
  const view = useSyncExternalStore(
    store ? store.subscribe : NOTHING,
    store ? store.getView : NO_VIEW,
  );
  return { store, view };
}

/**
 * Proactive is on (a level other than Off, on a server that has it): Home
 * shows For you in place of Today. Off, or before settings are read, Today
 * stays exactly as it was.
 */
export function useProactiveOn(): boolean {
  const { view } = useProactive();
  return Boolean(
    !view.unavailable && view.settings && view.settings.level !== 'off',
  );
}

/** The time now, moving on every minute: expired moments leave quietly. */
export function useMinute(given?: Date): Date {
  const [now, setNow] = useState(() => new Date());
  const fixed = Boolean(given);
  useEffect(() => {
    if (fixed) return;
    const timer = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(timer);
  }, [fixed]);
  return given ?? now;
}
