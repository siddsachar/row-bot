import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import {
  openRealtimeSession,
  type RealtimeEvent,
  type RealtimeSession,
  type RealtimeSnapshot,
  type RealtimeStart,
} from './voice_realtime';

class Peer {
  static instances: Peer[] = [];
  closed = false;
  ontrack: ((event: RTCTrackEvent) => void) | null = null;
  onconnectionstatechange: (() => void) | null = null;
  listeners = new Map<string, (event?: unknown) => void>();
  sent: string[] = [];
  constructor() {
    Peer.instances.push(this);
  }
  close() {
    this.closed = true;
  }
  addTrack() {
    /* fake device transport */
  }
  createDataChannel() {
    return {
      readyState: 'open',
      addEventListener: (name: string, fn: (event?: unknown) => void) =>
        this.listeners.set(name, fn),
      send: (value: string) => this.sent.push(value),
      close: vi.fn(),
    };
  }
  createOffer = vi.fn().mockResolvedValue({ sdp: 'v=0 synthetic offer' });
  setLocalDescription = vi.fn().mockResolvedValue(undefined);
  setRemoteDescription = vi.fn().mockResolvedValue(undefined);
  deliver(payload: unknown) {
    this.listeners.get('message')?.({ data: JSON.stringify(payload) });
  }
}

const snapshot: RealtimeSnapshot = {
  schema_version: 1,
  handle: {
    lease_id: '11111111-1111-4111-8111-111111111111',
    voice_session_id: 1,
    conversation_id: 'A',
    server_epoch: 'epoch',
  },
  state: 'listening',
  quiesced: true,
  expires_in_ms: 120_000,
  run_id: null,
  transport: 'openai_realtime',
};
let tracks: { stop: ReturnType<typeof vi.fn> }[];
let sessions: RealtimeSession[];
let media: ReturnType<typeof vi.fn>;

