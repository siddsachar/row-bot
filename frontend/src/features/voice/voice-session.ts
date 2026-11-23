/**
 * Row-Bot Voice in the browser: one socket on Row-Bot's own origin carries the
 * person's 16 kHz microphone frames up and the assistant's speech and events
 * down. The host decides turns, barge-in and what to say; this side captures,
 * plays, ducks and flushes on its word, and shows the state. While the person
 * shares their camera or screen, the host asks for a still at the end of each
 * spoken turn and this side sends one.
 */
import { setBuddyListening, setBuddyVoiceLevel } from '../buddy/living/voice';
import type { VoiceSocket } from '../../api/types';
import { BrowserVoiceAudio, type VoiceAudio } from './voice-audio';
import { approvalQuestion } from '../shell/tool-activity';
import type { DeviceNotice } from './devices';
import { VoicePresence } from './presence';
import {
  BrowserShare,
  sendStill,
  VoiceShare,
  type ShareCapture,
  type ShareSource,
  type ShareState,
} from './share';

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

/** What Row-Bot's line was: its answer, or a line it says on its own while working. */
export type VoiceSpeechKind =
  'answer' | 'control' | 'progress' | 'approval' | 'later';

export type VoiceCaption = {
  text: string;
  final: boolean;
  kind?: VoiceSpeechKind;
  /** The language it is in (a code such as "fr"), when known. */
  language?: string;
};

/** The run's waiting approval: its card's title, and `voice` when a spoken yes or no may answer it. */
export type VoiceApproval = { id: string; title: string; voice: boolean };

/** The title the approval card shows ("Move a file?"), from the same fields and the same wording. */
export function cardTitle(card: unknown): string {
  const view = (card ?? {}) as {
    action_label?: string;
    question?: string;
    app?: { name: string; tool?: string };
  };
  return view.question || approvalQuestion(view.action_label ?? '', view.app);
}

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
  approval: VoiceApproval | null;
  error: string;
  /** The language the person was last heard speaking (a code), or null. */
  heard: string | null;
  /** A language Row-Bot's voice can't speak: its answers in it stay on screen. */
  unspoken: string | null;
  /** The camera or screen being shared, or null. */
  share: ShareState | null;
  /**
   * Whether sharing can start: yes; blind (this chat's model can't see
   * images, so the bar says so); no (hidden: still connecting, or a Live
   * voice provider that takes no images).
   */
  canShare: 'yes' | 'blind' | 'no';
  /** Cloud speech parts the person chose that run locally for now: their key is missing. */
  fallbacks: VoiceFallback[];
  /** The chosen microphone or speaker isn't connected, so the default is used (or no microphone is left). */
  device: DeviceNotice | null;
};

/** A chosen cloud service standing in for: "listen" (speech to text) or "speak" (its voice). */
export type VoiceFallback = { part: 'listen' | 'speak'; company: string };

export type VoiceSessionDeps = {
  openSocket(): VoiceSocket;
  /** A fresh single-use ticket (the first comes with the start). */
  ticket(): Promise<string>;
  /** Stop the lease on the host (best effort when the socket is gone). */
  stop(): Promise<unknown>;
  audio?: VoiceAudio;
  share?: ShareCapture;
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
  voice_share_unavailable:
    'This chat’s model can’t see images, so nothing can be shared.',
  voice_still_refused: 'A still of what you share couldn’t be sent.',
  cloud_speech_failed:
    'The speech service didn’t answer, so that wasn’t heard. Check your key in Settings › Voice.',
  cloud_voice_failed:
    'The voice service didn’t answer, so that stays on screen. Check your key in Settings › Voice.',
  cloud_key_missing:
    'A speech service key is missing. Add it in Settings › Voice.',
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

function fallbacksOf(value: unknown): VoiceFallback[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const row = (item ?? {}) as { part?: unknown; company?: unknown };
    return (row.part === 'listen' || row.part === 'speak') &&
      typeof row.company === 'string'
      ? [{ part: row.part, company: row.company }]
      : [];
  });
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
  approval: null,
  error: '',
  heard: null,
  unspoken: null,
  share: null,
  canShare: 'no',
  fallbacks: [],
  device: null,
};

export class VoiceSession {
  private snapshot: VoiceSnapshot = INITIAL;
  private listeners = new Set<() => void>();
  private socket: VoiceSocket | null = null;
  private audio: VoiceAudio;
  private pendingCaptions = new Map<number, VoiceCaption>();
  private reported = new Set<number>();
  private ended = false;
  private reconnected = false;
  private wakeLock: { release(): Promise<void> } | null = null;
  private levelAt = 0;
  private share: VoiceShare;
  private presence: VoicePresence | null = null;

