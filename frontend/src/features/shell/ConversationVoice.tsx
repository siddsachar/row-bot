import { useEffect, useMemo, useRef, useState } from 'react';
import type { ClientController } from '../../api/controller';
import type { DictationHandle, TalkStart, VoiceRunView } from '../../api/types';
import type { DictationScope } from './VoiceControls';
import { Button, Field, Select } from '../../ui/primitives';
import TalkControls from './TalkControls';
import RealtimeTalkControls from './RealtimeTalkControls';

export default function ConversationVoice(props: {
  controller: ClientController;
  scope: DictationScope;
  available: boolean;
  disabled: boolean;
  running: boolean;
  context: Omit<TalkStart, 'request_id'> | null;
  targets: string[];
  onBusy(busy: boolean): void;
}) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<'talk' | 'realtime'>('talk');
  const [busy, setBusy] = useState(false);
  const [active, setActive] = useState<{
    handle: DictationHandle;
    run: string | null;
  } | null>(null);
  const [run, setRun] = useState<VoiceRunView | null>(null);
  const [error, setError] = useState('');
  const latest = useRef(props);
  latest.current = props;
  const { clientSessionId, serverEpoch, conversationId, selectionKey } =
    props.scope;
  const scope = useMemo(
    () => ({ clientSessionId, serverEpoch, conversationId, selectionKey }),
    [clientSessionId, serverEpoch, conversationId, selectionKey],
  );
  const controller = props.controller;
  const capturedTargets = useRef<string[]>([]);
  function busyChanged(value: boolean) {
    setBusy(value);
    latest.current.onBusy(value);
    if (!value) {
      setActive(null);
      setRun(null);
    }
  }
  function context(request: string): TalkStart {
    if (!props.context) throw { code: 'model_unavailable' };
    capturedTargets.current = [...props.targets];
    return { ...structuredClone(props.context), request_id: request };
  }
  useEffect(() => () => latest.current.onBusy(false), []);
  useEffect(() => {
    if (!active?.run) return;
    const abort = new AbortController();
    let pending = false,
      terminal = false;
    const read = async () => {
      if (
        pending ||
        terminal ||
        abort.signal.aborted ||
        document.visibilityState === 'hidden'
      )
        return;
      pending = true;
      try {
        const value = await controller.voiceRun(
          scope,
          mode,
          active.handle,
          abort.signal,
        );
        if (abort.signal.aborted || value.run_id !== active.run) return;
        setRun(value);
        terminal = !['running', 'stopping'].includes(value.state);
      } catch {
        if (!abort.signal.aborted) {
          terminal = true;
          setError(
            'Voice could not read this conversation’s result. Stop Talk before trying again.',
          );
        }
      } finally {
        pending = false;
      }
    };
    const timer = setInterval(() => void read(), 1000);
    document.addEventListener('visibilitychange', read);
    void read();
    return () => {
      abort.abort();
      clearInterval(timer);
      document.removeEventListener('visibilitychange', read);
    };
  }, [controller, scope, mode, active]);

  if (!props.available) return null;
  return (
    <div className="conversation-voice">
      {!busy && (
        <Button
          variant="ghost"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
          disabled={busy}
        >
          Talk
        </Button>
      )}
      {open && (
        <section
          aria-label="Conversation voice"
          className="surface conversation-voice-surface"
          aria-busy={busy}
        >
          {busy ? (
            <p className="muted voice-session-summary">
              {mode === 'talk' ? 'Talk' : 'Realtime Talk'} ·{' '}
              {capturedTargets.current.length
                ? `Targets: ${capturedTargets.current.join(' and ')}`
                : 'Chat only'}
            </p>
          ) : (
            <>
              <Field label="Talk mode">
                <Select
                  value={mode}
                  disabled={busy}
                  onChange={(event) => {
                    setMode(event.target.value as 'talk' | 'realtime');
                    setRun(null);
                    setError('');
                  }}
                >
                  <option value="talk">Talk with this conversation</option>
                  <option value="realtime">Realtime Talk</option>
                </Select>
              </Field>
              <p className="voice-intro">
                Spoken requests use this conversation’s selected model and
                approval settings.
              </p>
              <p className="muted voice-target-summary">
                {(busy ? capturedTargets.current : props.targets).length
                  ? `Starting Talk allows spoken requests to change ${(busy ? capturedTargets.current : props.targets).join(' and ')}. These resource targets stay fixed for this voice session.`
                  : 'No resource write targets are selected.'}
              </p>
            </>
          )}
          {mode === 'talk' ? (
            <TalkControls
              scope={scope}
              available={props.available}
              disabled={props.disabled || props.running || !props.context}
              start={async (request, signal) => {
                setError('');
                const value = await controller.startTalk(
                  scope,
                  context(request),
                  signal,
                );
                setActive({ handle: value.handle, run: value.run_id });
                return value;
              }}
              stop={(handle) =>
                controller.voiceControl(scope, 'talk', handle, 'stop')
              }
              heartbeat={(handle, signal) =>
                controller.voiceControl(
                  scope,
                  'talk',
                  handle,
                  'heartbeat',
                  signal,
                )
              }
              transcribe={(handle, utterance, audio, signal) =>
                controller.transcribeTalk(
                  scope,
                  handle,
                  utterance,
                  audio,
                  signal,
                )
              }
              output={(handle, runId, outputId, signal) =>
                controller.talkOutput(scope, handle, runId, outputId, signal)
              }
              run={
                run?.run_id
                  ? {
                      id: run.run_id,
                      state:
                        run.state === 'completed'
                          ? 'completed'
                          : ['running', 'stopping'].includes(run.state)
                            ? 'running'
                            : run.state === 'failed'
                              ? 'failed'
                              : 'cancelled',
                      outputId: run.output_id ?? undefined,
                    }
                  : undefined
              }
              onBusy={busyChanged}
              onSubmitted={(result) => {
                setRun(null);
                setActive({
                  handle: result.snapshot.handle,
                  run: result.run_id,
                });
              }}
            />
          ) : (
            <RealtimeTalkControls
              scope={scope}
              available={props.available}
              disabled={props.disabled || !props.context}
              start={async (request, signal) => {
                setError('');
                const value = await controller.startRealtime(
                  scope,
                  context(request),
                  signal,
                );
                setActive({
                  handle: value.snapshot.handle,
                  run: value.snapshot.run_id,
                });
                return value;
              }}
              stop={(handle) =>
                controller.voiceControl(scope, 'realtime', handle, 'stop')
              }
              heartbeat={(handle, signal) =>
                controller.voiceControl(
                  scope,
                  'realtime',
                  handle,
                  'heartbeat',
                  signal,
                )
              }
              exchange={(handle, sdp, signal) =>
                controller.realtimeExchange(scope, handle, sdp, signal)
              }
              event={(handle, event, signal) =>
                controller.realtimeEvent(scope, handle, event, signal)
              }
              output={
                run?.run_id && run.output_id && run.text
                  ? {
                      id: run.output_id,
                      runId: run.run_id,
                      text: run.text,
                      kind: 'final',
                    }
                  : undefined
              }
              onBusy={busyChanged}
              onRun={(runId) => {
                setRun(null);
                setActive((value) =>
                  value ? { ...value, run: runId } : value,
                );
              }}
            />
          )}
          {props.running && mode === 'talk' && !busy && (
            <p className="voice-state-reason">
              Talk will be available when the current response finishes.
            </p>
          )}
          {!props.context && !props.running && !busy && (
            <p className="voice-state-reason">
              Choose a configured model before starting a voice conversation.
            </p>
          )}
          {error && (
            <p role="alert" className="voice-error">
              {error}
            </p>
          )}
        </section>
      )}
    </div>
  );
}
