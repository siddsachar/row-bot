/**
 * Row-Bot Voice in the browser: one socket on Row-Bot's own origin carries the
 * person's 16 kHz microphone frames up and the assistant's speech and events
 * down. The host decides turns, barge-in and what to say; this side captures,
 * plays, ducks and flushes on its word, and shows the state.
 */
import { setBuddyListening, setBuddyVoiceLevel } from '../buddy/living/voice';
import type { VoiceSocket } from '../../api/types';
import { BrowserVoiceAudio, type VoiceAudio } from './voice-audio';

const OPEN = 1;

export type VoiceOrb =
  | 'connecting'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'waiting'
  | 'muted'
  | 'paused'
  | 'ended';

export type VoiceCaption = { text: string; final: boolean };

export type VoiceLatency = {
  last: Record<string, number>;
  summary: {
    turns: number;
    stages: Record<string, { p50: number; p90: number }>;
  };
};

export type VoiceSnapshot = {
  orb: VoiceOrb;
  muted: boolean;
  held: boolean;
  pushToTalk: boolean;
  captionsDefault: boolean;
  you: VoiceCaption | null;
  rowBot: VoiceCaption | null;
  inputLevel: number;
  latency: VoiceLatency | null;
  error: string;
};

export type VoiceSessionDeps = {
  openSocket(): VoiceSocket;
  /** A fresh single-use ticket (the first comes with the start). */
  ticket(): Promise<string>;
  /** Stop the lease on the host (best effort when the socket is gone). */
  stop(): Promise<unknown>;
  audio?: VoiceAudio;
};

const ERRORS: Record<string, string> = {
  voice_policy_changed:
    'The model or approval setting changed. End voice and start it again.',
  revision_conflict:
    'This chat changed. End voice and start it again to keep talking.',
  generation_active: 'Row-Bot is still working on something here.',
  voice_admission_failed: 'Row-Bot could not take that. Try saying it again.',
  microphone: 'Row-Bot can’t use the microphone. Check its permission.',
  no_microphone: 'No microphone was found.',
  microphone_failed: 'The microphone couldn’t start. Try again.',
  disconnected: 'Voice disconnected.',
};

/** Why the microphone could not start, as one of the codes above. */
export function microphoneError(error: unknown) {
  const name = (error as { name?: string })?.name ?? '';
  if (name === 'NotAllowedError' || name === 'SecurityError')
    return 'microphone';
  if (name === 'NotFoundError' || name === 'OverconstrainedError')
    return 'no_microphone';
  return 'microphone_failed';
}

export function voiceError(code: string) {
  return ERRORS[code] ?? 'Voice stopped working. End it and start again.';
}

const INITIAL: VoiceSnapshot = {
  orb: 'connecting',
  muted: false,
  held: false,
  pushToTalk: false,
  captionsDefault: true,
  you: null,
  rowBot: null,
  inputLevel: 0,
  latency: null,
  error: '',
};

export class VoiceSession {
  private snapshot: VoiceSnapshot = INITIAL;
  private listeners = new Set<() => void>();
  private socket: VoiceSocket | null = null;
  private audio: VoiceAudio;
  private pendingCaptions = new Map<number, string>();
  private reported = new Set<number>();
  private ended = false;
  private reconnected = false;
  private wakeLock: { release(): Promise<void> } | null = null;
  private levelAt = 0;

