import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  buddyVoiceLevel,
  followBuddyVoice,
  setBuddyVoiceLevel,
  wavEnvelope,
} from './voice';

/** A mono 16-bit WAV: silence, then a loud tone, each `seconds` long. */
function wav(seconds: number, rate = 8000): ArrayBuffer {
  const frames = Math.round(seconds * rate) * 2;
  const buffer = new ArrayBuffer(44 + frames * 2);
  const view = new DataView(buffer);
  const write = (offset: number, text: string) =>
    [...text].forEach((char, i) =>
      view.setUint8(offset + i, char.charCodeAt(0)),
    );
  write(0, 'RIFF');
  view.setUint32(4, 36 + frames * 2, true);
  write(8, 'WAVE');
  write(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, 'data');
  view.setUint32(40, frames * 2, true);
  for (let i = frames / 2; i < frames; i++)
    view.setInt16(44 + i * 2, Math.round(Math.sin(i / 3) * 20000), true);
  return buffer;
}

afterEach(() => vi.useRealTimers());

describe('Buddy voice', () => {
  it('reads a loudness envelope from 16-bit WAV samples', () => {
    const envelope = wavEnvelope(wav(0.5))!;
    expect(envelope.length).toBe(50);
    expect(Math.max(...envelope.slice(0, 24))).toBeLessThan(0.05);
    expect(Math.min(...envelope.slice(26))).toBeGreaterThan(0.8);
  });

  it('gives up on audio it cannot read', () => {
    expect(wavEnvelope(new ArrayBuffer(10))).toBeNull();
    const compressed = wav(0.1);
    new DataView(compressed).setUint16(20, 3, true); // float, not PCM
    expect(wavEnvelope(compressed)).toBeNull();
  });

  it('follows the playing clip and falls silent when stopped or stale', () => {
    vi.useFakeTimers();
    const envelope = new Float32Array([0, 0, 0.9, 0.9]);
    const audio = { currentTime: 0.05, paused: false } as HTMLMediaElement;
    const stop = followBuddyVoice(audio, envelope);
    vi.advanceTimersByTime(40);
    expect(buddyVoiceLevel()).toBeCloseTo(0.9, 5);
    stop();
    expect(buddyVoiceLevel()).toBe(0);
    setBuddyVoiceLevel(0.7);
    expect(buddyVoiceLevel(performance.now() + 300)).toBe(0);
  });
});
