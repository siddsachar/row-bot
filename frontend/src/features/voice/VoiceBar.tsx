import { useState, useSyncExternalStore, type CSSProperties } from 'react';
import {
  Captions,
  CaptionsOff,
  Gauge,
  Keyboard,
  Maximize2,
  Mic,
  MicOff,
  Minimize2,
  PhoneOff,
} from 'lucide-react';
import { Button, IconButton } from '../../ui/primitives';
import type { VoiceOrb, VoiceSession, VoiceSnapshot } from './voice-session';

export type VoiceBarSession = Pick<
  VoiceSession,
  | 'subscribe'
  | 'getSnapshot'
  | 'setMuted'
  | 'hold'
  | 'stopSpeaking'
  | 'resume'
  | 'end'
>;

const STATE_LABEL: Record<VoiceOrb, string> = {
  connecting: 'Connecting…',
  listening: 'Listening',
  thinking: 'Thinking',
  speaking: 'Speaking',
  waiting: 'Waiting for you',
  muted: 'Muted',
  paused: 'Paused',
  ended: 'Voice ended',
};

function seconds(ms: number) {
  return `${(ms / 1000).toFixed(1)} s`;
}

/** "Replied in 0.8 s · usually 0.9 s", from the host's latency marks. */
export function latencyLine(latency: VoiceSnapshot['latency']): string {
  if (!latency) return 'No timing yet';
  const last = latency.last.voice_to_ear ?? latency.last.voice_to_voice;
  const stage =
    latency.summary.stages.voice_to_ear ??
    latency.summary.stages.voice_to_voice;
  if (last === undefined) return 'No timing yet';
  const usual =
    stage && latency.summary.turns > 1
      ? ` · usually ${seconds(stage.p50)}`
      : '';
  return `Replied in ${seconds(last)}${usual}`;
}

function LevelMeter({ level, active }: { level: number; active: boolean }) {
  const bars = [0.2, 0.45, 0.7, 0.45, 0.2];
  return (
    <span className="voice-meter" aria-hidden data-active={active}>
      {bars.map((weight, index) => (
        <span
          key={index}
          style={
            {
              '--bar': active
                ? Math.max(0.12, Math.min(1, level * (0.6 + weight)))
                : 0.12,
            } as CSSProperties
          }
        />
      ))}
    </span>
  );
}

