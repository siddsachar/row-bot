/**
 * "Hey Row-Bot": the wake word, in the desktop app only, off until the person
 * turns it on in Settings › Voice.
 *
 * While armed, this window captures the microphone with Row-Bot Voice's
 * worklet and streams its 16 kHz frames to the host over the voice socket's
 * wake mode (loopback; the host never opens a microphone). The host runs the
 * small wake word model and sends `wake` when it hears it; nothing is
 * recorded. Then this side lets go of the microphone and starts Row-Bot Voice
 * in the open chat, or in a new one as Home's Talk does. While any voice
 * session runs, the wake word is held: no microphone, no frames.
 */
import { useSyncExternalStore } from 'react';
import type { VoiceSocket } from '../../api/types';
import { deviceChoice, openMicrophone } from './devices';
import { CAPTURE_CONSTRAINTS, captureFrames } from './voice-audio';
import { microphoneError, voiceError } from './voice-session';

const OPEN = 1;
// A dropped socket (the host restarting, say) is tried again after this long.
export const WAKE_RETRY_MS = 10_000;

export type WakeState =
  /** Not listening: off in Settings, or turned off in this window. */
  | 'off'
  | 'starting'
  | 'listening'
  /** A voice session or dictation has the microphone. */
  | 'held'
  /** The system muted the microphone (on a Mac, while the window is hidden). */
  | 'hidden'
  | 'failed';

export type WakeSnapshot = {
  state: WakeState;
  /** Whether the person turned it off in this window (Settings still has it on). */
  paused: boolean;
  error: string;
};

/** The microphone as the wake word uses it: frames out, and whether the system muted it. */
export type WakeMicrophone = {
  close(): void;
  onMuted(change: (muted: boolean) => void): void;
};

export type WakeDeps = {
  ticket(): Promise<string>;
  openSocket(): VoiceSocket;
  microphone?(frame: (pcm: ArrayBuffer) => void): Promise<WakeMicrophone>;
  /** Start Row-Bot Voice; the wake word stays held until this settles. */
  onWake(): Promise<unknown> | void;
  setTimer?(run: () => void, ms: number): () => void;
};

async function browserMicrophone(
  frame: (pcm: ArrayBuffer) => void,
): Promise<WakeMicrophone> {
  const { stream } = await openMicrophone(
    CAPTURE_CONSTRAINTS,
    deviceChoice().input,
  );
  const context = await captureFrames(stream, {
    frame,
    level: () => undefined,
  });
  const track = stream.getAudioTracks()[0];
  return {
    close() {
      stream.getTracks().forEach((item) => item.stop());
      void context.close().catch(() => undefined);
    },
    onMuted(change) {
      if (!track) return;
      track.onmute = () => change(true);
      track.onunmute = () => change(false);
    },
  };
}

function browserTimer(run: () => void, ms: number) {
  const timer = window.setTimeout(run, ms);
  return () => window.clearTimeout(timer);
}

/** One window's wake word listener. */
export class WakeListener {
  private snapshot: WakeSnapshot = { state: 'off', paused: false, error: '' };
  private listeners = new Set<() => void>();
  private wanted = false;
  private holds = 0;
  private socket: VoiceSocket | null = null;
  private mic: WakeMicrophone | null = null;
  private generation = 0;
  private cancelRetry: (() => void) | null = null;

  constructor(private deps: WakeDeps) {}

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = () => this.snapshot;

  private set(patch: Partial<WakeSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach((listener) => listener());
  }

  /** Settings has it on: listen, unless the person turned it off here or voice holds it. */
  arm() {
    if (this.wanted) return;
    this.wanted = true;
    if (!this.snapshot.paused) this.resume();
  }

  /** Settings has it off (or this window is going away): stop and forget the pause. */
  disarm() {
    this.wanted = false;
    this.teardown();
    this.set({ state: 'off', paused: false, error: '' });
  }

  /** The indicator's one click: stop listening in this window. */
  pause() {
    this.teardown();
    this.set({ state: 'off', paused: true, error: '' });
  }

  /** Listen again after a pause or a failure. */
  resume() {
    this.set({ paused: false, error: '' });
    if (!this.wanted) return;
    if (this.holds > 0) this.set({ state: 'held' });
    else void this.open();
  }

  /** A voice session or dictation starts (true) or ends (false). */
  hold(on: boolean) {
    this.holds = Math.max(0, this.holds + (on ? 1 : -1));
    if (on && this.holds === 1) {
      this.teardown();
      if (this.wanted && !this.snapshot.paused) this.set({ state: 'held' });
    } else if (!on && this.holds === 0 && this.wanted && !this.snapshot.paused)
      void this.open();
  }

