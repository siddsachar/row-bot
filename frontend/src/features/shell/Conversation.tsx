import {
  useCallback,
  useEffect,
  useEffectEvent,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import type {
  AttachmentView,
  ClientError,
  ConversationComposer,
  ErrorAction,
  PanelDescriptor,
  ResourceView,
  SlashCommandSpec,
  TranscriptRow,
  TranscriptTraceGroup,
  WriteTarget,
} from '../../api/types';
import { clientError, rejectedBeforeRunning } from '../../api/errors';
import { useClientState, useRuntime } from '../../runtime';
import { useSettledIdentity } from '../../shell-settled';
import { useOverlay } from '../../ui/overlays';
import { Button, Menu, Skeleton, type MenuAction } from '../../ui/primitives';
import ResourceSetup from './ResourceSetup';
import { MediaPreview } from './MediaPreview';
export { MediaPreview as Media } from './MediaPreview';
import ComposerControls from './ComposerControls';
import VoiceControls from './VoiceControls';
import ConversationVoice from './ConversationVoice';
import ResourceTargets from './ResourceTargets';
import {
  ArrowDown,
  ArrowUp,
  Bot,
  Code2,
  ImageOff,
  ListPlus,
  MoreHorizontal,
  Palette,
  Paperclip,
  CircleStop,
  Square,
  TriangleAlert,
  X,
} from 'lucide-react';
import SearchConversations from './SearchConversations';
import ConversationActions from '../settings/ConversationActions';
import DraftConflict from './DraftConflict';
import WaitingMessages, {
  sendable,
  useWaitingMessages,
  type WaitingAction,
  type WaitingMessage,
} from './WaitingMessages';
import ContextUsage from './ContextUsage';
import DelegatedActivity, { recentReads } from './DelegatedActivity';
import ConversationContextRail from './ConversationContextRail';
import { ContextSlot, useContextHost } from './context-host';
import {
  commandReceipts,
  ReceiptStorageError,
  type PendingCommand,
} from './command-receipts';
import SafeMarkdown from './chat-parity-markdown';
import TranscriptTrace from './TranscriptTrace';
import SlashPalette, { type SlashPaletteHandle } from './SlashPalette';
import MentionPalette, { type MentionItem } from './MentionPalette';
import {
  goalRequest,
  profileChoice,
  reasoningChoice,
  slashArgument,
  type SlashArgument,
} from './slash-arguments';
import { DEFAULT_GOAL_TURNS, resetContextGoals } from './ContextGoal';
import {
  currentProfileChoice,
  openAgentProfiles,
  profileChoices,
} from './agent-profiles';
import type { ProfileSummary } from '../settings/GoalProfileSettings';
import { ComposerSkillChips, chipSkills } from './ComposerSkills';
import {
  attachmentLimitProblem,
  pastedFileName,
  ATTACHMENT_LIMITS,
} from './attachment-limits';
import ApprovalCard from './ApprovalCard';
import ChatEmpty from './ChatEmpty';
import { ComputerUseCard, useComputerUse } from './ComputerUseCard';
import { registerPromptSender } from './composer-bridge';
import ConversationHeader from './ConversationHeader';
import ErrorFix from './ErrorFix';
import { TranscriptMessage } from './TranscriptMessage';
import { publicBlockText } from './TranscriptBlocks';
import { buildTranscript, liveMedia } from './transcript-model';
import {
  CardActionsContext,
  cardKey,
  liveCards,
  TranscriptCards,
  type CardActions,
} from './TranscriptCards';
import type { RecoveryAction } from './turn-errors';
import { modelRefName, splitModelLabel } from './model-choices';

const EMPTY_ROWS: readonly TranscriptRow[] = [];
const CONTEXT_HIDDEN_KEY = 'row-bot.context-hidden.v1';

/** Below 740px Context floats; a floating chat docks it again from 780px. */
export function isNarrowChat(width: number, narrow: boolean): boolean {
  return narrow ? width < 780 : width < 740;
}

function readContextHidden(): boolean {
  try {
    return localStorage.getItem(CONTEXT_HIDDEN_KEY) === '1';
  } catch {
    return false;
  }
}
const QUEUE_EVENTS = new Set([
  'queue.updated',
  'queue.changed',
  'steering.queued',
  'steering.consumed',
]);

const FIELD_SIZING =
  typeof CSS !== 'undefined' &&
  typeof CSS.supports === 'function' &&
  CSS.supports('field-sizing', 'content');

/** Below this composer width the composer is a single line. */
const SINGLE_LINE_COMPOSER = 480;

export default function Conversation({
  onPanel,
  completedDesignId,
  onResourceOpened,
  onResourceRemoved,
  onNewChat = () => undefined,
  focusConversationId,
  onComposerFocused,
  firstPrompt,
  onFirstPromptConsumed,
  compactContext: compactFromViewport = false,
  contextPlacement = compactFromViewport ? 'compact' : 'inline',
  contextToggle = 0,
  headerActions,
  headerLeading,
  headerMenu,
  onStartProfileChat,
}: {
  onPanel: (panel: PanelDescriptor, options?: { wide?: boolean }) => void;
  completedDesignId?: string;
  onResourceOpened?: (bindingId: string) => void;
  /** A resource is being removed (Undo on its card): close its panels. */
  onResourceRemoved?: (resourceRef: string) => void;
  onNewChat?: () => void;
  focusConversationId?: string | null;
  onComposerFocused?: () => void;
  firstPrompt?: { conversationId: string; text: string } | null;
  onFirstPromptConsumed?: (conversationId: string) => void;
  /** Legacy alias for contextPlacement="compact". */
  compactContext?: boolean;
  /**
   * Where Context lives: a floating card on desktop (inline) or a sheet on
   * compact layouts.
   */
  contextPlacement?: 'inline' | 'compact';
  /** Each change toggles Context (the workspace's Mod+. shortcut). */
  contextToggle?: number;
  /** Header icon actions owned by the workspace (Open panel). */
  headerActions?: ReactNode;
  /** Compact layouts: the navigation button before the title. */
  headerLeading?: ReactNode;
  /** Phone layout: the header folds its actions into ⋯ (these come first). */
  headerMenu?: MenuAction[];
  onStartProfileChat?: (profile: ProfileSummary) => void;
}) {
  const state = useClientState();
  const { controller, platform, conversationActionsOwner, goalProfileOwner } =
    useRuntime();
  const overlay = useOverlay();
  const navigate = useNavigate();
  const id = state.selectedConversationId;
  const historyReady =
    Boolean(id) && !state.loadingConversation && state.conversation?.id === id;
  const draft = controller.getDraft(id ?? 'new');
  const voiceScope = controller.dictationScope();
  const [talkBusy, setTalkBusy] = useState(false);
  // The Agents rail section stays hidden until delegated work exists (B7).
  const [agentsContent, setAgentsContent] = useState<{
    conversation: string | null;
    present: boolean;
  }>({ conversation: null, present: false });
  const agentsEmpty =
    agentsContent.conversation !== id || !agentsContent.present;
  const [agentsLive, setAgentsLive] = useState<{
    conversation: string | null;
    live: number;
  }>({ conversation: null, live: 0 });
  const liveAgents = agentsLive.conversation === id ? agentsLive.live : 0;
  const [recentDelegatedRead] = useState(() => recentReads());
  const terminalAdvertised = Boolean(
    state.handshake?.application_capabilities?.includes('native:terminal'),
  );
  const [terminalAvailable, setTerminalAvailable] = useState(false);
  useEffect(() => {
    let current = true;
    setTerminalAvailable(false);
    if (!terminalAdvertised) return () => void (current = false);
    void platform
      .discover()
      .then((result) => {
        if (!current) return;
        setTerminalAvailable(
          result.status === 'ok' &&
            result.value.kind === 'pywebview' &&
            result.value.capabilities.includes('terminal_open'),
        );
      })
      .catch(() => {
        if (current) setTerminalAvailable(false);
      });
    return () => void (current = false);
  }, [platform, state.handshake?.instance_id, terminalAdvertised]);
  const voiceHostKey = `${state.handshake?.client_session_id ?? ''}:${state.handshake?.server_epoch ?? ''}`;
  const [voiceExposure, setVoiceExposure] = useState({
    key: '',
    dictate: false,
    talk: false,
    realtime: false,
    dictateReason: 'Voice capability is unavailable.',
    talkReason: 'Voice capability is unavailable.',
  });
  // Read once per settled server identity, after the conversation snapshot
  // is confirmed, so it never competes with open or subscribe (B29).
  const settledIdentity = useSettledIdentity();
  useEffect(() => {
    const handshake = controller.getSnapshot().handshake;
    if (!settledIdentity || !handshake) return;
    const voiceHostKey = `${handshake.client_session_id}:${handshake.server_epoch}`;
    const abort = new AbortController();
    void controller
      .dictationCapability(abort.signal)
      .then((value) => {
        if (!abort.signal.aborted)
          setVoiceExposure({
            key: voiceHostKey,
            dictate: value.browser_dictation_available,
            talk: value.talk_available ?? false,
            realtime: value.realtime_available ?? false,
            dictateReason:
              value.reason === 'available'
                ? 'Dictate'
                : 'Dictation is unavailable on this host.',
            talkReason:
              value.talk_reason === 'available'
                ? 'Talk'
                : 'Talk is unavailable on this host.',
          });
      })
      .catch(() => {
        if (!abort.signal.aborted)
          setVoiceExposure({
            key: voiceHostKey,
            dictate: false,
            talk: false,
            realtime: false,
            dictateReason: 'Voice capability could not be read.',
            talkReason: 'Voice capability could not be read.',
          });
      });
    return () => abort.abort();
  }, [controller, settledIdentity]);
  const [busy, setBusy] = useState(false);
  const [error, setErrorState] = useState<{
    message: string;
    action?: ErrorAction;
    retry?: () => void;
  } | null>(null);
  /** A sentence, or a catalogued error with its one fix; Retry reruns `retry`. */
  const setError = useCallback(
    (value: string | ClientError | null, retry?: () => void) =>
      setErrorState(
        !value
          ? null
          : typeof value === 'string'
            ? { message: value }
            : { message: value.message, action: value.action, retry },
      ),
    [],
  );
  const [pending, setPending] = useState<{
    conversation: string;
    id: string;
    text: string;
  } | null>(null);
  const [targetSelection, setTargets] = useState<Record<string, string[]>>({});
  const [unknown, setUnknown] = useState<{
    key: string;
    claim: PendingCommand;
  } | null>(null);
  const [resumeClaim, setResumeClaim] = useState<{
    key: string;
    claim: PendingCommand;
  } | null>(null);
  const [steeringClaim, setSteeringClaim] = useState<{
    key: string;
    claim: PendingCommand;
  } | null>(null);
  const [skillClaim, setSkillClaim] = useState<{
    key: string;
    claim: PendingCommand;
  } | null>(null);
  const [missingReceipt, setMissingReceipt] = useState<{
    key: string;
    commandId: string;
  } | null>(null);
  const receiptOperation = useRef(false);
  const receiptAlive = useRef(true);
  const steeringDraft = useRef<{
    commandId: string;
    conversation: string;
    value: ReturnType<typeof controller.getDraft>;
  } | null>(null);
  const submittedDraft = useRef<{
    commandId: string;
    conversation: string;
    value: ReturnType<typeof controller.getDraft>;
  } | null>(null);
  const firstPromptSent = useRef<string | null>(null);
  useEffect(() => {
    receiptAlive.current = true;
    return () => {
      receiptAlive.current = false;
    };
  }, []);
  const steeringKey =
    state.handshake && id
      ? commandReceipts.scope(state.handshake.instance_id, id)
      : '';
  const submitKey =
    state.handshake && id
      ? commandReceipts.scope(state.handshake.instance_id, id, 'submit')
      : '';
  useEffect(() => {
    setUnknown(null);
    if (!submitKey) return;
    try {
      const claim = commandReceipts.read(submitKey);
      if (claim) setUnknown({ key: submitKey, claim });
    } catch (cause) {
      setError(
        cause instanceof ReceiptStorageError
          ? cause.message
          : clientError(cause).message,
      );
    }
  }, [setError, submitKey]);
  const pendingSubmit = unknown?.key === submitKey ? unknown.claim : null;
  const resumeKey =
    state.handshake && id
      ? commandReceipts.scope(state.handshake.instance_id, id, 'resume')
      : '';
  useEffect(() => {
    setResumeClaim(null);
    if (!resumeKey) return;
    try {
      const claim = commandReceipts.read(resumeKey);
      if (claim) setResumeClaim({ key: resumeKey, claim });
    } catch (cause) {
      setError(
        cause instanceof ReceiptStorageError
          ? cause.message
          : clientError(cause).message,
      );
    }
  }, [resumeKey, setError]);
  const pendingResume =
    resumeClaim?.key === resumeKey ? resumeClaim.claim : null;
  const skillKey =
    state.handshake && id
      ? commandReceipts.scope(state.handshake.instance_id, id, 'skill')
      : '';
  useEffect(() => {
    setSkillClaim(null);
    if (!skillKey) return;
    try {
      const claim = commandReceipts.read(skillKey);
      if (claim) setSkillClaim({ key: skillKey, claim });
    } catch (cause) {
      setError(
        cause instanceof ReceiptStorageError
          ? cause.message
          : clientError(cause).message,
      );
    }
  }, [setError, skillKey]);
  const pendingSkill = skillClaim?.key === skillKey ? skillClaim.claim : null;
  useEffect(() => {
    setSteeringClaim(null);
    setMissingReceipt(null);
    if (!steeringKey) return;
    try {
      const claim = commandReceipts.read(steeringKey);
      if (claim) setSteeringClaim({ key: steeringKey, claim });
    } catch (cause) {
      setError(
        cause instanceof ReceiptStorageError
          ? cause.message
          : clientError(cause).message,
      );
    }
  }, [setError, steeringKey]);
  const pendingSteering =
    steeringClaim?.key === steeringKey ? steeringClaim.claim : null;
  const chatContentRef = useRef<HTMLDivElement>(null);
  const chatWorkspaceRef = useRef<HTMLDivElement>(null);
  const [narrowChat, setNarrowChat] = useState(false);
  const contextHost = useContextHost();
  const compactPlacement = contextPlacement === 'compact';
  // Without a host (isolated renders) Context renders in place, as before.
  const hosted = Boolean(contextHost) && !compactPlacement;
  // Compact layouts show Context as a sheet that adopts the same mounted
  // host, so it stays live (agents, goal, resources) while it is open.
  const sheetHosted = Boolean(contextHost) && compactPlacement;
  // Context is a small floating card. A wide chat gives it a column of its
  // own (hidden only on request); a narrow one floats it over the chat on
  // demand. Panels such as Design keep the full-height right region.
  const [contextHidden, setContextHiddenState] = useState(readContextHidden);
  const [floatingOpen, setFloatingOpen] = useState(false);
  const floatingContext = hosted && narrowChat;
  const cardActive =
    Boolean(id) && hosted && (floatingContext ? floatingOpen : !contextHidden);
  const inlineContext = cardActive && !floatingContext;
  // The chat is one column unless the docked card takes its own.
  const compactContext = hosted
    ? !inlineContext
    : compactPlacement || narrowChat;
  useEffect(() => setFloatingOpen(false), [id]);
  useEffect(() => {
    if (!floatingContext || !floatingOpen) return;
    const close = () => setFloatingOpen(false);
    const keydown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      // A dialog or menu owns its own Escape.
      if (document.querySelector('[role="dialog"], [role="menu"]')) return;
      close();
    };
    const pointerdown = (event: PointerEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (
        target?.closest(
          '.context-card, [data-context-toggle], [role="dialog"], [role="menu"], [role="listbox"], .tooltip-layer',
        )
      )
        return;
      close();
    };
    document.addEventListener('keydown', keydown);
    document.addEventListener('pointerdown', pointerdown, true);
    return () => {
      document.removeEventListener('keydown', keydown);
      document.removeEventListener('pointerdown', pointerdown, true);
    };
  }, [floatingContext, floatingOpen]);
  // Measured before paint, with hysteresis: a chat that sits near the
  // threshold (a side panel at default sizes) must not flip modes.
  useLayoutEffect(() => {
    const element = chatWorkspaceRef.current;
    if (!element) return;
    // A hidden section (Home, a route) measures 0: keep the last mode.
    const measure = (width: number) => {
      if (width > 0) setNarrowChat((narrow) => isNarrowChat(width, narrow));
    };
    measure(element.getBoundingClientRect().width);
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) =>
      measure(entry.contentRect.width),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const transcriptContentRef = useRef<HTMLDivElement>(null);
  const followingLatest = useRef(true);
  // The transcript's size when it was last pinned to the latest row.
  const pinnedGeometry = useRef<{
    height: number;
    client: number;
    width: number;
  } | null>(null);
  const scrollOwner = useRef<string | null>(null);
  const wasHistory = useRef(false);
  const [showLatest, setShowLatest] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const slashPaletteRef = useRef<SlashPaletteHandle>(null);
  const mentionPaletteRef = useRef<SlashPaletteHandle>(null);
  const [composerCursor, setComposerCursor] = useState(0);
  const [composerSnapshot, setComposerSnapshot] =
    useState<ConversationComposer | null>(null);
  const [composerBusy, setComposerBusy] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const toolbarRef = useRef<HTMLDivElement>(null);
  const composerFieldRef = useRef<HTMLDivElement>(null);
  const [compactToolbar, setCompactToolbar] = useState(false);
  // A narrow composer (phones, a squeezed chat) is one line: + field mic
  // send, with the model, approvals and context usage under +.
  const [singleLine, setSingleLine] = useState(false);
  useLayoutEffect(() => {
    const composer = toolbarRef.current?.closest('.composer');
    if (!composer) return;
    const measure = (width: number) => {
      // A hidden conversation (Home, a route) measures 0: keep the mode.
      if (width <= 0) return;
      setCompactToolbar(width < 600);
      setSingleLine(width < SINGLE_LINE_COMPOSER);
    };
    measure(composer.getBoundingClientRect().width);
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) =>
      measure(entry.contentRect.width),
    );
    observer.observe(composer);
    return () => observer.disconnect();
  }, [id]);
  // The field grows from one line; an empty draft always rests at one line
  // (a wrapped placeholder must not size it), and width changes re-measure.
  const fitComposer = useCallback(() => {
    const composer = composerRef.current;
    // Engines with CSS field-sizing grow the field in their own layout pass;
    // measuring here would force an extra layout on every resize frame.
    if (!composer || FIELD_SIZING) return;
    composer.style.height = '';
    if (!composer.value) return;
    composer.style.height = 'auto';
    composer.style.height = `${Math.min(240, Math.max(24, composer.scrollHeight))}px`;
  }, []);
  useLayoutEffect(fitComposer, [draft.text, id, fitComposer]);
  useEffect(() => {
    const composer = composerRef.current;
    if (!composer || typeof ResizeObserver === 'undefined') return;
    let width = composer.clientWidth;
    const observer = new ResizeObserver(() => {
      if (composer.clientWidth === width) return;
      width = composer.clientWidth;
      fitComposer();
    });
    observer.observe(composer);
    return () => observer.disconnect();
  }, [id, fitComposer]);
  const generation = state.projection?.generation;
  const running = generation && !generation.quiesced;
  const controls = state.workspace?.controls;
  const resources = state.workspace?.resources ?? [];
  const defaultTargetIds = resources
    .filter(
      (resource) =>
        resource.available &&
        (resource.binding.kind === 'workspace' ||
          (resource.binding.kind === 'artifact' &&
            resources.filter((item) => item.binding.kind === 'artifact')
              .length === 1)),
    )
    .map((resource) => resource.binding.binding_id);
  const sendActionReady = Boolean(
    state.workspace?.actions.find((action) => action.action === 'send')?.ready,
  );
  const composerStateReason = pendingSteering
    ? 'Checking whether your waiting message went through before another message can be sent.'
    : pendingSubmit
      ? 'Checking whether your last message went through before another message can be sent.'
      : pendingResume
        ? 'Checking whether Resume went through before another action can start.'
        : pendingSkill
          ? 'Checking whether the skill change went through before another change can start.'
          : talkBusy
            ? 'Voice controls are finishing before another message can be sent.'
            : busy
              ? 'Finishing the current conversation action.'
              : running
                ? 'Row-Bot is answering. Anything you send now waits until it finishes, or stop it.'
                : state.status !== 'ready'
                  ? 'Reconnect to send. Your draft remains on this device.'
                  : !sendActionReady
                    ? state.workspace?.model_status?.state === 'unavailable'
                      ? `The model is unavailable: ${state.workspace.model_status.reason || "it isn't ready right now"}. Reconnect or choose another model; your message stays here.`
                      : 'Choose a model to send. Your message stays here.'
                    : '';
  const rows = (state.history ?? state.projection)?.rows ?? EMPTY_ROWS;
  useEffect(() => {
    if (
      pending?.conversation === id &&
      rows.some((row) => row.message_id === pending.id)
    )
      setPending(null);
  }, [id, pending, rows]);
  // Rows loaded by scrolling up sit above the live window; history pages
  // (from a search jump) replace it.
  const earlierRows = state.history ? EMPTY_ROWS : state.earlier;
  const transcriptRows = useMemo(() => {
    if (!earlierRows.length) return rows;
    const live = new Set(rows.map((row) => row.id));
    return [...earlierRows.filter((row) => !live.has(row.id)), ...rows];
  }, [earlierRows, rows]);
  // Tool results fold into their parent; charts and generated media hoist
  // onto it and render once (B4, B22).
  const items = useMemo(
    () => buildTranscript(transcriptRows),
    [transcriptRows],
  );
  const earlierIds = useMemo(
    () => new Set(earlierRows.map((row) => row.id)),
    [earlierRows],
  );
  const hasLiveTranscript =
    items.length > 0 || Boolean(pending) || Boolean(running);
  const settledTraceCalls = new Set(
    rows.flatMap((row) =>
      (row.traces ?? []).flatMap((group) =>
        group.items.map((item) => item.call_id),
      ),
    ),
  );
  const liveTraceEvents = new Map(
    state.activity.flatMap((record) =>
      record.event.type === 'tool.activity'
        ? [
            [
              record.event.payload.tool_call_id ?? record.event.event_id,
              record,
            ] as const,
          ]
        : [],
    ),
  );
  const liveTraceGroups: TranscriptTraceGroup[] = [
    ...liveTraceEvents.values(),
  ].flatMap((record, index) => {
    if (
      record.event.type !== 'tool.activity' ||
      settledTraceCalls.has(record.event.payload.tool_call_id ?? '')
    )
      return [];
    const value = record.event.payload;
    return [
      {
        group_id: value.group_id || value.tool_call_id || record.event.event_id,
        name: value.group_name || value.tool_name || 'tool',
        kind: value.group_kind ?? 'generic',
        group_order: index,
        status: value.status ?? 'pending',
        counts: { [value.status ?? 'pending']: 1 },
        items: [
          {
            item_id:
              value.item_id || value.tool_call_id || record.event.event_id,
            group_id:
              value.group_id || value.tool_call_id || record.event.event_id,
            call_id: value.tool_call_id || record.event.event_id,
            result_message_id: value.message_id ?? '',
            call_order: 0,
            group_order: index,
            canonical_name: value.tool_name || 'tool',
            group_name: value.group_name || value.tool_name || 'tool',
            group_kind: value.group_kind ?? 'generic',
            status: value.status ?? 'pending',
            safe_input: value.safe_input ?? '',
            safe_summary: value.safe_summary ?? '',
            summary_truncated: value.summary_truncated ?? false,
            content_ref: value.content_ref ?? '',
          },
        ],
      },
    ];
  });
  const approvalEvent = [...state.activity]
    .reverse()
    .find(
      (record) =>
        record.event.type === 'approval.required' &&
        record.event.payload.approval_id === generation?.approval_id,
    );
  // Computer use shows as a card while this conversation's turn holds the
  // computer; only this computer's owner sees it (other devices keep the
  // trace). The turn using the computer, or asking to, is what to follow.
  const computerLocal = Boolean(
    state.handshake?.application_capabilities?.includes('computer:interactive'),
  );
  const computer = useComputerUse({
    conversationId: id && computerLocal && !state.history ? id : '',
    watch:
      liveTraceGroups.some((group) => group.kind === 'computer') ||
      (approvalEvent?.event.type === 'approval.required' &&
        approvalEvent.event.payload.action_label === 'Computer activity'),
    generationId: generation?.generation_id ?? '',
    turnKey: `${generation?.generation_id ?? ''}:${generation?.status ?? ''}`,
    load: controller.computerUse,
  });
  // A paused computer waits on an approval; the card's Resume answers it,
  // so the approval card stays away (never both).
  const computerPause =
    Boolean(computer.snapshot?.approval_id) &&
    computer.snapshot?.approval_id === generation?.approval_id;
  const thinkingActive =
    Boolean(running) &&
    state.activity.at(-1)?.event.type === 'generation.activity';
  useEffect(() => {
    const composer = state.workspace?.composer;
    if (composer?.conversation_id === id) setComposerSnapshot(composer);
    else setComposerSnapshot(null);
  }, [id, state.workspace?.composer]);
  useEffect(() => {
    if (
      !id ||
      !state.workspace?.composer ||
      state.status !== 'ready' ||
      draft.text.length > 16000
    )
      return;
    const selection = controller.getSelectionVersion();
    const abort = new AbortController();
    const timer = setTimeout(() => {
      void controller
        .composer(id, { draft: draft.text, command_limit: 256 }, abort.signal)
        .then((value) => {
          if (
            !abort.signal.aborted &&
            selection === controller.getSelectionVersion() &&
            controller.getSnapshot().selectedConversationId === id
          )
            setComposerSnapshot(value);
        })
        .catch((cause) => {
          if (!abort.signal.aborted) setError(clientError(cause).message);
        });
    }, 240);
    return () => {
      clearTimeout(timer);
      abort.abort();
    };
  }, [
    controller,
    draft.text,
    id,
    setError,
    state.status,
    state.workspace?.composer,
  ]);

  function replaceSlashToken(
    token: { start: number; end: number },
    replacement = '',
  ) {
    if (!id) return;
    const current = controller.getDraft(id);
    const next =
      current.text.slice(0, token.start) +
      replacement +
      current.text.slice(token.end);
    const cursor = token.start + replacement.length;
    controller.setDraft(id, { ...current, text: next });
    setComposerCursor(cursor);
    requestAnimationFrame(() => {
      composerRef.current?.focus({ preventScroll: true });
      composerRef.current?.setSelectionRange(cursor, cursor);
    });
  }

  async function skillAction(
    skillActionName: 'activate' | 'remove' | 'dismiss' | 'reset',
    skillId = '',
  ) {
    if (
      !id ||
      !composerSnapshot ||
      !state.conversation ||
      composerBusy ||
      running
    )
      return;
    setComposerBusy(true);
    try {
      let claim: PendingCommand | null = commandReceipts.read(skillKey);
      const checking = Boolean(claim);
      if (!claim) {
        claim = { commandId: crypto.randomUUID(), steeringId: null };
        commandReceipts.reserve(skillKey, claim);
        setSkillClaim({ key: skillKey, claim });
      }
      const receipt = checking
        ? await controller.receipt(claim.commandId)
        : await controller.intent(
            id,
            'conversation.skills',
            {
              action: skillActionName,
              composer_revision: composerSnapshot.composer_revision,
              skill_id: skillId,
              draft: draft.text.slice(0, 16000),
            },
            state.conversation.revision,
            claim.commandId,
          );
      if (receipt.command_id !== claim.commandId)
        throw { code: 'operation_uncertain' };
      if (receipt.status !== 'completed' && receipt.status !== 'accepted') {
        if (receipt.status === 'rejected') {
          commandReceipts.clear(skillKey, claim.commandId);
          setSkillClaim(null);
        }
        throw { code: receipt.code || 'operation_uncertain' };
      }
      commandReceipts.clear(skillKey, claim.commandId);
      setSkillClaim(null);
      await controller.refreshWorkspace();
      const fresh = await controller.composer(id, {
        draft: controller.getDraft(id).text.slice(0, 16000),
        command_limit: 256,
      });
      if (controller.getSnapshot().selectedConversationId === id)
        setComposerSnapshot(fresh);
      setError('');
    } catch (cause) {
      setError(clientError(cause).message);
      await controller.refreshWorkspace();
    } finally {
      setComposerBusy(false);
    }
  }

  async function runSlashArgument({ command, argument }: SlashArgument) {
    if (!id || !state.workspace || !controls || !state.conversation) return;
    const target = id;
    const clear = () => {
      const current = controller.getDraft(target);
      controller.setDraft(target, { ...current, text: '' });
    };
    setBusy(true);
    try {
      if (command.handler_kind === 'reasoning') {
        const reasoning =
          state.workspace.reasoning?.model_ref ===
          controls.model_selection?.model_ref
            ? state.workspace.reasoning
            : null;
        const choice = reasoningChoice(argument, reasoning);
        if (!reasoning?.available || !choice) {
          setError(
            reasoning?.available
              ? `“${argument}” isn't a thinking level for this model. Try ${reasoning.choices.map((item) => item.label).join(', ')}.`
              : 'This model has no thinking levels to choose from.',
          );
          return;
        }
        await controller.intent(
          target,
          'conversation.controls',
          {
            model_selection: controls.model_selection,
            runtime_mode: controls.runtime_mode,
            profile_id: controls.profile_id,
            approval_mode: controls.approval_mode,
            reasoning: {
              model_ref: reasoning.model_ref,
              capability_revision: reasoning.capability_revision,
              selection: choice.selection,
            },
          },
          state.workspace.revision,
        );
        clear();
        overlay.notify(`Thinking: ${choice.label}`);
      } else if (command.handler_kind === 'profile') {
        const choice = profileChoice(
          argument,
          profileChoices(state.workspace.profiles ?? []),
        );
        if (!choice) {
          setError(`No agent profile is called “${argument}”.`);
          return;
        }
        await updateControls({ profile_id: choice.id });
        clear();
        overlay.notify(`Agent profile: ${choice.label}`);
      } else if (command.handler_kind === 'goal') {
        const request = goalRequest(argument);
        const page = await controller.goals(target, '');
        if (!page) throw { code: 'capability_unavailable' };
        if (request.operation !== 'start' && !page.current_goal_id) {
          setError(
            'This conversation has no goal yet. Type /goal and what it should achieve.',
          );
          return;
        }
        const start = request.operation === 'start';
        const payload = {
          conversation_id: target,
          goal_id: page.current_goal_id,
          revision: page.current_revision,
          operation: request.operation,
          objective: start ? request.objective : null,
          max_turns: start ? DEFAULT_GOAL_TURNS : null,
          reason: start ? null : '',
        };
        const review = await controller.reviewGoal(target, payload);
        if (!review) throw { code: 'capability_unavailable' };
        const receipt = await controller.executeGoal(target, {
          command_id: crypto.randomUUID(),
          type: 'goal.control',
          payload: { ...payload, review_id: review.review_id },
        });
        if (receipt.status !== 'completed') {
          setError("The goal change wasn't confirmed. Try again.");
          return;
        }
        resetContextGoals(target);
        clear();
        overlay.notify(
          start
            ? 'Goal set. Row-Bot is working on it.'
            : {
                pause: 'Goal paused.',
                resume: 'Goal resumed.',
                clear: 'Goal stopped.',
                complete: 'Goal marked done.',
              }[request.operation],
        );
      } else if (command.handler_kind === 'agent') {
        await controller.intent(
          target,
          'agent.start',
          { text: argument },
          state.conversation.revision,
        );
        clear();
        overlay.notify('Agent started. Follow it in Context › Agents.');
      }
      setError('');
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  async function chooseSlash(
    command: SlashCommandSpec,
    token: { start: number; end: number },
  ) {
    if (!id) return;
    if (command.handler_kind === 'open_skills') {
      replaceSlashToken(token);
      setSkillsOpen(true);
      return;
    }
    if (command.handler_kind === 'activate_skill' && command.skill_id) {
      replaceSlashToken(token);
      await skillAction('activate', command.skill_id);
      return;
    }
    if (command.handler_kind === 'skill_reset') {
      replaceSlashToken(token);
      await skillAction('reset');
      return;
    }
    if (command.handler_kind === 'noskill') {
      const removable =
        composerSnapshot?.active_skills.filter((skill) => skill.removable) ??
        [];
      if (removable.length === 1) {
        replaceSlashToken(token);
        await skillAction('remove', removable[0].id);
      } else replaceSlashToken(token, '/noskill ');
      return;
    }
    if (command.handler_kind === 'new_thread') {
      replaceSlashToken(token);
      onNewChat();
      return;
    }
    if (command.handler_kind === 'stop_generation') {
      replaceSlashToken(token);
      if (running) await action('conversation.stop');
      else overlay.notify('No response is currently running.');
      return;
    }
    if (command.handler_kind === 'export') {
      replaceSlashToken(token);
      manageConversation();
      return;
    }
    const profileSession = goalProfileOwner?.get();
    if (command.handler_kind === 'profiles' && profileSession) {
      // The reviewed profile library, not a text summary (B10).
      replaceSlashToken(token);
      openAgentProfiles({
        overlay,
        controller,
        session: profileSession,
        returnFocusTo: composerRef.current,
        onStartProfileChat,
      });
      return;
    }
    if (
      ['status', 'tools', 'profiles', 'agents', 'help'].includes(
        command.handler_kind,
      )
    ) {
      replaceSlashToken(token);
      try {
        const result = await controller.composerCommand(
          id,
          command.handler_kind as
            'status' | 'tools' | 'profiles' | 'agents' | 'help',
        );
        overlay.open({
          title: result.title,
          description: command.description,
          content: <SafeMarkdown text={result.text} />,
        });
      } catch (cause) {
        setError(clientError(cause).message);
      }
      return;
    }
    replaceSlashToken(token, `${command.token} `);
  }
  function scrollToLatest() {
    const transcript = transcriptRef.current;
    if (!transcript) return;
    const chatContent = chatContentRef.current;
    if (chatContent) {
      const contentBounds = chatContent.getBoundingClientRect();
      const transcriptBounds = transcript.getBoundingClientRect();
      if (transcriptBounds.top < contentBounds.top)
        chatContent.scrollTop -= contentBounds.top - transcriptBounds.top;
      else if (transcriptBounds.bottom > contentBounds.bottom)
        chatContent.scrollTop += transcriptBounds.bottom - contentBounds.bottom;
    }
    transcript.scrollTop = Math.max(
      0,
      transcript.scrollHeight - transcript.clientHeight,
    );
    pinnedGeometry.current = {
      height: transcript.scrollHeight,
      client: transcript.clientHeight,
      width: transcript.clientWidth,
    };
  }
  useLayoutEffect(() => {
    if (scrollOwner.current !== id || (wasHistory.current && !state.history)) {
      followingLatest.current = true;
      setShowLatest(false);
    }
    scrollOwner.current = id;
    wasHistory.current = Boolean(state.history);
    if (!hasLiveTranscript) {
      if (transcriptRef.current) transcriptRef.current.scrollTop = 0;
      if (chatContentRef.current) chatContentRef.current.scrollTop = 0;
    } else if (
      !state.history &&
      !state.loadingConversation &&
      followingLatest.current
    )
      scrollToLatest();
  }, [id, rows, hasLiveTranscript, state.history, state.loadingConversation]);
  useLayoutEffect(() => {
    const transcript = transcriptRef.current;
    const content = transcriptContentRef.current;
    if (!transcript || !content || typeof ResizeObserver === 'undefined')
      return;
    let active = true;
    const observer = new ResizeObserver(() => {
      // Media and pane resizing can change geometry without changing rows.
      // Never pull an older/history reader back to the live end.
      if (
        active &&
        !state.history &&
        !state.loadingConversation &&
        hasLiveTranscript &&
        followingLatest.current
      )
        scrollToLatest();
    });
    observer.observe(transcript);
    observer.observe(content);
    return () => {
      active = false;
      observer.disconnect();
    };
  }, [id, state.history, state.loadingConversation, hasLiveTranscript]);
  useEffect(() => {
    if (
      focusConversationId &&
      focusConversationId === state.conversation?.id &&
      !state.loadingConversation
    ) {
      composerRef.current?.focus();
      onComposerFocused?.();
    }
  }, [
    state.conversation?.id,
    state.loadingConversation,
    focusConversationId,
    onComposerFocused,
  ]);
  useEffect(() => {
    if (!state.historyFocus || !state.history) return;
    const row = Array.from(
      transcriptRef.current?.querySelectorAll<HTMLElement>(
        '[data-message-id]',
      ) ?? [],
    ).find((element) => element.dataset.messageId === state.historyFocus);
    row?.scrollIntoView({ block: 'center', behavior: 'instant' });
    row?.focus({ preventScroll: true });
  }, [state.historyFocus, state.history]);
  async function dispatch(
    text?: string,
    targets: WriteTarget[] = [],
    capturedDraft?: ReturnType<typeof controller.getDraft>,
    selectedVersion = controller.getSelectionVersion(),
  ) {
    if (
      !id ||
      !submitKey ||
      busy ||
      receiptOperation.current ||
      selectedVersion !== controller.getSelectionVersion() ||
      controller.getSnapshot().selectedConversationId !== id ||
      controller.getSnapshot().handshake?.instance_id !==
        state.handshake?.instance_id
    )
      return;
    const target = id,
      scope = submitKey,
      instance = state.handshake?.instance_id;
    const current = () =>
      receiptAlive.current &&
      controller.getSelectionVersion() === selectedVersion &&
      controller.getSnapshot().handshake?.instance_id === instance;
    receiptOperation.current = true;
    setBusy(true);
    setError('');
    let claim: PendingCommand | null = null;
    let checking = false;
    let sendControls = controls;
    let sendRevision = state.conversation?.revision ?? '';
    try {
      claim = commandReceipts.read(scope);
      checking = Boolean(claim);
      if (!claim) {
        // A model or mode change that is still saving goes first, so this
        // message carries it and the revision it made (B109).
        await controller.controlsSettled?.(target);
        if (!current()) return;
        const latest = controller.getSnapshot();
        if (latest.workspace?.conversation_id === target)
          sendControls = latest.workspace.controls;
        if (latest.conversation?.id === target)
          sendRevision = latest.conversation.revision;
        if (!text || !capturedDraft || !sendControls?.model_selection) return;
        if (resumeKey && commandReceipts.read(resumeKey)) {
          setResumeClaim({
            key: resumeKey,
            claim: commandReceipts.read(resumeKey)!,
          });
          setError(
            'Row-Bot is still checking whether Resume went through. Check it before sending another message.',
          );
          return;
        }
        const steering = steeringKey ? commandReceipts.read(steeringKey) : null;
        if (steering) {
          setSteeringClaim({ key: steeringKey, claim: steering });
          setError(
            'Row-Bot is still checking your waiting message. Check it before sending another message.',
          );
          return;
        }
        claim = {
          commandId: crypto.randomUUID(),
          steeringId: crypto.randomUUID(),
        };
        commandReceipts.reserve(scope, claim);
        submittedDraft.current = {
          commandId: claim.commandId,
          conversation: target,
          value: capturedDraft,
        };
        setPending({ conversation: target, id: claim.steeringId!, text });
      }
      setUnknown({ key: scope, claim });
      const receipt = checking
        ? await controller.receipt(claim.commandId)
        : await controller.intent(
            target,
            'conversation.submit',
            {
              submission_id: claim.steeringId,
              text,
              attachment_refs: capturedDraft!.attachments.map(
                (a) => a.attachment_ref,
              ),
              model_selection: sendControls!.model_selection,
              write_targets: targets,
            },
            sendRevision,
            claim.commandId,
          );
      if (
        receipt.command_id !== claim.commandId ||
        receipt.conversation_id !== target ||
        (receipt.submission_id && receipt.submission_id !== claim.steeringId)
      )
        throw { code: 'operation_uncertain' };
      if (receipt.status === 'accepted' || receipt.status === 'completed') {
        commandReceipts.clear(scope, claim.commandId);
        const captured = submittedDraft.current;
        if (
          captured?.commandId === claim.commandId &&
          captured.conversation === target &&
          controller.getSnapshot().handshake?.instance_id === instance &&
          controller.getDraft(target) === captured.value
        )
          controller.setDraft(target, { text: '', attachments: [] });
        if (current()) {
          setUnknown(null);
          setMissingReceipt(null);
          setError('');
          controller.showLatest();
        }
      } else if (current()) {
        const refused = clientError({ code: receipt.code });
        if (receipt.status === 'rejected' && rejectedBeforeRunning(refused)) {
          dropClaim(scope, claim.commandId);
          setError(refused);
        } else {
          if (receipt.status === 'rejected')
            setMissingReceipt({ key: scope, commandId: claim.commandId });
          setError(
            "Row-Bot couldn't confirm whether your message was sent. Check it before sending again.",
          );
        }
      }
    } catch (cause) {
      if (current()) {
        const safe = clientError(cause);
        if (cause instanceof ReceiptStorageError) setError(cause.message);
        else if (claim && checking && safe.code === 'not_found') {
          setMissingReceipt({ key: scope, commandId: claim.commandId });
          setError(safe);
        } else if (claim && rejectedBeforeRunning(safe)) {
          // The server refused before anything ran (a message waiting, a
          // run in progress, no model...): nothing can be duplicated, so
          // the request is dropped instead of blocking the composer (B107).
          dropClaim(scope, claim.commandId);
          setError(safe);
        } else setError(safe, claim ? () => void recover() : undefined);
      }
    } finally {
      receiptOperation.current = false;
      if (receiptAlive.current) setBusy(false);
    }
  }
  function send(
    example?: string,
    keepTargets = false,
    attachments: typeof draft.attachments = [],
  ) {
    const outgoing = example ? { text: example, attachments } : draft;
    if (
      !id ||
      !outgoing.text.trim() ||
      pendingSteering ||
      pendingSubmit ||
      pendingResume ||
      state.status !== 'ready' ||
      !sendActionReady
    )
      return;
    // "/goal Ship the docs", "/reasoning high", "/profile writer",
    // "/agent <task>" run here instead of reaching the model as text (B112).
    const withArgument = example
      ? null
      : slashArgument(outgoing.text, composerSnapshot?.commands ?? []);
    if (withArgument) {
      void runSlashArgument(withArgument);
      return;
    }
    const commandText = outgoing.text.trim().toLocaleLowerCase();
    const exactCommand = composerSnapshot?.commands.find((command) =>
      [command.token, ...command.aliases].some(
        (token) => token.toLocaleLowerCase() === commandText,
      ),
    );
    if (exactCommand) {
      const start = outgoing.text.indexOf(outgoing.text.trim());
      void chooseSlash(exactCommand, {
        start,
        end: start + outgoing.text.trim().length,
      });
      return;
    }
    try {
      if (steeringKey && commandReceipts.read(steeringKey)) {
        setSteeringClaim({
          key: steeringKey,
          claim: commandReceipts.read(steeringKey)!,
        });
        setError(
          'Row-Bot is still checking your waiting message. Check it before sending another message.',
        );
        return;
      }
    } catch (cause) {
      setError(
        cause instanceof ReceiptStorageError
          ? cause.message
          : clientError(cause).message,
      );
      return;
    }
    const targets: WriteTarget[] = (example && !keepTargets ? [] : resources)
      .filter((r) =>
        (targetSelection[id] ?? defaultTargetIds).includes(
          r.binding.binding_id,
        ),
      )
      .map((r) => ({
        kind: r.binding.kind as 'artifact' | 'workspace',
        binding_id: r.binding.binding_id,
        resource_id: r.binding.resource_id,
        binding_revision: r.binding.revision,
        resource_revision: r.resource_revision,
      }));
    const text = outgoing.text;
    const selectedVersion = controller.getSelectionVersion();
    void dispatch(text, targets, outgoing, selectedVersion);
  }
  const sendFirstPrompt = useEffectEvent(() => send());
  useEffect(() => {
    if (
      !firstPrompt ||
      firstPrompt.conversationId !== id ||
      firstPromptSent.current === id ||
      !historyReady ||
      state.status !== 'ready' ||
      !sendActionReady ||
      !controls?.model_selection ||
      busy ||
      talkBusy ||
      pendingSubmit ||
      pendingSteering ||
      pendingResume ||
      draft.text !== firstPrompt.text ||
      draft.attachments.length
    )
      return;
    firstPromptSent.current = id;
    onFirstPromptConsumed?.(id);
    sendFirstPrompt();
  }, [
    firstPrompt,
    id,
    historyReady,
    state.status,
    sendActionReady,
    controls,
    busy,
    talkBusy,
    pendingSubmit,
    pendingSteering,
    pendingResume,
    draft,
    onFirstPromptConsumed,
  ]);
  // Side panels (the Design panel's "Ask Row-Bot to change this…") hand a
  // short request to this composer, which sends it with its model and write
  // targets, queued behind a running turn like any other message.
  const sendPanelPrompt = useEffectEvent((text: string) => {
    if (
      !id ||
      !historyReady ||
      state.status !== 'ready' ||
      !sendActionReady ||
      !controls?.model_selection ||
      busy ||
      talkBusy ||
      pendingSubmit ||
      pendingSteering ||
      pendingResume
    )
      return false;
    send(text, true);
    return true;
  });
  useEffect(() => {
    if (!id) return;
    return registerPromptSender(id, (text) => sendPanelPrompt(text));
  }, [id]);
  async function action(
    type: 'conversation.stop' | 'conversation.steer' | 'conversation.resume',
  ) {
    if (type === 'conversation.steer') {
      await queueMessage();
      return;
    }
    if (type === 'conversation.resume') {
      await resume();
      return;
    }
    if (!id || (busy && type !== 'conversation.stop')) return;
    setBusy(true);
    setError('');
    try {
      await controller.intent(id, type, {}, state.conversation!.revision);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }
  async function resume(checkOnly = false) {
    if (!id || !resumeKey || busy || receiptOperation.current) return;
    const target = id,
      scope = resumeKey,
      selection = controller.getSelectionVersion(),
      instance = state.handshake?.instance_id;
    const current = () =>
      receiptAlive.current &&
      controller.getSelectionVersion() === selection &&
      controller.getSnapshot().handshake?.instance_id === instance;
    receiptOperation.current = true;
    setBusy(true);
    let claim: PendingCommand | null = null;
    let checking = false;
    try {
      claim = commandReceipts.read(scope);
      checking = Boolean(claim);
      if (!claim) {
        if (checkOnly) return;
        const submission = submitKey ? commandReceipts.read(submitKey) : null;
        const steering = steeringKey ? commandReceipts.read(steeringKey) : null;
        if (submission || steering) {
          if (submission) setUnknown({ key: submitKey, claim: submission });
          if (steering) setSteeringClaim({ key: steeringKey, claim: steering });
          setError(
            'Row-Bot is still checking whether your last message went through. Check it before resuming.',
          );
          return;
        }
        claim = { commandId: crypto.randomUUID(), steeringId: null };
        commandReceipts.reserve(scope, claim);
      }
      setResumeClaim({ key: scope, claim });
      setError('');
      const result = checking
        ? await controller.receipt(claim.commandId)
        : await controller.intent(
            target,
            'conversation.resume',
            { model_selection: controls?.model_selection },
            state.conversation!.revision,
            claim.commandId,
          );
      if (
        result.command_id !== claim.commandId ||
        result.conversation_id !== target
      )
        throw { code: 'operation_uncertain' };
      if (result.status === 'accepted' || result.status === 'completed') {
        commandReceipts.clear(scope, claim.commandId);
        if (current()) {
          setResumeClaim(null);
          setMissingReceipt(null);
          setError('');
          if (checking) overlay.notify('Resume went through.');
        }
      } else if (current()) {
        const refused = clientError({ code: result.code });
        if (result.status === 'rejected' && rejectedBeforeRunning(refused)) {
          dropClaim(scope, claim.commandId);
          setError(refused);
        } else {
          if (result.status === 'rejected')
            setMissingReceipt({ key: scope, commandId: claim.commandId });
          setError(
            "Row-Bot couldn't confirm whether Resume went through. Check it before resuming again.",
          );
        }
      }
    } catch (cause) {
      if (current()) {
        const safe = clientError(cause);
        if (cause instanceof ReceiptStorageError) setError(cause.message);
        else if (claim && checking && safe.code === 'not_found') {
          setMissingReceipt({ key: scope, commandId: claim.commandId });
          setError(safe);
        } else if (claim && rejectedBeforeRunning(safe)) {
          dropClaim(scope, claim.commandId);
          setError(safe);
        } else
          setError(
            "Row-Bot couldn't confirm whether Resume went through. Check it before resuming again.",
          );
      }
    } finally {
      receiptOperation.current = false;
      if (receiptAlive.current) setBusy(false);
    }
  }
  async function queueMessage() {
    if (!id || !steeringKey || busy || receiptOperation.current) return;
    receiptOperation.current = true;
    setBusy(true);
    const target = id,
      scope = steeringKey,
      selection = controller.getSelectionVersion();
    const instance = state.handshake?.instance_id;
    const current = () =>
      receiptAlive.current &&
      controller.getSelectionVersion() === selection &&
      controller.getSnapshot().handshake?.instance_id === instance;
    let claim: PendingCommand | null = null;
    let checking = false;
    try {
      claim = commandReceipts.read(scope);
      checking = Boolean(claim);
      if (!claim) {
        if (resumeKey && commandReceipts.read(resumeKey)) {
          setResumeClaim({
            key: resumeKey,
            claim: commandReceipts.read(resumeKey)!,
          });
          setError(
            'Row-Bot is still checking whether Resume went through. Check it before adding another message.',
          );
          return;
        }
        const submission = submitKey ? commandReceipts.read(submitKey) : null;
        if (submission) {
          setUnknown({ key: submitKey, claim: submission });
          setError(
            'Row-Bot is still checking whether your last message went through. Check it before adding another message.',
          );
          return;
        }
        const captured = controller.getDraft(target);
        if (!captured.text.trim() || captured.text.length > 16000) {
          if (current())
            setError(
              'A waiting message needs between 1 and 16,000 characters.',
            );
          return;
        }
        claim = {
          commandId: crypto.randomUUID(),
          steeringId: crypto.randomUUID(),
        };
        commandReceipts.reserve(scope, claim);
        steeringDraft.current = {
          commandId: claim.commandId,
          conversation: target,
          value: captured,
        };
      }
      setSteeringClaim({ key: scope, claim });
      setError('');
      const captured = steeringDraft.current;
      const result = checking
        ? await controller.receipt(claim.commandId)
        : await controller.intent(
            target,
            'conversation.steer',
            {
              steering_id: claim.steeringId,
              text: captured!.value.text,
            },
            state.conversation!.revision,
            claim.commandId,
          );
      if (
        result.command_id !== claim.commandId ||
        result.conversation_id !== target ||
        (result.submission_id && result.submission_id !== claim.steeringId)
      )
        throw { code: 'operation_uncertain' };
      if (result.status === 'accepted' || result.status === 'completed') {
        commandReceipts.clear(scope, claim.commandId);
        if (
          captured?.commandId === claim.commandId &&
          captured.conversation === target &&
          controller.getSnapshot().handshake?.instance_id === instance &&
          controller.getDraft(target) === captured.value
        )
          controller.setDraft(target, { ...captured.value, text: '' });
        if (current()) {
          setSteeringClaim(null);
          setMissingReceipt(null);
          setError('');
          overlay.notify(
            checking
              ? 'Your waiting message went through. Your current draft is kept.'
              : 'Message waiting: it sends when Row-Bot finishes.',
          );
          waiting.reload();
        }
      } else if (current()) {
        const refused = clientError({ code: result.code });
        if (result.status === 'rejected' && rejectedBeforeRunning(refused)) {
          dropClaim(scope, claim.commandId);
          setError(refused);
        } else {
          if (result.status === 'rejected')
            setMissingReceipt({ key: scope, commandId: claim.commandId });
          setError(
            "Row-Bot couldn't confirm your message is waiting. Check it before sending it again.",
          );
        }
      }
    } catch (cause) {
      if (current()) {
        const safe = clientError(cause);
        if (cause instanceof ReceiptStorageError) setError(cause.message);
        else if (claim && checking && safe.code === 'not_found') {
          setMissingReceipt({ key: scope, commandId: claim.commandId });
          setError(safe);
        } else if (claim && rejectedBeforeRunning(safe)) {
          dropClaim(scope, claim.commandId);
          setError(safe);
        } else
          setError(
            "Row-Bot couldn't confirm your message is waiting. Check it before sending it again.",
          );
      }
    } finally {
      receiptOperation.current = false;
      if (receiptAlive.current) setBusy(false);
    }
  }
  /** The server refused before anything ran: forget the request (B107). */
  function dropClaim(scope: string, commandId: string) {
    try {
      commandReceipts.clear(scope, commandId);
    } catch (cause) {
      setError(
        cause instanceof ReceiptStorageError
          ? cause.message
          : clientError(cause).message,
      );
      return;
    }
    if (scope === submitKey) {
      setUnknown(null);
      setPending(null);
    }
    if (scope === steeringKey) setSteeringClaim(null);
    if (scope === resumeKey) setResumeClaim(null);
    setMissingReceipt(null);
  }
  function reviewMissingReceipt() {
    if (!missingReceipt) return;
    const saved = missingReceipt,
      selection = controller.getSelectionVersion(),
      instance = state.handshake?.instance_id;
    overlay.open({
      kind: 'alert',
      title: 'Stop checking this message?',
      description:
        "Row-Bot can't find what happened to it, so it may still arrive. Look at the conversation first: sending it again could make it appear twice. Nothing is sent now.",
      confirmLabel: 'Stop checking',
      onConfirm: () => {
        if (
          !receiptAlive.current ||
          controller.getSelectionVersion() !== selection ||
          controller.getSnapshot().handshake?.instance_id !== instance
        )
          return;
        try {
          commandReceipts.clear(saved.key, saved.commandId);
          if (saved.key === steeringKey) setSteeringClaim(null);
          if (saved.key === submitKey) {
            setUnknown(null);
            setPending(null);
          }
          if (saved.key === resumeKey) setResumeClaim(null);
          setMissingReceipt(null);
          setError('');
          overlay.close();
        } catch (cause) {
          setError(
            cause instanceof ReceiptStorageError
              ? cause.message
              : clientError(cause).message,
          );
        }
      },
    });
  }
  async function attach() {
    if (!id) return;
    const target = id,
      picked = await platform.selectFile(undefined, {
        intentId: crypto.randomUUID(),
        intent: 'attachment',
        conversationId: target,
        destination: 'composer',
      });
    if (picked.status !== 'ok') return;
    if ('files' in picked.value) {
      await attachFiles(picked.value.files);
      return;
    }
    if (picked.value.kind !== 'file') return;
    setBusy(true);
    try {
      const uploaded = await controller.attachmentMetadata(
        picked.value.reference,
      );
      const previous = controller.getDraft(target);
      controller.setDraft(target, {
        ...previous,
        attachments: [...previous.attachments, uploaded],
      });
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }
  /**
   * Picked, dropped or pasted files (U18, parity row 1). Files over the
   * limits are named and left out before anything uploads; the rest attach.
   */
  async function attachFiles(files: File[]) {
    if (!id || !files.length) return;
    const target = id;
    const current = controller.getDraft(target);
    const { accepted, problem } = attachmentLimitProblem(
      files,
      current.attachments,
    );
    if (problem) setError(problem);
    if (!accepted.length) return;
    setBusy(true);
    try {
      for (const file of accepted) {
        const uploaded = await controller.upload(target, file);
        const previous = controller.getDraft(target);
        controller.setDraft(target, {
          ...previous,
          attachments: [...previous.attachments, uploaded],
        });
      }
      if (!problem) setError('');
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }
  const [dragging, setDragging] = useState(false);
  const attachBlocked = busy || !id || state.status !== 'ready';
  function hasFiles(event: DragEvent<HTMLElement>) {
    return Array.from(event.dataTransfer?.types ?? []).includes('Files');
  }
  function resourcePanel(resource: ResourceView) {
    onResourceOpened?.(resource.binding.binding_id);
    onPanel({
      panel_kind:
        resource.binding.kind === 'artifact'
          ? 'artifact.preview'
          : 'workspace.inspector',
      title: resource.title.slice(0, 160),
      resource_ref: resource.resource_ref,
      resource_kind: resource.binding.kind,
      resource_revision: resource.resource_revision,
    });
    overlay.close();
  }
  function useOutputInCode(output: { reference: string; mime: string }) {
    if (!id) return;
    const current = controller.getDraft(id);
    const instruction = `Import conversation output ${output.reference} (${output.mime}) into the primary code folder with Developer's media import. Choose an unused workspace-relative filename. Use the existing output; do not generate it again.`;
    controller.setDraft(id, {
      ...current,
      text: [current.text.trim(), instruction].filter(Boolean).join('\n\n'),
    });
    composerRef.current?.focus();
  }
  function setup(opener?: HTMLElement | null) {
    overlay.open({
      // Opened from a menu item that disappears: return focus to its trigger.
      returnFocusTo: opener,
      title: 'Add resource',
      description:
        'Create or add a design or coding workspace to this conversation.',
      content: <ResourceSetup conversationId={id} onPanel={onPanel} />,
    });
  }
  function manageConversation() {
    if (!id) return;
    const session = conversationActionsOwner?.get()?.get(id);
    if (!session) {
      setError(
        'Conversation actions are unavailable while other reviewed actions need attention.',
      );
      return;
    }
    overlay.open({
      title: 'Conversation actions',
      description:
        'Review changes to this saved conversation and keep its resources in place.',
      content: (
        <ConversationActions
          conversationId={id}
          session={session}
          load={controller.conversationActions}
          review={controller.reviewConversationAction}
          execute={controller.executeConversationAction}
          download={async (reference, fileName) => {
            const result = await platform.save(reference, fileName);
            if (result.status !== 'ok') throw Error(result.status);
          }}
          onChanged={() => {
            void Promise.all([
              controller.selectConversation(id),
              controller.loadMoreConversations(true),
            ]);
          }}
        />
      ),
    });
  }
  function manageBrowser() {
    if (!id) return;
    onPanel({
      panel_kind: 'browser.live',
      title: 'Managed browser',
      required_capabilities: ['browser_navigate'],
    });
    // Below 1024px Context is a sheet; the panel must not open behind it.
    overlay.dismiss('conversation-context');
  }
  function findConversation() {
    if (!id) return;
    overlay.open({
      title: 'Find in conversation',
      description: 'Search the complete conversation history.',
      content: <SearchConversations conversationId={id} />,
    });
  }
  function unbindResource(resource: ResourceView) {
    if (!id || !state.conversation) return;
    void controller
      .intent(
        id,
        'conversation.unbind',
        { binding_id: resource.binding.binding_id },
        state.conversation.revision,
      )
      .catch((e) => setError(clientError(e).message));
  }
  function deleteConversation() {
    if (!id || !state.conversation) return;
    overlay.open({
      kind: 'alert',
      title: 'Delete conversation?',
      description:
        'This removes its history. Bound resources are retained. Running work must stop before deletion completes.',
      confirmLabel: 'Delete conversation',
      onConfirm: () => {
        overlay.close();
        void controller
          .intent(id, 'conversation.delete', {}, state.conversation!.revision)
          .then((receipt) => {
            if (receipt.status === 'DeleteCompleted') navigate('/');
            else
              setError(
                'Deletion is waiting for running work to stop. Review and try again.',
              );
          })
          .catch((e) => setError(clientError(e).message));
      },
    });
  }
  async function recover() {
    if (pendingSubmit) await dispatch();
  }
  const delegatedActivity = id ? (
    <DelegatedActivity
      compact
      conversationId={id}
      onContentChange={(present) =>
        setAgentsContent({ conversation: id, present })
      }
      onLiveChange={(live) => setAgentsLive({ conversation: id, live })}
      recentRead={recentDelegatedRead}
      ready={Boolean(state.handshake) && state.status === 'ready'}
      refreshKey={
        state.activity
          .filter((record) => record.event.type === 'agent.activity')
          .at(-1)?.event.event_id ?? ''
      }
      loadPage={(cursor, signal) =>
        controller.delegatedActivity(id, cursor, signal)
      }
      loadRun={(run, signal) => controller.delegatedRun(id, run, signal)}
      // The dialog keeps these from when it opened: read the conversation's
      // revision when the person acts, not the render's.
      stopRun={async (run) => {
        await controller.intent(
          id,
          'agent.stop',
          { run_id: run },
          controller.getSnapshot().conversation?.revision ?? '0',
        );
      }}
      messageRun={async (run, text) => {
        await controller.intent(
          id,
          'agent.message',
          { run_id: run, message_id: crypto.randomUUID(), text },
          controller.getSnapshot().conversation?.revision ?? '0',
        );
      }}
      openConversation={async (target) => {
        if (controller.getSnapshot().selectedConversationId !== id) return;
        await controller.selectConversation(target);
        if (
          controller.getSnapshot().selectedConversationId === target &&
          controller.getSnapshot().conversation?.id === target
        )
          navigate(`/conversations/${target}`);
      }}
    />
  ) : (
    <p className="muted">Agents appear after a conversation is created.</p>
  );
  const lastWriterEvent = [...state.activity]
    .reverse()
    .find(
      (item) =>
        item.event.type === 'agent.activity' &&
        item.event.payload.run_id.startsWith('chat-'),
    )?.event;
  const writerQueued =
    lastWriterEvent?.type === 'agent.activity' &&
    lastWriterEvent.payload.status === 'queued';
  const durableOutputs = rows.flatMap((row) =>
    (row.traces ?? []).flatMap((group) =>
      group.items.flatMap((item) =>
        (item.specialization?.media ?? []).map((media) => ({
          id: media.media_ref,
          reference: media.media_ref,
          mime: media.mime_type,
        })),
      ),
    ),
  );
  const liveOutputs = state.activity
    .filter((item) => item.event.type === 'media.available')
    .map((item) => ({
      id: item.event.event_id,
      reference:
        item.event.type === 'media.available'
          ? item.event.payload.media_ref
          : '',
      mime:
        item.event.type === 'media.available'
          ? item.event.payload.mime_type
          : '',
    }));
  const outputs = [
    ...new Map(
      [
        ...(state.workspace?.generated_outputs ?? []).map((output) => ({
          id: output.media_ref,
          reference: output.media_ref,
          mime: output.mime_type,
        })),
        ...durableOutputs,
        ...liveOutputs,
      ].map((output) => [output.reference, output]),
    ).values(),
  ];
  const contextReady = state.status === 'ready' && historyReady;
  const contextRail = id ? (
    <ConversationContextRail
      conversationId={id}
      conversationRevision={state.workspace?.revision ?? '0'}
      turnActivity={`${generation?.generation_id ?? ''}:${generation?.status ?? ''}`}
      turnRunning={Boolean(running)}
      onStopTurn={() => void action('conversation.stop')}
      resources={resources}
      suggestions={(state.suggestions ?? []).filter(
        (suggestion) => suggestion.conversation_id === id,
      )}
      ready={contextReady}
      connectionStatus={state.status}
      terminalAvailable={terminalAvailable}
      compactHeading={hosted ? false : compactContext}
      agents={delegatedActivity}
      agentsEmpty={agentsEmpty}
      agentsLive={liveAgents}
      childConversation={Boolean(
        state.conversation?.id === id &&
        state.conversation.parent_conversation_id,
      )}
      outputs={outputs}
      completedDesignId={completedDesignId}
      writerQueued={writerQueued}
      onCancelWait={() => void action('conversation.stop')}
      onUseOutputInCode={useOutputInCode}
      onAddResource={() => setup()}
      onOpenResource={resourcePanel}
      onUnbindResource={unbindResource}
      onFind={findConversation}
      onManageConversation={manageConversation}
      onManageBrowser={manageBrowser}
      onDeleteConversation={deleteConversation}
      onOpenTerminal={() =>
        onPanel({
          panel_kind: 'native.terminal',
          title: 'Interactive terminal',
          required_capabilities: ['native:terminal'],
        })
      }
      onOpenSuggestion={(suggestion) => {
        onPanel(suggestion.descriptor);
        controller.dismissSuggestion(suggestion);
      }}
      onDismissSuggestion={(suggestion) =>
        controller.dismissSuggestion(suggestion)
      }
    />
  ) : null;
  const isRunning = Boolean(running);
  // The latest turn is still in flight while it waits for an approval, even
  // though the worker is parked: no actions until it settles.
  const turnInFlight = isRunning || generation?.status === 'waiting_approval';
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  // Elapsed time for the live activity row, as this client observed it.
  const [runStartedAt, setRunStartedAt] = useState<number | undefined>();
  useEffect(() => {
    if (isRunning) setRunStartedAt((value) => value ?? Date.now());
    else setRunStartedAt(undefined);
  }, [isRunning]);
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
  const interrupted = !isRunning && generation?.status === 'interrupted';
  const failed =
    !isRunning &&
    !interrupted &&
    Boolean(failure) &&
    failure!.generation === (generation?.generation_id ?? '');
  const settledMedia = useMemo(
    () =>
      new Set(
        items.flatMap((item) => item.media.map((media) => media.reference)),
      ),
    [items],
  );
  const pendingMedia = liveMedia(state.activity, settledMedia);
  const settledCards = useMemo(
    () => new Set(items.flatMap((item) => item.cards.map(cardKey))),
    [items],
  );
  const pendingCards = liveCards(state.activity, settledCards);
  const cardActions: CardActions = {
    resource: (bindingId) =>
      resources.find((item) => item.binding.binding_id === bindingId),
    open: resourcePanel,
    rename: async (bindingId, name) => {
      if (!id || !state.conversation) return;
      await controller.intent(
        id,
        'resource.rename',
        { binding_id: bindingId, name },
        state.conversation.revision,
      );
    },
    undo: async (bindingId) => {
      if (!id || !state.conversation) return;
      const removed = resources.find(
        (item) => item.binding.binding_id === bindingId,
      );
      // Close its panels first, so nothing reads it once it is gone.
      if (removed) onResourceRemoved?.(removed.resource_ref);
      await controller.intent(
        id,
        'resource.discard',
        { binding_id: bindingId },
        state.conversation.revision,
      );
    },
    connect: (page) => navigate(`/settings/${page}`),
  };
  // Retry and Send again resend the last message as it was: its words and
  // its files, never the files' names as text (B136). A follow-up note is
  // the server continuing, not something the person sent.
  const lastUser = useMemo(() => {
    for (let index = items.length - 1; index >= 0; index -= 1)
      if (items[index].row.role === 'user' && !items[index].row.note) {
        const blocks = items[index].row.blocks;
        return {
          text: blocks
            .filter((block) => block.type !== 'attachment')
            .map(publicBlockText)
            .filter(Boolean)
            .join('\n'),
          attachments: blocks.flatMap((block) =>
            block.type === 'attachment'
              ? [
                  {
                    attachment_ref: block.attachment_ref,
                    name: block.name,
                    mime_type: block.mime_type as AttachmentView['mime_type'],
                    size_bytes: block.size_bytes,
                    revision: block.revision,
                  },
                ]
              : [],
          ),
        };
      }
    return { text: '', attachments: [] as AttachmentView[] };
  }, [items]);
  const lastUserText = lastUser.text;
  let lastAssistant = -1;
  for (let index = items.length - 1; index >= 0; index -= 1)
    if (items[index].row.role === 'assistant') {
      lastAssistant = index;
      break;
    }
  // Actions show once per assistant turn (its last row) and copy the turn.
  const turns = useMemo(() => {
    const ends: boolean[] = [];
    const texts: (string | undefined)[] = [];
    let start = 0;
    items.forEach((item, index) => {
      const next = items[index + 1];
      const end =
        item.row.role !== 'assistant' || next?.row.role !== 'assistant';
      ends.push(end);
      if (item.row.role !== 'assistant') {
        texts.push(undefined);
        start = index + 1;
        return;
      }
      texts.push(
        end && index > start
          ? items
              .slice(start, index + 1)
              .map((part) =>
                part.row.blocks.map(publicBlockText).filter(Boolean).join('\n'),
              )
              .filter(Boolean)
              .join('\n\n')
          : undefined,
      );
      if (end) start = index + 1;
    });
    return { ends, texts };
  }, [items]);
  const newChatRef = useRef(onNewChat);
  useLayoutEffect(() => {
    newChatRef.current = onNewChat;
  });
  const recoverTurn = useCallback(
    (next: RecoveryAction) => {
      if (next === 'model') setModelPickerOpen(true);
      else if (next === 'providers') navigate('/settings/providers');
      else if (next === 'new') newChatRef.current();
    },
    [navigate],
  );
  // Waiting messages come from the server's list, re-read when a queue event
  // arrives, a run starts or ends, or the server changes (B107, B108).
  const lastQueueEvent =
    [...state.activity]
      .reverse()
      .find((record) => QUEUE_EVENTS.has(record.event.type))?.event.event_id ??
    '';
  const waiting = useWaitingMessages({
    conversationId: id ?? '',
    refreshKey: [
      lastQueueEvent,
      generation?.generation_id ?? '',
      generation?.status ?? '',
      String(Boolean(generation?.quiesced)),
      state.projection?.server_epoch ?? '',
      pendingSteering?.commandId ?? '',
    ].join(':'),
    steeringGeneration:
      turnInFlight &&
      state.activity.some((record) => record.event.type.startsWith('steering.'))
        ? (generation?.generation_id ?? '')
        : '',
    readWaiting: (conversation, signal) =>
      controller.waitingMessages(conversation, signal),
    readSteering: (conversation, run, signal) =>
      controller.steering(conversation, run, undefined, signal),
  });
  const waitingAction = useCallback(
    async (action: WaitingAction, item: WaitingMessage, text?: string) => {
      if (!id) return;
      setError('');
      const run = async (revision: string) => {
        const current = await controller.workspaceFor(id);
        await controller.intent(
          id,
          `conversation.queue.${action}`,
          {
            submission_id: item.id,
            expected_queue_revision: revision,
            ...(action === 'edit' ? { text } : {}),
          },
          current.revision,
        );
      };
      try {
        try {
          await run(item.revision);
        } catch (cause) {
          // Stop pauses waiting messages, which moves their revision. When
          // only that changed (the text is what the person saw), act on the
          // current revision once instead of refusing.
          if (clientError(cause).code !== 'queue_revision_conflict')
            throw cause;
          const fresh = (await controller.waitingMessages(id)).items.find(
            (value) => value.submission_id === item.id,
          );
          if (!fresh?.editable || fresh.text !== item.text) throw cause;
          await run(fresh.revision);
        }
      } catch (cause) {
        setError(clientError(cause), () => waiting.reload());
        throw cause;
      } finally {
        waiting.reload();
      }
    },
    [controller, id, setError, waiting],
  );
  const nextWaiting = sendable(waiting.items, turnInFlight);
  const sendWaitingNow = () => {
    if (nextWaiting)
      void waitingAction('dispatch', nextWaiting).catch(() => undefined);
  };
  // A stopped run whose transcript ends on the person's own message.
  const unanswered =
    !turnInFlight &&
    !state.history &&
    generation?.status === 'stopped' &&
    items.at(-1)?.row.role === 'user' &&
    !items.at(-1)?.row.note &&
    Boolean(lastUserText) &&
    pending?.conversation !== id;
  const listedTitle = state.conversations.find((item) => item.id === id)?.title;
  const title =
    listedTitle || state.conversation?.title || 'Start a conversation';
  const currentModel = state.handshake?.models.find(
    (model) => model.model_ref === controls?.model_selection?.model_ref,
  );
  const modelName = currentModel
    ? splitModelLabel(currentModel.label).name
    : modelRefName(controls?.model_selection?.model_ref);
  const editMessage = useCallback(
    (text: string) => {
      if (!id) return;
      const current = controller.getDraft(id);
      const next = current.text.trim()
        ? `${current.text.replace(/\s+$/, '')}\n\n${text}`
        : text;
      controller.setDraft(id, { ...current, text: next });
      requestAnimationFrame(() => {
        const input = composerRef.current;
        input?.focus();
        input?.setSelectionRange(next.length, next.length);
      });
    },
    [controller, id],
  );
  const retryAction = useRef<() => void>(() => undefined);
  useLayoutEffect(() => {
    retryAction.current = () => {
      if (lastUserText) send(lastUserText, true, lastUser.attachments);
    };
  });
  const retryLast = useCallback(() => retryAction.current(), []);
  async function rename(next: string) {
    if (!id || !state.conversation) return;
    try {
      await controller.intent(
        id,
        'conversation.rename',
        { title: next },
        state.conversation.revision,
      );
      setError('');
    } catch (cause) {
      setError(clientError(cause).message);
    }
  }
  async function updateControls(patch: {
    profile_id?: string;
    approval_mode?: 'allow_all';
  }) {
    if (!id || !controls || !state.workspace) return;
    await controller.intent(
      id,
      'conversation.controls',
      {
        model_selection: controls.model_selection,
        runtime_mode: controls.runtime_mode,
        profile_id: controls.profile_id,
        approval_mode: controls.approval_mode,
        ...patch,
      },
      state.workspace.revision,
    );
  }
  const allowInChat = () => updateControls({ approval_mode: 'allow_all' });
  function chooseTarget(
    kind: 'artifact' | 'workspace',
    bindingId: string | null,
  ) {
    if (!id) return;
    setTargets((previous) => ({
      ...previous,
      [id]: [
        ...(previous[id] ?? defaultTargetIds).filter(
          (binding) =>
            !resources.some(
              (resource) =>
                resource.binding.binding_id === binding &&
                resource.binding.kind === kind,
            ),
        ),
        ...(bindingId ? [bindingId] : []),
      ],
    }));
  }
  // "@" mentions: a keyboard path to agent profile, write target and files.
  const selectedTargets = id ? (targetSelection[id] ?? defaultTargetIds) : [];
  const mentionItems: MentionItem[] = [
    ...profileChoices(state.workspace?.profiles ?? []).map((profile) => ({
      id: `profile:${profile.id}`,
      group: 'Agents',
      label: profile.label,
      // One line per profile: its name, no sentence repeated on every row (U20).
      description: '',
      icon: <Bot size={16} />,
      current:
        currentProfileChoice(
          state.workspace?.profiles ?? [],
          controls?.profile_id,
        ) === profile.id,
      onChoose: () =>
        void updateControls({ profile_id: profile.id }).catch((cause) =>
          setError(clientError(cause).message),
        ),
    })),
    ...resources
      .filter((resource) => resource.available)
      .map((resource) => ({
        id: `resource:${resource.binding.binding_id}`,
        group: 'Resources',
        label: resource.title,
        description:
          resource.binding.kind === 'artifact'
            ? 'Send changes to this design'
            : 'Send changes to this code folder',
        icon:
          resource.binding.kind === 'artifact' ? (
            <Palette size={16} />
          ) : (
            <Code2 size={16} />
          ),
        current: selectedTargets.includes(resource.binding.binding_id),
        onChoose: () =>
          chooseTarget(
            resource.binding.kind as 'artifact' | 'workspace',
            resource.binding.binding_id,
          ),
      })),
    {
      id: 'file',
      group: 'Files',
      label: 'Attach a file…',
      description: 'Choose a file from this device',
      icon: <Paperclip size={16} />,
      onChoose: () => void attach(),
    },
  ];
  // "↓ N new" counts live rows that arrived while the reader was away.
  const liveItemCount =
    items.length -
    (earlierIds.size
      ? items.filter((item) => earlierIds.has(item.row.id)).length
      : 0);
  const [newCount, setNewCount] = useState(0);
  const seenItems = useRef<{ conversation: string | null; count: number }>({
    conversation: null,
    count: 0,
  });
  useEffect(() => {
    const previous = seenItems.current;
    seenItems.current = { conversation: id, count: liveItemCount };
    if (
      previous.conversation !== id ||
      state.history ||
      followingLatest.current
    ) {
      setNewCount(0);
      return;
    }
    if (liveItemCount > previous.count)
      setNewCount((count) => count + liveItemCount - previous.count);
  }, [id, liveItemCount, state.history]);
  // Scrolling up loads earlier rows; the first visible row stays in place.
  const earlierAnchor = useRef<{ row: string; top: number } | null>(null);
  const loadEarlier = useCallback(() => {
    const first =
      transcriptRef.current?.querySelector<HTMLElement>('[data-row-id]');
    earlierAnchor.current = first
      ? { row: first.dataset.rowId!, top: first.getBoundingClientRect().top }
      : null;
    void controller.loadEarlier().catch((cause) => {
      earlierAnchor.current = null;
      setError(clientError(cause).message);
    });
  }, [controller, setError]);
  useLayoutEffect(() => {
    const anchor = earlierAnchor.current;
    const transcript = transcriptRef.current;
    if (!anchor || !transcript) return;
    earlierAnchor.current = null;
    const element = Array.from(
      transcript.querySelectorAll<HTMLElement>('[data-row-id]'),
    ).find((row) => row.dataset.rowId === anchor.row);
    if (element)
      transcript.scrollTop += element.getBoundingClientRect().top - anchor.top;
  }, [state.earlier]);
  const topSentinel = useRef<HTMLDivElement>(null);
  const canLoadEarlier =
    !state.history && state.earlierAvailable && historyReady;
  useEffect(() => {
    const target = topSentinel.current;
    const root = transcriptRef.current;
    if (
      !canLoadEarlier ||
      !target ||
      !root ||
      typeof IntersectionObserver === 'undefined'
    )
      return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting && !followingLatest.current) loadEarlier();
      },
      { root, rootMargin: '480px 0px 0px 0px' },
    );
    observer.observe(target);
    return () => observer.disconnect();
  }, [canLoadEarlier, loadEarlier, id]);
  function openContextSheet() {
    overlay.open({
      kind: 'sheet',
      key: 'conversation-context',
      // Full height on phones, a side sheet on tablets (responsive.css).
      className: 'context-sheet',
      title: 'Conversation context',
      description: '',
      content: sheetHosted ? (
        <ContextSlot active className="context-sheet-slot" host={contextHost} />
      ) : (
        contextRail
      ),
    });
  }
  function toggleContext() {
    if (!hosted) {
      openContextSheet();
      return;
    }
    // Act on the width now, not the last render: layout can still be
    // settling right after a load or a panel change.
    const width = chatWorkspaceRef.current?.getBoundingClientRect().width;
    const narrowNow = width ? isNarrowChat(width, narrowChat) : narrowChat;
    if (narrowNow !== narrowChat) setNarrowChat(narrowNow);
    if (narrowNow) setFloatingOpen((open) => (floatingContext ? !open : true));
    else {
      const hidden = !contextHidden;
      setContextHiddenState(hidden);
      try {
        if (hidden) localStorage.setItem(CONTEXT_HIDDEN_KEY, '1');
        else localStorage.removeItem(CONTEXT_HIDDEN_KEY);
      } catch {
        /* The choice still applies for this session. */
      }
    }
  }
  const toggleFromShortcut = useEffectEvent(() => {
    if (id && hosted) toggleContext();
  });
  const shortcutCount = useRef(contextToggle);
  useEffect(() => {
    if (shortcutCount.current === contextToggle) return;
    shortcutCount.current = contextToggle;
    toggleFromShortcut();
  }, [contextToggle]);
  const contextShown = hosted ? cardActive : undefined;
  const needsModel =
    !isRunning &&
    Boolean(state.workspace) &&
    state.status === 'ready' &&
    !sendActionReady;
  // One way forward when the model can't answer (decision 10): reconnect its
  // provider, or choose a model (Setup when nothing can be chosen yet).
  const modelStatus = state.workspace?.model_status;
  const reconnect =
    modelStatus?.state === 'unavailable' && modelStatus.fix === 'reconnect';
  const anyModel = (state.handshake?.models ?? []).some(
    (model) => model.available,
  );
  const setupModelButton = (
    <Button
      className="composer-setup-model"
      onClick={() =>
        reconnect
          ? navigate('/settings/providers')
          : anyModel
            ? setModelPickerOpen(true)
            : navigate('/setup')
      }
    >
      {reconnect
        ? 'Reconnect'
        : modelStatus?.state === 'unavailable'
          ? 'Choose another model'
          : anyModel
            ? 'Choose a model'
            : 'Set up a model'}
    </Button>
  );
  const composerControls = (
    <ComposerControls
      key={id}
      composer={composerSnapshot ?? undefined}
      onSkillAction={skillAction}
      skillsOpen={skillsOpen}
      onSkillsOpenChange={setSkillsOpen}
      disabled={isRunning || busy || talkBusy || composerBusy}
      onError={setError}
      onAttach={() => void attach()}
      attachDisabled={busy}
      onAddResource={setup}
      modelPickerOpen={modelPickerOpen}
      onModelPickerOpenChange={setModelPickerOpen}
      singleLine={singleLine}
      contextUsage={state.workspace?.context_usage}
      anchor={composerFieldRef}
    />
  );
  const sendBlocked =
    busy ||
    Boolean(pendingSteering) ||
    Boolean(pendingSubmit) ||
    Boolean(pendingResume);
  const runAnnouncement = generation
    ? ({
        running: 'Row-Bot is working.',
        stopping: 'Stopping — waiting for the worker to finish.',
        stopped: 'Stopped.',
        waiting_approval: 'Waiting for your approval.',
        completed: 'Response complete.',
        interrupted: 'The response was interrupted.',
      }[generation.status] ?? '')
    : '';
  return (
    <CardActionsContext.Provider value={cardActions}>
      <div
        className={`chat-workspace${compactContext ? ' compact-context' : ''}`}
        ref={chatWorkspaceRef}
      >
        <div
          className="chat-content"
          ref={chatContentRef}
          role="region"
          tabIndex={0}
          aria-label="Conversation details"
        >
          <ConversationHeader
            title={title}
            canRename={Boolean(
              id && state.conversation && state.status === 'ready',
            )}
            onRename={rename}
            model={id ? modelName : undefined}
            onFind={id ? findConversation : undefined}
            onShare={id ? manageConversation : undefined}
            onContext={
              id && (hosted || compactContext) ? toggleContext : undefined
            }
            contextPressed={contextShown}
            contextDisabled={!contextReady}
            actions={headerActions}
            leading={headerLeading}
            menuActions={headerMenu}
          >
            {missingReceipt &&
              (missingReceipt.key === steeringKey ||
                missingReceipt.key === submitKey ||
                missingReceipt.key === resumeKey) && (
                <Button disabled={busy} onClick={reviewMissingReceipt}>
                  Stop checking
                </Button>
              )}
          </ConversationHeader>
          <div
            role="log"
            aria-label="Conversation"
            aria-live="polite"
            aria-relevant="additions"
            className="transcript"
            ref={transcriptRef}
            tabIndex={0}
            onScroll={(event) => {
              if (state.loadingConversation) return;
              const transcript = event.currentTarget;
              const following =
                transcript.scrollHeight -
                  transcript.clientHeight -
                  transcript.scrollTop <=
                24;
              if (state.history) {
                setShowLatest(true);
                return;
              }
              const pinned = pinnedGeometry.current;
              if (
                !following &&
                followingLatest.current &&
                pinned &&
                (transcript.scrollHeight !== pinned.height ||
                  transcript.clientHeight !== pinned.client ||
                  transcript.clientWidth !== pinned.width)
              ) {
                // The layout moved under a reader who was following (a panel
                // opened, the composer changed height): stay on the latest.
                scrollToLatest();
                return;
              }
              followingLatest.current = following;
              setShowLatest(!following);
              if (following) setNewCount(0);
            }}
          >
            <div
              className="transcript-content"
              ref={transcriptContentRef}
              style={{ display: 'flow-root' }}
            >
              {canLoadEarlier && (
                <div className="transcript-sentinel" ref={topSentinel}>
                  <Button
                    variant="ghost"
                    disabled={state.loadingEarlier}
                    onClick={loadEarlier}
                  >
                    {state.loadingEarlier
                      ? 'Loading earlier messages…'
                      : 'Earlier messages'}
                  </Button>
                </div>
              )}
              {state.history?.previous_cursor && (
                <div className="transcript-sentinel">
                  <Button
                    variant="ghost"
                    disabled={!historyReady}
                    onClick={() =>
                      void controller
                        .showHistory(undefined, state.history!.previous_cursor!)
                        .catch((e) => setError(clientError(e).message))
                    }
                  >
                    Earlier messages
                  </Button>
                </div>
              )}
              {state.loadingConversation ? (
                <Skeleton label="Opening conversation" />
              ) : items.length || pending?.conversation === id ? (
                <>
                  {earlierIds.size > 0 && (
                    // Loaded history is not announced as new conversation.
                    <div className="transcript-earlier" aria-live="off">
                      {items.map((item, index) =>
                        earlierIds.has(item.row.id) ? (
                          <TranscriptMessage
                            key={`${id}:${item.row.id}`}
                            row={item.row}
                            conversationId={id}
                            traces={item.traces}
                            embeds={item.embeds}
                            media={item.media}
                            cards={item.cards}
                            toolbar={turns.ends[index]}
                            copyText={turns.texts[index]}
                            onRecover={recoverTurn}
                            onEdit={
                              item.row.role === 'user' ? editMessage : undefined
                            }
                          />
                        ) : null,
                      )}
                    </div>
                  )}
                  {items.map((item, index) =>
                    earlierIds.has(item.row.id) ? null : (
                      <TranscriptMessage
                        key={`${id}:${item.row.id}`}
                        row={item.row}
                        conversationId={id}
                        traces={item.traces}
                        embeds={item.embeds}
                        media={item.media}
                        cards={item.cards}
                        latest={index === lastAssistant && !turnInFlight}
                        toolbar={turns.ends[index]}
                        copyText={turns.texts[index]}
                        onRecover={recoverTurn}
                        streaming={
                          turnInFlight &&
                          index === items.length - 1 &&
                          item.row.role === 'assistant'
                        }
                        onRetry={
                          index === lastAssistant &&
                          !turnInFlight &&
                          !state.history &&
                          lastUserText &&
                          sendActionReady
                            ? retryLast
                            : undefined
                        }
                        onEdit={
                          item.row.role === 'user' ? editMessage : undefined
                        }
                      />
                    ),
                  )}
                </>
              ) : (
                <ChatEmpty
                  conversationId={id}
                  disabled={busy}
                  // A prompt fills the composer to edit first (U17).
                  onChoose={editMessage}
                  recent={state.conversations}
                  onOpen={(target) => navigate(`/conversations/${target}`)}
                />
              )}
              {pending?.conversation === id &&
                !rows.some((row) => row.message_id === pending.id) && (
                  <article
                    className="message message-user message-pending"
                    aria-label="You message awaiting confirmation"
                    data-message-id={pending.id}
                  >
                    <div className="transcript-content">
                      <div className="message-text">{pending.text}</div>
                      <small className="message-delivery-state">
                        Awaiting confirmation
                      </small>
                    </div>
                  </article>
                )}
              {id &&
                !state.history &&
                (isRunning ||
                  liveTraceGroups.length > 0 ||
                  pendingMedia.length > 0 ||
                  pendingCards.length > 0 ||
                  computer.visible ||
                  (generation?.approval_id &&
                    generation.status === 'waiting_approval')) && (
                  <div className="message message-assistant message-live">
                    <div className="transcript-content">
                      <TranscriptTrace
                        conversation={id}
                        groups={liveTraceGroups}
                        live={{
                          running: isRunning,
                          thinking: thinkingActive,
                          waiting:
                            generation?.status === 'waiting_approval' &&
                            !computerPause,
                          paused: computerPause,
                          stopping: generation?.status === 'stopping',
                          startedAt: runStartedAt,
                        }}
                      >
                        {computer.visible && (
                          <ComputerUseCard
                            conversationId={id}
                            snapshot={computer.snapshot}
                            stopped={computer.stopped}
                            loadPreview={controller.computerUsePreview}
                            send={controller.computerUseCommand}
                            onChange={computer.apply}
                            onStopped={computer.markStopped}
                          />
                        )}
                        {generation?.approval_id &&
                          generation.status === 'waiting_approval' &&
                          !computerPause &&
                          computer.checked && (
                            <ApprovalCard
                              id={generation.approval_id}
                              hint={
                                approvalEvent?.event.type ===
                                'approval.required'
                                  ? approvalEvent.event.payload
                                  : undefined
                              }
                              onAllowInChat={
                                controls?.approval_mode === 'allow_all'
                                  ? undefined
                                  : allowInChat
                              }
                            />
                          )}
                      </TranscriptTrace>
                      <TranscriptCards cards={pendingCards} live />
                      {pendingMedia.length > 0 && (
                        <div
                          className="message-media-grid"
                          data-count={pendingMedia.length}
                        >
                          {pendingMedia.map((media) => (
                            <MediaPreview
                              key={media.reference}
                              reference={media.reference}
                              mime={media.mime}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              {unanswered && (
                // A stop can land after queued guidance was delivered: say so
                // instead of leaving the last message silently unanswered.
                <div className="turn-notice" data-tone="neutral">
                  <CircleStop className="turn-notice-icon" aria-hidden />
                  <div className="turn-notice-text">
                    <strong>Stopped before a reply</strong>
                    <span>
                      {nextWaiting
                        ? "Your last message wasn't answered. The message waiting below goes next."
                        : "Your last message wasn't answered."}
                    </span>
                  </div>
                  {!waiting.items.length && (
                    <div className="turn-notice-actions">
                      <Button
                        variant="secondary"
                        disabled={sendBlocked || !sendActionReady}
                        onClick={retryLast}
                      >
                        Send again
                      </Button>
                    </div>
                  )}
                </div>
              )}
              {(interrupted || failed) && (
                <div
                  className="turn-notice"
                  data-tone={interrupted ? 'warning' : 'danger'}
                >
                  <TriangleAlert className="turn-notice-icon" aria-hidden />
                  <div className="turn-notice-text">
                    <strong>
                      {interrupted
                        ? 'The response was interrupted'
                        : 'The response could not finish'}
                    </strong>
                    <span>
                      {interrupted
                        ? 'Resume to continue where it stopped, or switch to another model.'
                        : 'Try again, or switch to another model.'}
                      {generation?.external_outcome === 'uncertain'
                        ? ' Some steps may already have run, so check what changed before you try again.'
                        : ''}
                    </span>
                  </div>
                  <div className="turn-notice-actions">
                    {interrupted && (
                      <Button
                        disabled={
                          busy ||
                          Boolean(pendingSubmit) ||
                          Boolean(pendingSteering) ||
                          Boolean(pendingResume)
                        }
                        onClick={() => void action('conversation.resume')}
                      >
                        Resume
                      </Button>
                    )}
                    {failed && lastUserText && (
                      <Button
                        disabled={sendBlocked || !sendActionReady}
                        onClick={retryLast}
                      >
                        Retry
                      </Button>
                    )}
                    {controls && (
                      <Button
                        variant="ghost"
                        onClick={() => setModelPickerOpen(true)}
                      >
                        Switch model
                      </Button>
                    )}
                  </div>
                </div>
              )}
              {state.history?.next_cursor && (
                <div className="transcript-sentinel">
                  <Button
                    variant="ghost"
                    disabled={!historyReady}
                    onClick={() =>
                      void controller
                        .showHistory(undefined, state.history!.next_cursor!)
                        .catch((e) => setError(clientError(e).message))
                    }
                  >
                    Later messages
                  </Button>
                </div>
              )}
            </div>
          </div>
          {(state.history || showLatest) && (
            <button
              type="button"
              className="latest-pill"
              aria-label="Latest messages"
              aria-description={
                generation?.status === 'waiting_approval' && !state.history
                  ? 'An approval is waiting'
                  : newCount > 0 && !state.history
                    ? `${newCount} new`
                    : undefined
              }
              onClick={() => {
                followingLatest.current = true;
                setShowLatest(false);
                setNewCount(0);
                if (state.history || state.earlier.length)
                  controller.showLatest();
                else scrollToLatest();
              }}
            >
              <ArrowDown aria-hidden />
              <span aria-hidden>
                {generation?.status === 'waiting_approval' && !state.history
                  ? 'Approval needed'
                  : newCount > 0 && !state.history
                    ? `${newCount} new`
                    : 'Latest'}
              </span>
            </button>
          )}
          <p role="status" className="visually-hidden run-status">
            {runAnnouncement}
          </p>
          {error && (
            <div role="alert" className="chat-error">
              <TriangleAlert aria-hidden />
              <span>{error.message}</span>
              {error.action && (
                <ErrorFix
                  action={error.action}
                  retry={error.retry}
                  sendNow={nextWaiting ? sendWaitingNow : undefined}
                  chooseModel={
                    controls ? () => setModelPickerOpen(true) : undefined
                  }
                />
              )}
              <button
                type="button"
                className="chat-error-dismiss"
                aria-label="Dismiss error"
                onClick={() => setError('')}
              >
                <X aria-hidden />
              </button>
            </div>
          )}
        </div>
        {id && (
          <form
            className="composer"
            data-single-line={singleLine ? 'true' : undefined}
            aria-label="Message composer"
            aria-busy={busy || talkBusy}
            onSubmit={(e) => {
              e.preventDefault();
              send();
            }}
          >
            <WaitingMessages
              items={waiting.items}
              running={turnInFlight}
              busy={busy || state.status !== 'ready'}
              onAction={waitingAction}
            />
            <div
              className="composer-field"
              ref={composerFieldRef}
              data-dragging={dragging ? 'true' : undefined}
              onDragEnter={(event) => {
                if (!hasFiles(event)) return;
                event.preventDefault();
                setDragging(true);
              }}
              onDragOver={(event) => {
                if (!hasFiles(event)) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = attachBlocked ? 'none' : 'copy';
              }}
              onDragLeave={(event) => {
                const next = event.relatedTarget;
                if (!(
                  next instanceof Node && event.currentTarget.contains(next)
                ))
                  setDragging(false);
              }}
              onDrop={(event) => {
                if (!hasFiles(event)) return;
                event.preventDefault();
                setDragging(false);
                if (!attachBlocked)
                  void attachFiles(Array.from(event.dataTransfer.files));
              }}
            >
              {dragging && (
                <div className="composer-drop" aria-hidden>
                  <Paperclip />
                  <span>
                    Drop to attach · up to {ATTACHMENT_LIMITS.fileMegabytes} MB
                    each
                  </span>
                </div>
              )}
              {(!!resources.length ||
                !!draft.attachments.length ||
                (singleLine && needsModel) ||
                Boolean(
                  composerSnapshot &&
                  (chipSkills(composerSnapshot).length ||
                    composerSnapshot.suggestions.length),
                )) && (
                <div className="composer-chips">
                  {singleLine && needsModel && setupModelButton}
                  {!!resources.length && (
                    <ResourceTargets
                      resources={resources}
                      selected={targetSelection[id] ?? defaultTargetIds}
                      onChange={chooseTarget}
                    />
                  )}
                  {!!draft.attachments.length && (
                    <ul
                      className="composer-attachments"
                      aria-label="Attachments"
                    >
                      {draft.attachments.map((a) => (
                        <li key={a.attachment_ref} className="attachment-chip">
                          <Paperclip aria-hidden />
                          <span>{a.name}</span>
                          <button
                            type="button"
                            className="attachment-chip-remove"
                            aria-label={`Remove ${a.name}`}
                            onClick={() =>
                              controller.setDraft(id, {
                                ...draft,
                                attachments: draft.attachments.filter(
                                  (item) =>
                                    item.attachment_ref !== a.attachment_ref,
                                ),
                              })
                            }
                          >
                            <X aria-hidden />
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                  {state.workspace?.model_status?.sees_images === false &&
                    draft.attachments.some((a) =>
                      a.mime_type?.startsWith('image/'),
                    ) && (
                      <p className="composer-vision-note" role="status">
                        <ImageOff aria-hidden />
                        <span>
                          {modelName || 'This model'} can't see images.
                        </span>
                        <button
                          type="button"
                          className="composer-vision-choose"
                          onClick={() => navigate('/settings/models#vision')}
                        >
                          Choose a vision model
                        </button>
                      </p>
                    )}
                  {composerSnapshot && (
                    <ComposerSkillChips
                      composer={composerSnapshot}
                      disabled={isRunning || busy || composerBusy}
                      action={skillAction}
                    />
                  )}
                </div>
              )}
              {singleLine && composerControls}
              <label className="sr-only" htmlFor="message-composer">
                Message
              </label>
              <textarea
                ref={composerRef}
                id="message-composer"
                className="input message-composer"
                value={draft.text}
                rows={1}
                maxLength={200000}
                placeholder={
                  // A one-line field keeps its hint to one line.
                  isRunning
                    ? singleLine
                      ? 'Follow-up…'
                      : 'Queue a follow-up…'
                    : singleLine
                      ? 'Message'
                      : 'Message Row-Bot…'
                }
                aria-describedby={
                  composerStateReason ? 'message-composer-state' : undefined
                }
                onChange={(e) => {
                  controller.setDraft(id, { ...draft, text: e.target.value });
                  setComposerCursor(
                    e.target.selectionStart ?? e.target.value.length,
                  );
                }}
                onSelect={(e) =>
                  setComposerCursor(e.currentTarget.selectionStart ?? 0)
                }
                onPaste={(e) => {
                  // A pasted screenshot or copied files attach; text pastes
                  // as text (parity row 1).
                  const files = Array.from(e.clipboardData?.files ?? []);
                  if (!files.length) return;
                  if (!e.clipboardData.getData('text/plain'))
                    e.preventDefault();
                  if (!attachBlocked)
                    void attachFiles(
                      files.map((file, index) =>
                        pastedFileName(file, new Date(), index),
                      ),
                    );
                }}
                onKeyDown={(e) => {
                  if (
                    !e.nativeEvent.isComposing &&
                    (slashPaletteRef.current?.key(e) ||
                      mentionPaletteRef.current?.key(e))
                  ) {
                    e.preventDefault();
                    return;
                  }
                  if (
                    e.key === 'ArrowUp' &&
                    !draft.text &&
                    !e.shiftKey &&
                    !e.altKey &&
                    !e.metaKey &&
                    !e.ctrlKey &&
                    lastUserText
                  ) {
                    // ↑ in an empty composer brings back the last message.
                    e.preventDefault();
                    editMessage(lastUserText);
                    return;
                  }
                  if (
                    e.key === 'Enter' &&
                    !e.shiftKey &&
                    !e.metaKey &&
                    !e.ctrlKey &&
                    !e.nativeEvent.isComposing
                  ) {
                    e.preventDefault();
                    if (!isRunning) send();
                    else if (
                      draft.text.trim() &&
                      draft.text.length <= 16000 &&
                      !sendBlocked
                    )
                      void action('conversation.steer');
                  }
                }}
              />
              {composerSnapshot && (
                <SlashPalette
                  ref={slashPaletteRef}
                  text={draft.text}
                  cursor={composerCursor}
                  commands={composerSnapshot.commands}
                  disabled={isRunning || busy || composerBusy}
                  inputRef={composerRef}
                  onChoose={(command, token) =>
                    void chooseSlash(command, token)
                  }
                />
              )}
              <MentionPalette
                ref={mentionPaletteRef}
                text={draft.text}
                cursor={composerCursor}
                items={mentionItems}
                disabled={busy || composerBusy}
                inputRef={composerRef}
                onConsume={(token) => replaceSlashToken(token)}
              />
              <div className="composer-toolbar" ref={toolbarRef}>
                {!singleLine && composerControls}
                <div className="composer-actions">
                  {compactToolbar &&
                    (pendingSubmit || pendingResume || pendingSteering) && (
                      <Menu
                        label="Message actions"
                        iconOnly
                        variant="ghost"
                        actions={[
                          ...(pendingSubmit
                            ? [
                                {
                                  label: 'Check message',
                                  disabled: busy,
                                  onSelect: () => void recover(),
                                },
                              ]
                            : []),
                          ...(pendingResume
                            ? [
                                {
                                  label: 'Check Resume',
                                  disabled: busy,
                                  onSelect: () => void resume(true),
                                },
                              ]
                            : []),
                          ...(pendingSteering
                            ? [
                                {
                                  label: 'Check waiting message',
                                  disabled: busy,
                                  onSelect: () => void queueMessage(),
                                },
                              ]
                            : []),
                        ]}
                      >
                        <MoreHorizontal size={18} aria-hidden />
                      </Menu>
                    )}
                  {!compactToolbar && pendingSubmit && (
                    <Button disabled={busy} onClick={() => void recover()}>
                      Check message
                    </Button>
                  )}
                  {!compactToolbar && pendingResume && (
                    <Button disabled={busy} onClick={() => void resume(true)}>
                      Check Resume
                    </Button>
                  )}
                  {!compactToolbar && pendingSteering && (
                    <Button disabled={busy} onClick={() => void queueMessage()}>
                      Check waiting message
                    </Button>
                  )}
                  {!singleLine && (
                    <ContextUsage usage={state.workspace?.context_usage} />
                  )}
                  {voiceScope && (
                    <span className="composer-voice">
                      <VoiceControls
                        compact
                        scope={voiceScope}
                        available={
                          voiceExposure.key === voiceHostKey &&
                          voiceExposure.dictate
                        }
                        unavailableReason={voiceExposure.dictateReason}
                        disabled={busy || talkBusy}
                        start={(request, signal) =>
                          controller.startDictation(voiceScope, request, signal)
                        }
                        transcribe={(handle, utterance, audio, signal) =>
                          controller.transcribeDictation(
                            voiceScope,
                            handle,
                            utterance,
                            audio,
                            signal,
                          )
                        }
                        stop={(handle, signal) =>
                          controller.stopDictation(voiceScope, handle, signal)
                        }
                        applyTranscript={(scope, result) =>
                          controller.applyDictation(scope, result)
                        }
                      />
                      <ConversationVoice
                        key={`talk:${voiceScope.clientSessionId}:${voiceScope.serverEpoch}:${voiceScope.conversationId}:${voiceScope.selectionKey}`}
                        compact
                        chevron
                        controller={controller}
                        scope={voiceScope}
                        available={
                          voiceExposure.key === voiceHostKey &&
                          (voiceExposure.talk || voiceExposure.realtime)
                        }
                        unavailableReason={voiceExposure.talkReason}
                        disabled={busy}
                        running={isRunning}
                        onBusy={setTalkBusy}
                        context={
                          controls?.model_selection && state.conversation
                            ? {
                                conversation_revision:
                                  state.conversation.revision,
                                model_selection: controls.model_selection,
                                write_targets: resources
                                  .filter((resource) =>
                                    (
                                      targetSelection[id ?? ''] ??
                                      defaultTargetIds
                                    ).includes(resource.binding.binding_id),
                                  )
                                  .map((resource) => ({
                                    kind: resource.binding.kind as
                                      'artifact' | 'workspace',
                                    binding_id: resource.binding.binding_id,
                                    resource_id: resource.binding.resource_id,
                                    binding_revision: resource.binding.revision,
                                    resource_revision:
                                      resource.resource_revision,
                                  })),
                              }
                            : null
                        }
                        targets={resources
                          .filter((resource) =>
                            (
                              targetSelection[id ?? ''] ?? defaultTargetIds
                            ).includes(resource.binding.binding_id),
                          )
                          .map((resource) => resource.title)}
                      />
                    </span>
                  )}
                  {isRunning && draft.text.trim() && (
                    <Button
                      variant="ghost"
                      className="composer-queue-button"
                      aria-label="Queue message"
                      title="Queue this message for the running response (Enter)"
                      disabled={
                        draft.text.length > 16000 ||
                        busy ||
                        Boolean(pendingSteering) ||
                        Boolean(pendingSubmit) ||
                        Boolean(pendingResume)
                      }
                      onClick={() => void action('conversation.steer')}
                    >
                      <ListPlus size={16} aria-hidden />
                      <span>Queue</span>
                    </Button>
                  )}
                  {!singleLine && needsModel && setupModelButton}
                  <span className="composer-primary-slot">
                    {/* One button morphs between Send and Stop, so focus and
                      position hold while a response starts and ends. */}
                    <Button
                      type={running ? 'button' : 'submit'}
                      variant={running ? 'secondary' : 'primary'}
                      iconOnly
                      className={`composer-primary ${running ? 'composer-stop' : 'composer-send'}`}
                      data-state={running ? 'stop' : 'send'}
                      aria-label={running ? 'Stop' : 'Send'}
                      title={running ? 'Stop' : undefined}
                      aria-describedby={
                        !running && composerStateReason
                          ? 'message-composer-state'
                          : undefined
                      }
                      disabled={
                        running
                          ? !generation.can_stop
                          : sendBlocked ||
                            !draft.text.trim() ||
                            state.status !== 'ready' ||
                            !sendActionReady
                      }
                      onClick={
                        running
                          ? (event) => {
                              event.preventDefault();
                              void action('conversation.stop');
                            }
                          : undefined
                      }
                    >
                      <ArrowUp
                        className="composer-primary-send"
                        size={18}
                        aria-hidden
                      />
                      <Square
                        className="composer-primary-stop"
                        size={14}
                        aria-hidden
                      />
                    </Button>
                  </span>
                </div>
              </div>
            </div>
            {composerStateReason && (
              <p
                id="message-composer-state"
                className="composer-state-reason visually-hidden"
                role="status"
              >
                {composerStateReason}
              </p>
            )}
            <div
              className="composer-status-row"
              data-visible={
                state.draftStatus === 'conflict' ||
                state.draftStatus === 'failed'
                  ? 'true'
                  : undefined
              }
            >
              {/* Saving is silent; only a failure or conflict is shown. */}
              <small
                role="status"
                className={`draft-status${
                  state.draftStatus === 'conflict' ||
                  state.draftStatus === 'failed'
                    ? ''
                    : ' visually-hidden'
                }`}
              >
                {state.draftStatus === 'saving'
                  ? 'Saving draft…'
                  : state.draftStatus === 'conflict'
                    ? 'Draft changed in another client. Your local text is preserved.'
                    : state.draftStatus === 'failed'
                      ? 'Draft could not be saved. Keep this page open and reconnect.'
                      : 'Draft saved'}
              </small>
              {state.draftStatus === 'conflict' && (
                <Button
                  variant="ghost"
                  onClick={() =>
                    overlay.open({
                      title: 'Resolve draft conflict',
                      description:
                        'Choose which draft to keep. Messages are unchanged.',
                      content: <DraftConflict id={id} />,
                    })
                  }
                >
                  Resolve draft conflict
                </Button>
              )}
              {state.draftStatus === 'failed' && (
                <Button
                  variant="ghost"
                  onClick={() => void controller.retryDraft(id)}
                >
                  Retry saving draft
                </Button>
              )}
            </div>
          </form>
        )}
        {(hosted || sheetHosted) &&
          contextRail &&
          createPortal(contextRail, contextHost!.element)}
        {hosted ? (
          <ContextSlot
            active={cardActive}
            className={`context-card ${floatingContext ? 'context-card-floating' : 'context-card-docked'}`}
          />
        ) : (
          !compactContext && contextRail
        )}
      </div>
    </CardActionsContext.Provider>
  );
}
