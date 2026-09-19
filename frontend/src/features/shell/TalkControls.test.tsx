import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import TalkControls, {
  type TalkControlsProps,
  type TalkSnapshot,
} from './TalkControls';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

class Recorder {
  static instances: Recorder[] = [];
  static isTypeSupported = (type: string) => type.startsWith('audio/webm');
  state: RecordingState = 'inactive';
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ondataavailable: ((event: BlobEvent) => void) | null = null;
  constructor(
    public stream: MediaStream,
    public options: MediaRecorderOptions,
  ) {
    Recorder.instances.push(this);
  }
  start() {
    this.state = 'recording';
  }
  stop() {
    this.state = 'inactive';
    this.ondataavailable?.({
      data: new Blob(['synthetic'], { type: this.options.mimeType }),
    } as BlobEvent);
    this.onstop?.();
  }
}

class FakeAudio {
  static instances: FakeAudio[] = [];
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  pause = vi.fn();
  play = vi.fn().mockResolvedValue(undefined);
  constructor(public src: string) {
    FakeAudio.instances.push(this);
  }
}

const snapshot: TalkSnapshot = {
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
  transport: 'browser_local',
  max_audio_bytes: 8_388_608,
  max_utterance_ms: 30_000,
};
let media: ReturnType<typeof vi.fn>;
let track: { stop: ReturnType<typeof vi.fn>; onended: (() => void) | null };
let stream: MediaStream;

function props(): TalkControlsProps {
  return {
    scope: {
      conversationId: 'A',
      clientSessionId: 'client',
      serverEpoch: 'epoch',
      selectionKey: 'selection',
    },
    available: true,
    start: vi.fn().mockResolvedValue(snapshot),
    transcribe: vi.fn().mockImplementation(async (_handle, id) => ({
      snapshot: { ...snapshot, run_id: 'run' },
      utterance_id: id,
      outcome: 'submitted',
      text: 'Spoken words',
      run_id: 'run',
    })),
    stop: vi.fn().mockResolvedValue({ ...snapshot, state: 'stopped' }),
    heartbeat: vi.fn().mockResolvedValue(snapshot),
    output: vi
      .fn()
      .mockResolvedValue(new Blob(['synthetic'], { type: 'audio/wav' })),
    onBusy: vi.fn(),
    onSubmitted: vi.fn(),
  };
}

beforeEach(() => {
  track = { stop: vi.fn(), onended: null };
  stream = { getTracks: () => [track] } as unknown as MediaStream;
  media = vi.fn().mockResolvedValue(stream);
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: media },
  });
  vi.stubGlobal('isSecureContext', true);
  vi.stubGlobal('MediaRecorder', Recorder);
  vi.stubGlobal('Audio', FakeAudio);
  vi.stubGlobal('AudioContext', undefined);
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: vi.fn().mockReturnValue('blob:owned-audio'),
    revokeObjectURL: vi.fn(),
  });
  Recorder.instances = [];
  FakeAudio.instances = [];
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

async function listen() {
  fireEvent.click(screen.getByRole('button', { name: 'Talk' }));
  await screen.findByRole('button', { name: 'Send speech' });
  await waitFor(() => expect(Recorder.instances.length).toBeGreaterThan(0));
}
async function submit() {
  await listen();
  fireEvent.click(screen.getByRole('button', { name: 'Send speech' }));
  await screen.findByText('Thinking…');
}

it('opens no microphone or provider on render, submits speech once, and never touches the draft', async () => {
  const p = props();
  render(<TalkControls {...p} />);
  expect(p.start).not.toHaveBeenCalled();
  expect(media).not.toHaveBeenCalled();
  await submit();
  expect(p.transcribe).toHaveBeenCalledTimes(1);
  expect(p.onSubmitted).toHaveBeenCalledWith(
    expect.objectContaining({ run_id: 'run', text: 'Spoken words' }),
  );
  expect(track.stop).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText('Voice caption')).toHaveTextContent(
    'Spoken words',
  );
  expect(p.onBusy).toHaveBeenCalledWith(true);
});

