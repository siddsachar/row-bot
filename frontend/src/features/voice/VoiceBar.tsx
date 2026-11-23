import {
  useState,
  useSyncExternalStore,
  type CSSProperties,
  type ReactNode,
} from 'react';
import { Link } from 'react-router-dom';
import {
  Captions,
  CaptionsOff,
  Gauge,
  ImageOff,
  KeyRound,
  Keyboard,
  Languages,
  LocateFixed,
  Maximize2,
  Mic,
  MicOff,
  Minimize2,
  PhoneOff,
  ScreenShareOff,
  SwitchCamera,
  Video,
  VideoOff,
  ShieldCheck,
} from 'lucide-react';
import { Button, IconButton, Menu } from '../../ui/primitives';
import type {
  VoiceFallback,
  VoiceOrb,
  VoiceSession,
  VoiceSnapshot,
} from './voice-session';
import { shareSupport, type ShareSource, type ShareState } from './share';

export type VoiceBarSession = Pick<
  VoiceSession,
  | 'subscribe'
  | 'getSnapshot'
  | 'setMuted'
  | 'hold'
  | 'stopSpeaking'
  | 'resume'
  | 'end'
  | 'startShare'
  | 'stopShare'
  | 'switchCamera'
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

/** The newest result the run made while voice is on, shown in the phone's focus mode. */
export type VoiceResult = { label: string; thumb?: ReactNode; open(): void };

function seconds(ms: number) {
  return `${(ms / 1000).toFixed(1)} s`;
}

/** A language's name in English ("French"), from its code; the code itself when unknown. */
export function languageName(code: string): string {
  try {
    return new Intl.DisplayNames(['en'], { type: 'language' }).of(code) ?? code;
  } catch {
    return code;
  }
}

