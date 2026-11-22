/**
 * Live voice in the browser, shown in the same voice bar as Row-Bot Voice.
 *
 * The page talks only to Row-Bot: one socket on its own origin (opened with a
 * single-use ticket) carries the session's events and what Row-Bot asks the
 * front to say. Gemini Live and Grok Voice are relayed by the host, so their
 * audio rides that socket too (16 kHz microphone frames up, 24 kHz speech
 * down, through the same capture and playback as Row-Bot Voice). OpenAI
 * Realtime keeps its WebRTC call for the media (the host exchanged its SDP);
 * the call's events go up the socket.
 */
import { setBuddyListening, setBuddyVoiceLevel } from '../buddy/living/voice';
import {
  startRealtimeRuntime,
  type RealtimeRuntime,
} from '../../../../src/row_bot/voice/realtime_runtime.js';
import { BrowserVoiceAudio, type VoiceAudio } from './voice-audio';
import {
  microphoneError,
  voiceError,
  type VoiceOrb,
  type VoiceSnapshot,
} from './voice-session';

const OPEN = 1;

export type LiveTransport = 'openai_realtime' | 'gemini_live' | 'grok_voice';

export type LiveVoiceDeps = {
  transport: LiveTransport;
  /** The lease's identity: the socket's events belong to its session and chat. */
  sessionId: number;
  conversationId: string;
  openSocket(): WebSocket;
  /** A fresh single-use ticket (the first comes with the start). */
  ticket(): Promise<string>;
  /** Stop the lease on the host (best effort when the socket is gone). */
  stop(): Promise<unknown>;
  /** OpenAI: the host exchanges the call's SDP. */
  exchange(sdp: string): Promise<string>;
  audio?: VoiceAudio;
  startRuntime?: typeof startRealtimeRuntime;
};

const EVENTS = new Set([
  'connected',
  'disconnected',
  'fatal_error',
  'speech_started',
  'speech_stopped',
  'transcript_final',
  'assistant_transcript_final',
  'function_call_ready',
  'output_started',
  'output_item_started',
  'response_done',
  'response_cancelled',
  'output_audio_done',
  'barge_in_cancelled',
  'provider_response_create_sent',
]);
const IDENTIFIER = /^[A-Za-z0-9:_-]{1,128}$/;
const ORIGINS = new Set(['', 'front', 'row_bot']);

const ERRORS: Record<string, string> = {
  realtime_auth_unavailable:
    'The provider refused its key. Check it in Providers.',
  realtime_quota_or_rate_limit:
    'The provider’s quota or rate limit was reached. Try again later.',
  realtime_provider_unavailable:
    'The provider isn’t responding. Try again in a moment.',
  realtime_disconnected: 'Live voice disconnected.',
};

