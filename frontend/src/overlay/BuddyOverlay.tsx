import {
  useEffect,
  useEffectEvent,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
  type PointerEvent,
} from 'react';
import {
  ArrowUp,
  CircleStop,
  EyeOff,
  PanelLeft,
  Play,
  ShieldAlert,
  Square,
  SquareArrowOutUpRight,
  TriangleAlert,
} from 'lucide-react';
import type { ClientController } from '../api/controller';
import { clientError } from '../api/errors';
import type { ApprovalView, WriteTarget } from '../api/types';
import type { ClientPlatform, PlatformInfo } from '../platform';
import { BuddyAvatar, rememberBuddyMedia } from '../features/buddy/BuddyAvatar';
import type { BuddyPack, BuddySnapshot } from '../features/shell/BuddyControls';
import { approvalQuestion, keyArgument } from '../features/shell/tool-activity';
import { IconButton } from '../ui/primitives';
import {
  avatarActivity,
  isLive,
  kindLabel,
  latestResponse,
  overlayPhase,
  phaseLabel,
  progressLabel,
} from './overlay-model';
import { useBuddyTarget } from './use-buddy-target';

/** The host reveals the window only after this, or after its own 2s timeout. */
const AVATAR_WAIT_MS = 700;

type Props = {
  controller: ClientController;
  platform: ClientPlatform;
  /** `?conversation=` in a browser or test; the native host decides otherwise. */
  explicitConversation: string | null;
};

type ApprovalHint = { action_label?: string; reason?: string };

function useGlobalBuddy(controller: ClientController) {
  const [snapshot, setSnapshot] = useState<BuddySnapshot | null>(null);
  const [pack, setPack] = useState<BuddyPack | null>(null);
  useEffect(() => {
    const request = new AbortController();
    let timer = 0;
    const load = async () => {
      try {
        const next = await controller.globalBuddy(request.signal);
        const selected = await controller.globalBuddyPack(
          next.preferences.pack_id,
          request.signal,
        );
        if (request.signal.aborted) return;
        setSnapshot(next);
        setPack(selected);
        timer = window.setTimeout(load, 30000);
      } catch {
        if (!request.signal.aborted) timer = window.setTimeout(load, 15000);
      }
    };
    // Buddy reads wait for the authenticated handshake, like the shell's.
    const start = () => {
      if (!controller.getSnapshot().handshake) return false;
      void load();
      return true;
    };
    let unsubscribe = () => {};
    if (!start())
      unsubscribe = controller.subscribe(() => {
        if (start()) unsubscribe();
      });
    return () => {
      unsubscribe();
      request.abort();
      window.clearTimeout(timer);
    };
  }, [controller]);
  return { snapshot, pack };
}