export default function VoiceBar(props: {
  session: VoiceBarSession;
  typing: boolean;
  onTypeInstead(): void;
  onEnded(): void;
  phone?: boolean;
  /** Resources spoken requests may change, fixed when voice started. */
  targets?: string[];
  /** Live voice: the provider talking, named beside the state. */
  label?: string;
}) {
  const state = useSyncExternalStore(
    props.session.subscribe,
    props.session.getSnapshot,
  );
  const [captions, setCaptions] = useState<boolean | null>(null);
  const [details, setDetails] = useState(false);
  const [focus, setFocus] = useState(false);
  const showCaptions = captions ?? state.captionsDefault;
  const orb = state.orb;
  const orbAction =
    orb === 'speaking'
      ? { label: 'Stop speaking', run: () => props.session.stopSpeaking() }
      : orb === 'paused'
        ? { label: 'Resume voice', run: () => void props.session.resume() }
        : orb === 'muted'
          ? { label: 'Unmute', run: () => props.session.setMuted(false) }
          : null;
  const label =
    orb === 'paused' ? 'Paused · tap to resume' : (STATE_LABEL[orb] ?? 'Voice');
  const end = () => {
    void props.session.end();
    props.onEnded();
  };
  const hold = (held: boolean) => props.session.hold(held);
  return (
    <section
      className="voice-bar"
      data-state={orb}
      data-focus={focus || undefined}
      aria-label="Voice conversation"
    >
      <button
        type="button"
        className="voice-orb"
        data-state={orb}
        aria-label={orbAction?.label ?? label}
        title={orbAction?.label ?? label}
        disabled={!orbAction}
        onClick={orbAction?.run}
        style={
          {
            '--level': orb === 'listening' ? state.inputLevel : 0,
          } as CSSProperties
        }
      >
        <span className="voice-orb-core" aria-hidden />
        <span className="voice-orb-ring" aria-hidden />
        {orb === 'muted' && (
          <MicOff className="voice-orb-glyph" size={18} aria-hidden />
        )}
      </button>
      <div className="voice-bar-body">
        <p className="voice-bar-status" aria-live="polite">
          <span className="voice-bar-state">
            {props.label ? `${props.label} · ${label}` : label}
          </span>
          <LevelMeter level={state.inputLevel} active={orb === 'listening'} />
        </p>
        {showCaptions && (state.you || state.rowBot) && (
          <div className="voice-captions">
            {state.you && (
              <p className="voice-caption" data-role="you">
                <span className="voice-caption-who">You</span>
                <span className="voice-caption-text">
                  {state.you.text || (state.you.final ? '' : '…')}
                </span>
              </p>
            )}
            {state.rowBot && (
              <p className="voice-caption" data-role="row-bot">
                <span className="voice-caption-who">Row-Bot</span>
                <span className="voice-caption-text">{state.rowBot.text}</span>
              </p>
            )}
          </div>
        )}
        {details && state.latency && (
          <p className="voice-bar-details">{latencyLine(state.latency)}</p>
        )}
        {Boolean(props.targets?.length) && (
          <p className="voice-bar-targets">
            Spoken requests can change {props.targets!.join(' and ')}
          </p>
        )}
        {state.error && (
          <p role="alert" className="voice-bar-error">
            {state.error}
          </p>
        )}
      </div>
      <div className="voice-bar-actions">
        {state.pushToTalk && orb !== 'ended' && (
          <Button
            className="voice-hold"
            variant={state.held ? 'primary' : 'secondary'}
            aria-pressed={state.held}
            onPointerDown={(event) => {
              event.currentTarget.setPointerCapture?.(event.pointerId);
              hold(true);
            }}
            onPointerUp={() => hold(false)}
            onPointerCancel={() => hold(false)}
            onKeyDown={(event) => {
              if ((event.key === ' ' || event.key === 'Enter') && !event.repeat)
                hold(true);
            }}
            onKeyUp={(event) => {
              if (event.key === ' ' || event.key === 'Enter') hold(false);
            }}
          >
            Hold to talk
          </Button>
        )}
        <IconButton
          size="sm"
          label={showCaptions ? 'Hide captions' : 'Show captions'}
          pressed={showCaptions}
          onClick={() => setCaptions(!showCaptions)}
        >
          {showCaptions ? (
            <Captions size={16} aria-hidden />
          ) : (
            <CaptionsOff size={16} aria-hidden />
          )}
        </IconButton>
        <IconButton
          size="sm"
          label={state.muted ? 'Unmute' : 'Mute'}
          pressed={state.muted}
          disabled={orb === 'ended' || orb === 'connecting'}
          onClick={() => props.session.setMuted(!state.muted)}
        >
          {state.muted ? (
            <MicOff size={16} aria-hidden />
          ) : (
            <Mic size={16} aria-hidden />
          )}
        </IconButton>
        <IconButton
          size="sm"
          label={props.typing ? 'Hide the keyboard' : 'Type instead'}
          pressed={props.typing}
          onClick={props.onTypeInstead}
        >
          <Keyboard size={16} aria-hidden />
        </IconButton>
        <IconButton
          size="sm"
          label={
            !state.latency
              ? 'No timing yet'
              : details
                ? 'Hide timing'
                : 'Show timing'
          }
          pressed={details && Boolean(state.latency)}
          disabled={!state.latency}
          onClick={() => setDetails(!details)}
        >
          <Gauge size={16} aria-hidden />
        </IconButton>
        {props.phone && (
          <IconButton
            size="sm"
            label={focus ? 'Leave full screen' : 'Full screen'}
            pressed={focus}
            onClick={() => setFocus(!focus)}
          >
            {focus ? (
              <Minimize2 size={16} aria-hidden />
            ) : (
              <Maximize2 size={16} aria-hidden />
            )}
          </IconButton>
        )}
        <IconButton size="sm" variant="danger" label="End voice" onClick={end}>
          <PhoneOff size={16} aria-hidden />
        </IconButton>
      </div>
    </section>
  );
}
