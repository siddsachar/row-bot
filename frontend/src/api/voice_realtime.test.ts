import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import {
  openRealtimeSession,
  type RealtimeEvent,
  type RealtimeSession,
  type RealtimeSnapshot,
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
      client_secret: 'synthetic-ephemeral',
      secret_expires_in_ms: 60_000,
    },
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
  const exchange = vi.fn().mockResolvedValue('v=0 synthetic host answer');
  await openRealtimeSession({
    ...base,
    start: { ...base.start, client_secret: null, exchange_available: true },
    exchange,
  });
  expect(exchange).toHaveBeenCalledWith(
    'v=0 synthetic offer',
    expect.any(AbortSignal),
  );
  expect(fetch).not.toHaveBeenCalled();
  expect(Peer.instances[0].setRemoteDescription).toHaveBeenCalledWith({
    type: 'answer',
    sdp: 'v=0 synthetic host answer',
  });
});

it('refuses a managed exchange response without its authenticated exchange callback', async () => {
  const base = options();
  await openRealtimeSession({
    ...base,
    start: { ...base.start, client_secret: null, exchange_available: true },
  });
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

it('uses the sole arbiter with exact provider exchange and projects only whitelisted event fields', async () => {
  const o = options();
  const session = await openRealtimeSession(o);
  expect(media).toHaveBeenCalledTimes(1);
  expect(fetch).toHaveBeenCalledWith(
    'https://api.openai.com/v1/realtime/calls',
    expect.objectContaining({
      credentials: 'omit',
      redirect: 'error',
      cache: 'no-store',
      headers: {
        Authorization: 'Bearer synthetic-ephemeral',
        'Content-Type': 'application/sdp',
      },
    }),
  );
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
  expect(fetch).not.toHaveBeenCalled();
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
  expect(fetch).not.toHaveBeenCalled();
  expect(o.onFailure).toHaveBeenCalledWith('realtime_credential_expired');
});