  private teardown() {
    this.generation++;
    this.cancelRetry?.();
    this.cancelRetry = null;
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      if (socket.readyState === OPEN)
        socket.send(JSON.stringify({ type: 'end' }));
      socket.close();
    }
    this.mic?.close();
    this.mic = null;
  }

  private async open() {
    this.teardown();
    const generation = this.generation;
    const current = () => generation === this.generation;
    this.set({ state: 'starting', error: '' });
    let mic: WakeMicrophone;
    try {
      mic = await (this.deps.microphone ?? browserMicrophone)((pcm) =>
        this.frame(pcm),
      );
    } catch (error) {
      if (current()) this.fail(voiceError(microphoneError(error)), false);
      return;
    }
    if (!current()) {
      mic.close();
      return;
    }
    this.mic = mic;
    mic.onMuted((muted) => {
      if (!current()) return;
      if (muted) this.set({ state: 'hidden' });
      else if (this.snapshot.state === 'hidden')
        this.set({ state: 'listening' });
    });
    let ticket: string;
    try {
      ticket = await this.deps.ticket();
    } catch (error) {
      if (current()) this.fail(wakeError(error), true);
      return;
    }
    if (!current()) return;
    const socket = this.deps.openSocket();
    this.socket = socket;
    socket.binaryType = 'arraybuffer';
    socket.onopen = () =>
      socket.send(JSON.stringify({ type: 'hello', mode: 'wake', ticket }));
    socket.onmessage = (message) => this.receive(message.data);
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.fail(
        'Listening for the wake word stopped. Trying again soon.',
        true,
      );
    };
  }

  private frame(pcm: ArrayBuffer) {
    const socket = this.socket;
    if (
      !socket ||
      socket.readyState !== OPEN ||
      this.snapshot.state !== 'listening'
    )
      return;
    // Never queue: a frame that can't go now is dropped.
    if (socket.bufferedAmount > 64_000) return;
    socket.send(pcm);
  }

  private receive(data: unknown) {
    if (typeof data !== 'string') return;
    let event: { type?: string };
    try {
      event = JSON.parse(data);
    } catch {
      return;
    }
    if (event.type === 'ready') this.set({ state: 'listening' });
    else if (event.type === 'wake' && this.snapshot.state === 'listening') {
      // Voice takes the microphone from here: held while it starts, and by
      // the session itself while it runs.
      this.hold(true);
      void Promise.resolve()
        .then(() => this.deps.onWake())
        .catch(() => undefined)
        .finally(() => this.hold(false));
    }
  }

  private fail(error: string, retry: boolean) {
    this.teardown();
    this.set({ state: 'failed', error });
    if (!retry || !this.wanted) return;
    const generation = this.generation;
    this.cancelRetry = (this.deps.setTimer ?? browserTimer)(() => {
      if (
        generation === this.generation &&
        this.wanted &&
        !this.snapshot.paused
      )
        void this.open();
    }, WAKE_RETRY_MS);
  }
}

function wakeError(error: unknown) {
  const code = (error as { code?: string })?.code ?? '';
  if (code === 'wake_model_missing')
    return 'This Row-Bot doesn’t have the wake word model.';
  return 'Listening for the wake word stopped. Trying again soon.';
}

// The window's one listener, so the indicator and the chats that start voice share it.
let active: WakeListener | null = null;
// Voice sessions open in this window now: a listener made meanwhile starts held.
let holding = 0;
const changes = new Set<() => void>();
const OFF: WakeSnapshot = { state: 'off', paused: false, error: '' };

export function setWakeListener(listener: WakeListener | null) {
  active = listener;
  for (let index = 0; index < holding; index++) listener?.hold(true);
  changes.forEach((change) => change());
}

/** The window's wake word state, and its indicator's actions (null when there is none). */
export function useWakeWord(): {
  snapshot: WakeSnapshot;
  listener: WakeListener | null;
} {
  const listener = useSyncExternalStore(
    (change) => {
      changes.add(change);
      return () => changes.delete(change);
    },
    () => active,
  );
  const snapshot = useSyncExternalStore(
    (change) => listener?.subscribe(change) ?? (() => undefined),
    () => listener?.getSnapshot() ?? OFF,
  );
  return { snapshot, listener };
}

/** A voice session or dictation in this window took (true) or gave back (false) the microphone. */
export function holdWakeWord(on: boolean) {
  holding = Math.max(0, holding + (on ? 1 : -1));
  active?.hold(on);
}

// The open chat's "start Row-Bot Voice here", while one is showing.
type WakeTarget = { conversationId: string; start(): Promise<unknown> };
let target: WakeTarget | null = null;

export function setWakeTarget(next: WakeTarget) {
  target = next;
  return () => {
    if (target === next) target = null;
  };
}

/** Start voice in the open chat; null when no chat is showing. */
export function wakeOpenChat(): Promise<unknown> | null {
  return target ? target.start() : null;
}

// The saved switch (Settings › Voice › Wake word), as this window last read it.
let enabled = false;
const enabledChanges = new Set<() => void>();

export function announceWakeWord(on: boolean) {
  if (on === enabled) return;
  enabled = on;
  enabledChanges.forEach((change) => change());
}

export function useWakeWordEnabled(): boolean {
  return useSyncExternalStore(
    (change) => {
      enabledChanges.add(change);
      return () => enabledChanges.delete(change);
    },
    () => enabled,
  );
}