/** Only the whitelisted fields of one of the call's events, checked. */
export function projectEvent(
  raw: Record<string, unknown>,
): Record<string, string> | null {
  if (typeof raw.type !== 'string' || !EVENTS.has(raw.type)) return null;
  const event: Record<string, string> = {
    event_id: crypto.randomUUID(),
    type: raw.type,
  };
  for (const key of [
    'generation_id',
    'response_id',
    'output_item_id',
    'item_id',
    'call_id',
    'name',
  ]) {
    const value = raw[key] ?? '';
    if (typeof value !== 'string' || (value && !IDENTIFIER.test(value)))
      throw new Error('invalid_voice_event');
    if (value) event[key] = value;
  }
  for (const [key, limit] of [
    ['arguments', 8192],
    ['text', 4000],
  ] as const) {
    const value = raw[key] ?? '';
    if (typeof value !== 'string' || value.length > limit)
      throw new Error('voice_event_too_large');
    if (value) event[key] = value;
  }
  const origin = raw.origin ?? '';
  if (typeof origin !== 'string' || !ORIGINS.has(origin))
    throw new Error('invalid_voice_event');
  if (origin) event.origin = origin;
  return event;
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

export class LiveVoiceSession {
  private snapshot: VoiceSnapshot = INITIAL;
  private listeners = new Set<() => void>();
  private socket: WebSocket | null = null;
  private audio: VoiceAudio | null = null;
  private runtime: RealtimeRuntime | null = null;
  private generation = '';
  private ended = false;
  private reconnected = false;
  private ready: (() => void) | null = null;
  private levelAt = 0;
  private stopped: Promise<unknown> = Promise.resolve();

  constructor(
    private deps: LiveVoiceDeps,
    private firstTicket: string,
    generation: string | null,
  ) {
    this.generation = generation ?? '';
  }

  private get relayed() {
    return this.deps.transport !== 'openai_realtime';
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
    if (this.relayed) {
      const audio = this.deps.audio ?? new BrowserVoiceAudio();
      this.audio = audio;
      try {
        await audio.startCapture({
          frame: (pcm) => this.sendAudio(pcm),
          level: (level) => this.inputLevel(level),
        });
      } catch (error) {
        this.finish(microphoneError(error));
        return;
      }
      audio.onPlayback({
        started: () => undefined,
        idle: () => undefined,
        level: (level) => setBuddyVoiceLevel(level),
      });
    }
    const opened = new Promise<void>((resolve) => (this.ready = resolve));
    this.connect(this.firstTicket);
    await opened;
    if (!this.ended && !this.relayed) await this.call();
  }

  /** OpenAI: the WebRTC call, its SDP exchanged by the host and its events sent up the socket. */
  private async call() {
    const start = this.deps.startRuntime ?? startRealtimeRuntime;
    await start({
      exchangeManaged: true,
      sessionId: this.deps.sessionId,
      threadId: this.deps.conversationId,
      generationId: this.generation,
      current: () => !this.ended,
      onRuntime: (runtime) => {
        this.runtime = runtime;
      },
      bootstrap: async () => ({ value: null }),
      exchange: (sdp) => this.deps.exchange(sdp),
      emit: (raw) => {
        if (this.ended) return;
        if (raw.type === 'fatal_error' || raw.type === 'disconnected') {
          this.finish('realtime_disconnected');
          return;
        }
        try {
          const event = projectEvent(raw);
          if (event) this.send({ type: 'event', event });
        } catch {
          this.finish('realtime_disconnected');
        }
      },
    });
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
        this.audio?.flush();
        this.set({ orb: 'connecting' });
        this.deps
          .ticket()
          .then((next) => !this.ended && this.connect(next))
          .catch(() => this.finish('realtime_disconnected'));
        return;
      }
      this.finish('realtime_disconnected');
    };
  }

  private receive(data: unknown) {
    if (data instanceof ArrayBuffer) {
      if (data.byteLength < 5 || !this.audio) return;
      const view = new DataView(data);
      if (view.getUint8(0) !== 1) return;
      this.audio.play(view.getUint32(1, true), new Int16Array(data.slice(5)));
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
        this.audio?.setOutputRate(Number(event.output_sample_rate) || 24000);
        this.set({ orb: 'listening' });
        this.ready?.();
        this.ready = null;
        break;
      case 'state':
        this.set({
          orb: String(event.state) as VoiceOrb,
          muted: Boolean(event.muted),
        });
        break;
      case 'caption':
        this.set(
          event.role === 'user'
            ? { you: { text: String(event.text ?? ''), final: true } }
            : { rowBot: { text: String(event.text ?? ''), final: true } },
        );
        break;
      case 'flush':
        this.audio?.flush();
        break;
      case 'say':
        this.say(event);
        break;
      case 'event_result':
        this.result(event);
        break;
      case 'error':
        this.finish(String(event.code ?? ''));
        break;
    }
  }

  private meta(extra: Record<string, unknown> = {}) {
    return {
      thread_id: this.deps.conversationId,
      generation_id: this.generation,
      ...extra,
    };
  }

  /** OpenAI: what Row-Bot asks the front to say, worded by the host. */
  private say(event: Record<string, unknown>) {
    const runtime = this.runtime;
    const text = String(event.text ?? '');
    if (!runtime || !text || text.length > 8192) return;
    if (event.kind === 'function_output') {
      const call = String(event.call_id ?? '');
      if (IDENTIFIER.test(call))
        runtime.sendFunctionOutput(call, text, this.meta());
    } else
      runtime.sendRunEvent(
        text,
        this.meta({ origin: event.kind === 'final' ? 'final' : 'status' }),
      );
  }

  private result(event: Record<string, unknown>) {
    const runtime = this.runtime;
    if (!runtime) return;
    const next = String(event.run_id ?? '');
    if (next && next !== this.generation && next.length <= 128) {
      runtime.setGeneration(
        this.deps.sessionId,
        this.deps.conversationId,
        this.generation,
        next,
      );
      this.generation = next;
    }
    const call = String(event.call_id ?? '');
    const output = String(event.function_output ?? '');
    if (call && output && IDENTIFIER.test(call) && output.length <= 8192)
      runtime.sendFunctionOutput(
        call,
        output,
        this.meta({ silent: Boolean(event.silent) }),
      );
  }

  private inputLevel(level: number) {
    const now = performance.now();
    if (now - this.levelAt < 66) return;
    this.levelAt = now;
    this.set({ inputLevel: Math.min(1, level * 4) });
  }

  private sendAudio(pcm: ArrayBuffer) {
    const socket = this.socket;
    if (
      socket?.readyState !== OPEN ||
      this.snapshot.muted ||
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
    this.audio?.setMicEnabled(!muted);
    const call = this.runtime?.session;
    call?.stream?.getAudioTracks().forEach((track) => (track.enabled = !muted));
    this.set({
      muted,
      orb:
        muted && this.snapshot.orb === 'listening'
          ? 'muted'
          : this.snapshot.orb,
    });
    this.send({ type: 'mute', muted });
  }

  /** Live voice is always hands-free: the provider decides when a turn ends. */
  hold() {}

  stopSpeaking() {
    if (this.runtime) this.runtime.cancelActiveOutput('stop_speaking');
    else this.send({ type: 'stop_speaking' });
  }

  async resume() {}

  async end() {
    this.send({ type: 'end' });
    this.finish('');
    await this.stopped;
  }

  private finish(code: string) {
    if (this.ended) return;
    this.ended = true;
    this.ready?.();
    this.ready = null;
    // However it ends, the host's lease goes too, so voice can start again.
    this.stopped = this.deps.stop().catch(() => undefined);
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      socket.onclose = null;
      socket.close();
    }
    this.audio?.close();
    void this.runtime?.stop();
    setBuddyVoiceLevel(0);
    this.set({
      orb: 'ended',
      error: code ? (ERRORS[code] ?? voiceError(code)) : '',
    });
    setBuddyListening(false);
  }
}
