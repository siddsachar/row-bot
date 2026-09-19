/** Transport entry for the sole packaged realtime arbiter. No UI or state store. */
import {
  startRealtimeRuntime,
  type RealtimeRuntime,
} from '../../../src/row_bot/voice/realtime_runtime.js';
import type { DictationHandle } from './types';

export type RealtimeSnapshot = {
  schema_version: 1;
  handle: DictationHandle;
  state: string;
  quiesced: boolean;
  expires_in_ms: number;
  run_id: string | null;
  transport: 'openai_realtime';
};
export type RealtimeStart = {
  snapshot: RealtimeSnapshot;
  client_secret: string | null;
  secret_expires_in_ms: number;
  exchange_available?: boolean;
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
  'consult_fallback_needed',
  'output_started',
  'output_item_started',
  'response_done',
  'response_cancelled',
  'output_audio_done',
  'barge_in_cancelled',
]);
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
  };
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

async function exchangeSdp(
  sdp: string,
  key: string,
  signal: AbortSignal,
): Promise<string> {
  if (
    typeof sdp !== 'string' ||
    sdp.length > 1_048_576 ||
    !key ||
    key.length > 4096
  )
    throw new Error('invalid_realtime_exchange');
  const response = await fetch('https://api.openai.com/v1/realtime/calls', {
    method: 'POST',
    body: sdp,
    signal,
    credentials: 'omit',
    redirect: 'error',
    cache: 'no-store',
    headers: {
      Authorization: `Bearer ${key}`,
      'Content-Type': 'application/sdp',
    },
  });
  if (response.status === 401 || response.status === 403)
    throw new Error('realtime_auth_unavailable');
  if (response.status === 429) throw new Error('realtime_quota_or_rate_limit');
  if (!response.ok || !response.body)
    throw new Error('realtime_connection_failed');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let text = '';
  let bytes = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      bytes += chunk.value.byteLength;
      if (bytes > 1_048_576) throw new Error('realtime_response_too_large');
      text += decoder.decode(chunk.value, { stream: true });
    }
    return text + decoder.decode();
  } finally {
    await reader.cancel().catch(() => undefined);
  }
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
        origin: output.kind === 'cue' ? 'tool_progress' : 'final',
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

  const managed =
    options.start.exchange_available === true && !!options.exchange;
  if ((!managed && !secret) || options.start.secret_expires_in_ms <= 0) {
    fail('realtime_credential_expired');
    return session;
  }
  await startRealtimeRuntime({
    exchangeManaged: managed,
    sessionId: handle.voice_session_id,
    threadId: handle.conversation_id,
    generationId: generation,
    current: isCurrent,
    onRuntime(value) {
      runtime = value;
      if (!isCurrent()) void value.stop();
    },
    async bootstrap() {
      if (!isCurrent() || (!managed && !secret))
        throw new Error('voice_session_expired');
      const value = secret;
      secret = null;
      return { value };
    },
    exchange: (sdp, key) =>
      managed
        ? options.exchange!(sdp, network.signal)
        : exchangeSdp(sdp, key, network.signal),
    emit(raw) {
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
  });
  return session;
}
