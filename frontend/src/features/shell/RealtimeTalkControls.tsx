import { useEffect, useRef, useState } from 'react';
import { Button } from '../../ui/primitives';
import type { DictationHandle } from '../../api/types';
import type { DictationScope } from './VoiceControls';
import {
  openRealtimeSession,
  type RealtimeEvent,
  type RealtimeEventResult,
  type RealtimeOutput,
  type RealtimeSession,
  type RealtimeSnapshot,
  type RealtimeStart,
} from '../../api/voice_realtime';

export type RealtimeTalkControlsProps = {
  scope: DictationScope;
  available: boolean;
  disabled?: boolean;
  start(requestId: string, signal: AbortSignal): Promise<RealtimeStart>;
  exchange?(
    handle: DictationHandle,
    sdp: string,
    signal: AbortSignal,
  ): Promise<string>;
  stop(handle: DictationHandle): Promise<RealtimeSnapshot>;
  heartbeat(
    handle: DictationHandle,
    signal: AbortSignal,
  ): Promise<RealtimeSnapshot>;
  event(
    handle: DictationHandle,
    event: RealtimeEvent,
    signal: AbortSignal,
  ): Promise<RealtimeEventResult>;
  output?: RealtimeOutput;
  onBusy?(busy: boolean): void;
  onRun?(runId: string): void;
};
type Attempt = {
  scope: DictationScope;
  stopped: boolean;
  abort: AbortController;
  stop: RealtimeTalkControlsProps['stop'];
  handle?: DictationHandle;
  session?: RealtimeSession;
  heartbeat?: ReturnType<typeof setInterval>;
  expiry?: ReturnType<typeof setTimeout>;
  polling: boolean;
  runId?: string;
};

function dispose(a: Attempt) {
  a.stopped = true;
  a.abort.abort();
  clearInterval(a.heartbeat);
  clearTimeout(a.expiry);
  if (a.session) void a.session.stop();
}

function failureMessage(code: unknown) {
  if (code === 'realtime_auth_unavailable')
    return 'Realtime provider authorization failed. Review the connected provider.';
  if (code === 'realtime_quota_or_rate_limit')
    return 'Realtime provider quota or rate limit reached. Review provider limits before retrying.';
  return 'Realtime Talk stopped. Check voice readiness and the conversation before retrying.';
}