export default function BuddyOverlay({
  controller,
  platform,
  explicitConversation,
}: Props) {
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
  );
  const { conversationId: target } = useBuddyTarget(
    platform,
    explicitConversation,
    state.conversations,
  );
  const [info, setInfo] = useState<PlatformInfo | null>(null);
  useEffect(() => {
    let active = true;
    void platform.discover().then((result) => {
      if (active && result.status === 'ok') setInfo(result.value);
    });
    return () => {
      active = false;
    };
  }, [platform]);
  useEffect(() => {
    document.documentElement.dataset.platform = info?.platform ?? 'browser';
  }, [info]);
  const native = info?.kind === 'pywebview';
  const placementReady = Boolean(
    info?.capabilities.includes('buddy_placement'),
  );
  const mainReady = Boolean(info?.capabilities.includes('main_window'));

  // Follow the target: open it through this window's own session.
  const ready = Boolean(state.handshake);
  useEffect(() => {
    if (!ready || !target) return;
    if (controller.getSnapshot().selectedConversationId !== target)
      void controller.selectConversation(target);
  }, [controller, ready, target]);

  const id = state.selectedConversationId;
  const conversation =
    (state.conversation?.id === id ? state.conversation : null) ??
    state.conversations.find((row) => row.id === id) ??
    null;
  const generation = state.projection?.generation ?? null;
  const rows = state.projection?.rows;
  const response = useMemo(() => latestResponse(rows ?? []), [rows]);

  // A failed run is announced by one generation.error event (B23 rules).
  const lastError =
    [...state.activity]
      .reverse()
      .find((record) => record.event.type === 'generation.error')?.event
      .event_id ?? '';
  const [failure, setFailure] = useState<{
    event: string;
    generation: string;
  } | null>(null);
  const captureFailure = useEffectEvent((event: string) =>
    setFailure({ event, generation: generation?.generation_id ?? '' }),
  );
  useEffect(() => {
    if (lastError) captureFailure(lastError);
  }, [lastError]);
  const settledPhase = overlayPhase(state, failure?.generation ?? null);
  const live = isLive(settledPhase);
  // Only a run this window watched ends with "completed" (the avatar's
  // celebration); opening a finished conversation stays at rest.
  const generationId = generation?.generation_id ?? null;
  const [watched, setWatched] = useState<string | null>(null);
  useEffect(() => {
    if ((live || settledPhase === 'approval') && generationId)
      setWatched(generationId);
  }, [live, settledPhase, generationId]);
  const phase =
    settledPhase === 'completed' && watched !== generationId
      ? 'idle'
      : settledPhase;
  const connected = state.status === 'ready' || state.status === 'loading';

  const { snapshot: buddy, pack } = useGlobalBuddy(controller);
  const loadMedia = useMemo(
    () =>
      rememberBuddyMedia(
        controller,
        (_conversation, packId, asset, revision, signal) =>
          controller.globalBuddyMedia(packId, asset, revision, signal),
      ),
    [controller],
  );
  const avatarSnapshot = useMemo(
    () =>
      buddy ? { ...buddy, activity: avatarActivity(phase, connected) } : null,
    [buddy, phase, connected],
  );

  // Approvals: the live hint first, then the reviewed view with its nonce.
  const approvalId =
    phase === 'approval' ? (generation?.approval_id ?? null) : null;
  const approvalHint = [...state.activity]
    .reverse()
    .find(
      (record) =>
        record.event.type === 'approval.required' &&
        record.event.payload.approval_id === approvalId,
    )?.event;
  const hint: ApprovalHint | undefined =
    approvalHint?.type === 'approval.required'
      ? approvalHint.payload
      : undefined;
  const [approval, setApproval] = useState<ApprovalView | null>(null);
  const [resolving, setResolving] = useState('');
  useEffect(() => {
    setApproval(null);
    setResolving('');
    if (!approvalId) return;
    const abort = new AbortController();
    void controller
      .approval(approvalId, abort.signal)
      .then((view) => {
        if (!abort.signal.aborted) setApproval(view);
      })
      .catch(() => {});
    return () => abort.abort();
  }, [controller, approvalId]);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => setError(''), [id]);
  const [composerHint, setComposerHint] = useState('');
  const turnActive = isLive(phase) || phase === 'approval';
  useEffect(() => {
    if (!turnActive) setComposerHint('');
  }, [turnActive]);
  const draft = id ? controller.getDraft(id) : { text: '', attachments: [] };
  const controls =
    state.workspace?.conversation_id === id
      ? state.workspace?.controls
      : undefined;
  const sendReady = Boolean(
    state.workspace?.actions.find((action) => action.action === 'send')?.ready,
  );
  const turnInFlight = live || phase === 'approval';
  const canSend =
    Boolean(id && conversation) &&
    state.status === 'ready' &&
    !busy &&
    !turnInFlight &&
    Boolean(controls?.model_selection) &&
    sendReady &&
    draft.text.trim().length > 0 &&
    draft.text.length <= 16000;

  // The same default write targets the main composer uses (bound folders and
  // a single bound design), so a turn sent from here acts on the same things.
  function writeTargets(): WriteTarget[] {
    const resources = state.workspace?.resources ?? [];
    const artifacts = resources.filter(
      (item) => item.binding.kind === 'artifact',
    );
    return resources
      .filter(
        (item) =>
          item.available &&
          (item.binding.kind === 'workspace' ||
            (item.binding.kind === 'artifact' && artifacts.length === 1)),
      )
      .map((item) => ({
        kind: item.binding.kind as 'artifact' | 'workspace',
        binding_id: item.binding.binding_id,
        resource_id: item.binding.resource_id,
        binding_revision: item.binding.revision,
        resource_revision: item.resource_revision,
      }));
  }

  async function run(action: () => Promise<unknown>, failure: string) {
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (cause) {
      const safe = clientError(cause);
      setError(safe.code === 'operation_uncertain' ? failure : safe.message);
    } finally {
      setBusy(false);
    }
  }
  function send() {
    if (!canSend || !id || !conversation || !controls?.model_selection) return;
    const captured = controller.getDraft(id);
    const selection = controls.model_selection;
    void run(async () => {
      const receipt = await controller.intent(
        id,
        'conversation.submit',
        {
          submission_id: crypto.randomUUID(),
          text: captured.text,
          attachment_refs: captured.attachments.map((a) => a.attachment_ref),
          model_selection: selection,
          write_targets: writeTargets(),
        },
        conversation.revision,
      );
      if (receipt.status !== 'accepted' && receipt.status !== 'completed')
        throw { code: 'operation_uncertain' };
      // Clear only what was sent; later typing stays.
      if (controller.getDraft(id) === captured)
        controller.setDraft(id, { text: '', attachments: [] });
    }, 'The message may not have been sent. Open the full thread to check before sending again.');
  }
  function stop() {
    if (!id || !conversation || !generation?.can_stop) return;
    void run(
      () =>
        controller.intent(id, 'conversation.stop', {}, conversation.revision),
      'Stop may not have reached Row-Bot. Open the full thread to check.',
    );
  }
  function resume() {
    if (!id || !conversation || !controls?.model_selection) return;
    const selection = controls.model_selection;
    void run(
      () =>
        controller.intent(
          id,
          'conversation.resume',
          { model_selection: selection },
          conversation.revision,
        ),
      'Resume may not have reached Row-Bot. Open the full thread to check.',
    );
  }
  async function resolve(decision: 'approve' | 'reject') {
    if (!approval || resolving) return;
    setResolving(decision);
    setError('');
    try {
      await controller.intent(
        approval.id,
        'approval.resolve',
        { decision, nonce: approval.nonce },
        approval.revision,
      );
    } catch (cause) {
      setResolving('');
      setError(clientError(cause).message);
    }
  }
  async function openFullThread() {
    if (native && mainReady) {
      const result = await platform.showMainWindow(id);
      if (result.status === 'ok') return;
    }
    if (id)
      window.open(
        `/app-v2/conversations/${encodeURIComponent(id)}`,
        '_blank',
        'noopener',
      );
  }
  async function placement(action: 'dock' | 'hide') {
    const result = await platform.buddyPlacement(action);
    if (result.status !== 'ok')
      setError(
        action === 'dock' ? 'Buddy could not dock.' : 'Buddy could not hide.',
      );
  }

  // Keyboard: Enter sends, Shift+Enter breaks the line, Mod+Enter approves.
  function onComposerKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.nativeEvent.isComposing) return;
    if ((event.metaKey || event.ctrlKey) && approval && !resolving) {
      event.preventDefault();
      void resolve('approve');
      return;
    }
    if (event.shiftKey) return;
    event.preventDefault();
    // A message cannot start while this turn is working or waiting: say so
    // instead of ignoring Enter; the draft stays (B98).
    if (turnInFlight && draft.text.trim())
      setComposerHint(
        phase === 'approval'
          ? 'Row-Bot is waiting for your approval. Your message stays here.'
          : 'Row-Bot is still working. Send when it finishes, or Stop it.',
      );
    else send();
  }

  // The header is the drag region. pywebview binds its own drag regions when
  // the page loads, before this renders, so the header moves the window here.
  const drag = useRef<{
    pointer: number;
    offsetX: number;
    offsetY: number;
    startX: number;
    startY: number;
    moving: boolean;
    frame: number;
    x: number;
    y: number;
  } | null>(null);
  function dragStart(event: PointerEvent<HTMLElement>) {
    if (
      event.button !== 0 ||
      !native ||
      (event.target as Element).closest(
        'button, a, input, textarea, [data-no-drag]',
      )
    )
      return;
    drag.current = {
      pointer: event.pointerId,
      offsetX: event.clientX,
      offsetY: event.clientY,
      startX: event.screenX,
      startY: event.screenY,
      moving: false,
      frame: 0,
      x: 0,
      y: 0,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }
  function dragMove(event: PointerEvent<HTMLElement>) {
    const current = drag.current;
    if (!current || current.pointer !== event.pointerId) return;
    if (
      !current.moving &&
      Math.hypot(
        event.screenX - current.startX,
        event.screenY - current.startY,
      ) < 3
    )
      return;
    current.moving = true;
    current.x = event.screenX - current.offsetX;
    current.y = event.screenY - current.offsetY;
    if (current.frame) return;
    current.frame = requestAnimationFrame(() => {
      current.frame = 0;
      platform.moveWindow(current.x, current.y);
    });
  }
  function dragEnd(event: PointerEvent<HTMLElement>) {
    const current = drag.current;
    if (!current || current.pointer !== event.pointerId) return;
    if (current.frame) {
      cancelAnimationFrame(current.frame);
      if (current.moving) platform.moveWindow(current.x, current.y);
    }
    drag.current = null;
  }

  // Reveal once the first view is settled: the conversation (or its absence)
  // is known and the avatar has its still, or AVATAR_WAIT_MS passed.
  const settled =
    Boolean(state.handshake) &&
    !state.loadingConversation &&
    !state.loadingConversations &&
    (!target || Boolean(state.projection) || Boolean(state.error));
  const revealed = useRef(false);
  const reveal = useEffectEvent(() => {
    if (revealed.current) return;
    revealed.current = true;
    document.documentElement.dataset.buddyOverlayReady = String(
      Math.round(performance.now()),
    );
    void platform.buddyPlacement('ready');
  });
  useEffect(() => {
    if (!settled || revealed.current) return;
    const started = performance.now();
    let timer = 0;
    const check = () => {
      const avatar = document.querySelector(
        '.buddy-overlay .buddy-avatar-frame',
      );
      const media = avatar?.getAttribute('data-media');
      if (
        (avatar && media && media !== 'loading') ||
        performance.now() - started >= AVATAR_WAIT_MS
      )
        reveal();
      else timer = window.setTimeout(check, 50);
    };
    check();
    return () => window.clearTimeout(timer);
  }, [settled]);

  // Keep the newest words in view unless the reader scrolled up.
  const body = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  useEffect(() => {
    const element = body.current;
    if (element && pinned.current) element.scrollTop = element.scrollHeight;
  }, [response.text, phase]);

  const title =
    conversation?.title?.trim() ||
    (target ? 'Opening conversation…' : 'No conversation yet');
  // The running step, under any words the turn already has.
  const progress =
    live && (phase === 'tool' || !response.current)
      ? progressLabel(state.activity)
      : '';
  // The latest turn has no reply yet (or was stopped before one): never pass
  // an older answer off as this turn's.
  const unanswered =
    !live &&
    phase !== 'approval' &&
    !response.current &&
    Boolean(rows?.some((row) => row.role === 'user'));
  const shownText =
    live || unanswered
      ? response.current
        ? response.text
        : ''
      : response.text;
  const statusLine = `${kindLabel(conversation?.category)} · ${
    !connected
      ? 'Reconnecting…'
      : phase === 'tool'
        ? progressLabel(state.activity).replace(/…$/, '')
        : phaseLabel(phase)
  }`;
  const announcement =
    phase === 'approval'
      ? `Approval needed: ${approvalQuestion(approval?.action_label || hint?.action_label || '')}`
      : phase === 'streaming'
        ? 'Row-Bot is responding.'
        : phase === 'failed'
          ? 'The response could not finish.'
          : phase === 'interrupted'
            ? 'The response was interrupted.'
            : phase === 'completed'
              ? 'Response ready.'
              : '';
  const actionLabel = approval?.action_label || hint?.action_label || '';
  const argument = keyArgument(approval?.safe_argument_summary);

  return (
    <main
      className="buddy-overlay"
      aria-label="Buddy"
      data-phase={phase}
      data-native={native ? 'true' : 'false'}
    >
      <header
        className="buddy-overlay-header"
        data-draggable={native ? 'true' : 'false'}
        onPointerDown={dragStart}
        onPointerMove={dragMove}
        onPointerUp={dragEnd}
        onPointerCancel={dragEnd}
        onLostPointerCapture={dragEnd}
      >
        <span
          className="buddy-overlay-avatar"
          data-activity={avatarSnapshot?.activity ?? 'idle'}
        >
          {avatarSnapshot ? (
            <BuddyAvatar
              conversation={null}
              pack={pack}
              snapshot={avatarSnapshot}
              loadMedia={loadMedia}
            />
          ) : (
            <span
              className="buddy-avatar-frame"
              data-media="loading"
              aria-hidden="true"
            />
          )}
        </span>
        <div className="buddy-overlay-heading">
          <h1 title={title}>{title}</h1>
          <p>{statusLine}</p>
        </div>
        <div
          className="buddy-overlay-actions"
          role="toolbar"
          aria-label="Buddy actions"
        >
          <IconButton
            size="sm"
            label="Open full thread"
            disabled={!id}
            onClick={() => void openFullThread()}
          >
            <SquareArrowOutUpRight size={15} aria-hidden />
          </IconButton>
          <IconButton
            size="sm"
            label="Dock Buddy"
            disabled={!placementReady}
            onClick={() => void placement('dock')}
          >
            <PanelLeft size={15} aria-hidden />
          </IconButton>
          <IconButton
            size="sm"
            label="Hide Buddy"
            disabled={!placementReady}
            onClick={() => void placement('hide')}
          >
            <EyeOff size={15} aria-hidden />
          </IconButton>
        </div>
      </header>

      <div
        ref={body}
        className="buddy-overlay-body"
        role="region"
        aria-label="Latest response"
        tabIndex={0}
        onScroll={(event) => {
          const element = event.currentTarget;
          pinned.current =
            element.scrollHeight - element.scrollTop - element.clientHeight < 8;
        }}
      >
        {shownText && (
          <p
            className="buddy-overlay-response"
            data-live={live && !progress ? 'true' : 'false'}
          >
            {shownText}
          </p>
        )}
        {progress && <p className="buddy-overlay-progress">{progress}</p>}
        {shownText || progress ? null : unanswered && phase === 'stopped' ? (
          <div className="buddy-overlay-notice" data-tone="neutral">
            <CircleStop size={14} aria-hidden />
            <span>
              Stopped before a reply. Your last message wasn’t answered.
            </span>
          </div>
        ) : unanswered && phase !== 'interrupted' && phase !== 'failed' ? (
          <p className="buddy-overlay-empty">
            {busy ? 'Sending…' : 'Your last message has no reply.'}
          </p>
        ) : unanswered ? null : (
          <p className="buddy-overlay-empty">
            {!id
              ? target
                ? 'Opening the conversation…'
                : 'Start a conversation in Row-Bot and Buddy follows it here.'
              : state.loadingConversation
                ? 'Opening the conversation…'
                : 'Ready when you are.'}
          </p>
        )}
        {phase === 'interrupted' && (
          <div className="buddy-overlay-notice" data-tone="warning">
            <TriangleAlert size={14} aria-hidden />
            <span>The response was interrupted.</span>
            <button
              type="button"
              className="buddy-overlay-text-action"
              disabled={busy || !controls?.model_selection}
              onClick={resume}
            >
              <Play size={12} aria-hidden />
              Resume
            </button>
          </div>
        )}
        {phase === 'failed' && (
          <div className="buddy-overlay-notice" data-tone="danger">
            <TriangleAlert size={14} aria-hidden />
            <span>The response could not finish.</span>
            <button
              type="button"
              className="buddy-overlay-text-action"
              onClick={() => void openFullThread()}
            >
              Open thread
            </button>
          </div>
        )}
      </div>

      {phase === 'approval' && (
        <aside
          className="buddy-overlay-approval"
          aria-label={`Approval required for ${actionLabel || 'requested action'}`}
          data-risk={approval?.risk_class ?? 'unknown'}
        >
          <ShieldAlert
            size={15}
            aria-hidden
            className="buddy-overlay-approval-icon"
          />
          <span
            className="buddy-overlay-approval-text"
            title={[
              approvalQuestion(actionLabel),
              argument || approval?.reason || hint?.reason,
            ]
              .filter(Boolean)
              .join(' · ')}
          >
            <strong>{approvalQuestion(actionLabel)}</strong>
            {(argument || approval?.reason || hint?.reason) && (
              <span> {argument || approval?.reason || hint?.reason}</span>
            )}
          </span>
          <button
            type="button"
            className="buddy-overlay-text-action"
            disabled={!approval || Boolean(resolving)}
            onClick={() => void resolve('reject')}
          >
            Deny
          </button>
          <button
            type="button"
            className="buddy-overlay-text-action"
            onClick={() => void openFullThread()}
          >
            Details
          </button>
          <button
            type="button"
            className="buddy-overlay-text-action"
            data-variant="primary"
            aria-label="Approve"
            aria-keyshortcuts="Control+Enter Meta+Enter"
            disabled={!approval || Boolean(resolving)}
            onClick={() => void resolve('approve')}
          >
            Approve
          </button>
        </aside>
      )}

      <form
        className="buddy-overlay-composer"
        onSubmit={(event) => {
          event.preventDefault();
          send();
        }}
      >
        <textarea
          aria-label="Buddy message"
          placeholder={
            id ? 'Message this thread' : 'No conversation to message'
          }
          rows={1}
          value={draft.text}
          disabled={!id}
          onChange={(event) => {
            if (!id) return;
            controller.setDraft(id, {
              ...controller.getDraft(id),
              text: event.target.value,
            });
          }}
          onKeyDown={onComposerKey}
        />
        <button
          type={live ? 'button' : 'submit'}
          className={`buddy-overlay-primary ${live ? 'is-stop' : 'is-send'}`}
          data-state={live ? 'stop' : 'send'}
          aria-label={live ? 'Stop' : 'Send'}
          title={live ? 'Stop' : 'Send'}
          disabled={live ? !generation?.can_stop || busy : !canSend}
          onClick={
            live
              ? (event) => {
                  event.preventDefault();
                  stop();
                }
              : undefined
          }
        >
          <ArrowUp
            className="buddy-overlay-primary-send"
            size={16}
            aria-hidden
          />
          <Square
            className="buddy-overlay-primary-stop"
            size={12}
            aria-hidden
          />
        </button>
      </form>
      {error ? (
        <p className="buddy-overlay-error" role="alert">
          {error}
        </p>
      ) : (
        composerHint &&
        turnInFlight && (
          <p className="buddy-overlay-hint" role="status">
            {composerHint}
          </p>
        )
      )}
      <p className="visually-hidden" role="status" aria-live="polite">
        {announcement}
      </p>
    </main>
  );
}