it('Stop discards microphone input and restores availability only after quiescence', async () => {
  const p = props();
  vi.mocked(p.stop).mockResolvedValueOnce({
    ...snapshot,
    state: 'stopping',
    quiesced: false,
  });
  render(<TalkControls {...p} />);
  await listen();
  fireEvent.click(screen.getByRole('button', { name: 'Stop Talk' }));
  await screen.findByRole('button', { name: 'Check voice status' });
  expect(p.transcribe).not.toHaveBeenCalled();
  expect(track.stop).toHaveBeenCalledTimes(1);
  expect(p.onBusy).not.toHaveBeenCalledWith(false);
  fireEvent.click(screen.getByRole('button', { name: 'Check voice status' }));
  await screen.findByRole('button', { name: 'Talk' });
  expect(p.onBusy).toHaveBeenCalledWith(false);
});

it('late start after scope change is stopped without acquiring the new microphone', async () => {
  const start = deferred<TalkSnapshot>();
  const p = props();
  p.start = vi.fn().mockReturnValue(start.promise);
  const view = render(<TalkControls {...p} />);
  fireEvent.click(screen.getByRole('button', { name: 'Talk' }));
  view.rerender(
    <TalkControls
      {...p}
      scope={{ ...p.scope, conversationId: 'B', selectionKey: 'B' }}
    />,
  );
  await act(async () => start.resolve(snapshot));
  expect(p.stop).toHaveBeenCalledWith(snapshot.handle);
  expect(media).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Talk' })).toBeVisible();
});

it('late permission after Stop only releases tracks and never records', async () => {
  const permission = deferred<MediaStream>();
  media.mockReturnValue(permission.promise);
  const p = props();
  render(<TalkControls {...p} />);
  fireEvent.click(screen.getByRole('button', { name: 'Talk' }));
  await waitFor(() => expect(media).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('button', { name: 'Stop Talk' }));
  await act(async () => permission.resolve(stream));
  expect(track.stop).toHaveBeenCalledTimes(1);
  expect(Recorder.instances).toHaveLength(0);
});

it('plays only the exact admitted final response once and resumes listening after playback', async () => {
  const p = props();
  const view = render(<TalkControls {...p} />);
  await submit();
  view.rerender(
    <TalkControls
      {...p}
      run={{ id: 'foreign', state: 'completed', outputId: 'other' }}
    />,
  );
  expect(p.output).not.toHaveBeenCalled();
  view.rerender(
    <TalkControls
      {...p}
      run={{ id: 'run', state: 'completed', outputId: 'message' }}
    />,
  );
  await waitFor(() => expect(FakeAudio.instances).toHaveLength(1));
  expect(p.output).toHaveBeenCalledWith(
    snapshot.handle,
    'run',
    'message',
    expect.any(AbortSignal),
  );
  expect(FakeAudio.instances[0].play).toHaveBeenCalledTimes(1);
  view.rerender(
    <TalkControls
      {...p}
      run={{ id: 'run', state: 'completed', outputId: 'message' }}
    />,
  );
  await act(async () => FakeAudio.instances[0].onended?.());
  await screen.findByRole('button', { name: 'Send speech' });
  expect(p.output).toHaveBeenCalledTimes(1);
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:owned-audio');
});

it('mute during pending synthesis prevents late playback and captions can be hidden', async () => {
  const pending = deferred<Blob>();
  const p = props();
  p.output = vi.fn().mockReturnValue(pending.promise);
  const view = render(<TalkControls {...p} />);
  await submit();
  fireEvent.click(screen.getByRole('button', { name: 'Captions' }));
  expect(screen.queryByLabelText('Voice caption')).not.toBeInTheDocument();
  view.rerender(
    <TalkControls
      {...p}
      run={{ id: 'run', state: 'completed', outputId: 'message' }}
    />,
  );
  await waitFor(() => expect(p.output).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole('button', { name: 'Speech' }));
  await act(async () =>
    pending.resolve(new Blob(['synthetic'], { type: 'audio/wav' })),
  );
  expect(FakeAudio.instances).toHaveLength(0);
  await screen.findByRole('button', { name: 'Send speech' });
});

it('scope revocation while output is pending never plays or restarts microphone', async () => {
  const pending = deferred<Blob>();
  const p = props();
  p.output = vi.fn().mockReturnValue(pending.promise);
  const view = render(<TalkControls {...p} />);
  await submit();
  view.rerender(
    <TalkControls
      {...p}
      run={{ id: 'run', state: 'completed', outputId: 'message' }}
    />,
  );
  await waitFor(() => expect(p.output).toHaveBeenCalledTimes(1));
  view.rerender(<TalkControls {...p} available={false} />);
  await act(async () =>
    pending.resolve(new Blob(['synthetic'], { type: 'audio/wav' })),
  );
  expect(FakeAudio.instances).toHaveLength(0);
  expect(media).toHaveBeenCalledTimes(1);
  expect(p.stop).toHaveBeenCalledWith(snapshot.handle);
});

