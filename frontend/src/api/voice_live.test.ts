import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import {
  openRealtimeSession,
  type RealtimeEvent,
  type RealtimeSession,
  type RealtimeStart,
} from './voice_realtime';
import { decodePcm16, encodePcm16 } from './voice_live_audio';

class FakeSocket {
  static OPEN = 1;
  static instances: FakeSocket[] = [];
  readyState = 0;
  binaryType = 'blob';
  sent: unknown[] = [];
  listeners = new Map<string, ((event: unknown) => void)[]>();
  constructor(
    public url: string,
    public protocols?: string[],
  ) {
    FakeSocket.instances.push(this);
  }
  addEventListener(type: string, listener: (event: unknown) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  removeEventListener(type: string, listener: (event: unknown) => void) {
    this.listeners.set(
      type,
      (this.listeners.get(type) ?? []).filter((item) => item !== listener),
    );
  }
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    for (const listener of this.listeners.get('open') ?? []) listener({});
  }
  receive(payload: unknown, binary = false) {
    const text = JSON.stringify(payload);
    const data = binary ? new TextEncoder().encode(text).buffer : text;
    for (const listener of this.listeners.get('message') ?? [])
      listener({ data });
  }
}

const sources: { stopped: boolean }[] = [];
let processor: { onaudioprocess: ((event: unknown) => void) | null };
class FakeAudioContext {
  sampleRate = 48_000;
  currentTime = 0;
  destination = {};
  createMediaStreamSource() {
    return { connect: vi.fn(), disconnect: vi.fn() };
  }
  createScriptProcessor() {
    processor = {
      onaudioprocess: null,
      connect: vi.fn(),
      disconnect: vi.fn(),
    } as never;
    return processor;
  }
  createGain() {
    return { gain: { value: 1 }, connect: vi.fn() };
  }
  createBuffer(_channels: number, length: number, rate: number) {
    return { duration: length / rate, copyToChannel: vi.fn() };
  }
  createBufferSource() {
    const source = {
      stopped: false,
      buffer: null,
      onended: null,
      connect: vi.fn(),
      start: vi.fn(),
      stop() {
        source.stopped = true;
      },
    };
    sources.push(source);
    return source;
  }
  close() {
    return Promise.resolve();
  }
}

const handle = {
  lease_id: '11111111-1111-4111-8111-111111111111',
  voice_session_id: 1,
  conversation_id: 'A',
  server_epoch: 'epoch',
};
let sessions: RealtimeSession[];
let track: { stop: ReturnType<typeof vi.fn> };

function start(
  transport: 'gemini_live' | 'grok_voice',
  url: string,
  setup: unknown,
): RealtimeStart {
  return {
    snapshot: {
      schema_version: 1,
      handle,
      state: 'connecting',
      quiesced: true,
      expires_in_ms: 120_000,
      run_id: null,
      transport,
    },
    client_secret: 'synthetic-ephemeral',
    secret_expires_in_ms: 60_000,
    connect_url: url,
    setup: JSON.stringify(setup),
  };
}
function options(value: RealtimeStart) {
  return {
    start: value,
    signal: new AbortController().signal,
    current: () => true,
    event: vi.fn().mockImplementation(async (event: RealtimeEvent) => ({
      snapshot: { ...value.snapshot, state: 'listening', run_id: 'run-1' },
      event_id: event.event_id,
      accepted: true,
      caption: '',
      call_id: '',
      function_output: '',
      silent: false,
    })),
    onSnapshot: vi.fn(),
    onCaption: vi.fn(),
    onFailure: vi.fn(),
    onSession: (session: RealtimeSession) => {
      sessions.push(session);
    },
  };
}
async function flush() {
  for (let index = 0; index < 30; index++) await Promise.resolve();
}
const pcm = btoa(String.fromCharCode(0, 0, 255, 127));

