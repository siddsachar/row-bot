/**
 * Microphone capture and playback for the socket Live voice fronts (Gemini
 * Live, Grok Voice): mono PCM16 at the front's rate, base64 per chunk. The
 * browser's echo cancellation, noise suppression and gain control stay on.
 */
import type {
  RealtimeChannel,
  RealtimeLink,
  RealtimePlayer,
} from '../../../src/row_bot/voice/realtime_runtime.js';

const MAX_CHUNK = 262_144;

function toBase64(bytes: Uint8Array): string {
  let binary = '';
  for (let index = 0; index < bytes.length; index += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  return btoa(binary);
}

/** Resample float samples to `rate` (linear) and encode them as PCM16 little-endian. */
export function encodePcm16(
  input: Float32Array,
  inputRate: number,
  rate: number,
): Uint8Array {
  const length = Math.max(0, Math.floor((input.length * rate) / inputRate));
  const output = new DataView(new ArrayBuffer(length * 2));
  for (let index = 0; index < length; index++) {
    const position = (index * inputRate) / rate;
    const left = Math.floor(position);
    const right = Math.min(left + 1, input.length - 1);
    const value =
      input[left] + (input[right] - input[left]) * (position - left);
    const clamped = Math.max(-1, Math.min(1, value));
    output.setInt16(
      index * 2,
      clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff,
      true,
    );
  }
  return new Uint8Array(output.buffer);
}

export function decodePcm16(base64: string): Float32Array<ArrayBuffer> {
  const binary = atob(base64);
  const samples = new Float32Array(binary.length >> 1);
  for (let index = 0; index < samples.length; index++) {
    const low = binary.charCodeAt(index * 2);
    const high = binary.charCodeAt(index * 2 + 1);
    const value = (high << 8) | low;
    samples[index] = (value & 0x8000 ? value - 0x10000 : value) / 0x8000;
  }
  return samples;
}

export async function openMicrophone(
  rate: number,
  onChunk: (base64: string) => void,
): Promise<{ stream: MediaStream; stop(): void }> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
  const context = new AudioContext();
  const source = context.createMediaStreamSource(stream);
  // A ScriptProcessor needs no worklet module, which this page's policy would refuse.
  const processor = context.createScriptProcessor(2048, 1, 1);
  processor.onaudioprocess = (event) =>
    onChunk(
      toBase64(
        encodePcm16(
          event.inputBuffer.getChannelData(0),
          context.sampleRate,
          rate,
        ),
      ),
    );
  source.connect(processor);
  processor.connect(context.destination);
  return {
    stream,
    stop() {
      processor.onaudioprocess = null;
      source.disconnect();
      processor.disconnect();
      stream.getTracks().forEach((track) => track.stop());
      void context.close().catch(() => undefined);
    },
  };
}

export function createPlayer(rate: number): RealtimePlayer {
  const context = new AudioContext();
  const gain = context.createGain();
  gain.connect(context.destination);
  const playing = new Set<AudioBufferSourceNode>();
  let next = 0;
  let muted = false;
  return {
    get muted() {
      return muted;
    },
    set muted(value: boolean) {
      muted = value;
      gain.gain.value = value ? 0 : 1;
    },
    play(base64) {
      if (!base64 || base64.length > MAX_CHUNK) return;
      const samples = decodePcm16(base64);
      if (!samples.length) return;
      const buffer = context.createBuffer(1, samples.length, rate);
      buffer.copyToChannel(samples, 0);
      const node = context.createBufferSource();
      node.buffer = buffer;
      node.connect(gain);
      next = Math.max(next, context.currentTime);
      node.start(next);
      next += buffer.duration;
      playing.add(node);
      node.onended = () => playing.delete(node);
    },
    flush() {
      playing.forEach((node) => {
        try {
          node.stop();
        } catch {
          /* already ended */
        }
      });
      playing.clear();
      next = 0;
    },
    stop() {
      this.flush();
      void context.close().catch(() => undefined);
    },
  };
}

/** The socket's message surface, as the runtime's data channel. */
export function socketChannel(
  socket: WebSocket,
  closed: () => void = () => undefined,
): RealtimeChannel {
  return {
    get readyState() {
      return socket.readyState === WebSocket.OPEN ? 'open' : 'closed';
    },
    send: (data) => socket.send(data),
    close: () => {
      closed();
      socket.close();
    },
    addEventListener: (type: string, listener: (event: never) => void) =>
      socket.addEventListener(
        type,
        type === 'message'
          ? (event) =>
              (listener as (event: { data: unknown }) => void)({
                data: frameText((event as MessageEvent).data),
              })
          : (listener as EventListener),
      ),
  };
}

const decoder = new TextDecoder();

/** A JSON frame as text: Gemini sends its frames as binary. */
export function frameText(data: unknown): unknown {
  return data && typeof data === 'object' && 'byteLength' in data
    ? decoder.decode(data as ArrayBuffer)
    : data;
}

/**
 * Open a front's socket with its ephemeral credential, send its setup first,
 * then stream the microphone with `frame` (each provider's audio message).
 */
export async function openLiveSocket(options: {
  url: string;
  protocols?: string[];
  setup: unknown;
  inputRate: number;
  outputRate: number;
  frame(base64: string): unknown;
  /** A front that answers its setup (Gemini's setupComplete) is ready only then. */
  ready?(message: Record<string, unknown>): boolean;
  signal: AbortSignal;
}): Promise<RealtimeLink> {
  const player = createPlayer(options.outputRate);
  const socket = new WebSocket(options.url, options.protocols);
  socket.binaryType = 'arraybuffer';
  try {
    await new Promise<void>((resolve, reject) => {
      const abort = () => reject(new Error('voice_session_expired'));
      options.signal.addEventListener('abort', abort, { once: true });
      socket.addEventListener('open', () => resolve(), { once: true });
      socket.addEventListener(
        'error',
        () => reject(new Error('realtime_connection_failed')),
        { once: true },
      );
    });
    socket.send(JSON.stringify(options.setup));
    const ready = options.ready;
    if (ready)
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(
          () => reject(new Error('realtime_connection_failed')),
          15_000,
        );
        const listen = (event: MessageEvent) => {
          try {
            const text = frameText(event.data);
            if (typeof text === 'string' && ready(JSON.parse(text))) {
              clearTimeout(timer);
              socket.removeEventListener('message', listen);
              resolve();
            }
          } catch {
            /* not JSON: keep waiting */
          }
        };
        socket.addEventListener('message', listen);
        socket.addEventListener(
          'close',
          () => {
            clearTimeout(timer);
            reject(new Error('realtime_connection_failed'));
          },
          { once: true },
        );
      });
    const microphone = await openMicrophone(options.inputRate, (chunk) => {
      if (socket.readyState === WebSocket.OPEN)
        socket.send(JSON.stringify(options.frame(chunk)));
    });
    if (options.signal.aborted) {
      microphone.stop();
      throw new Error('voice_session_expired');
    }
    return {
      dc: socketChannel(socket, () => microphone.stop()),
      stream: microphone.stream,
      audio: player,
    };
  } catch (error) {
    player.stop();
    socket.close();
    throw error;
  }
}
