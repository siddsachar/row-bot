/**
 * Sharing the camera or a screen during voice. Nothing is captured before the
 * person starts sharing, and nothing is streamed: when a still is needed this
 * side takes one, scaled to at most 768 px on its long side, as a JPEG.
 * Stopping (from the bar, the browser's own stop button, or by ending voice)
 * releases the camera or screen at once.
 */

export type ShareSource = 'camera' | 'screen';

/** Stills are scaled to this many pixels on their long side, at most. */
export const STILL_SIDE = 768;
const STILL_QUALITY = 0.8;

export type ShareStarted = {
  stream: MediaStream;
  /** The camera faces the person: its preview is mirrored (see mirrorPreview). */
  facingUser: boolean;
  /** More than one camera to switch between. */
  canSwitch: boolean;
};

export interface ShareCapture {
  start(source: ShareSource): Promise<ShareStarted>;
  /** The next camera (front and back on a phone, the next webcam on a computer). */
  switchCamera(): Promise<ShareStarted>;
  /** One still of what is shared now, or null when nothing is. */
  still(): Promise<Blob | null>;
  /** Called once when the browser ends sharing itself (its own stop button). */
  onEnded(ended: () => void): void;
  stop(): void;
}

type Devices = MediaDevices & {
  getDisplayMedia?: (
    constraints: MediaStreamConstraints,
  ) => Promise<MediaStream>;
};

function devices(): Devices | undefined {
  return (globalThis.navigator as Navigator | undefined)?.mediaDevices as
    Devices | undefined;
}

/** What this browser can share: a camera, and a screen or window (desktop browsers and the Windows app). */
export function shareSupport(): Record<ShareSource, boolean> {
  const media = devices();
  return {
    camera: typeof media?.getUserMedia === 'function',
    screen: typeof media?.getDisplayMedia === 'function',
  };
}

/** Why sharing could not start, in plain words; '' when the person simply cancelled the picker. */
export function shareError(error: unknown, source: ShareSource) {
  const name = (error as { name?: string })?.name ?? '';
  if (source === 'screen')
    return name === 'NotAllowedError' || name === 'AbortError'
      ? ''
      : 'Screen sharing isn’t available here.';
  if (name === 'NotAllowedError' || name === 'SecurityError')
    return 'Row-Bot can’t use the camera. Check its permission.';
  if (name === 'NotFoundError' || name === 'OverconstrainedError')
    return 'No camera was found.';
  return 'The camera couldn’t start. Try again.';
}

/**
 * Whether the preview is mirrored, like a selfie view: only a camera facing
 * the person (a phone's front camera, or a computer's webcam, which reports no
 * facing). Never the back camera or a shared screen. Only the preview is
 * mirrored: a still is always drawn as the camera sees it.
 */
export function mirrorPreview(source: ShareSource, facingMode?: string) {
  return source === 'camera' && facingMode !== 'environment';
}

