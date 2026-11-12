/**
 * Speech loudness for lip sync. While Buddy's voice plays, the talk controls
 * follow the clip's loudness envelope (read from its WAV samples, so the
 * audio itself is never rerouted) and living Buddies read it every frame. A
 * level not refreshed for a quarter second counts as silence.
 */
let level = 0;
let updated = -Infinity;

export function setBuddyVoiceLevel(value: number) {
  level = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
  updated = performance.now();
}

export function buddyVoiceLevel(now = performance.now()): number {
  return now - updated < 250 ? level : 0;
}

const WINDOW_MS = 20;

/**
 * Loudness per 20 ms of a 16-bit PCM WAV, scaled so its loud parts reach 1.
 * Anything else (compressed audio, a malformed header) gives null.
 */
export function wavEnvelope(buffer: ArrayBuffer): Float32Array | null {
  const view = new DataView(buffer);
  const text = (offset: number) =>
    String.fromCharCode(...new Uint8Array(buffer, offset, 4));
  if (buffer.byteLength < 44 || text(0) !== 'RIFF' || text(8) !== 'WAVE')
    return null;
  let channels = 0;
  let rate = 0;
  let bits = 0;
  let offset = 12;
  while (offset + 8 <= buffer.byteLength) {
    const id = text(offset);
    const size = view.getUint32(offset + 4, true);
    const body = offset + 8;
    if (id === 'fmt ' && size >= 16) {
      if (view.getUint16(body, true) !== 1) return null;
      channels = view.getUint16(body + 2, true);
      rate = view.getUint32(body + 4, true);
      bits = view.getUint16(body + 14, true);
    } else if (id === 'data') {
      if (bits !== 16 || !channels || !rate) return null;
      const frames = Math.floor(
        Math.min(size, buffer.byteLength - body) / (2 * channels),
      );
      const perWindow = Math.max(1, Math.round((rate * WINDOW_MS) / 1000));
      const envelope = new Float32Array(Math.ceil(frames / perWindow));
      for (let w = 0; w < envelope.length; w++) {
        let sum = 0;
        const start = w * perWindow;
        const end = Math.min(frames, start + perWindow);
        for (let frame = start; frame < end; frame++) {
          const sample =
            view.getInt16(body + frame * 2 * channels, true) / 32768;
          sum += sample * sample;
        }
        envelope[w] = Math.sqrt(sum / Math.max(1, end - start));
      }
      const sorted = Float32Array.from(envelope).sort();
      const loud = sorted[Math.floor(sorted.length * 0.95)] || 1;
      for (let i = 0; i < envelope.length; i++)
        envelope[i] = Math.min(1, envelope[i] / loud);
      return envelope;
    }
    offset = body + size + (size % 2);
  }
  return null;
}

/** Keep Buddy's voice level in step with a playing clip; returns a stop. */
export function followBuddyVoice(
  audio: HTMLMediaElement,
  envelope: Float32Array,
): () => void {
  const timer = window.setInterval(() => {
    const index = Math.floor((audio.currentTime * 1000) / WINDOW_MS);
    setBuddyVoiceLevel(audio.paused ? 0 : (envelope[index] ?? 0));
  }, 40);
  return () => {
    window.clearInterval(timer);
    setBuddyVoiceLevel(0);
  };
}
