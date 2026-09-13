import { useEffect, useRef, useState } from 'react';
import type {
  DictationHandle,
  DictationResult,
  DictationSnapshot,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { Button } from '../../ui/primitives';

export type DictationScope = Readonly<{
  conversationId: string;
  clientSessionId: string;
  serverEpoch: string;
  selectionKey: string;
}>;

export type VoiceControlsProps = {
  scope: DictationScope;
  available: boolean;
  disabled?: boolean;
  start(requestId: string, signal: AbortSignal): Promise<DictationSnapshot>;
  transcribe(
    handle: DictationHandle,
    utteranceId: string,
    audio: Blob,
    signal: AbortSignal,
  ): Promise<DictationResult>;
  stop(
    handle: DictationHandle,
    signal?: AbortSignal,
  ): Promise<DictationSnapshot>;
  applyTranscript(
    scope: DictationScope,
    result: DictationResult,
  ): 'applied' | 'stale' | 'draft_full';
};

type Stage = 'idle' | 'requesting' | 'recording' | 'transcribing' | 'stopping';
type Attempt = {
  scope: DictationScope;
  abort: AbortController;
  cancelled: boolean;
  handle?: DictationHandle;
  stream?: MediaStream;
  recorder?: MediaRecorder;
  timer?: ReturnType<typeof setTimeout>;
  chunks: Blob[];
  bytes: number;
  maxBytes: number;
  mimeType?: string;
  stop: VoiceControlsProps['stop'];
};

function releaseCapture(attempt: Attempt) {
  clearTimeout(attempt.timer);
  attempt.timer = undefined;
  const stream = attempt.stream;
  attempt.stream = undefined;
  stream?.getTracks().forEach((track) => track.stop());
}

function discard(attempt: Attempt) {
  attempt.cancelled = true;
  attempt.abort.abort();
  releaseCapture(attempt);
  const recorder = attempt.recorder;
  attempt.recorder = undefined;
  if (recorder?.state === 'recording') recorder.stop();
  attempt.chunks = [];
}

export default function VoiceControls(props: VoiceControlsProps) {
  const [stage, setStage] = useState<Stage>('idle');
  const [error, setError] = useState('');
  const [recovery, setRecovery] = useState('');
  const [checking, setChecking] = useState(false);
  const current = useRef<Attempt | null>(null);
  const callbacks = useRef(props);
  callbacks.current = props;

  useEffect(() => {
    setStage('idle');
    setError('');
    setRecovery('');
    setChecking(false);
    return () => {
      const attempt = current.current;
      current.current = null;
      if (attempt) {
        discard(attempt);
        if (attempt.handle)
          void attempt.stop(attempt.handle).catch(() => undefined);
      }
    };
  }, [
    props.scope.conversationId,
    props.scope.clientSessionId,
    props.scope.serverEpoch,
    props.scope.selectionKey,
    props.available,
  ]);

  async function confirmStopped(attempt: Attempt) {
    if (!attempt.handle) return;
    setChecking(true);
    try {
      const stopped = await attempt.stop(attempt.handle);
      if (current.current !== attempt) return;
      if (stopped.quiesced) {
        current.current = null;
        setStage('idle');
      } else setStage('stopping');
    } catch {
      if (current.current === attempt) {
        setStage('stopping');
        setError(
          'Microphone stopped. Server cancellation could not be confirmed. Check voice status again.',
        );
      }
    } finally {
      if (current.current === attempt || current.current === null)
        setChecking(false);
    }
  }

  function cancel() {
    const attempt = current.current;
    if (!attempt) return;
    discard(attempt);
    if (attempt.handle) {
      setStage('stopping');
      void confirmStopped(attempt);
    } else {
      current.current = null;
      setStage('idle');
    }
  }

  function fail(attempt: Attempt, message: string) {
    if (current.current !== attempt) return;
    setError(message);
    discard(attempt);
    if (attempt.handle) {
      setStage('stopping');
      void confirmStopped(attempt);
    } else {
      current.current = null;
      setStage('idle');
    }
  }

  async function submit(attempt: Attempt) {
    releaseCapture(attempt);
    attempt.recorder = undefined;
    if (attempt.cancelled || current.current !== attempt || !attempt.handle)
      return;
    setStage('transcribing');
    const audio = new Blob(attempt.chunks, {
      type: attempt.mimeType || 'audio/webm',
    });
    attempt.chunks = [];
    if (!audio.size || audio.size > attempt.maxBytes) {
      fail(attempt, 'No usable recording was captured. Try dictation again.');
      return;
    }
    try {
      const utteranceId = crypto.randomUUID();
      const result = await callbacks.current.transcribe(
        attempt.handle,
        utteranceId,
        audio,
        attempt.abort.signal,
      );
      if (attempt.cancelled || current.current !== attempt) return;
      if (
        result.utterance_id !== utteranceId ||
        result.snapshot.handle.lease_id !== attempt.handle.lease_id ||
        result.snapshot.handle.voice_session_id !==
          attempt.handle.voice_session_id ||
        result.snapshot.handle.server_epoch !== attempt.handle.server_epoch ||
        result.snapshot.handle.conversation_id !== attempt.scope.conversationId
      ) {
        fail(
          attempt,
          'The dictation result no longer matches this conversation.',
        );
        return;
      }
      const applied =
        result.outcome === 'no_speech'
          ? 'applied'
          : callbacks.current.applyTranscript(attempt.scope, result);
      if (applied === 'draft_full') {
        setRecovery(result.text);
        setError(
          'The draft is full. Your dictation is shown below and has not been added.',
        );
      } else if (result.outcome === 'no_speech')
        setError('No speech was recognized. Try dictation again.');
      current.current = null;
      setStage('idle');
    } catch (cause) {
      if (!attempt.cancelled && current.current === attempt)
        fail(attempt, clientError(cause).message);
    }
  }

  async function begin() {
    if (current.current || !props.available || props.disabled) return;
    setError('');
    setRecovery('');
    if (
      !window.isSecureContext ||
      !navigator.mediaDevices?.getUserMedia ||
      typeof MediaRecorder === 'undefined'
    ) {
      setError(
        'Browser dictation requires a secure connection and microphone support.',
      );
      return;
    }
    const attempt: Attempt = {
      scope: { ...props.scope },
      abort: new AbortController(),
      cancelled: false,
      chunks: [],
      bytes: 0,
      maxBytes: 8 * 1024 * 1024,
      stop: props.stop,
    };
    current.current = attempt;
    setStage('requesting');
    try {
      const snapshot = await props.start(
        crypto.randomUUID(),
        attempt.abort.signal,
      );
      attempt.handle = snapshot.handle;
      if (current.current !== attempt || attempt.cancelled) {
        void attempt.stop(snapshot.handle).catch(() => undefined);
        return;
      }
      if (snapshot.state !== 'capturing') {
        fail(
          attempt,
          'This dictation session is no longer recording. Start dictation again.',
        );
        return;
      }
      if (
        snapshot.handle.conversation_id !== attempt.scope.conversationId ||
        snapshot.handle.server_epoch !== attempt.scope.serverEpoch
      ) {
        fail(
          attempt,
          'This dictation session no longer matches this conversation.',
        );
        return;
      }
      attempt.maxBytes = Math.min(
        snapshot.max_audio_bytes ?? 8 * 1024 * 1024,
        8 * 1024 * 1024,
      );
      const expires = performance.now() + snapshot.expires_in_ms;
      attempt.timer = setTimeout(
        () => fail(attempt, 'The dictation session expired. Try again.'),
        snapshot.expires_in_ms,
      );
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (current.current !== attempt || attempt.cancelled) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      clearTimeout(attempt.timer);
      attempt.stream = stream;
      const mimeType = [
        'audio/webm;codecs=opus',
        'audio/ogg;codecs=opus',
        'audio/mp4',
      ].find((mime) => MediaRecorder.isTypeSupported(mime));
      if (!mimeType) {
        fail(attempt, 'This browser has no supported recording format.');
        return;
      }
      const recorder = new MediaRecorder(stream, { mimeType });
      attempt.mimeType = mimeType;
      attempt.recorder = recorder;
      recorder.ondataavailable = (event) => {
        if (
          attempt.cancelled ||
          current.current !== attempt ||
          !event.data.size
        )
          return;
        if (attempt.bytes + event.data.size > attempt.maxBytes) {
          fail(attempt, 'The recording is too large. Try a shorter dictation.');
          return;
        }
        attempt.bytes += event.data.size;
        attempt.chunks.push(event.data);
      };
      recorder.onerror = () => {
        if (!attempt.cancelled)
          fail(attempt, 'Microphone recording failed. Try dictation again.');
      };
      recorder.onstop = () => {
        void submit(attempt);
      };
      recorder.start(250);
      attempt.timer = setTimeout(
        () => {
          if (
            current.current === attempt &&
            !attempt.cancelled &&
            recorder.state === 'recording'
          )
            recorder.stop();
        },
        Math.max(
          0,
          Math.min(
            snapshot.max_utterance_ms ?? 30000,
            30000,
            expires - performance.now(),
          ),
        ),
      );
      setStage('recording');
    } catch (cause) {
      if (current.current !== attempt || attempt.cancelled) return;
      const denied =
        cause instanceof DOMException && cause.name === 'NotAllowedError';
      fail(
        attempt,
        denied
          ? 'Microphone permission was denied. Allow it in your browser to use dictation.'
          : clientError(cause).message,
      );
    }
  }

  return (
    <div className="stack">
      <div className="actions" role="group" aria-label="Dictation controls">
        {stage === 'idle' && (
          <Button
            disabled={!props.available || props.disabled}
            onClick={() => void begin()}
          >
            Dictate
          </Button>
        )}
        {stage === 'recording' && (
          <Button
            onClick={() => {
              const recorder = current.current?.recorder;
              if (recorder?.state === 'recording') {
                setStage('transcribing');
                recorder.stop();
              }
            }}
          >
            Finish dictation
          </Button>
        )}
        {['requesting', 'recording', 'transcribing'].includes(stage) && (
          <Button onClick={cancel}>Cancel dictation</Button>
        )}
        {stage === 'stopping' && (
          <Button
            disabled={checking}
            onClick={() => {
              if (current.current) void confirmStopped(current.current);
            }}
          >
            Check voice status
          </Button>
        )}
      </div>
      {stage !== 'idle' && (
        <p role="status">
          {stage === 'requesting'
            ? 'Preparing dictation and microphone permission…'
            : stage === 'recording'
              ? 'Listening. Finish dictation to add text to your draft.'
              : stage === 'transcribing'
                ? 'Transcribing your dictation…'
                : 'Microphone stopped. Waiting for server transcription to finish.'}
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {recovery && <div aria-label="Unadded dictation">{recovery}</div>}
    </div>
  );
}
