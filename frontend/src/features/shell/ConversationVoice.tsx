import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import type { ClientController } from '../../api/controller';
import type { DictationHandle, TalkStart, VoiceRunView } from '../../api/types';
import type { DictationScope } from './VoiceControls';
import { Button, Menu } from '../../ui/primitives';
import { AudioLines } from 'lucide-react';
import { takeVoiceOnOpen } from './new-chat';
import RealtimeTalkControls from './RealtimeTalkControls';
import VoiceBar from '../voice/VoiceBar';
import { VoiceSession, voiceError } from '../voice/voice-session';
import { primeVoicePlayback } from '../voice/voice-audio';

/**
 * Voice in a chat: Row-Bot Voice (the default, hands-free, with barge-in) runs
 * in a bar above the composer; Live voice (OpenAI Realtime) keeps its panel.
 */
export default function ConversationVoice(props: {
  controller: ClientController;
  scope: DictationScope;
  available: boolean;
  /** Live voice (a paid provider) can be chosen here too. */
  liveAvailable?: boolean;
  compact?: boolean;
  /** Render the trigger as a small chevron beside the dictation mic. */
  chevron?: boolean;
  /** One tap starts voice at once (phones). */
  startOnOpen?: boolean;
  unavailableReason?: string;
  disabled: boolean;
  running: boolean;
  context: Omit<TalkStart, 'request_id'> | null;
  targets: string[];
  onBusy(busy: boolean): void;
  /** Show and focus the composer ("Type instead"). */
  focusComposer?(): void;
  phone?: boolean;
}) {
  const [voice, setVoice] = useState<{
    session: VoiceSession;
    handle: DictationHandle;
    targets: string[];
  } | null>(null);
  const [starting, setStarting] = useState(false);
  const [typing, setTyping] = useState(false);
  const [live, setLive] = useState(false);
  const [liveBusy, setLiveBusy] = useState(false);
  const [liveActive, setLiveActive] = useState<{
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
  const current = useRef(voice);
  current.current = voice;
  // The bar is the composer's first row, whatever holds the Talk button.
  const root = useRef<HTMLDivElement>(null);
  const [barHost, setBarHost] = useState<HTMLElement | null>(null);
  useEffect(() => {
    setBarHost(root.current?.closest<HTMLElement>('.composer') ?? null);
  }, []);

  function context(request: string): TalkStart {
    if (!latest.current.context) throw { code: 'model_unavailable' };
    return { ...structuredClone(latest.current.context), request_id: request };
  }

  async function startVoice() {
    if (starting || current.current) return;
    primeVoicePlayback();
    setStarting(true);
    setError('');
    try {
      const started = await controller.startTalk(
        scope,
        context(crypto.randomUUID()),
        new AbortController().signal,
      );
      const handle = started.snapshot.handle;
      const session = new VoiceSession(
        {
          openSocket: () => controller.openVoiceSocket(scope),
          ticket: async () =>
            (await controller.talkTicket(scope, handle)).ticket,
          stop: () => controller.stopTalk(scope, handle),
        },
        started.ticket,
      );
      setVoice({ session, handle, targets: [...latest.current.targets] });
      setTyping(false);
      await session.start();
    } catch (failure) {
      const code = (failure as { code?: string })?.code ?? '';
      setError(voiceError(code));
    } finally {
      setStarting(false);
    }
  }

  // Leaving the chat ends its voice session.
  useEffect(
    () => () => {
      const active = current.current;
      if (active) void active.session.end();
      latest.current.onBusy(false);
    },
    [scope],
  );

  // Home's Talk opened this chat: voice starts once it can, as a tap would.
  useEffect(() => {
    if (
      props.available &&
      props.context &&
      !voice &&
      takeVoiceOnOpen(conversationId, 'talk')
    )
      void startVoice();
    // startVoice reads the latest props through refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.available, props.context, voice, conversationId]);

  // Live voice reads its run's final answer for the provider to speak.
  useEffect(() => {
    if (!liveActive?.run) return;
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
          'realtime',
          liveActive.handle,
          abort.signal,
        );
        if (abort.signal.aborted || value.run_id !== liveActive.run) return;
        setRun(value);
        terminal = !['running', 'stopping'].includes(value.state);
      } catch {
        if (!abort.signal.aborted) {
          terminal = true;
          setError(
            'Voice could not read this conversation’s result. Stop Live voice before trying again.',
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
  }, [controller, scope, liveActive]);

  const canStart =
    props.available &&
    !props.disabled &&
    !props.running &&
    Boolean(props.context);
  const reason = !props.available
    ? props.unavailableReason || 'Talk is unavailable'
    : props.running
      ? 'Talk starts when the current reply finishes'
      : !props.context
        ? 'Choose a model to talk'
        : 'Talk with Row-Bot';
  const portal = (node: ReactNode) =>
    barHost ? createPortal(node, barHost) : node;
  const glyph = <AudioLines size={props.compact ? 18 : 17} aria-hidden />;

  return (
    <div className="conversation-voice" ref={root}>
      {!voice &&
        !liveBusy &&
        (props.liveAvailable && !props.startOnOpen ? (
          <Menu
            label="Talk"
            iconOnly
            variant="ghost"
            actions={[
              {
                label: 'Talk with Row-Bot',
                disabled: !canStart,
                onSelect: () => void startVoice(),
              },
              {
                label: 'Live voice · OpenAI, paid per minute',
                disabled: !canStart,
                onSelect: () => setLive(true),
              },
            ]}
          >
            {glyph}
          </Menu>
        ) : (
          <Button
            iconOnly={props.compact}
            variant="ghost"
            className={props.chevron ? 'conversation-voice-toggle' : undefined}
            aria-label="Talk"
            title={reason}
            disabled={!canStart || starting}
            aria-busy={starting}
            onClick={() => void startVoice()}
          >
            {props.compact || props.chevron ? glyph : 'Talk'}
          </Button>
        ))}
      {voice &&
        portal(
          <VoiceBar
            session={voice.session}
            typing={typing}
            phone={props.phone}
            targets={voice.targets}
            onTypeInstead={() => {
              const next = !typing;
              setTyping(next);
              voice.session.setMuted(next);
              if (next) latest.current.focusComposer?.();
            }}
            onEnded={() => setVoice(null)}
          />,
        )}
      {error && !voice && (
        <p role="alert" className="voice-error conversation-voice-error">
          {error}
        </p>
      )}
      {live && (
        <section
          aria-label="Live voice"
          className="surface conversation-voice-surface"
        >
          <p className="voice-intro">
            Live voice talks through OpenAI Realtime, paid per minute. Spoken
            requests use this chat’s model and approval settings.
          </p>
          <RealtimeTalkControls
            scope={scope}
            available={Boolean(props.liveAvailable)}
            disabled={props.disabled || !props.context}
            start={async (request, signal) => {
              setError('');
              const value = await controller.startRealtime(
                scope,
                context(request),
                signal,
              );
              setLiveActive({
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
            onBusy={(busy) => {
              setLiveBusy(busy);
              latest.current.onBusy(busy);
              if (!busy) {
                setLiveActive(null);
                setRun(null);
              }
            }}
            onRun={(runId) => {
              setRun(null);
              setLiveActive((value) =>
                value ? { ...value, run: runId } : value,
              );
            }}
          />
          {!liveBusy && (
            <Button variant="ghost" onClick={() => setLive(false)}>
              Close
            </Button>
          )}
        </section>
      )}
    </div>
  );
}