  constructor(
    private deps: VoiceSessionDeps,
    private firstTicket: string,
  ) {
    this.audio = deps.audio ?? new BrowserVoiceAudio();
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = () => this.snapshot;

  private set(patch: Partial<VoiceSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    setBuddyListening(this.snapshot.orb === 'listening');
    this.listeners.forEach((listener) => listener());
  }

  async start() {
    try {
      await this.audio.startCapture({
        frame: (pcm) => this.sendAudio(pcm),
        level: (level) => this.inputLevel(level),
      });
    } catch (error) {
      this.finish(microphoneError(error));
      return;
    }
    this.audio.onPlayback({
      started: (segment, delayMs) => this.played(segment, delayMs),
      idle: () => this.send({ type: 'playback', state: 'idle' }),
      level: (level) => setBuddyVoiceLevel(level),
    });
    this.connect(this.firstTicket);
    void this.holdWakeLock();
  }

  private connect(ticket: string) {
    const socket = this.deps.openSocket();
    this.socket = socket;
    socket.binaryType = 'arraybuffer';
    socket.onopen = () =>
      socket.send(JSON.stringify({ type: 'hello', ticket }));
    socket.onmessage = (message) => this.receive(message.data);
    socket.onclose = () => {
      if (this.socket !== socket || this.ended) return;
      this.socket = null;
      // One quiet retry with a fresh ticket covers a dropped phone connection.
      if (!this.reconnected && this.snapshot.orb !== 'connecting') {
        this.reconnected = true;
        this.audio.flush();
        this.set({ orb: 'connecting' });
        this.deps
          .ticket()
          .then((next) => !this.ended && this.connect(next))
          .catch(() => this.finish('disconnected'));
        return;
      }
      this.finish('disconnected');
    };
  }

  private receive(data: unknown) {
    if (data instanceof ArrayBuffer) {
      if (data.byteLength < 5) return;
      const view = new DataView(data);
      if (view.getUint8(0) !== 1) return;
      const segment = view.getUint32(1, true);
      this.audio.play(segment, new Int16Array(data.slice(5)));
      return;
    }
    if (typeof data !== 'string') return;
    let event: Record<string, unknown>;
    try {
      event = JSON.parse(data);
    } catch {
      return;
    }
    switch (event.type) {
      case 'ready':
        this.audio.setOutputRate(Number(event.output_sample_rate) || 24000);
        this.set({
          pushToTalk: Boolean(event.push_to_talk),
          captionsDefault: event.captions !== false,
        });
        break;
      case 'state': {
        const orb = String(event.state) as VoiceOrb;
        this.set({ orb, muted: Boolean(event.muted) });
        if (orb === 'paused') this.audio.pauseCapture();
        break;
      }
      case 'caption':
        if (event.role === 'user')
          this.set({
            you: {
              text: String(event.text ?? ''),
              final: Boolean(event.final),
            },
          });
        else if (typeof event.segment === 'number') {
          this.pendingCaptions.set(event.segment, String(event.text ?? ''));
          this.audio.setOutputRate(Number(event.sample_rate) || 24000);
        }
        break;
      case 'duck':
        this.audio.duck(Boolean(event.on));
        break;
      case 'flush':
        this.audio.flush();
        this.pendingCaptions.clear();
        break;
      case 'latency':
        this.set({ latency: event as unknown as VoiceLatency });
        break;
      case 'error':
        this.set({ error: voiceError(String(event.code ?? '')) });
        break;
    }
  }

  private played(segment: number, delayMs: number) {
    const text = this.pendingCaptions.get(segment);
    if (text !== undefined) {
      this.pendingCaptions.delete(segment);
      this.set({ rowBot: { text, final: true } });
    }
    if (!this.reported.has(segment)) {
      this.reported.add(segment);
      this.send({
        type: 'playback',
        state: 'playing',
        segment,
        delay_ms: Math.max(0, Math.round(delayMs)),
      });
    }
  }

  private inputLevel(level: number) {
    const now = performance.now();
    // About fifteen updates a second is smooth for a meter, light for React.
    if (now - this.levelAt < 66) return;
    this.levelAt = now;
    this.set({ inputLevel: Math.min(1, level * 4) });
  }

  private sendAudio(pcm: ArrayBuffer) {
    const socket = this.socket;
    if (
      socket?.readyState !== OPEN ||
      this.snapshot.muted ||
      this.snapshot.orb === 'paused' ||
      this.snapshot.orb === 'connecting'
    )
      return;
    // Keep latency low on a slow link: drop audio rather than queue it.
    if (socket.bufferedAmount > 64 * 1024) return;
    socket.send(pcm);
  }

  private send(message: Record<string, unknown>) {
    const socket = this.socket;
    if (socket?.readyState === OPEN) socket.send(JSON.stringify(message));
  }

  setMuted(muted: boolean) {
    this.audio.setMicEnabled(!muted);
    this.set({
      muted,
      orb:
        muted && this.snapshot.orb === 'listening'
          ? 'muted'
          : this.snapshot.orb,
    });
    this.send({ type: 'mute', muted });
  }

  hold(held: boolean) {
    this.set({ held });
    this.send({ type: 'hold', held });
  }

  stopSpeaking() {
    this.send({ type: 'stop_speaking' });
  }

  async resume() {
    try {
      await this.audio.resumeCapture();
    } catch (error) {
      this.finish(microphoneError(error));
      return;
    }
    this.send({ type: 'resume' });
  }

  async end() {
    this.send({ type: 'end' });
    this.finish('');
    await this.stopped;
  }

  private stopped: Promise<unknown> = Promise.resolve();

  private finish(code: string) {
    if (this.ended) return;
    this.ended = true;
    // However it ends, the host's lease goes too, so voice can start again.
    this.stopped = this.deps.stop().catch(() => undefined);
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      socket.onclose = null;
      socket.close();
    }
    this.audio.close();
    setBuddyVoiceLevel(0);
    void this.wakeLock?.release().catch(() => undefined);
    this.wakeLock = null;
    document.removeEventListener('visibilitychange', this.relock);
    this.set({ orb: 'ended', error: code ? voiceError(code) : '' });
    setBuddyListening(false);
  }

  private relock = () => {
    if (document.visibilityState === 'visible' && !this.ended)
      void this.holdWakeLock();
  };

  private async holdWakeLock() {
    // Phones keep the screen on while talking; the lock is released on end.
    const lock = (
      navigator as Navigator & {
        wakeLock?: {
          request(type: 'screen'): Promise<{ release(): Promise<void> }>;
        };
      }
    ).wakeLock;
    if (!lock) return;
    document.addEventListener('visibilitychange', this.relock);
    try {
      this.wakeLock = await lock.request('screen');
    } catch {
      this.wakeLock = null;
    }
  }
}