beforeEach(() => {
  FakeSocket.instances = [];
  sources.length = 0;
  sessions = [];
  track = { stop: vi.fn() };
  vi.stubGlobal('WebSocket', FakeSocket);
  vi.stubGlobal('AudioContext', FakeAudioContext);
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {
      getUserMedia: vi.fn().mockResolvedValue({
        getTracks: () => [track],
      } as unknown as MediaStream),
    },
  });
});
afterEach(async () => {
  for (const session of sessions) await session.stop();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it('Grok Voice: the ephemeral credential opens the socket once, the session update goes first, then audio', async () => {
  const setup = {
    type: 'session.update',
    session: { instructions: 'Synthetic' },
  };
  const o = options(
    start(
      'grok_voice',
      'wss://api.x.ai/v1/realtime?model=grok-voice-latest',
      setup,
    ),
  );
  const opening = openRealtimeSession(o);
  await flush();
  const socket = FakeSocket.instances[0];
  expect(socket.url).toBe('wss://api.x.ai/v1/realtime?model=grok-voice-latest');
  expect(socket.protocols).toEqual(['xai-client-secret.synthetic-ephemeral']);
  socket.open();
  await opening;
  await flush();
  expect(socket.sent[0]).toEqual(setup);
  processor.onaudioprocess?.({
    inputBuffer: { getChannelData: () => new Float32Array(480) },
  });
  expect(socket.sent[1]).toMatchObject({ type: 'input_audio_buffer.append' });
  // The schema is OpenAI's: a function call and audio deltas arrive as events.
  socket.receive({ type: 'response.created', response: { id: 'r1' } });
  socket.receive({
    type: 'response.output_audio.delta',
    response_id: 'r1',
    delta: pcm,
  });
  socket.receive({
    type: 'response.function_call_arguments.done',
    response_id: 'r1',
    call_id: 'consult',
    name: 'row_bot_agent_consult',
    arguments: '{"request":"weather"}',
  });
  await flush();
  expect(sources).toHaveLength(1);
  const forwarded = o.event.mock.calls.map(([event]) => event);
  expect(
    forwarded.find((event) => event.type === 'function_call_ready'),
  ).toMatchObject({
    call_id: 'consult',
    name: 'row_bot_agent_consult',
    origin: 'front',
  });
  // Barge-in stops the audio already queued here; Grok has no output buffer to clear.
  vi.spyOn(performance, 'now').mockReturnValue(1e9);
  socket.receive({ type: 'input_audio_buffer.speech_started' });
  expect(sources[0].stopped).toBe(true);
  expect(
    socket.sent.map((event) => (event as { type: string }).type),
  ).not.toContain('output_audio_buffer.clear');
});

it('Gemini Live: waits for setupComplete, maps its turns to Row-Bot events and answers the consult with a toolResponse', async () => {
  const setup = { setup: { model: 'models/gemini-3.8-live' } };
  const o = options(
    start(
      'gemini_live',
      'wss://generativelanguage.googleapis.com/ws/synthetic',
      setup,
    ),
  );
  const opening = openRealtimeSession(o);
  await flush();
  const socket = FakeSocket.instances[0];
  expect(socket.url).toBe(
    'wss://generativelanguage.googleapis.com/ws/synthetic?access_token=synthetic-ephemeral',
  );
  socket.open();
  await flush();
  expect(socket.sent).toEqual([setup]);
  expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
  socket.receive({ setupComplete: {} }, true);
  await opening;
  await flush();
  processor.onaudioprocess?.({
    inputBuffer: { getChannelData: () => new Float32Array(480) },
  });
  expect(socket.sent[1]).toMatchObject({
    realtimeInput: { audio: { mimeType: 'audio/pcm;rate=16000' } },
  });
  socket.receive(
    { serverContent: { inputTranscription: { text: 'Good ' } } },
    true,
  );
  socket.receive(
    { serverContent: { inputTranscription: { text: 'morning' } } },
    true,
  );
  socket.receive(
    {
      serverContent: {
        modelTurn: { parts: [{ inlineData: { data: pcm } }] },
        outputTranscription: { text: 'Morning!' },
      },
    },
    true,
  );
  socket.receive({ serverContent: { turnComplete: true } }, true);
  socket.receive(
    {
      toolCall: {
        functionCalls: [
          {
            id: 'call-1',
            name: 'row_bot_agent_consult',
            args: { request: 'weather' },
          },
        ],
      },
    },
    true,
  );
  await flush();
  const events = o.event.mock.calls.map(([event]) => event);
  expect(events.map((event) => event.type)).toEqual([
    'connected',
    'speech_started',
    'speech_stopped',
    'transcript_final',
    'output_started',
    'assistant_transcript_final',
    'response_done',
    'function_call_ready',
  ]);
  expect(events[3].text).toBe('Good morning');
  expect(events[5]).toMatchObject({ text: 'Morning!', origin: 'front' });
  expect(events[7]).toMatchObject({
    call_id: 'call-1',
    arguments: '{"request":"weather"}',
  });
  const session = sessions[0];
  // A cue while nothing is said goes as a short status turn.
  expect(
    session.sendOutput({
      id: 'cue',
      runId: 'run-1',
      kind: 'cue',
      text: 'Working on: web search.',
    }),
  ).toBe(true);
  expect(socket.sent[2]).toEqual({
    clientContent: {
      turns: [
        {
          role: 'user',
          parts: [
            {
              text: 'Pass on this status from Row-Bot in a few words: Working on: web search.',
            },
          ],
        },
      ],
      turnComplete: true,
    },
  });
  expect(
    session.sendOutput({
      id: 'answer',
      runId: 'run-1',
      kind: 'function_output',
      callId: 'call-1',
      text: '{"status":"completed","answer":"Sunny."}',
    }),
  ).toBe(true);
  expect(socket.sent[3]).toEqual({
    toolResponse: {
      functionResponses: [
        {
          id: 'call-1',
          name: 'row_bot_agent_consult',
          response: {
            status: 'completed',
            answer: 'Sunny.',
            scheduling: 'INTERRUPT',
          },
        },
      ],
    },
  });
  // Talked over: queued audio stops and the turn ends cancelled.
  socket.receive(
    {
      serverContent: { modelTurn: { parts: [{ inlineData: { data: pcm } }] } },
    },
    true,
  );
  socket.receive({ serverContent: { interrupted: true } }, true);
  await flush();
  expect(sources.every((source) => source.stopped)).toBe(true);
  const later = o.event.mock.calls.map(([event]) => event.type);
  expect(later.slice(-3)).toEqual([
    'output_started',
    'barge_in_cancelled',
    'response_cancelled',
  ]);
  await session.stop();
  expect(track.stop).toHaveBeenCalled();
});

it('encodes microphone audio as 16-bit PCM at the front rate and decodes its replies', () => {
  const encoded = encodePcm16(
    new Float32Array([0, 0.5, -0.5, 1]),
    48_000,
    24_000,
  );
  expect(encoded.byteLength).toBe(4);
  const decoded = decodePcm16(btoa(String.fromCharCode(...encoded)));
  expect(decoded).toHaveLength(2);
  expect(decoded[0]).toBeCloseTo(0);
  expect(decoded[1]).toBeCloseTo(-0.5, 2);
});
