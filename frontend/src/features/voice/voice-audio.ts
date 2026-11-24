/**
 * The microphone and speaker side of Row-Bot Voice.
 *
 * Capture asks the browser for its echo canceller, noise suppression and
 * gain control, and an AudioWorklet sends 16 kHz frames. Playback schedules
 * the assistant's PCM gaplessly; a gain node ducks it and a flush stops it at
 * once. Chromium engines (Chrome, Edge, WebView2) cancel the page's own
 * output. WebKit (Safari, the macOS app's WKWebView, iPhone) is given the
 * known in-page fix: playback goes through a local loopback peer connection
 * pair (no ICE servers, no network), so its echo canceller hears it.
 *
 * The microphone and speaker are the ones chosen in this browser
 * (`devices.ts`). A chosen microphone that goes away (unplugged, a headset
 * switched off) gives way to the default at once, and comes back when it is
 * connected again; `device` tells the bar either way.
 */
import captureWorkletUrl from './capture.worklet.ts?worker&url';
import {
  canChooseOutput,
  connected,
  deviceChoice,
  onDeviceChoice,
  openMicrophone,
  type DeviceNotice,
} from './devices';

export type CaptureCallbacks = {
  frame(pcm: ArrayBuffer): void;
  level(level: number): void;
  /** The chosen microphone or speaker isn't the one in use (null: it is again). */
  device?(notice: DeviceNotice | null): void;
};

export type PlaybackCallbacks = {
  started(segment: number, delayMs: number): void;
  idle(): void;
  level(level: number): void;
};

export interface VoiceAudio {
  startCapture(callbacks: CaptureCallbacks): Promise<void>;
  pauseCapture(): void;
  resumeCapture(): Promise<void>;
  setMicEnabled(enabled: boolean): void;
  onPlayback(callbacks: PlaybackCallbacks): void;
  setOutputRate(rate: number): void;
  play(segment: number, pcm: Int16Array): void;
  duck(on: boolean): void;
  flush(): void;
  close(): void;
}

export const CAPTURE_CONSTRAINTS: MediaTrackConstraints = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
  channelCount: 1,
};

const DUCKED_GAIN = 0.25;

export function isWebKit(userAgent = navigator.userAgent) {
  return (
    /AppleWebKit/.test(userAgent) && !/Chrome|Chromium|Edg/.test(userAgent)
  );
}

let primed: AudioContext | null = null;

/**
 * Create and start the playback context inside the tap that starts voice:
 * Safari and iPhone only let audio start from a gesture.
 */
export function primeVoicePlayback() {
  if (typeof AudioContext === 'undefined') return;
  if (!primed || primed.state === 'closed')
    primed = new AudioContext({ latencyHint: 'interactive' });
  void primed.resume().catch(() => undefined);
}

type Loopback = { input: AudioNode; close(): void };

/** Route a context's output through a peer connection pair inside the page. */
async function loopbackOutput(context: AudioContext): Promise<Loopback> {
  const destination = context.createMediaStreamDestination();
  const sender = new RTCPeerConnection({ iceServers: [] });
  const receiver = new RTCPeerConnection({ iceServers: [] });
  const element = new Audio();
  const close = () => {
    sender.close();
    receiver.close();
    element.srcObject = null;
  };
  try {
    sender.onicecandidate = (event) =>
      event.candidate && void receiver.addIceCandidate(event.candidate);
    receiver.onicecandidate = (event) =>
      event.candidate && void sender.addIceCandidate(event.candidate);
    const track = new Promise<MediaStream>((resolve) => {
      receiver.ontrack = (event) =>
        resolve(event.streams[0] ?? new MediaStream([event.track]));
    });
    destination.stream
      .getTracks()
      .forEach((item) => sender.addTrack(item, destination.stream));
    const offer = await sender.createOffer();
    await sender.setLocalDescription(offer);
    await receiver.setRemoteDescription(offer);
    const answer = await receiver.createAnswer();
    await receiver.setLocalDescription(answer);
    await sender.setRemoteDescription(answer);
    const stream = await Promise.race([
      track,
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error('loopback_timeout')), 2000),
      ),
    ]);
    element.srcObject = stream;
    await element.play();
    return { input: destination, close };
  } catch (error) {
    close();
    throw error;
  }
}

/**
 * Read a stream's loudness each frame (for Buddy's lip sync) without playing
 * it again; returns a stop. Live voice's OpenAI call plays its own speech.
 */