function stream(): MediaStream {
  const track = { stop: vi.fn() };
  tracks.push(track);
  return { getTracks: () => [track] } as unknown as MediaStream;
}
function options() {
  return {
    start: {
      snapshot,
      client_secret: null as string | null,
      secret_expires_in_ms: 60_000,
      exchange_available: true,
    } as RealtimeStart,
    exchange: vi.fn().mockResolvedValue('v=0 synthetic host answer'),
    signal: new AbortController().signal,
    current: () => true,
    event: vi.fn().mockImplementation(async (event: RealtimeEvent) => ({
      snapshot,
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
  for (let index = 0; index < 25; index++) await Promise.resolve();
}

it('exchanges through the authenticated host without a browser credential', async () => {
  const base = options();
  await openRealtimeSession(base);
  expect(base.exchange).toHaveBeenCalledWith(
    'v=0 synthetic offer',
    expect.any(AbortSignal),
  );
  expect(fetch).not.toHaveBeenCalled();
  expect(Peer.instances[0].setRemoteDescription).toHaveBeenCalledWith({
    type: 'answer',
    sdp: 'v=0 synthetic host answer',
  });
  expect(media).toHaveBeenCalledWith({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
});

it('refuses a managed exchange response without its authenticated exchange callback', async () => {
  const base = options();
  await openRealtimeSession({ ...base, exchange: undefined });
  expect(base.onFailure).toHaveBeenCalledWith('realtime_credential_expired');
  expect(media).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
});

beforeEach(() => {
  Peer.instances = [];
  tracks = [];
  sessions = [];
  media = vi.fn().mockImplementation(async () => stream());
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: media },
  });
  vi.stubGlobal('RTCPeerConnection', Peer);
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(
      async () =>
        new Response('v=0 synthetic answer', {
          headers: { 'Content-Type': 'application/sdp' },
        }),
    ),
  );
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(
    () => undefined,
  );
});
afterEach(async () => {
  for (const session of sessions) await session.stop();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('uses the sole arbiter and projects only whitelisted event fields', async () => {
  const o = options();
  const session = await openRealtimeSession(o);
  expect(media).toHaveBeenCalledTimes(1);
  expect(fetch).not.toHaveBeenCalled();
  const peer = Peer.instances[0];
  peer.deliver({
    type: 'response.created',
    response: {
      id: 'response',
      metadata: { generation_id: '', thread_id: 'A' },
    },
  });
  peer.deliver({
    type: 'response.output_item.done',
    response_id: 'response',
    item: {
      type: 'function_call',
      call_id: 'call',
      name: 'wait_for_user',
      arguments: '{}',
    },
    private_raw: 'discarded',
  });
  await flush();
  const calls = o.event.mock.calls.map(([event]) => event);
  const forwarded = calls.find((event) => event.type === 'function_call_ready');
  expect(forwarded).toMatchObject({
    generation_id: '',
    call_id: 'call',
    name: 'wait_for_user',
    arguments: '{}',
  });
  expect(forwarded).not.toHaveProperty('raw');
  expect(JSON.stringify(calls)).not.toContain('private_raw');
  session.mute(true);
  expect(document.querySelector('audio')?.muted).toBe(true);
  await session.stop();
  expect(tracks[0].stop).toHaveBeenCalledTimes(1);
  expect(peer.closed).toBe(true);
  const count = o.event.mock.calls.length;
  peer.deliver({ type: 'response.created', response: { id: 'late' } });
  await flush();
  expect(o.event).toHaveBeenCalledTimes(count);
});

it('a late microphone grant after cancellation closes all tracks and never exchanges SDP', async () => {
  let grant!: (stream: MediaStream) => void;
  media.mockImplementation(
    () =>
      new Promise<MediaStream>((resolve) => {
        grant = resolve;
      }),
  );
  const o = options();
  const pending = openRealtimeSession(o);
  await flush();
  expect(media).toHaveBeenCalledTimes(1);
  await sessions[0].stop();
  grant(stream());
  await pending;
  expect(o.exchange).not.toHaveBeenCalled();
  expect(tracks[0].stop).toHaveBeenCalledTimes(1);
  expect(Peer.instances[0].closed).toBe(true);
});

it('records the admitted run transition and never sends an old run output', async () => {
  const o = options();
  o.event.mockImplementation(async (event) => ({
    snapshot: { ...snapshot, run_id: 'run-1' },
    event_id: event.event_id,
    accepted: true,
    caption: 'Accepted caption',
    call_id: '',
    function_output: '',
    silent: false,
  }));
  const session = await openRealtimeSession(o);
  Peer.instances[0].listeners.get('open')?.();
  await flush();
  expect(o.onCaption).toHaveBeenCalledWith('Accepted caption');
  expect(
    session.sendOutput({
      id: 'output',
      runId: 'old',
      kind: 'final',
      text: 'Old text',
    }),
  ).toBe(false);
  expect(
    session.sendOutput({
      id: 'output',
      runId: 'run-1',
      kind: 'final',
      text: 'Saved answer',
    }),
  ).toBe(true);
  expect(
    session.sendOutput({
      id: 'output',
      runId: 'run-1',
      kind: 'final',
      text: 'Saved answer',
    }),
  ).toBe(false);
  const events = Peer.instances[0].sent.map((value) => JSON.parse(value));
  expect(events).toHaveLength(1);
  expect(events[0].response.metadata.generation_id).toBe('run-1');
  expect(JSON.stringify(events)).not.toContain('Old text');
});

it('bounded argument accumulation stops the runtime before forwarding oversized content', async () => {
  const o = options();
  await openRealtimeSession(o);
  const peer = Peer.instances[0];
  peer.deliver({
    type: 'response.function_call_arguments.delta',
    call_id: 'call',
    delta: 'x'.repeat(8193),
  });
  await flush();
  expect(o.onFailure).toHaveBeenCalled();
  expect(o.event).not.toHaveBeenCalled();
  expect(peer.closed).toBe(true);
  expect(tracks[0].stop).toHaveBeenCalledTimes(1);
});

it('a rejected authenticated event stops transport without automatic retry', async () => {
  const o = options();
  o.event.mockRejectedValue(new Error('revoked'));
  await openRealtimeSession(o);
  Peer.instances[0].listeners.get('open')?.();
  await flush();
  expect(o.event).toHaveBeenCalledTimes(1);
  expect(o.onFailure).toHaveBeenCalledWith('realtime_event_failed');
  expect(Peer.instances[0].closed).toBe(true);
});

it('expired credential never opens microphone or provider exchange', async () => {
  const o = options();
  o.start.secret_expires_in_ms = 0;
  await openRealtimeSession(o);
  expect(media).not.toHaveBeenCalled();
  expect(o.exchange).not.toHaveBeenCalled();
  expect(o.onFailure).toHaveBeenCalledWith('realtime_credential_expired');
});

it('marks responses the front makes by itself apart from the ones Row-Bot asks for', async () => {
  const o = options();
  o.event.mockImplementation(async (event: RealtimeEvent) => ({
    snapshot: { ...snapshot, run_id: 'run-1' },
    event_id: event.event_id,
    accepted: true,
    caption: '',
    call_id: '',
    function_output: '',
    silent: false,
  }));
  const session = await openRealtimeSession(o);
  const peer = Peer.instances[0];
  peer.listeners.get('open')?.();
  await flush();
  peer.deliver({ type: 'response.created', response: { id: 'front-reply' } });
  peer.deliver({
    type: 'response.output_audio_transcript.done',
    response_id: 'front-reply',
    transcript: 'Hello!',
  });
  peer.deliver({ type: 'response.done', response: { id: 'front-reply' } });
  await flush();
  session.sendOutput({
    id: 'answer',
    runId: 'run-1',
    kind: 'function_output',
    callId: 'consult',
    text: '{"status":"completed","answer":"Sunny."}',
  });
  await new Promise((done) => setTimeout(done, 220));
  peer.deliver({
    type: 'response.created',
    response: { id: 'row-bot-answer' },
  });
  await flush();
  const events = o.event.mock.calls.map(([event]) => event);
  const origin = (type: string, response: string) =>
    events.find(
      (event) => event.type === type && event.response_id === response,
    )?.origin;
  expect(origin('assistant_transcript_final', 'front-reply')).toBe('front');
  expect(origin('output_started', 'row-bot-answer')).toBe('row_bot');
  expect(
    events.find((event) => event.type === 'provider_response_create_sent')
      ?.origin,
  ).toBe('row_bot');
  const sent = peer.sent.map((value) => JSON.parse(value));
  expect(sent[0]).toEqual({
    type: 'conversation.item.create',
    item: {
      type: 'function_call_output',
      call_id: 'consult',
      output: '{"status":"completed","answer":"Sunny."}',
    },
  });
  expect(sent[1].type).toBe('response.create');
});

it('a progress cue is dropped while the front is speaking, never queued behind it', async () => {
  const o = options();
  o.event.mockImplementation(async (event: RealtimeEvent) => ({
    snapshot: { ...snapshot, run_id: 'run-1' },
    event_id: event.event_id,
    accepted: true,
    caption: '',
    call_id: '',
    function_output: '',
    silent: false,
  }));
  const session = await openRealtimeSession(o);
  const peer = Peer.instances[0];
  peer.listeners.get('open')?.();
  await flush();
  peer.deliver({ type: 'response.created', response: { id: 'speaking' } });
  await flush();
  expect(
    session.sendOutput({
      id: 'cue',
      runId: 'run-1',
      kind: 'cue',
      text: 'Working on: web search.',
    }),
  ).toBe(true);
  expect(peer.sent).toEqual([]);
});
