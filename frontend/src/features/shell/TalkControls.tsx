import { useEffect, useRef, useState } from 'react';
import type { DictationHandle } from '../../api/types';
import type { DictationScope } from './VoiceControls';
import { Button } from '../../ui/primitives';

export type TalkSnapshot = {
  schema_version: 1;
  handle: DictationHandle;
  state:
    | 'listening'
    | 'receiving'
    | 'transcribing'
    | 'thinking'
    | 'speaking'
    | 'stopping'
    | 'stopped';
  quiesced: boolean;
  expires_in_ms: number;
  run_id: string | null;
  transport: 'browser_local';
  max_audio_bytes: number;
  max_utterance_ms: number;
};
export type TalkResult = {
  snapshot: TalkSnapshot;
  utterance_id: string;
  outcome: 'submitted' | 'no_speech';
  text: string;
  run_id: string | null;
};
export type TalkControlsProps = {
  scope: DictationScope;
  available: boolean;
  disabled?: boolean;
  start(requestId: string, signal: AbortSignal): Promise<TalkSnapshot>;
  transcribe(
    handle: DictationHandle,
    utteranceId: string,
    audio: Blob,
    signal: AbortSignal,
  ): Promise<TalkResult>;
  stop(handle: DictationHandle): Promise<TalkSnapshot>;
  heartbeat(
    handle: DictationHandle,
    signal: AbortSignal,
  ): Promise<TalkSnapshot>;
  output(
    handle: DictationHandle,
    runId: string,
    outputId: string,
    signal: AbortSignal,
  ): Promise<Blob>;
  run?: {
    id: string;
    state: 'running' | 'completed' | 'failed' | 'cancelled';
    outputId?: string;
  };
  onBusy?(busy: boolean): void;
  onSubmitted?(result: TalkResult): void;
};

type Stage =
  | 'idle'
  | 'starting'
  | 'listening'
  | 'transcribing'
  | 'thinking'
  | 'speaking'
  | 'stopping';
type Attempt = {
  scope: DictationScope;
  abort: AbortController;
  stopped: boolean;
  handle?: DictationHandle;
  snapshot?: TalkSnapshot;
  runId?: string;
  outputRun?: string;
  stream?: MediaStream;
  recorder?: MediaRecorder;
  context?: AudioContext;
  audio?: HTMLAudioElement;
  audioUrl?: string;
  endPlayback?: () => void;
  captureTimer?: ReturnType<typeof setTimeout>;
  silenceTimer?: ReturnType<typeof setInterval>;
  heartbeatTimer?: ReturnType<typeof setInterval>;
  expiryTimer?: ReturnType<typeof setTimeout>;
  heartbeatPending: boolean;
  stop: TalkControlsProps['stop'];
};

function releaseMic(a: Attempt) {
  clearTimeout(a.captureTimer);
  clearInterval(a.silenceTimer);
  a.captureTimer = a.silenceTimer = undefined;
  const stream = a.stream;
  a.stream = undefined;
  stream?.getTracks().forEach((track) => {
    track.onended = null;
    track.stop();
  });
  const context = a.context;
  a.context = undefined;
  if (context) void context.close().catch(() => undefined);
}

function releaseAudio(a: Attempt) {
  a.audio?.pause();
  if (a.audio) {
    a.audio.onended = null;
    a.audio.onerror = null;
    a.audio.src = '';
  }
  a.audio = undefined;
  if (a.audioUrl) URL.revokeObjectURL(a.audioUrl);
  a.audioUrl = undefined;
  a.endPlayback?.();
  a.endPlayback = undefined;
}

function dispose(a: Attempt) {
  a.stopped = true;
  a.abort.abort();
  clearInterval(a.heartbeatTimer);
  clearTimeout(a.expiryTimer);
  releaseMic(a);
  const recorder = a.recorder;
  a.recorder = undefined;
  if (recorder?.state === 'recording') recorder.stop();
  releaseAudio(a);
}