/** "Replied in 0.8 s · usually 0.9 s · French": the timing, and the language last heard. */
export function detailsLine(
  latency: VoiceSnapshot['latency'],
  heard: string | null,
): string {
  const timing = latency ? latencyLine(latency) : '';
  const language = heard ? languageName(heard) : '';
  return [timing, language].filter(Boolean).join(' · ');
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

const SHARE_LABEL: Record<ShareSource, string> = {
  camera: 'Camera on',
  screen: 'Sharing your screen',
};

/** The live preview of what is shared: mirrored for a camera that faces the person. */
function SharePreview({ share }: { share: ShareState }) {
  return (
    <video
      className="voice-share-preview"
      data-source={share.source}
      data-mirrored={share.facingUser || undefined}
      aria-hidden
      autoPlay
      muted
      playsInline
      ref={(element) => {
        if (element && element.srcObject !== share.stream)
          element.srcObject = share.stream;
      }}
    />
  );
}

/** Start sharing (a camera, or a screen or window where the browser can), or stop with one tap. */
function ShareControl(props: {
  session: VoiceBarSession;
  share: ShareState | null;
  disabled: boolean;
  onBlind(): void;
  blind: boolean;
}) {
  const support = shareSupport();
  if (props.share)
    return (
      <IconButton
        size="sm"
        label="Stop sharing"
        pressed
        onClick={() => props.session.stopShare()}
      >
        {props.share.source === 'screen' ? (
          <ScreenShareOff size={16} aria-hidden />
        ) : (
          <VideoOff size={16} aria-hidden />
        )}
      </IconButton>
    );
  const label = support.screen ? 'Share camera or screen' : 'Share camera';
  const glyph = <Video size={16} aria-hidden />;
  if (props.blind || !support.screen)
    return (
      <IconButton
        size="sm"
        label={label}
        disabled={props.disabled || !support.camera}
        onClick={() =>
          props.blind
            ? props.onBlind()
            : void props.session.startShare('camera')
        }
      >
        {glyph}
      </IconButton>
    );
  return (
    <Menu
      label={label}
      iconOnly
      variant="ghost"
      hint={label}
      disabled={props.disabled}
      className="voice-share-menu"
      actions={[
        {
          label: 'Camera',
          description: 'What your camera sees goes with each thing you say',
          disabled: !support.camera,
          onSelect: () => void props.session.startShare('camera'),
        },
        {
          label: 'Screen or window',
          description: 'What you choose to share goes with each thing you say',
          onSelect: () => void props.session.startShare('screen'),
        },
      ]}
    >
      {glyph}
    </Menu>
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
  /** Bring the waiting approval's card into view. */
  onShowApproval?(): void;
  result?: VoiceResult | null;
}) {
  const state = useSyncExternalStore(
    props.session.subscribe,
    props.session.getSnapshot,
  );
  const [captions, setCaptions] = useState<boolean | null>(null);
  const [details, setDetails] = useState(false);
  const [focus, setFocus] = useState(false);
  // This chat's model can't see images: tapping share says so instead.
  const [blindNotice, setBlindNotice] = useState(false);
  const showCaptions = captions ?? state.captionsDefault;
  const share = state.share;
  const canShare = state.canShare;
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
        {share && (
          <div className="voice-share" role="status">
            <SharePreview share={share} />
            <span className="voice-share-dot" aria-hidden />
            <span className="voice-share-label">
              {SHARE_LABEL[share.source]}
            </span>
            {share.canSwitch && (
              <IconButton
                size="sm"
                label="Switch camera"
                onClick={() => void props.session.switchCamera()}
              >
                <SwitchCamera size={16} aria-hidden />
              </IconButton>
            )}
          </div>
        )}
        {state.fallbacks.length > 0 && (
          <FallbackNotice fallbacks={state.fallbacks} />
        )}
        {blindNotice && canShare === 'blind' && !share && (
          <BlindNotice onClose={() => setBlindNotice(false)} />
        )}
        {showCaptions && (state.you || state.rowBot) && (
          <div className="voice-captions">
            {state.you && (
              <p className="voice-caption" data-role="you">
                <span className="voice-caption-who">You</span>
                <span
                  className="voice-caption-text"
                  dir="auto"
                  lang={state.you.language}
                >
                  {state.you.text || (state.you.final ? '' : '…')}
                </span>
              </p>
            )}
            {state.rowBot && (
              <p
                className="voice-caption"
                data-role="row-bot"
                data-kind={state.rowBot.kind}
              >
                <span className="voice-caption-who">Row-Bot</span>
                <span className="voice-caption-text" dir="auto">
                  {state.rowBot.text}
                </span>
              </p>
            )}
          </div>
        )}
        {state.approval && (
          <div
            className="voice-bar-approval"
            data-voice={state.approval.voice || undefined}
          >
            <ShieldCheck
              className="voice-bar-approval-icon"
              size={16}
              aria-hidden
            />
            <p className="voice-bar-approval-text">
              <span className="voice-bar-approval-action">
                {state.approval.title || 'An action needs your OK'}
              </span>
              <span className="voice-bar-approval-hint">
                {state.approval.voice
                  ? 'Say yes or no, or tap the card'
                  : 'Needs a tap on Approve'}
              </span>
            </p>
            {props.onShowApproval && (
              <IconButton
                size="sm"
                label="Show the approval"
                onClick={() => {
                  setFocus(false);
                  props.onShowApproval?.();
                }}
              >
                <LocateFixed size={16} aria-hidden />
              </IconButton>
            )}
          </div>
        )}
        {focus && props.result && (
          <button
            type="button"
            className="voice-bar-result"
            title={props.result.label}
            onClick={props.result.open}
          >
            {props.result.thumb}
            <span className="voice-bar-result-label">
              <span className="voice-bar-result-kicker">New</span>
              {props.result.label}
            </span>
          </button>
        )}
        {state.unspoken && (
          <p className="voice-bar-notice" role="status">
            <Languages size={16} aria-hidden />
            <span>
              Row-Bot’s voice can’t speak {languageName(state.unspoken)}, so its
              answers stay on screen.
            </span>
          </p>
        )}
        {details && (state.latency || state.heard) && (
          <p className="voice-bar-details">
            {detailsLine(state.latency, state.heard)}
          </p>
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
        {(canShare !== 'no' || share) && orb !== 'ended' && (
          <ShareControl
            session={props.session}
            share={share}
            blind={canShare === 'blind'}
            disabled={orb === 'connecting'}
            onBlind={() => setBlindNotice(!blindNotice)}
          />
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
            !state.latency && !state.heard
              ? 'No details yet'
              : details
                ? 'Hide details'
                : 'Show details'
          }
          pressed={details && Boolean(state.latency || state.heard)}
          disabled={!state.latency && !state.heard}
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

/** A chosen cloud service whose key is missing: what runs instead, and where to add the key. */
export function FallbackNotice(props: {
  fallbacks: VoiceFallback[];
}): ReactNode {
  const said = props.fallbacks.map((fallback) =>
    fallback.part === 'listen'
      ? `Listening with Row-Bot’s own speech model: your ${fallback.company} key is missing.`
      : `Speaking with Kokoro: your ${fallback.company} key is missing.`,
  );
  return (
    <p className="voice-bar-notice" role="status">
      <KeyRound size={16} aria-hidden />
      <span>
        {said.join(' ')}{' '}
        <Link to="/settings/voice">Add it in Settings › Voice</Link>.
      </span>
    </p>
  );
}

/** Why nothing can be shared here, and the way to a model that can see. */
function BlindNotice(props: { onClose(): void }): ReactNode {
  return (
    <p className="voice-bar-notice" role="status">
      <ImageOff size={16} aria-hidden />
      <span>
        This chat’s model can’t see images, so the camera and screen can’t be
        shared. Switch to a model that can, or{' '}
        <Link to="/settings/models#vision-model" onClick={props.onClose}>
          choose a vision model
        </Link>
        .
      </span>
    </p>
  );
}