/** The size of a still: the frame scaled down to fit STILL_SIDE, never up. */
export function stillSize(width: number, height: number) {
  const scale = Math.min(1, STILL_SIDE / Math.max(width, height, 1));
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

export class BrowserShare implements ShareCapture {
  private stream: MediaStream | null = null;
  private video: HTMLVideoElement | null = null;
  private ended: (() => void) | null = null;
  private source: ShareSource = 'camera';
  private camera = 0;

  onEnded(ended: () => void) {
    this.ended = ended;
  }

  async start(source: ShareSource): Promise<ShareStarted> {
    this.stop();
    this.source = source;
    const media = devices();
    if (!media) throw new DOMException('No media devices', 'NotFoundError');
    const stream =
      source === 'screen'
        ? await media.getDisplayMedia!({
            // Only stills are taken: a low frame rate keeps the computer quiet.
            video: { frameRate: { ideal: 5, max: 10 } },
            audio: false,
          })
        : await media.getUserMedia({ video: this.cameraConstraints() });
    return this.attach(stream);
  }

  async switchCamera(): Promise<ShareStarted> {
    this.camera += 1;
    const stream = await devices()!.getUserMedia({
      video: await this.nextCamera(),
    });
    return this.attach(stream);
  }

  private cameraConstraints(extra: MediaTrackConstraints = {}) {
    return {
      width: { ideal: 1280 },
      height: { ideal: 720 },
      frameRate: { ideal: 15 },
      ...extra,
    };
  }

  /** A phone switches front and back; a computer goes to its next webcam. */
  private async nextCamera(): Promise<MediaTrackConstraints> {
    const track = this.stream?.getVideoTracks()[0];
    const facing = track?.getSettings?.().facingMode;
    this.release();
    if (facing === 'user' || facing === 'environment')
      return this.cameraConstraints({
        facingMode: facing === 'user' ? 'environment' : 'user',
      });
    const cameras = (await devices()!.enumerateDevices()).filter(
      (device) => device.kind === 'videoinput',
    );
    const next = cameras[this.camera % Math.max(1, cameras.length)];
    return this.cameraConstraints(
      next?.deviceId ? { deviceId: { exact: next.deviceId } } : {},
    );
  }

  private async attach(stream: MediaStream): Promise<ShareStarted> {
    this.release();
    this.stream = stream;
    const track = stream.getVideoTracks()[0];
    track?.addEventListener('ended', () => {
      if (this.stream === stream) {
        this.stop();
        this.ended?.();
      }
    });
    const video = document.createElement('video');
    video.muted = true;
    video.playsInline = true;
    video.srcObject = stream;
    this.video = video;
    void video.play().catch(() => undefined);
    let canSwitch = false;
    if (this.source === 'camera') {
      try {
        canSwitch =
          (await devices()!.enumerateDevices()).filter(
            (device) => device.kind === 'videoinput',
          ).length > 1;
      } catch {
        canSwitch = false;
      }
    }
    return {
      stream,
      facingUser: mirrorPreview(this.source, track?.getSettings?.().facingMode),
      canSwitch,
    };
  }

  async still(): Promise<Blob | null> {
    const video = this.video;
    if (!video || !this.stream) return null;
    if (video.readyState < 2)
      await new Promise<void>((resolve) => {
        const done = () => resolve();
        video.addEventListener('loadeddata', done, { once: true });
        setTimeout(done, 1000);
      });
    if (!video.videoWidth || !video.videoHeight) return null;
    const size = stillSize(video.videoWidth, video.videoHeight);
    const canvas = document.createElement('canvas');
    canvas.width = size.width;
    canvas.height = size.height;
    canvas.getContext('2d')?.drawImage(video, 0, 0, size.width, size.height);
    return new Promise((resolve) =>
      canvas.toBlob((blob) => resolve(blob), 'image/jpeg', STILL_QUALITY),
    );
  }

  private release() {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    if (this.video) this.video.srcObject = null;
    this.video = null;
  }

  stop() {
    this.release();
    this.camera = 0;
  }
}

/** What the voice bar shows while sharing. */
export type ShareState = {
  source: ShareSource;
  stream: MediaStream;
  facingUser: boolean;
  canSwitch: boolean;
};

/**
 * One voice session's sharing: the capture, what the bar shows, and telling
 * the host when it starts or stops (the host never sees a still otherwise).
 */
export class VoiceShare {
  state: ShareState | null = null;

  constructor(
    private capture: ShareCapture,
    private hooks: {
      changed(state: ShareState | null, error: string): void;
      tell(source: ShareSource | null): void;
    },
  ) {
    capture.onEnded(() => this.stop());
  }

  async start(source: ShareSource) {
    try {
      const started = await this.capture.start(source);
      this.state = { source, ...started };
      this.hooks.tell(source);
      this.hooks.changed(this.state, '');
    } catch (error) {
      this.capture.stop();
      const had = this.state !== null;
      this.state = null;
      if (had) this.hooks.tell(null);
      this.hooks.changed(null, shareError(error, source));
    }
  }

  async switchCamera() {
    if (this.state?.source !== 'camera') return;
    try {
      this.state = { source: 'camera', ...(await this.capture.switchCamera()) };
      this.hooks.changed(this.state, '');
    } catch (error) {
      this.stop(shareError(error, 'camera'));
    }
  }

  /** Stop sharing; *told* false when the host stopped it. */
  stop(error = '', told = true) {
    this.capture.stop();
    if (!this.state) return;
    this.state = null;
    if (told) this.hooks.tell(null);
    this.hooks.changed(null, error);
  }

  /** A still of what is shared, as bytes; null when nothing is shared. */
  async still(): Promise<ArrayBuffer | null> {
    if (!this.state) return null;
    const blob = await this.capture.still();
    return blob && this.state ? blob.arrayBuffer() : null;
  }
}

/** A still goes up as a ``still`` message with its size, then its bytes, back to back. */
export function sendStill(
  socket: { send(data: string | ArrayBuffer): void },
  data: ArrayBuffer,
) {
  socket.send(JSON.stringify({ type: 'still', bytes: data.byteLength }));
  socket.send(data);
}
