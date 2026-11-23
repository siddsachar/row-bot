/**
 * Voice on the lock screen, in the system's media controls and on headset
 * buttons, through the Media Session API where the browser has it:
 * "Row-Bot · Listening" (or what voice is doing), play and pause unmute and
 * mute, stop and hang up end voice. A browser without it gets nothing. When
 * the controls show at all is the browser's and the system's choice.
 */
import type { VoiceOrb } from './voice-session';

type Controls = { mute(muted: boolean): void; end(): void };
type Session = MediaSession & {
  setMicrophoneActive?(active: boolean): Promise<void> | void;
};

const SAID: Record<VoiceOrb, string> = {
  connecting: 'Connecting',
  listening: 'Listening',
  thinking: 'Thinking',
  speaking: 'Speaking',
  waiting: 'Waiting for you',
  muted: 'Muted',
  paused: 'Paused',
  ended: 'Voice ended',
};
const ACTIONS = ['play', 'pause', 'stop', 'hangup', 'togglemicrophone'];

export class VoicePresence {
  private session: Session | null;
  private title = '';
  private muted: boolean | null = null;

  constructor(
    controls: Controls,
    session: MediaSession | undefined = globalThis.navigator?.mediaSession,
  ) {
    this.session = (session as Session | undefined) ?? null;
    if (!this.session) return;
    this.handle('play', () => controls.mute(false));
    this.handle('pause', () => controls.mute(true));
    this.handle('stop', () => controls.end());
    this.handle('hangup', () => controls.end());
    this.handle('togglemicrophone', () => controls.mute(!this.muted));
  }

  private handle(action: string, run: (() => void) | null) {
    try {
      this.session?.setActionHandler(action as MediaSessionAction, run);
    } catch {
      // An action this browser doesn't know.
    }
  }

  update(state: { orb: VoiceOrb; muted: boolean }) {
    const session = this.session;
    if (!session) return;
    const title = `Row-Bot · ${SAID[state.muted ? 'muted' : state.orb]}`;
    if (title !== this.title) {
      this.title = title;
      try {
        session.metadata = new MediaMetadata({ title });
      } catch {
        // No MediaMetadata here: the controls still work.
      }
    }
    if (state.muted === this.muted) return;
    this.muted = state.muted;
    session.playbackState = state.muted ? 'paused' : 'playing';
    try {
      void Promise.resolve(session.setMicrophoneActive?.(!state.muted)).catch(
        () => undefined,
      );
    } catch {
      // Not allowed outside a call in this browser.
    }
  }

  release() {
    const session = this.session;
    if (!session) return;
    this.session = null;
    ACTIONS.forEach((action) => {
      try {
        session.setActionHandler(action as MediaSessionAction, null);
      } catch {
        // Never set.
      }
    });
    session.metadata = null;
    session.playbackState = 'none';
  }
}
