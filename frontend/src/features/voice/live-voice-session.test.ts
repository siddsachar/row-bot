import { afterEach, expect, it, vi } from 'vitest';
import type {
  RealtimeOptions,
  RealtimeRuntime,
} from '../../../../src/row_bot/voice/realtime_runtime.js';
import type { VoiceSocket } from '../../api/types';
import { LiveVoiceSession, projectEvent } from './live-voice-session';
import { FakeAudio, FakeSocket, audioFrame } from './voice-test-fakes';

const TICKET = 'ticket-xxxxxxxxxxxxxxxxxxxxxxxxxxxx';

function fakeRuntime() {
  const runtime = {
    session: null,
    stop: vi.fn(async () => undefined),
    sendFunctionOutput: vi.fn(() => true),
    sendRunEvent: vi.fn(() => true),
    clearGeneration: vi.fn(() => true),
    setGeneration: vi.fn(() => true),
    cancelActiveOutput: vi.fn(() => true),
  } satisfies RealtimeRuntime;
  let options!: RealtimeOptions;
  const start = vi.fn(async (given: RealtimeOptions) => {
    options = given;
    given.onRuntime?.(runtime);
    return runtime;
  });
  return { runtime, start, options: () => options };
}

function session(transport: 'openai_realtime' | 'gemini_live') {
  const sockets: FakeSocket[] = [];
  const audio = new FakeAudio();
  const runtime = fakeRuntime();
  const deps = {
    transport,
    sessionId: 3,
    conversationId: 'A',
    openSocket: () => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket as unknown as VoiceSocket;
    },
    ticket: vi.fn(async () => TICKET),
    stop: vi.fn(async () => undefined),
    exchange: vi.fn(async () => 'v=0 host answer'),
    audio,
    startRuntime: runtime.start as never,
  };
  return {
    live: new LiveVoiceSession(deps, TICKET, null),
    sockets,
    audio,
    runtime,
    deps,
  };
}

afterEach(() => vi.restoreAllMocks());

it('OpenAI: the call starts once the socket is ready, its events go up the socket and the host’s words come back to it', async () => {
  const { live, sockets, runtime, deps } = session('openai_realtime');
  const started = live.start();
  sockets[0].open();
  expect(sockets[0].json()[0]).toEqual({ type: 'hello', ticket: TICKET });
  expect(runtime.start).not.toHaveBeenCalled();
  sockets[0].emit({ type: 'ready', output_sample_rate: 24000 });
  await started;
  const options = runtime.options();
  expect(options.exchangeManaged).toBe(true);
  await options.exchange('v=0 offer', '');
  expect(deps.exchange).toHaveBeenCalledWith('v=0 offer');
  options.emit({
    type: 'transcript_final',
    text: 'Hello',
    item_id: 'u1',
    private_raw: 'never sent',
  });
  const event = sockets[0].json().at(-1)!;
  expect(event).toMatchObject({
    type: 'event',
    event: { type: 'transcript_final', text: 'Hello', item_id: 'u1' },
  });
  expect(JSON.stringify(event)).not.toContain('never sent');
  // An event's result moves the call to the admitted run and answers its call.
  sockets[0].emit({
    type: 'event_result',
    run_id: 'run-1',
    call_id: 'wait',
    function_output: '{"status":"waiting"}',
    silent: true,
  });
  expect(runtime.runtime.setGeneration).toHaveBeenCalledWith(
    3,
    'A',
    '',
    'run-1',
  );
  expect(runtime.runtime.sendFunctionOutput).toHaveBeenCalledWith(
    'wait',
    '{"status":"waiting"}',
    { thread_id: 'A', generation_id: 'run-1', silent: true },
  );
  sockets[0].emit({
    type: 'say',
    kind: 'status',
    text: 'Pass on this status from Row-Bot in a few words: Working on: web search.',
  });
  expect(runtime.runtime.sendRunEvent).toHaveBeenCalledWith(
    'Pass on this status from Row-Bot in a few words: Working on: web search.',
    { thread_id: 'A', generation_id: 'run-1', origin: 'status' },
  );
  sockets[0].emit({
    type: 'say',
    kind: 'function_output',
    call_id: 'consult-1',
    text: '{"status":"completed","answer":"Sunny."}',
  });
  expect(runtime.runtime.sendFunctionOutput).toHaveBeenLastCalledWith(
    'consult-1',
    '{"status":"completed","answer":"Sunny."}',
    { thread_id: 'A', generation_id: 'run-1' },
  );
  sockets[0].emit({ type: 'state', state: 'speaking', muted: false });
  expect(live.getSnapshot().orb).toBe('speaking');
  await live.end();
  expect(sockets[0].json().at(-1)).toEqual({ type: 'end' });
  expect(deps.stop).toHaveBeenCalled();
  expect(runtime.runtime.stop).toHaveBeenCalled();
});

it('a relayed front: microphone frames go up the socket, its speech plays and a flush stops it', async () => {
  const { live, sockets, audio, runtime } = session('gemini_live');
  const started = live.start();
  await Promise.resolve();
  sockets[0].open();
  sockets[0].emit({ type: 'ready', output_sample_rate: 24000 });
  await started;
  expect(runtime.start).not.toHaveBeenCalled();
  audio.capture!.frame(new ArrayBuffer(640));
  expect(sockets[0].sent.at(-1)).toBeInstanceOf(ArrayBuffer);
  sockets[0].emit(audioFrame(2));
  expect(audio.played).toEqual([2]);
  sockets[0].emit({ type: 'flush' });
  expect(audio.flush).toHaveBeenCalled();
  live.setMuted(true);
  const sent = sockets[0].sent.length;
  audio.capture!.frame(new ArrayBuffer(640));
  expect(sockets[0].sent.length).toBe(sent);
  expect(sockets[0].json().at(-1)).toEqual({ type: 'mute', muted: true });
  live.stopSpeaking();
  expect(sockets[0].json().at(-1)).toEqual({ type: 'stop_speaking' });
  sockets[0].emit({ type: 'error', code: 'realtime_auth_unavailable' });
  expect(live.getSnapshot()).toMatchObject({
    orb: 'ended',
    error: 'The provider refused its key. Check it in Providers.',
  });
});

it('projects only whitelisted fields and refuses malformed ones', () => {
  expect(projectEvent({ type: 'server_event' })).toBeNull();
  expect(() =>
    projectEvent({ type: 'function_call_ready', call_id: 'bad id!' }),
  ).toThrow();
  expect(() =>
    projectEvent({ type: 'transcript_final', text: 'x'.repeat(4001) }),
  ).toThrow();
});