export default function RealtimeTalkControls(props: RealtimeTalkControlsProps) {
  const [state, setState] = useState('idle');
  const [message, setMessage] = useState('');
  const [caption, setCaption] = useState('');
  const [captions, setCaptions] = useState(true);
  const [speech, setSpeech] = useState(true);
  const current = useRef<Attempt | null>(null);
  const latest = useRef(props);
  latest.current = props;
  const muted = useRef(false);

  useEffect(() => {
    setState('idle');
    setMessage('');
    setCaption('');
    return () => {
      const a = current.current;
      current.current = null;
      if (a) {
        dispose(a);
        latest.current.onBusy?.(false);
        if (a.handle) void a.stop(a.handle).catch(() => undefined);
      }
    };
  }, [
    props.scope.conversationId,
    props.scope.clientSessionId,
    props.scope.serverEpoch,
    props.scope.selectionKey,
    props.available,
  ]);

  const live = (a: Attempt) => current.current === a && !a.stopped;
  function identity(a: Attempt, handle: DictationHandle) {
    return (
      handle.conversation_id === a.scope.conversationId &&
      handle.server_epoch === a.scope.serverEpoch &&
      (!a.handle ||
        (handle.lease_id === a.handle.lease_id &&
          handle.voice_session_id === a.handle.voice_session_id))
    );
  }

  async function stop(a: Attempt, reason = '') {
    dispose(a);
    if (current.current === a) {
      setState('stopping');
      if (reason) setMessage(reason);
    }
    if (!a.handle) return;
    try {
      const result = await a.stop(a.handle);
      if (current.current !== a) return;
      if (!identity(a, result.handle))
        throw new Error('voice_identity_changed');
      if (result.quiesced) {
        current.current = null;
        setState('idle');
        latest.current.onBusy?.(false);
      }
    } catch {
      if (current.current === a)
        setMessage(
          'Voice is stopping. Check its status before starting again.',
        );
    }
  }

  function apply(a: Attempt, snapshot: RealtimeSnapshot) {
    if (!live(a)) return;
    if (['stopped', 'stopping', 'error'].includes(snapshot.state)) {
      void stop(a, 'Realtime session ended.');
      return;
    }
    if (
      !identity(a, snapshot.handle) ||
      !Number.isFinite(snapshot.expires_in_ms) ||
      snapshot.expires_in_ms <= 0
    ) {
      void stop(a, 'Realtime session expired.');
      return;
    }
    clearTimeout(a.expiry);
    a.expiry = setTimeout(
      () => void stop(a, 'Realtime session expired.'),
      Math.min(snapshot.expires_in_ms, 120_000),
    );
    setState(snapshot.state);
    if (snapshot.run_id && snapshot.run_id !== a.runId) {
      a.runId = snapshot.run_id;
      latest.current.onRun?.(snapshot.run_id);
    }
  }

  async function start() {
    if (current.current || !props.available || props.disabled) return;
    if (
      !globalThis.isSecureContext ||
      !navigator.mediaDevices?.getUserMedia ||
      typeof RTCPeerConnection === 'undefined'
    ) {
      setMessage(
        'Realtime Talk needs a secure browser context with microphone and WebRTC support.',
      );
      return;
    }
    const a: Attempt = {
      scope: { ...props.scope },
      stopped: false,
      abort: new AbortController(),
      stop: props.stop,
      polling: false,
    };
    current.current = a;
    setState('starting');
    setMessage('');
    setCaption('');
    props.onBusy?.(true);
    try {
      const result = await props.start(crypto.randomUUID(), a.abort.signal);
      if (!identity(a, result.snapshot.handle))
        throw new Error('voice_identity_changed');
      a.handle = result.snapshot.handle;
      if (!live(a)) {
        await stop(a);
        return;
      }
      apply(a, result.snapshot);
      if (!live(a)) return;
      a.heartbeat = setInterval(() => {
        if (!live(a) || a.polling) return;
        a.polling = true;
        void latest.current
          .heartbeat(a.handle!, a.abort.signal)
          .then((snapshot) => apply(a, snapshot))
          .catch(() => {
            if (live(a))
              void stop(a, 'Voice connection lost. Realtime Talk stopped.');
          })
          .finally(() => {
            a.polling = false;
          });
      }, 30_000);
      await openRealtimeSession({
        start: result,
        exchange: props.exchange
          ? (sdp, signal) => props.exchange!(a.handle!, sdp, signal)
          : undefined,
        signal: a.abort.signal,
        current: () => live(a),
        event: (event, signal) =>
          latest.current.event(a.handle!, event, signal),
        onSession: (session) => {
          a.session = session;
          session.mute(muted.current);
          if (!live(a)) void session.stop();
        },
        onSnapshot: (snapshot) => apply(a, snapshot),
        onCaption: (text) => {
          if (live(a)) setCaption(text);
        },
        onFailure: (code) => {
          if (live(a)) void stop(a, failureMessage(code));
        },
      });
    } catch (error) {
      const code =
        error && typeof error === 'object' && 'code' in error ? error.code : '';
      const reason = failureMessage(code);
      if (current.current === a) {
        if (a.handle) await stop(a, reason);
        else {
          dispose(a);
          current.current = null;
          setState('idle');
          props.onBusy?.(false);
          setMessage(reason);
        }
      }
    }
  }

  useEffect(() => {
    const a = current.current;
    if (a && !a.stopped && props.output) a.session?.sendOutput(props.output);
  }, [props.output, state]);

  const label =
    state === 'idle'
      ? ''
      : state === 'stopping'
        ? 'Stopping…'
        : state === 'speaking'
          ? 'Speaking…'
          : ['thinking', 'consulting_row_bot'].includes(state)
            ? 'Thinking…'
            : [
                  'listening',
                  'connected',
                  'user_speaking',
                  'interrupted',
                ].includes(state)
              ? 'Listening…'
              : 'Connecting…';
  return (
    <div className="voice-controls" aria-label="Realtime Talk controls">
      {state === 'idle' ? (
        <Button
          disabled={!props.available || props.disabled}
          onClick={() => void start()}
        >
          Talk
        </Button>
      ) : (
        <Button
          onClick={() => {
            const a = current.current;
            if (a) void stop(a);
          }}
        >
          {state === 'stopping' ? 'Check voice status' : 'Stop Talk'}
        </Button>
      )}
      <Button
        aria-pressed={speech}
        onClick={() => {
          muted.current = speech;
          setSpeech(!speech);
          current.current?.session?.mute(speech);
        }}
      >
        Speech
      </Button>
      <Button aria-pressed={captions} onClick={() => setCaptions(!captions)}>
        Captions
      </Button>
      <span role="status">{label}</span>
      {captions && caption && <p aria-label="Voice caption">{caption}</p>}
      {message && <p role="alert">{message}</p>}
    </div>
  );
}
