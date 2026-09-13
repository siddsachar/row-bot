import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { DictationHandle, DictationSnapshot } from '../../api/types';
import VoiceControls, { type VoiceControlsProps } from './VoiceControls';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

class Recorder {
  static instances: Recorder[] = [];
  static isTypeSupported = vi.fn((mime: string) =>
    mime.startsWith('audio/webm'),
  );
  state: RecordingState = 'inactive';
  ondataavailable: ((event: BlobEvent) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(
    public stream: MediaStream,
    public options: MediaRecorderOptions,
  ) {
    Recorder.instances.push(this);
  }
  start = vi.fn(() => {
    this.state = 'recording';
  });
  stop = vi.fn(() => {
    if (this.state !== 'recording') throw new Error('duplicate recorder stop');
    this.state = 'inactive';
    this.emit(new Blob(['recording'], { type: this.options.mimeType }));
    this.onstop?.();
  });
  emit(blob: Blob) {
    this.ondataavailable?.({ data: blob } as BlobEvent);
  }
}

const handle: DictationHandle = {
  lease_id: '11111111-1111-4111-8111-111111111111',
  voice_session_id: 1,
  conversation_id: 'conversation-A',
  server_epoch: 'epoch',
};
const capture: DictationSnapshot = {
  schema_version: 1,
  handle,
  state: 'capturing',
  quiesced: true,
  expires_in_ms: 120000,
  max_audio_bytes: 8388608,
  max_utterance_ms: 30000,
};
let track: { stop: ReturnType<typeof vi.fn> };
let stream: MediaStream;
let media: ReturnType<typeof vi.fn>;

function props(): VoiceControlsProps {
  return {
    scope: {
      conversationId: 'conversation-A',
      clientSessionId: 'session',
      serverEpoch: 'epoch',
      selectionKey: 'open-1',
    },
    available: true,
    start: vi.fn().mockResolvedValue(capture),
    transcribe: vi.fn().mockImplementation(async (current, utterance) => ({
      snapshot: { ...capture, handle: current, state: 'completed' },
      utterance_id: utterance,
      outcome: 'transcribed',
      text: 'New words',
    })),
    stop: vi.fn().mockResolvedValue({ ...capture, state: 'stopped' }),
    applyTranscript: vi.fn().mockReturnValue('applied'),
  };
}

beforeEach(() => {
  track = { stop: vi.fn() };
  stream = { getTracks: () => [track] } as unknown as MediaStream;
  media = vi.fn().mockResolvedValue(stream);
  vi.stubGlobal('isSecureContext', true);
  vi.stubGlobal('MediaRecorder', Recorder);
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: media },
  });
  Recorder.instances = [];
  Recorder.isTypeSupported.mockImplementation((mime) =>
    mime.startsWith('audio/webm'),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

async function recording() {
  fireEvent.click(screen.getByRole('button', { name: 'Dictate' }));
  await screen.findByRole('button', { name: 'Finish dictation' });
}

it('does not acquire anything before an explicit gesture and finishes into the same draft', async () => {
  const p = props();
  render(<VoiceControls {...p} />);
  expect(media).not.toHaveBeenCalled();
  expect(p.start).not.toHaveBeenCalled();
  await recording();
  expect(p.start).toHaveBeenCalledWith(
    expect.any(String),
    expect.any(AbortSignal),
  );
  expect(media).toHaveBeenCalledWith({ audio: true });
  fireEvent.click(screen.getByRole('button', { name: 'Finish dictation' }));
  await screen.findByRole('button', { name: 'Dictate' });
  expect(p.transcribe).toHaveBeenCalledTimes(1);
  expect(p.applyTranscript).toHaveBeenCalledWith(
    p.scope,
    expect.objectContaining({ text: 'New words' }),
  );
  expect(track.stop).toHaveBeenCalledTimes(1);
});

it('Cancel discards recording without uploading or changing a draft', async () => {
  const p = props();
  render(<VoiceControls {...p} />);
  await recording();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel dictation' }));
  await screen.findByRole('button', { name: 'Dictate' });
  expect(p.transcribe).not.toHaveBeenCalled();
  expect(p.applyTranscript).not.toHaveBeenCalled();
  expect(track.stop).toHaveBeenCalledTimes(1);
  expect(p.stop).toHaveBeenCalledWith(handle);
});

it('cleans up a late permission grant after Cancel', async () => {
  const permission = deferred<MediaStream>();
  media.mockReturnValue(permission.promise);
  const p = props();
  render(<VoiceControls {...p} />);
  fireEvent.click(screen.getByRole('button', { name: 'Dictate' }));
  await act(async () => {});
  fireEvent.click(screen.getByRole('button', { name: 'Cancel dictation' }));
  await act(async () => permission.resolve(stream));
  expect(track.stop).toHaveBeenCalledTimes(1);
  expect(Recorder.instances).toHaveLength(0);
  expect(p.transcribe).not.toHaveBeenCalled();
});

it('stops a lease returned after cancellation while Start was pending', async () => {
  const pending = deferred<DictationSnapshot>();
  const p = props();
  vi.mocked(p.start).mockReturnValue(pending.promise);
  render(<VoiceControls {...p} />);
  fireEvent.click(screen.getByRole('button', { name: 'Dictate' }));
  fireEvent.click(screen.getByRole('button', { name: 'Cancel dictation' }));
  await act(async () => pending.resolve(capture));
  expect(p.stop).toHaveBeenCalledWith(handle);
  expect(media).not.toHaveBeenCalled();
});

it('rejects permission denial and allows an explicit retry', async () => {
  media.mockRejectedValueOnce(new DOMException('denied', 'NotAllowedError'));
  render(<VoiceControls {...props()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Dictate' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'permission was denied',
  );
  await screen.findByRole('button', { name: 'Dictate' });
  await recording();
  expect(media).toHaveBeenCalledTimes(2);
});

it('never invokes Start or microphone when insecure or capability unavailable', () => {
  vi.stubGlobal('isSecureContext', false);
  const p = props();
  const { rerender } = render(<VoiceControls {...p} />);
  fireEvent.click(screen.getByRole('button', { name: 'Dictate' }));
  expect(screen.getByRole('alert')).toHaveTextContent('secure connection');
  expect(p.start).not.toHaveBeenCalled();
  rerender(<VoiceControls {...p} available={false} />);
  expect(screen.getByRole('button', { name: 'Dictate' })).toBeDisabled();
  expect(media).not.toHaveBeenCalled();
});

it('refuses unsupported recorder MIME and releases the granted microphone', async () => {
  Recorder.isTypeSupported.mockReturnValue(false);
  render(<VoiceControls {...props()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Dictate' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'supported recording format',
  );
  expect(track.stop).toHaveBeenCalledTimes(1);
});

it('enforces the incremental byte ceiling before uploading', async () => {
  const p = props();
  render(<VoiceControls {...p} />);
  await recording();
  act(() => Recorder.instances[0].emit(new Blob([new Uint8Array(8388609)])));
  expect(await screen.findByRole('alert')).toHaveTextContent('too large');
  expect(p.transcribe).not.toHaveBeenCalled();
  expect(track.stop).toHaveBeenCalledTimes(1);
});

it('finishes at the existing 30-second utterance ceiling', async () => {
  vi.useFakeTimers();
  const p = props();
  render(<VoiceControls {...p} />);
  fireEvent.click(screen.getByRole('button', { name: 'Dictate' }));
  await act(async () => {});
  await act(async () => {
    vi.advanceTimersByTime(30000);
  });
  expect(p.transcribe).toHaveBeenCalledTimes(1);
  expect(track.stop).toHaveBeenCalledTimes(1);
});

it('expires a pending permission request and stops the late granted stream', async () => {
  vi.useFakeTimers();
  const permission = deferred<MediaStream>();
  media.mockReturnValue(permission.promise);
  const p = props();
  vi.mocked(p.start).mockResolvedValue({ ...capture, expires_in_ms: 100 });
  render(<VoiceControls {...p} />);
  fireEvent.click(screen.getByRole('button', { name: 'Dictate' }));
  await act(async () => {});
  await act(async () => {
    vi.advanceTimersByTime(100);
  });
  await act(async () => permission.resolve(stream));
  expect(screen.getByRole('alert')).toHaveTextContent('expired');
  expect(track.stop).toHaveBeenCalledTimes(1);
  expect(Recorder.instances).toHaveLength(0);
});

it('never applies a late transcription after A-B-A selection replacement', async () => {
  const response =
    deferred<Awaited<ReturnType<VoiceControlsProps['transcribe']>>>();
  const p = props();
  vi.mocked(p.transcribe).mockReturnValue(response.promise);
  const { rerender } = render(<VoiceControls {...p} />);
  await recording();
  fireEvent.click(screen.getByRole('button', { name: 'Finish dictation' }));
  const call = vi.mocked(p.transcribe).mock.calls[0];
  rerender(
    <VoiceControls
      {...p}
      scope={{ ...p.scope, conversationId: 'B', selectionKey: 'open-2' }}
    />,
  );
  rerender(
    <VoiceControls {...p} scope={{ ...p.scope, selectionKey: 'open-3' }} />,
  );
  await act(async () =>
    response.resolve({
      snapshot: { ...capture, state: 'completed' },
      utterance_id: call[1],
      outcome: 'transcribed',
      text: 'late',
    }),
  );
  expect(call[3].aborted).toBe(true);
  expect(p.applyTranscript).not.toHaveBeenCalled();
  expect(p.stop).toHaveBeenCalledWith(handle);
});

it('shows truthful draining and checks server acknowledgement without restarting capture', async () => {
  const p = props();
  vi.mocked(p.stop).mockResolvedValueOnce({
    ...capture,
    state: 'stopping',
    quiesced: false,
  });
  render(<VoiceControls {...p} />);
  await recording();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel dictation' }));
  const check = await screen.findByRole('button', {
    name: 'Check voice status',
  });
  await act(async () => {});
  expect(screen.getByRole('status')).toHaveTextContent(
    'Waiting for server transcription',
  );
  expect(screen.queryByRole('button', { name: 'Dictate' })).toBeNull();
  fireEvent.click(check);
  await screen.findByRole('button', { name: 'Dictate' });
  expect(media).toHaveBeenCalledTimes(1);
});

it('retains escaped final text when the owning draft is full', async () => {
  const p = props();
  vi.mocked(p.applyTranscript).mockReturnValue('draft_full');
  vi.mocked(p.transcribe).mockImplementation(async (current, utterance) => ({
    snapshot: { ...capture, handle: current, state: 'completed' },
    utterance_id: utterance,
    outcome: 'transcribed',
    text: '<img src=x onerror=boom>',
  }));
  render(<VoiceControls {...p} />);
  await recording();
  fireEvent.click(screen.getByRole('button', { name: 'Finish dictation' }));
  expect(await screen.findByLabelText('Unadded dictation')).toHaveTextContent(
    '<img src=x onerror=boom>',
  );
  expect(screen.queryByRole('img')).toBeNull();
});

it('refuses a mismatched response identity instead of editing the draft', async () => {
  const p = props();
  vi.mocked(p.transcribe).mockResolvedValue({
    snapshot: { ...capture, state: 'completed' },
    utterance_id: 'another',
    outcome: 'transcribed',
    text: 'wrong',
  });
  render(<VoiceControls {...p} />);
  await recording();
  fireEvent.click(screen.getByRole('button', { name: 'Finish dictation' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'no longer matches',
  );
  expect(p.applyTranscript).not.toHaveBeenCalled();
});

it('unmount stops tracks and sends cancellation without retaining recording', async () => {
  const p = props();
  const view = render(<VoiceControls {...p} />);
  await recording();
  view.unmount();
  expect(track.stop).toHaveBeenCalledTimes(1);
  expect(p.stop).toHaveBeenCalledWith(handle);
  expect(p.transcribe).not.toHaveBeenCalled();
});

it('rejects a wrong-conversation start handle before microphone access', async () => {
  const p = props();
  vi.mocked(p.start).mockResolvedValue({
    ...capture,
    handle: { ...handle, conversation_id: 'other' },
  });
  render(<VoiceControls {...p} />);
  fireEvent.click(screen.getByRole('button', { name: 'Dictate' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'no longer matches',
  );
  expect(media).not.toHaveBeenCalled();
  expect(p.stop).toHaveBeenCalledTimes(1);
});

it('does not append text for an explicit no-speech result', async () => {
  const p = props();
  vi.mocked(p.transcribe).mockImplementation(async (current, utterance) => ({
    snapshot: { ...capture, handle: current, state: 'completed' },
    utterance_id: utterance,
    outcome: 'no_speech',
    text: '',
  }));
  render(<VoiceControls {...p} />);
  await recording();
  fireEvent.click(screen.getByRole('button', { name: 'Finish dictation' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('No speech');
  expect(p.applyTranscript).not.toHaveBeenCalled();
});

it('ignores duplicate Finish events while one upload is pending', async () => {
  const pending =
    deferred<Awaited<ReturnType<VoiceControlsProps['transcribe']>>>();
  const p = props();
  vi.mocked(p.transcribe).mockReturnValue(pending.promise);
  render(<VoiceControls {...p} />);
  await recording();
  const finish = screen.getByRole('button', { name: 'Finish dictation' });
  fireEvent.click(finish);
  fireEvent.click(finish);
  expect(p.transcribe).toHaveBeenCalledTimes(1);
  expect(Recorder.instances[0].stop).toHaveBeenCalledTimes(1);
});

it('stops capture when the host withdraws dictation capability', async () => {
  const p = props();
  const view = render(<VoiceControls {...p} />);
  await recording();
  view.rerender(<VoiceControls {...p} available={false} />);
  expect(track.stop).toHaveBeenCalledTimes(1);
  expect(p.transcribe).not.toHaveBeenCalled();
  expect(p.stop).toHaveBeenCalledWith(handle);
  expect(screen.getByRole('button', { name: 'Dictate' })).toBeDisabled();
});