export function followStreamLevel(
  stream: MediaStream,
  level: (value: number) => void,
): () => void {
  if (typeof AudioContext === 'undefined') return () => undefined;
  const context = new AudioContext();
  const analyser = context.createAnalyser();
  analyser.fftSize = 512;
  context.createMediaStreamSource(stream).connect(analyser);
  const samples = new Float32Array(analyser.fftSize);
  let frame = 0;
  const measure = () => {
    analyser.getFloatTimeDomainData(samples);
    let sum = 0;
    for (const sample of samples) sum += sample * sample;
    level(Math.min(1, Math.sqrt(sum / samples.length) * 5));
    frame = requestAnimationFrame(measure);
  };
  frame = requestAnimationFrame(measure);
  return () => {
    cancelAnimationFrame(frame);
    level(0);
    void context.close().catch(() => undefined);
  };
}

/**
 * Feed an open microphone stream through the capture worklet: 16 kHz frames
 * and their loudness go to the callbacks. On failure the stream is stopped.
 */
export async function captureFrames(
  stream: MediaStream,
  callbacks: Pick<CaptureCallbacks, 'frame' | 'level'>,
): Promise<AudioContext> {
  let context: AudioContext;
  try {
    context = new AudioContext({
      sampleRate: 16000,
      latencyHint: 'interactive',
    });
  } catch {
    context = new AudioContext({ latencyHint: 'interactive' });
  }
  try {
    await context.audioWorklet.addModule(captureWorkletUrl);
    const source = context.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(context, 'row-bot-capture');
    node.port.onmessage = (message) => {
      const { pcm, level } = message.data as {
        pcm: ArrayBuffer;
        level: number;
      };
      callbacks.frame(pcm);
      callbacks.level(level);
    };
    const sink = context.createGain();
    sink.gain.value = 0;
    source.connect(node).connect(sink).connect(context.destination);
  } catch (error) {
    stream.getTracks().forEach((track) => track.stop());
    void context.close();
    throw error;
  }
  return context;
}

export class BrowserVoiceAudio implements VoiceAudio {
  private stream: MediaStream | null = null;
  private captureContext: AudioContext | null = null;
  private capture: CaptureCallbacks | null = null;
  private playback: AudioContext | null = null;
  private output: GainNode | null = null;
  private analyser: AnalyserNode | null = null;
  private loopback: Loopback | null = null;
  private callbacks: PlaybackCallbacks | null = null;
  private rate = 24000;
  private nextAt = 0;
  private sources = new Set<AudioBufferSourceNode>();
  private started = new Set<number>();
  private meter = 0;
  private closed = false;
  private micEnabled = true;
  // The microphone it was opened for, and whether the default stands in.
  private micChosen = '';
  private fellBack = { input: false, output: false };
  private noInput = false;
  private notice: DeviceNotice | null = null;
  private unfollow: (() => void) | null = null;

  async startCapture(callbacks: CaptureCallbacks) {
    this.capture = callbacks;
    await this.openMic();
    await this.openPlayback();
    const media = navigator.mediaDevices;
    const unchoose = onDeviceChoice(() => void this.chosen());
    media.addEventListener?.('devicechange', this.devicesChanged);
    this.unfollow = () => {
      unchoose();
      media.removeEventListener?.('devicechange', this.devicesChanged);
    };
  }

  private async openMic() {
    const chosen = deviceChoice().input;
    const { stream, fellBack } = await openMicrophone(
      CAPTURE_CONSTRAINTS,
      chosen,
    );
    if (this.closed) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    const context = await captureFrames(stream, {
      frame: (pcm) => this.capture?.frame(pcm),
      level: (level) => this.capture?.level(level),
    });
    this.stream = stream;
    this.captureContext = context;
    this.micChosen = chosen;
    this.fellBack.input = fellBack;
    this.noInput = false;
    stream.getAudioTracks().forEach((track) => {
      track.enabled = this.micEnabled;
      // The browser ends the track when the device goes away.
      track.addEventListener('ended', this.micLost);
    });
    this.tell();
  }

  private micLost = () => {
    if (!this.closed && this.stream) void this.reopenMic();
  };

  private async reopenMic() {
    this.closeMic();
    try {
      await this.openMic();
    } catch {
      // No microphone left: listening waits for one to be connected.
      this.noInput = true;
      this.tell();
    }
  }

  private devicesChanged = () => void this.recheck();

  /** A device came or went: go back to the chosen ones when they're here again. */
  private async recheck() {
    if (this.closed) return;
    const chosen = deviceChoice();
    if (
      this.noInput ||
      (this.stream &&
        this.fellBack.input &&
        (await connected('inputs', chosen.input)))
    )
      await this.reopenMic();
    if (!this.playback || !chosen.output || !canChooseOutput()) return;
    const present = await connected('outputs', chosen.output);
    if (present === this.fellBack.output)
      await this.useSpeaker(present ? chosen.output : '');
  }

  /** The person chose another microphone or speaker. */
  private async chosen() {
    if (this.closed) return;
    const chosen = deviceChoice();
    if (this.stream && chosen.input !== this.micChosen) await this.reopenMic();
    if (this.playback && canChooseOutput())
      await this.useSpeaker(chosen.output);
  }