it('checks secure context before starting voice or asking for microphone permission', () => {
  vi.stubGlobal('isSecureContext', false);
  const p = props();
  render(<TalkControls {...p} />);
  fireEvent.click(screen.getByRole('button', { name: 'Talk' }));
  expect(p.start).not.toHaveBeenCalled();
  expect(media).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent('secure browser context');
});

it('microphone loss stops the exact session without submitting a partial recording', async () => {
  const p = props();
  render(<TalkControls {...p} />);
  await listen();
  await act(async () => track.onended?.());
  expect(p.stop).toHaveBeenCalledWith(snapshot.handle);
  expect(p.transcribe).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Microphone disconnected',
  );
});

it('heartbeat authority failure stops recording and does not retry provider work', async () => {
  const p = props();
  vi.mocked(p.heartbeat).mockRejectedValue(new Error('revoked'));
  render(<TalkControls {...p} />);
  await listen();
  vi.useFakeTimers();
  // Existing timers were installed under the real clock; restarting with fake
  // timers exercises the actual heartbeat without a wall-clock wait.
  fireEvent.click(screen.getByRole('button', { name: 'Stop Talk' }));
  await act(async () => undefined);
  fireEvent.click(screen.getByRole('button', { name: 'Talk' }));
  await act(async () => undefined);
  await act(async () => vi.advanceTimersByTimeAsync(30_000));
  expect(p.heartbeat).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('alert')).toHaveTextContent('Voice connection lost');
});

it('stale recorder events cannot stop the next admitted utterance', async () => {
  const p = props();
  vi.mocked(p.transcribe).mockImplementation(async (_handle, id) => ({
    snapshot,
    utterance_id: id,
    outcome: 'no_speech',
    text: '',
    run_id: null,
  }));
  render(<TalkControls {...p} />);
  await listen();
  const old = Recorder.instances[0];
  fireEvent.click(screen.getByRole('button', { name: 'Send speech' }));
  await waitFor(() => expect(Recorder.instances).toHaveLength(2));
  const stops = track.stop.mock.calls.length;
  await act(async () => {
    old.onstop?.();
    old.onerror?.();
  });
  expect(track.stop).toHaveBeenCalledTimes(stops);
  expect(p.stop).not.toHaveBeenCalled();
  expect(Recorder.instances[1].state).toBe('recording');
});

it('automatic local silence detection submits one complete utterance', async () => {
  let voiced = true;
  const close = vi.fn().mockResolvedValue(undefined);
  class Context {
    close = close;
    createAnalyser() {
      return {
        fftSize: 2048,
        getFloatTimeDomainData: (data: Float32Array) =>
          data.fill(voiced ? 0.05 : 0),
      };
    }
    createMediaStreamSource() {
      return { connect: vi.fn() };
    }
  }
  vi.stubGlobal('AudioContext', Context);
  vi.useFakeTimers();
  const p = props();
  render(<TalkControls {...p} />);
  fireEvent.click(screen.getByRole('button', { name: 'Talk' }));
  await act(async () => undefined);
  await act(async () => vi.advanceTimersByTimeAsync(300));
  voiced = false;
  await act(async () => vi.advanceTimersByTimeAsync(1_000));
  expect(p.transcribe).toHaveBeenCalledTimes(1);
  expect(close).toHaveBeenCalledTimes(1);
  expect(screen.getByText('Thinking…')).toBeVisible();
});

it('terminal heartbeat stops capture even when its expiry is still positive', async () => {
  vi.useFakeTimers();
  const p = props();
  vi.mocked(p.heartbeat).mockResolvedValue({
    ...snapshot,
    state: 'stopped',
    expires_in_ms: 60_000,
  });
  render(<TalkControls {...p} />);
  fireEvent.click(screen.getByRole('button', { name: 'Talk' }));
  await act(async () => undefined);
  await act(async () => vi.advanceTimersByTimeAsync(30_000));
  expect(track.stop).toHaveBeenCalled();
  expect(p.stop).toHaveBeenCalledWith(snapshot.handle);
  expect(screen.getByRole('button', { name: 'Talk' })).toBeVisible();
});