export default function TalkControls(props: TalkControlsProps) {
  const [stage, setStage] = useState<Stage>('idle');
  const [message, setMessage] = useState('');
  const [caption, setCaption] = useState('');
  const [captions, setCaptions] = useState(true);
  const [speech, setSpeech] = useState(true);
  const current = useRef<Attempt | null>(null);
  const latest = useRef(props);
  latest.current = props;
  const speechEnabled = useRef(speech);
  speechEnabled.current = speech;

  useEffect(() => {
    setStage('idle');
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

  function live(a: Attempt) {
    return current.current === a && !a.stopped;
  }
  function sameHandle(a: Attempt, handle: DictationHandle) {
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
      setStage('stopping');
      if (reason) setMessage(reason);
    }
    if (!a.handle) return; // The issued handle, if any, must first return and be stopped.
    try {
      const result = await a.stop(a.handle);
      if (current.current !== a) return;
      if (!sameHandle(a, result.handle))
        throw new Error('voice_identity_changed');
      if (result.quiesced) {
        current.current = null;
        latest.current.onBusy?.(false);
        setStage('idle');
      }
    } catch {
      if (current.current === a)
        setMessage(
          'Voice is stopping. Check its status before starting again.',
        );
    }
  }

  function expiry(a: Attempt, snapshot: TalkSnapshot) {
    if (snapshot.state === 'stopped' || snapshot.state === 'stopping') {
      void stop(a, 'Talk session ended.');
      return false;
    }
    if (
      !sameHandle(a, snapshot.handle) ||
      !Number.isFinite(snapshot.expires_in_ms) ||
      snapshot.expires_in_ms <= 0
    ) {
      void stop(a, 'Talk session expired.');
      return false;
    }
    a.snapshot = snapshot;
    clearTimeout(a.expiryTimer);
    a.expiryTimer = setTimeout(
      () => void stop(a, 'Talk session expired.'),
      Math.min(snapshot.expires_in_ms, 120_000),
    );
    return true;
  }

  async function listen(a: Attempt) {
    if (!live(a)) return;
    setStage('listening');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!live(a)) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      a.stream = stream;
      stream.getTracks().forEach((track) => {
        track.onended = () => void stop(a, 'Microphone disconnected.');
      });
      const mime = [
        'audio/webm;codecs=opus',
        'audio/ogg;codecs=opus',
        'audio/mp4',
      ].find((type) => MediaRecorder.isTypeSupported(type));
      if (!mime) throw new Error('unsupported_recording');
      const recorder = new MediaRecorder(stream, { mimeType: mime });
      a.recorder = recorder;
      let chunks: Blob[] = [];
      let bytes = 0;
      const utterance = crypto.randomUUID();
      recorder.ondataavailable = (event) => {
        if (!live(a) || a.recorder !== recorder || !event.data.size) return;
        bytes += event.data.size;
        if (bytes > Math.min(a.snapshot?.max_audio_bytes ?? 0, 8_388_608)) {
          chunks = [];
          void stop(a, 'Recording exceeded the audio limit.');
        } else chunks.push(event.data);
      };
      recorder.onerror = () => {
        if (live(a) && a.recorder === recorder)
          void stop(a, 'Microphone recording failed.');
      };
      recorder.onstop = () => {
        if (!live(a) || a.recorder !== recorder || !a.handle) {
          chunks = [];
          return;
        }
        releaseMic(a);
        a.recorder = undefined;
        const audio = new Blob(chunks, { type: mime });
        chunks = [];
        setStage('transcribing');
        void latest.current
          .transcribe(a.handle, utterance, audio, a.abort.signal)
          .then((result) => {
            if (!live(a)) return;
            if (
              result.utterance_id !== utterance ||
              !sameHandle(a, result.snapshot.handle)
            )
              throw new Error('voice_identity_changed');
            if (result.snapshot.state === 'stopped') {
              void stop(a, 'No speech detected. Talk stopped.');
              return;
            }
            if (!expiry(a, result.snapshot)) return;
            setCaption(result.text);
            if (result.outcome === 'submitted' && result.run_id) {
              a.runId = result.run_id;
              setStage('thinking');
              latest.current.onSubmitted?.(result);
            } else void listen(a);
          })
          .catch(() => {
            if (live(a))
              void stop(
                a,
                'Talk could not complete this utterance. Check the conversation before trying again.',
              );
          });
      };
      recorder.start(250);
      a.captureTimer = setTimeout(
        () => {
          if (live(a) && recorder.state === 'recording') recorder.stop();
        },
        Math.min(a.snapshot?.max_utterance_ms ?? 30_000, 30_000),
      );
      // Browser-local VAD only ends an utterance. The existing STT owner decides
      // whether it contains speech; no transcript or provider work runs here.
      if (typeof AudioContext !== 'undefined') {
        const context = new AudioContext();
        a.context = context;
        const analyser = context.createAnalyser();
        analyser.fftSize = 2048;
        context.createMediaStreamSource(stream).connect(analyser);
        const samples = new Float32Array(analyser.fftSize);
        let voiced = 0;
        let quiet = 0;
        a.silenceTimer = setInterval(() => {
          if (!live(a) || recorder.state !== 'recording') return;
          analyser.getFloatTimeDomainData(samples);
          const rms = Math.sqrt(
            samples.reduce((sum, value) => sum + value * value, 0) /
              samples.length,
          );
          if (rms >= 0.02) {
            voiced += 100;
            quiet = 0;
          } else quiet += 100;
          if (voiced >= 200 && quiet >= 900) recorder.stop();
        }, 100);
      }
    } catch {
      if (live(a))
        void stop(
          a,
          'Microphone unavailable. Check permission and audio device access.',
        );
    }
  }

  async function start() {
    if (current.current || !props.available || props.disabled) return;
    if (
      !globalThis.isSecureContext ||
      !navigator.mediaDevices?.getUserMedia ||
      typeof MediaRecorder === 'undefined'
    ) {
      setMessage(
        'Talk needs a secure browser context with microphone recording support.',
      );
      return;
    }
    const a: Attempt = {
      scope: { ...props.scope },
      abort: new AbortController(),
      stopped: false,
      heartbeatPending: false,
      stop: props.stop,
    };
    current.current = a;
    props.onBusy?.(true);
    setStage('starting');
    setMessage('');
    setCaption('');
    try {
      const result = await props.start(crypto.randomUUID(), a.abort.signal);
      a.handle = result.handle;
      if (!live(a)) {
        await stop(a);
        return;
      }
      if (!expiry(a, result)) return;
      a.heartbeatTimer = setInterval(() => {
        if (!live(a) || a.heartbeatPending || !a.handle) return;
        a.heartbeatPending = true;
        void latest.current
          .heartbeat(a.handle, a.abort.signal)
          .then((snapshot) => {
            if (live(a)) expiry(a, snapshot);
          })
          .catch(() => {
            if (live(a)) void stop(a, 'Voice connection lost. Talk stopped.');
          })
          .finally(() => {
            a.heartbeatPending = false;
          });
      }, 30_000);
      await listen(a);
    } catch {
      if (current.current === a) {
        if (a.handle) await stop(a, 'Talk could not start.');
        else {
          dispose(a);
          current.current = null;
          props.onBusy?.(false);
          setStage('idle');
          setMessage('Talk unavailable. Check local speech readiness.');
        }
      }
    }
  }

  useEffect(() => {
    const a = current.current;
    const run = props.run;
    if (
      !a ||
      !live(a) ||
      stage !== 'thinking' ||
      !run ||
      run.id !== a.runId ||
      run.state === 'running' ||
      a.outputRun === run.id
    )
      return;
    a.outputRun = run.id;
    if (run.state !== 'completed' || !run.outputId || !speechEnabled.current) {
      void listen(a);
      return;
    }
    setStage('speaking');
    void (async () => {
      try {
        const blob = await latest.current.output(
          a.handle!,
          run.id,
          run.outputId!,
          a.abort.signal,
        );
        if (!live(a) || !speechEnabled.current) return;
        if (
          !['audio/wav', 'audio/x-wav'].includes(blob.type) ||
          !blob.size ||
          blob.size > 8_388_608
        )
          throw new Error('invalid_voice_audio');
        const url = URL.createObjectURL(blob);
        a.audioUrl = url;
        const audio = new Audio(url);
        a.audio = audio;
        await new Promise<void>((resolve, reject) => {
          const timeout = setTimeout(
            () => reject(new Error('audio_playback_timeout')),
            180_000,
          );
          const end = () => {
            clearTimeout(timeout);
            resolve();
          };
          a.endPlayback = end;
          audio.onended = end;
          audio.onerror = () => {
            clearTimeout(timeout);
            reject(new Error('audio_playback_failed'));
          };
          void audio.play().catch((error: unknown) => {
            clearTimeout(timeout);
            reject(error);
          });
        });
      } catch {
        if (live(a))
          setMessage(
            'Speech playback unavailable. The response remains in the conversation.',
          );
      } finally {
        releaseAudio(a);
        if (live(a)) void listen(a);
      }
    })();
    // Session functions use the admitted attempt and latest callback refs. A
    // render must never release the operation or replay a completed output.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.run?.id, props.run?.state, props.run?.outputId, stage]);

  return (
    <div className="voice-controls" aria-label="Talk controls">
      {stage === 'idle' ? (
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
          {stage === 'stopping' ? 'Check voice status' : 'Stop Talk'}
        </Button>
      )}
      {stage === 'listening' && (
        <Button
          onClick={() => {
            const recorder = current.current?.recorder;
            if (recorder?.state === 'recording') recorder.stop();
          }}
        >
          Send speech
        </Button>
      )}
      <Button
        aria-pressed={speech}
        onClick={() => {
          speechEnabled.current = !speech;
          setSpeech(!speech);
          if (speech && current.current) releaseAudio(current.current);
        }}
      >
        Speech
      </Button>
      <Button aria-pressed={captions} onClick={() => setCaptions(!captions)}>
        Captions
      </Button>
      <span role="status">
        {stage === 'idle'
          ? ''
          : stage === 'listening'
            ? 'Listening…'
            : stage === 'thinking'
              ? 'Thinking…'
              : stage === 'speaking'
                ? 'Speaking…'
                : stage === 'transcribing'
                  ? 'Transcribing…'
                  : stage === 'stopping'
                    ? 'Stopping…'
                    : 'Starting Talk…'}
      </span>
      {captions && caption && <p aria-label="Voice caption">{caption}</p>}
      {message && <p role="alert">{message}</p>}
    </div>
  );
}