  private async useSpeaker(id: string) {
    const context = this.playback as
      (AudioContext & { setSinkId?(id: string): Promise<void> }) | null;
    if (!context?.setSinkId) return;
    const wanted = deviceChoice().output;
    try {
      await context.setSinkId(id);
      this.fellBack.output = Boolean(wanted) && id !== wanted;
    } catch {
      // That speaker can't be used (it just went away): the default plays.
      await context.setSinkId('').catch(() => undefined);
      this.fellBack.output = Boolean(wanted);
    }
    this.tell();
  }

  private tell() {
    const notice: DeviceNotice | null = this.noInput
      ? 'no-input'
      : this.fellBack.input
        ? 'input'
        : this.fellBack.output
          ? 'output'
          : null;
    if (notice === this.notice) return;
    this.notice = notice;
    this.capture?.device?.(notice);
  }

  private async openPlayback() {
    if (this.playback) return;
    const context =
      primed && primed.state !== 'closed'
        ? primed
        : new AudioContext({ latencyHint: 'interactive' });
    primed = null;
    const output = context.createGain();
    const analyser = context.createAnalyser();
    analyser.fftSize = 512;
    output.connect(analyser);
    let target: AudioNode = context.destination;
    if (isWebKit()) {
      try {
        this.loopback = await loopbackOutput(context);
        target = this.loopback.input;
      } catch {
        // No loopback (an offline machine with no network interface): the
        // voice still plays; the echo canceller may hear less of it.
        this.loopback = null;
      }
    }
    output.connect(target);
    this.playback = context;
    this.output = output;
    this.analyser = analyser;
    if (context.state === 'suspended') void context.resume();
    const speaker = deviceChoice().output;
    if (speaker && canChooseOutput()) await this.useSpeaker(speaker);
    this.measure();
  }

  private measure = () => {
    if (this.closed || !this.analyser) return;
    const samples = new Float32Array(this.analyser.fftSize);
    this.analyser.getFloatTimeDomainData(samples);
    let sum = 0;
    for (const sample of samples) sum += sample * sample;
    const level = Math.min(1, Math.sqrt(sum / samples.length) * 5);
    if (level > 0.01 || this.meter > 0.01) this.callbacks?.level(level);
    this.meter = level;
    requestAnimationFrame(this.measure);
  };

  private closeMic() {
    this.stream?.getTracks().forEach((track) => {
      track.removeEventListener('ended', this.micLost);
      track.stop();
    });
    this.stream = null;
    void this.captureContext?.close().catch(() => undefined);
    this.captureContext = null;
  }

  pauseCapture() {
    this.closeMic();
  }

  async resumeCapture() {
    if (!this.stream) await this.openMic();
  }

  setMicEnabled(enabled: boolean) {
    this.micEnabled = enabled;
    this.stream?.getAudioTracks().forEach((track) => (track.enabled = enabled));
  }

  onPlayback(callbacks: PlaybackCallbacks) {
    this.callbacks = callbacks;
  }

  setOutputRate(rate: number) {
    if (rate >= 8000 && rate <= 48000) this.rate = rate;
  }

  play(segment: number, pcm: Int16Array) {
    const context = this.playback;
    const output = this.output;
    if (!context || !output || !pcm.length) return;
    const buffer = context.createBuffer(1, pcm.length, this.rate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(output);
    const startAt = Math.max(context.currentTime + 0.02, this.nextAt);
    source.start(startAt);
    this.nextAt = startAt + buffer.duration;
    this.sources.add(source);
    if (!this.started.has(segment)) {
      this.started.add(segment);
      const delay =
        (startAt - context.currentTime + (context.outputLatency || 0)) * 1000;
      // The caption appears as its words start.
      window.setTimeout(
        () => this.callbacks?.started(segment, delay),
        Math.max(0, (startAt - context.currentTime) * 1000),
      );
    }
    source.onended = () => {
      this.sources.delete(source);
      if (!this.sources.size) this.callbacks?.idle();
    };
  }

  duck(on: boolean) {
    const context = this.playback;
    if (!context || !this.output) return;
    this.output.gain.setTargetAtTime(
      on ? DUCKED_GAIN : 1,
      context.currentTime,
      0.03,
    );
  }

  flush() {
    for (const source of this.sources) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        // Already stopped.
      }
    }
    const hadAudio = this.sources.size > 0;
    this.sources.clear();
    this.nextAt = 0;
    if (this.output && this.playback)
      this.output.gain.setValueAtTime(1, this.playback.currentTime);
    if (hadAudio) this.callbacks?.idle();
  }

  close() {
    this.closed = true;
    this.unfollow?.();
    this.unfollow = null;
    this.flush();
    this.pauseCapture();
    this.loopback?.close();
    this.loopback = null;
    void this.playback?.close().catch(() => undefined);
    this.playback = null;
    this.output = null;
    this.analyser = null;
  }
}
