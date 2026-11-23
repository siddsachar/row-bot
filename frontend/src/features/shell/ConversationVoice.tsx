import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import type { ClientController } from '../../api/controller';
import type {
  DictationHandle,
  LiveVoiceProvider,
  OutputView,
  TalkStart,
} from '../../api/types';
import type { DictationScope } from './VoiceControls';
import { Button, Menu } from '../../ui/primitives';
import { AudioLines } from 'lucide-react';
import { takeVoiceOnOpen } from './new-chat';
import VoiceBar, {
  type VoiceBarSession,
  type VoiceResult,
} from '../voice/VoiceBar';
import { VoiceSession, voiceError } from '../voice/voice-session';
import { LiveVoiceSession } from '../voice/live-voice-session';
import { primeVoicePlayback } from '../voice/voice-audio';
import {
  LiveVoiceDisclosure,
  liveVoiceActions,
  liveVoiceDisclosed,
} from './live-voice';
import { useOptionalOverlay } from '../../ui/overlays';
import { OutputThumb } from './ContextOutputs';
import { openMediaViewer } from './MediaPreview';
import { outputTitle } from './outputs';

/**
 * What the chat had when voice began (W3): its outputs, or, while the chat's
 * list wasn't read yet (a chat voice opened), the time voice began.
 */
type OutputsBefore = { refs: ReadonlySet<string> | null; since: number };

/** Server times have whole seconds and may run a moment apart from this device's clock. */
const CLOCK_SLACK_MS = 2000;

const outputsBefore = (outputs?: OutputView[]): OutputsBefore => ({
  refs: outputs ? new Set(outputs.map((output) => output.ref)) : null,
  since: Date.now() - CLOCK_SLACK_MS,
});

/** The newest picture or video made since voice began, as the phone's focus mode shows it. */
function newestResult(
  outputs: OutputView[],
  before: OutputsBefore,
  open: (output: OutputView, name: string) => void,
): VoiceResult | null {
  const output = outputs.find(
    (item) =>
      (item.kind === 'image' || item.kind === 'video') &&
      (before.refs
        ? !before.refs.has(item.ref)
        : Date.parse(item.created_at) >= before.since),
  );
  if (!output) return null;
  const name = outputTitle(output);
  return {
    label: name,
    thumb: <OutputThumb output={output} />,
    open: () => open(output, name),
  };
}

/**
 * Voice in a chat, in a bar above the composer: Row-Bot Voice (the default,
 * hands-free, with barge-in) or Live voice (a cloud speech-to-speech provider
 * chosen from the Talk menu, after a one-time disclosure of what it sends).
 */
export default function ConversationVoice(props: {
  controller: ClientController;
  scope: DictationScope;
  available: boolean;
  /** The Live voice fronts and whether each provider's key is saved. */
  liveProviders?: LiveVoiceProvider[];
  /** An incognito chat sends Live voice no memories; its disclosure says so. */
  incognito?: boolean;
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
  /** The chat's outputs list (W3), newest first; undefined until it is read. */
  outputs?: OutputView[];
}) {
  const [voice, setVoice] = useState<{
    session: VoiceBarSession;
    handle: DictationHandle;
    targets: string[];
    label?: string;
    /** What the chat already had when voice began. */
    outputsBefore: OutputsBefore;
  } | null>(null);
  const overlay = useOptionalOverlay();
  const [starting, setStarting] = useState(false);
  const [typing, setTyping] = useState(false);
  // The Live voice provider whose disclosure (or missing key) is showing.
  const [disclosing, setDisclosing] = useState<LiveVoiceProvider | null>(null);
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
      setVoice({
        session,
        handle,
        targets: [...latest.current.targets],
        outputsBefore: outputsBefore(latest.current.outputs),
      });
      setTyping(false);
      await session.start();
    } catch (failure) {
      const code = (failure as { code?: string })?.code ?? '';
      setError(voiceError(code));
    } finally {
      setStarting(false);
    }
  }

  async function startLive(provider: LiveVoiceProvider) {
    if (starting || current.current) return;
    primeVoicePlayback();
    setStarting(true);
    setDisclosing(null);
    setError('');
    try {
      const started = await controller.startRealtime(
        scope,
        { ...context(crypto.randomUUID()), live_provider: provider.id },
        new AbortController().signal,
      );
      const handle = started.snapshot.handle;
      const session = new LiveVoiceSession(
        {
          transport: started.snapshot.transport,
          sessionId: handle.voice_session_id,
          conversationId: handle.conversation_id,
          openSocket: () => controller.openLiveSocket(scope),
          ticket: async () =>
            (await controller.realtimeTicket(scope, handle)).ticket,
          stop: () => controller.stopRealtime(scope, handle),
          exchange: (sdp) =>
            controller.realtimeExchange(
              scope,
              handle,
              sdp,
              new AbortController().signal,
            ),
        },
        started.ticket,
        started.snapshot.run_id,
      );
      setVoice({
        session,
        handle,
        targets: [...latest.current.targets],
        label: provider.label,
        outputsBefore: outputsBefore(latest.current.outputs),
      });
      setTyping(false);
      await session.start();
    } catch (failure) {
      const code = (failure as { code?: string })?.code ?? '';
      setError(voiceError(code));
    } finally {
      setStarting(false);
    }
  }

  function chooseLive(provider: LiveVoiceProvider) {
    if (provider.available && liveVoiceDisclosed(provider.id))
      void startLive(provider);
    else setDisclosing(provider);
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

  const live = props.liveProviders ?? [];
  // The menu shows when Live voice is a choice; a phone with no provider key keeps its one tap.
  const menu =
    live.length > 0 &&
    (!props.startOnOpen || live.some((provider) => provider.available));
  return (
    <div className="conversation-voice" ref={root}>
      {!voice &&
        (menu ? (
          <Menu
            label="Talk"
            iconOnly
            variant="ghost"
            hint={reason}
            disabled={starting}
            actions={[
              {
                label: 'Talk with Row-Bot',
                description: 'Row-Bot Voice, with this chat’s model',
                disabled: !canStart,
                onSelect: () => void startVoice(),
              },
              ...liveVoiceActions(live, chooseLive, !canStart),
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
            label={voice.label}
            result={
              overlay &&
              newestResult(
                props.outputs ?? [],
                voice.outputsBefore,
                (output, name) =>
                  openMediaViewer(overlay, {
                    reference: output.ref,
                    mime: output.mime_type,
                    name,
                    size: output.size,
                  }),
              )
            }
            onShowApproval={() =>
              document
                .querySelector<HTMLElement>('.approval-card')
                ?.scrollIntoView({ block: 'center', behavior: 'smooth' })
            }
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
      {disclosing && !voice && (
        <LiveVoiceDisclosure
          provider={disclosing}
          incognito={props.incognito}
          disabled={!canStart || starting}
          onStart={() => void startLive(disclosing)}
          onClose={() => setDisclosing(null)}
        />
      )}
    </div>
  );
}
