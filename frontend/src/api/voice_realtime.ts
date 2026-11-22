/**
 * Transport entry for a Live voice session: OpenAI Realtime (WebRTC, the host
 * exchanges the SDP), Grok Voice (the same event schema over a socket) and
 * Gemini Live (its own runtime). No UI or state store.
 */
import {
  startRealtimeRuntime,
  type RealtimeRuntime,
} from '../../../src/row_bot/voice/realtime_runtime.js';
import type { DictationHandle } from './types';
import { openLiveSocket } from './voice_live_audio';
import {
  GEMINI_INPUT_RATE,
  GEMINI_OUTPUT_RATE,
  geminiAudioFrame,
  startGeminiRuntime,
} from './voice_live_gemini';

export type LiveTransport = 'openai_realtime' | 'gemini_live' | 'grok_voice';
const GROK_RATE = 24_000;

export type RealtimeSnapshot = {
  schema_version: 1;
  handle: DictationHandle;
  state: string;
  quiesced: boolean;
  expires_in_ms: number;
  run_id: string | null;
  transport: LiveTransport;
};
export type RealtimeStart = {
  snapshot: RealtimeSnapshot;
  /** A socket front's ephemeral credential; null for OpenAI, whose SDP the host exchanges. */
  client_secret: string | null;
  secret_expires_in_ms: number;
  exchange_available?: boolean;
  connect_url?: string;
  setup?: string;
};
export type RealtimeEvent = {
  event_id: string;
  type: string;
  generation_id: string;
  response_id: string;
  output_item_id: string;
  item_id: string;
  call_id: string;
  name: string;
  arguments: string;
  text: string;
  origin: '' | 'front' | 'row_bot';
};
export type RealtimeEventResult = {
  snapshot: RealtimeSnapshot;
  event_id: string;
  accepted: boolean;
  caption: string;
  call_id: string;
  function_output: string;
  silent: boolean;
};
export type RealtimeOutput = {
  id: string;
  runId: string;
  text: string;
  kind: 'final' | 'cue' | 'function_output';
  callId?: string;
  silent?: boolean;
};
export type RealtimeSession = {
  stop(): Promise<void>;
  mute(muted: boolean): void;
  sendOutput(output: RealtimeOutput): boolean;
};

