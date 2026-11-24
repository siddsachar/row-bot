import { vi } from 'vitest';
import type {
  CaptureCallbacks,
  PlaybackCallbacks,
  VoiceAudio,
} from './voice-audio';
import type { ShareCapture, ShareSource } from './share';

/** A camera and screen with no devices: what was started, a still to hand back, the browser's own stop. */
export class FakeShare implements ShareCapture {
  started: ShareSource[] = [];
  ended: (() => void) | null = null;
  bytes = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3]);
  failure: Error | null = null;
  stream = { id: 'synthetic-stream' } as unknown as MediaStream;
  start = vi.fn(async (source: ShareSource) => {
    if (this.failure) throw this.failure;
    this.started.push(source);
    return {
      stream: this.stream,
      facingUser: source === 'camera',
      canSwitch: source === 'camera',
    };
  });
  switchCamera = vi.fn(async () => ({
    stream: this.stream,
    facingUser: false,
    canSwitch: true,
  }));
  still = vi.fn(async () => new Blob([this.bytes], { type: 'image/jpeg' }));
  onEnded(ended: () => void) {
    this.ended = ended;
  }
  stop = vi.fn();
}

/** The voice socket's server end, driven by a test. */
export class FakeSocket {
  readyState = 0;
  binaryType = '';
  bufferedAmount = 0;
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((message: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  send(data: unknown) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  emit(event: Record<string, unknown> | ArrayBuffer) {
    this.onmessage?.({
      data: event instanceof ArrayBuffer ? event : JSON.stringify(event),
    });
  }
  drop() {
    this.readyState = 3;
    this.onclose?.();
  }
  json() {
    return this.sent
      .filter((item): item is string => typeof item === 'string')
      .map((item) => JSON.parse(item) as Record<string, unknown>);
  }
}

/** A speaker and microphone with no audio devices. */
export class FakeAudio implements VoiceAudio {
  capture: CaptureCallbacks | null = null;
  playback: PlaybackCallbacks | null = null;
  played: number[] = [];
  startCapture = vi.fn(async (callbacks: CaptureCallbacks) => {
    this.capture = callbacks;
  });
  pauseCapture = vi.fn();
  resumeCapture = vi.fn(async () => undefined);
  setMicEnabled = vi.fn();
  onPlayback(callbacks: PlaybackCallbacks) {
    this.playback = callbacks;
  }
  setOutputRate = vi.fn();
  play = vi.fn((segment: number) => {
    this.played.push(segment);
  });
  duck = vi.fn();
  flush = vi.fn();
  close = vi.fn();
}

export function audioFrame(segment: number, samples = 240) {
  const buffer = new ArrayBuffer(5 + samples * 2);
  const view = new DataView(buffer);
  view.setUint8(0, 1);
  view.setUint32(1, segment, true);
  return buffer;
}