  constructor(
    private deps: VoiceSessionDeps,
    private firstTicket: string,
  ) {
    this.audio = deps.audio ?? new BrowserVoiceAudio();
    this.share = new VoiceShare(deps.share ?? new BrowserShare(), {
      changed: (share, error) => this.set(error ? { share, error } : { share }),
      tell: (source) => this.send({ type: 'share', source }),
    });
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = () => this.snapshot;

  private set(patch: Partial<VoiceSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    setBuddyListening(this.snapshot.orb === 'listening');
    this.presence?.update(this.snapshot);
    this.listeners.forEach((listener) => listener());
  }

  async start() {
    try {
      await this.audio.startCapture({
        frame: (pcm) => this.sendAudio(pcm),
        level: (level) => this.inputLevel(level),
        device: (device) => this.set({ device }),
      });
    } catch (error) {
      this.finish(microphoneError(error));
      return;
    }
    if (this.ended) return;
    this.presence = new VoicePresence({
      mute: (muted) => this.setMuted(muted),
      end: () => void this.end(),
    });
    this.presence.update(this.snapshot);
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
          canShare: event.sees_images === false ? 'blind' : 'yes',
          fallbacks: fallbacksOf(event.fallbacks),
        });
        if (event.sees_images === false) this.share.stop('', false);
        // A reconnected socket is a new host session: it hears the sharing again.
        else if (this.share.state)
          this.send({ type: 'share', source: this.share.state.source });
        break;
      case 'sharing':
        // The host stopped it (a pause): the camera or screen is released here too.
        if (!event.source) this.share.stop('', false);
        break;
      case 'look':
        void this.look();
        break;
      case 'state': {
        const orb = String(event.state) as VoiceOrb;
        this.set({ orb, muted: Boolean(event.muted) });
        if (orb === 'paused') this.audio.pauseCapture();
        break;
      }
      case 'caption':
        if (event.role === 'user') {
          const language =
            typeof event.language === 'string' ? event.language : undefined;
          this.set({
            you: {
              text: String(event.text ?? ''),
              final: Boolean(event.final),
              language,
            },
            ...(language ? { heard: language } : {}),
          });
        } else if (event.spoken === false) {
          // A language the voice can't speak: nothing will play, so the words show now.
          this.set({
            rowBot: {
              text: String(event.text ?? ''),
              final: true,
              kind: (event.kind as VoiceSpeechKind | undefined) ?? 'answer',
            },
          });
        } else if (typeof event.segment === 'number') {
          this.pendingCaptions.set(event.segment, {
            text: String(event.text ?? ''),
            final: true,
            kind: (event.kind as VoiceSpeechKind | undefined) ?? 'answer',
          });
          this.audio.setOutputRate(Number(event.sample_rate) || 24000);
        }
        break;
      case 'unspoken':
        this.set({ unspoken: String(event.language ?? '') || null });
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
      case 'approval': {
        if (!event.approval_id) {
          this.set({ approval: null });
          break;
        }
        const id = String(event.approval_id);
        const title = cardTitle(event.card);
        this.set({ approval: { id, title, voice: event.voice === true } });
        // Row-Bot says the title the card shows, in the same words.
        this.send({ type: 'approval_title', approval_id: id, title });
        break;
      }
      case 'error':
        this.set({ error: voiceError(String(event.code ?? '')) });
        break;
    }
  }

  /** The host needs a still of what is shared, for the turn that just ended. */
  private async look() {
    const data = await this.share.still();
    const socket = this.socket;
    if (data && socket?.readyState === OPEN) sendStill(socket, data);
  }

  startShare(source: ShareSource) {
    if (this.ended || this.snapshot.canShare !== 'yes')
      return Promise.resolve();
    return this.share.start(source);
  }

  stopShare() {
    this.share.stop();
  }

  switchCamera() {
    return this.share.switchCamera();
  }

  private played(segment: number, delayMs: number) {
    const caption = this.pendingCaptions.get(segment);
    if (caption !== undefined) {
      this.pendingCaptions.delete(segment);
      this.set({ rowBot: caption, unspoken: null });
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
    // Ending voice stops sharing: the camera or screen is released at once.
    this.share.stop('', false);
    setBuddyVoiceLevel(0);
    this.presence?.release();
    this.presence = null;
    void this.wakeLock?.release().catch(() => undefined);
    this.wakeLock = null;
    document.removeEventListener('visibilitychange', this.relock);
    this.set({
      orb: 'ended',
      approval: null,
      error: code ? voiceError(code) : '',
    });
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