const eventTypes = new Set([
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
const origins = new Set(['', 'front', 'row_bot']);
const identifier = /^[A-Za-z0-9:_-]{1,128}$/;

function projectEvent(raw: Record<string, unknown>): RealtimeEvent | null {
  if (typeof raw.type !== 'string' || !eventTypes.has(raw.type)) return null;
  const event: RealtimeEvent = {
    event_id: crypto.randomUUID(),
    type: raw.type,
    generation_id: '',
    response_id: '',
    output_item_id: '',
    item_id: '',
    call_id: '',
    name: '',
    arguments: '',
    text: '',
    origin: '',
  };
  const origin = raw.origin ?? '';
  if (typeof origin !== 'string' || !origins.has(origin))
    throw new Error('invalid_voice_event');
  event.origin = origin as RealtimeEvent['origin'];
  for (const key of [
    'generation_id',
    'response_id',
    'output_item_id',
    'item_id',
    'call_id',
    'name',
  ] as const) {
    const value = raw[key] ?? '';
    if (typeof value !== 'string' || (value !== '' && !identifier.test(value)))
      throw new Error('invalid_voice_event');
    event[key] = value;
  }
  for (const key of ['arguments', 'text'] as const) {
    const value = raw[key] ?? '';
    if (
      typeof value !== 'string' ||
      value.length > (key === 'text' ? 4000 : 8192)
    )
      throw new Error('voice_event_too_large');
    event[key] = value;
  }
  if (new TextEncoder().encode(JSON.stringify(event)).byteLength > 16_384)
    throw new Error('voice_event_too_large');
  return event;
}

export async function openRealtimeSession(options: {
  start: RealtimeStart;
  exchange?(sdp: string, signal: AbortSignal): Promise<string>;
  signal: AbortSignal;
  current(): boolean;
  event(
    event: RealtimeEvent,
    signal: AbortSignal,
  ): Promise<RealtimeEventResult>;
  onSnapshot(snapshot: RealtimeSnapshot): void;
  onCaption(text: string): void;
  onFailure(code: string): void;
  onSession(session: RealtimeSession): void;
}): Promise<RealtimeSession> {
  const handle = options.start.snapshot.handle;
  let secret = options.start.client_secret;
  let generation = options.start.snapshot.run_id || '';
  let runtime: RealtimeRuntime | undefined;
  let stopped = false;
  let draining = false;
  let muted = false;
  const network = new AbortController();
  const pending: RealtimeEvent[] = [];
  const outputs = new Set<string>();
  const isCurrent = () =>
    !stopped && !options.signal.aborted && options.current();
  const session: RealtimeSession = {
    async stop() {
      stopped = true;
      network.abort();
      secret = null;
      pending.length = 0;
      outputs.clear();
      options.signal.removeEventListener('abort', abort);
      await runtime?.stop();
    },
    mute(value) {
      muted = value;
      if (runtime?.session) runtime.session.audio.muted = value;
    },
    sendOutput(output) {
      if (
        !isCurrent() ||
        !runtime ||
        output.runId !== generation ||
        !identifier.test(output.id) ||
        outputs.has(output.id)
      )
        return false;
      if (
        outputs.size >= 200 ||
        output.text.length > 8192 ||
        (output.callId && !identifier.test(output.callId))
      ) {
        fail('voice_output_too_large');
        return false;
      }
      outputs.add(output.id);
      const meta = {
        thread_id: handle.conversation_id,
        generation_id: generation,
        origin: output.kind === 'cue' ? 'status' : 'final',
        silent: Boolean(output.silent),
      };
      if (output.kind === 'function_output')
        return runtime.sendFunctionOutput(
          output.callId || '',
          output.text,
          meta,
        );
      return runtime.sendRunEvent(output.text, meta);
    },
  };
  function fail(code: string) {
    if (!isCurrent()) return;
    options.onFailure(code);
    void session.stop();
  }
  function abort() {
    void session.stop();
  }
  options.signal.addEventListener('abort', abort, { once: true });
  options.onSession(session);

  async function drain() {
    if (draining || !isCurrent()) return;
    draining = true;
    try {
      while (pending.length && isCurrent()) {
        const event = pending.shift()!;
        const reply = await options.event(event, network.signal);
        if (!isCurrent()) return;
        const replyHandle = reply.snapshot.handle;
        if (
          reply.event_id !== event.event_id ||
          replyHandle.lease_id !== handle.lease_id ||
          replyHandle.voice_session_id !== handle.voice_session_id ||
          replyHandle.conversation_id !== handle.conversation_id ||
          replyHandle.server_epoch !== handle.server_epoch
        )
          throw new Error('voice_identity_changed');
        options.onSnapshot(reply.snapshot);
        if (!reply.accepted) continue;
        const next = reply.snapshot.run_id || '';
        if (generation !== next) {
          if (
            !runtime?.setGeneration(
              handle.voice_session_id,
              handle.conversation_id,
              generation,
              next,
            )
          )
            throw new Error('voice_identity_changed');
          generation = next;
        }
        if (reply.caption) options.onCaption(reply.caption);
        if (reply.function_output && reply.call_id)
          runtime?.sendFunctionOutput(reply.call_id, reply.function_output, {
            thread_id: handle.conversation_id,
            generation_id: generation,
            silent: reply.silent,
          });
        if (
          reply.snapshot.state === 'stopped' ||
          reply.snapshot.state === 'stopping'
        ) {
          await session.stop();
          options.onFailure('realtime_disconnected');
          return;
        }
      }
    } catch {
      fail('realtime_event_failed');
    } finally {
      draining = false;
    }
  }

  const transport = options.start.snapshot.transport;
  let setup: unknown = null;
  try {
    setup = options.start.setup ? JSON.parse(options.start.setup) : null;
  } catch {
    setup = null;
  }
  const url = options.start.connect_url || '';
  const ready =
    options.start.secret_expires_in_ms > 0 &&
    (transport === 'openai_realtime'
      ? options.start.exchange_available === true && !!options.exchange
      : !!secret && !!setup && url.startsWith('wss://'));
  if (!ready) {
    fail('realtime_credential_expired');
    return session;
  }
  // A socket front's credential is used once, to open its socket.
  function takeSecret(): string {
    if (!isCurrent() || !secret) throw new Error('voice_session_expired');
    const value = secret;
    secret = null;
    return value;
  }
  const common = {
    sessionId: handle.voice_session_id,
    threadId: handle.conversation_id,
    generationId: generation,
    current: isCurrent,
    onRuntime(value: RealtimeRuntime) {
      runtime = value;
      if (!isCurrent()) void value.stop();
    },
    emit(raw: Record<string, unknown>) {
      if (!isCurrent()) return;
      if (runtime?.session) runtime.session.audio.muted = muted;
      if (raw.type === 'fatal_error' || raw.type === 'disconnected') {
        fail(
          raw.message === 'realtime_auth_unavailable' ||
            raw.message === 'realtime_quota_or_rate_limit'
            ? raw.message
            : 'realtime_disconnected',
        );
        return;
      }
      try {
        const event = projectEvent(raw);
        if (!event) return;
        if (pending.length >= 32) {
          fail('voice_event_limit');
          return;
        }
        pending.push(event);
        void drain();
      } catch {
        fail('invalid_voice_event');
      }
    },
  };
  if (transport === 'gemini_live') {
    await startGeminiRuntime({
      ...common,
      socket: () =>
        openLiveSocket({
          url: `${url}?access_token=${encodeURIComponent(takeSecret())}`,
          setup,
          inputRate: GEMINI_INPUT_RATE,
          outputRate: GEMINI_OUTPUT_RATE,
          frame: geminiAudioFrame,
          ready: (message) => 'setupComplete' in message,
          signal: network.signal,
        }),
    });
  } else if (transport === 'grok_voice') {
    await startRealtimeRuntime({
      ...common,
      bootstrap: async () => ({ value: null }),
      exchange: async () => {
        throw new Error('invalid_realtime_exchange');
      },
      socket: () =>
        openLiveSocket({
          url,
          protocols: [`xai-client-secret.${takeSecret()}`],
          setup,
          inputRate: GROK_RATE,
          outputRate: GROK_RATE,
          frame: (audio) => ({ type: 'input_audio_buffer.append', audio }),
          signal: network.signal,
        }),
    });
  } else {
    await startRealtimeRuntime({
      ...common,
      exchangeManaged: true,
      bootstrap: async () => {
        if (!isCurrent()) throw new Error('voice_session_expired');
        return { value: null };
      },
      exchange: (sdp) => options.exchange!(sdp, network.signal),
    });
  }
  return session;
}
