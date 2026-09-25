import {
  useCallback,
  useEffect,
  useEffectEvent,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useNavigate } from 'react-router-dom';
import type {
  ConversationComposer,
  PanelDescriptor,
  ResourceView,
  SlashCommandSpec,
  TranscriptRow,
  TranscriptTraceGroup,
  WriteTarget,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useClientState, useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Button, Menu, Skeleton } from '../../ui/primitives';
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
  ListPlus,
  MoreHorizontal,
  Palette,
  Paperclip,
  Square,
  TriangleAlert,
  X,
} from 'lucide-react';
import SearchConversations from './SearchConversations';
import SteeringQueue from './SteeringQueue';
import ConversationActions from '../settings/ConversationActions';
import DraftConflict from './DraftConflict';
import QueueControls from './QueueControls';
import ContextUsage from './ContextUsage';
import DelegatedActivity, { type DelegatedRead } from './DelegatedActivity';
import ConversationContextRail from './ConversationContextRail';
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
  currentProfileChoice,
  openAgentProfiles,
  profileChoices,
} from './agent-profiles';
import type { ProfileSummary } from '../settings/GoalProfileSettings';
import { ComposerSkillChips } from './ComposerSkills';
import ApprovalCard from './ApprovalCard';
import ChatEmpty from './ChatEmpty';
import ConversationHeader from './ConversationHeader';
import { TranscriptMessage } from './TranscriptMessage';
import { publicBlockText } from './TranscriptBlocks';
import { buildTranscript, liveMedia } from './transcript-model';
import type { RecoveryAction } from './turn-errors';
import { modelRefName, splitModelLabel } from './model-choices';

const EMPTY_ROWS: readonly TranscriptRow[] = [];
const QUEUE_EVENTS = new Set([
  'queue.updated',
  'queue.changed',
  'steering.queued',
  'steering.consumed',
]);

export default function Conversation({
  onPanel,
  completedDesignId,
  onResourceOpened,
  onNewChat = () => undefined,
  focusConversationId,
  onComposerFocused,
  firstPrompt,
  onFirstPromptConsumed,
  compactContext: compactFromViewport = false,
  onStartProfileChat,
}: {
  onPanel: (panel: PanelDescriptor) => void;
  completedDesignId?: string;
  onResourceOpened?: (bindingId: string) => void;
  onNewChat?: () => void;
  focusConversationId?: string | null;
  onComposerFocused?: () => void;
  firstPrompt?: { conversationId: string; text: string } | null;
  onFirstPromptConsumed?: (conversationId: string) => void;
  compactContext?: boolean;
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
  const delegatedRead = useRef<DelegatedRead | null>(null);
  const [recentDelegatedRead] = useState(() => ({
    get: () => delegatedRead.current,
    set: (read: DelegatedRead) => {
      delegatedRead.current = read;
    },
  }));
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
  useEffect(() => {
    if (!state.handshake) return;
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
  }, [controller, voiceHostKey, state.handshake]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
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
  }, [submitKey]);
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
  }, [resumeKey]);
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
  }, [skillKey]);
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
  }, [steeringKey]);
  const pendingSteering =
    steeringClaim?.key === steeringKey ? steeringClaim.claim : null;
  const [steeringOpen, setSteeringOpen] = useState(false);
  const chatContentRef = useRef<HTMLDivElement>(null);
  const chatWorkspaceRef = useRef<HTMLDivElement>(null);
  const [narrowChat, setNarrowChat] = useState(false);
  const compactContext = compactFromViewport || narrowChat;
  useEffect(() => {
    const element = chatWorkspaceRef.current;
    if (!element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => {
      setNarrowChat(entry.contentRect.width < 720);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const transcriptContentRef = useRef<HTMLDivElement>(null);
  const followingLatest = useRef(true);
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
  const [compactToolbar, setCompactToolbar] = useState(false);
  useEffect(() => {
    const composer = toolbarRef.current?.closest('.composer');
    if (!composer || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => {
      setCompactToolbar(entry.contentRect.width < 600);
    });
    observer.observe(composer);
    return () => observer.disconnect();
  }, [id]);
  // The field grows from one line; an empty draft always rests at one line
  // (a wrapped placeholder must not size it), and width changes re-measure.
  const fitComposer = useCallback(() => {
    const composer = composerRef.current;
    if (!composer) return;
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
    ? 'Checking the queued message receipt before another message can be sent.'
    : pendingSubmit
      ? 'Checking the message receipt before another message can be sent.'
      : pendingResume
        ? 'Checking the resume receipt before another action can start.'
        : pendingSkill
          ? 'Checking the Smart Skills receipt before another change can start.'
          : talkBusy
            ? 'Voice controls are finishing before another message can be sent.'
            : busy
              ? 'Finishing the current conversation action.'
              : running
                ? 'A response is in progress. Add text to queue guidance, or stop the run.'
                : state.status !== 'ready'
                  ? 'Reconnect to send. Your draft remains on this device.'
                  : !sendActionReady
                    ? 'Choose a configured model to send. You can still create or open resources.'
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
  }, [controller, draft.text, id, state.status, state.workspace?.composer]);

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
    try {
      claim = commandReceipts.read(scope);
      const checking = Boolean(claim);
      if (!claim) {
        if (!text || !capturedDraft || !controls?.model_selection) return;
        if (resumeKey && commandReceipts.read(resumeKey)) {
          setResumeClaim({
            key: resumeKey,
            claim: commandReceipts.read(resumeKey)!,
          });
          setError(
            'Check the pending Resume receipt before sending another message.',
          );
          return;
        }
        const steering = steeringKey ? commandReceipts.read(steeringKey) : null;
        if (steering) {
          setSteeringClaim({ key: steeringKey, claim: steering });
          setError(
            'Check the pending queued message receipt before sending another message.',
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
              model_selection: controls!.model_selection,
              write_targets: targets,
            },
            state.conversation!.revision,
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
        if (receipt.status === 'rejected')
          setMissingReceipt({ key: scope, commandId: claim.commandId });
        setError(
          'The message outcome is unresolved. Check its receipt before sending again.',
        );
      }
    } catch (cause) {
      if (current()) {
        if (claim && clientError(cause).code === 'not_found')
          setMissingReceipt({ key: scope, commandId: claim.commandId });
        setError(
          cause instanceof ReceiptStorageError
            ? cause.message
            : clientError(cause).message,
        );
      }
    } finally {
      receiptOperation.current = false;
      if (receiptAlive.current) setBusy(false);
    }
  }
  function send(example?: string, keepTargets = false) {
    const outgoing = example
      ? { text: example, attachments: [] as typeof draft.attachments }
      : draft;
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
          'Check the pending queued message receipt before sending another message.',
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
    try {
      claim = commandReceipts.read(scope);
      const checking = Boolean(claim);
      if (!claim) {
        if (checkOnly) return;
        const submission = submitKey ? commandReceipts.read(submitKey) : null;
        const steering = steeringKey ? commandReceipts.read(steeringKey) : null;
        if (submission || steering) {
          if (submission) setUnknown({ key: submitKey, claim: submission });
          if (steering) setSteeringClaim({ key: steeringKey, claim: steering });
          setError('Check the pending message receipt before resuming.');
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
          overlay.notify('Resume receipt confirmed.');
        }
      } else if (current()) {
        if (result.status === 'rejected')
          setMissingReceipt({ key: scope, commandId: claim.commandId });
        setError(
          'The Resume outcome is unresolved. Check its receipt before resuming again.',
        );
      }
    } catch (cause) {
      if (current()) {
        if (claim && clientError(cause).code === 'not_found')
          setMissingReceipt({ key: scope, commandId: claim.commandId });
        setError(
          cause instanceof ReceiptStorageError
            ? cause.message
            : 'The Resume outcome is unresolved. Check its receipt before resuming again.',
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
    try {
      claim = commandReceipts.read(scope);
      const checking = Boolean(claim);
      if (!claim) {
        if (resumeKey && commandReceipts.read(resumeKey)) {
          setResumeClaim({
            key: resumeKey,
            claim: commandReceipts.read(resumeKey)!,
          });
          setError(
            'Check the pending Resume receipt before queuing another message.',
          );
          return;
        }
        const submission = submitKey ? commandReceipts.read(submitKey) : null;
        if (submission) {
          setUnknown({ key: submitKey, claim: submission });
          setError(
            'Check the pending message receipt before queuing another message.',
          );
          return;
        }
        const captured = controller.getDraft(target);
        if (!captured.text.trim() || captured.text.length > 16000) {
          if (current())
            setError(
              'Queued messages must contain between 1 and 16,000 characters.',
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
              ? 'Queued message receipt confirmed. Your current draft is preserved unless it is the original unchanged draft.'
              : 'Message queued.',
          );
        }
      } else if (current()) {
        if (result.status === 'rejected')
          setMissingReceipt({ key: scope, commandId: claim.commandId });
        setError(
          'The queued message outcome is unresolved. Check its receipt before sending it again.',
        );
      }
    } catch (cause) {
      if (current()) {
        if (claim && clientError(cause).code === 'not_found')
          setMissingReceipt({ key: scope, commandId: claim.commandId });
        setError(
          cause instanceof ReceiptStorageError
            ? cause.message
            : 'The queued message outcome is unresolved. Check its receipt before sending it again.',
        );
      }
    } finally {
      receiptOperation.current = false;
      if (receiptAlive.current) setBusy(false);
    }
  }
  function reviewMissingReceipt() {
    if (!missingReceipt) return;
    const saved = missingReceipt,
      selection = controller.getSelectionVersion(),
      instance = state.handshake?.instance_id;
    overlay.open({
      kind: 'alert',
      title: 'Clear the pending receipt?',
      description:
        'The receipt was rejected or could not be found. An unavailable action may still finish. Clear this record only after reviewing the conversation; sending again could create a duplicate. This does not resend an action.',
      confirmLabel: 'Clear pending receipt',
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
    setBusy(true);
    try {
      const uploadedAttachments =
        'files' in picked.value
          ? await Promise.all(
              picked.value.files.map((file) => controller.upload(target, file)),
            )
          : picked.value.kind === 'file'
            ? [await controller.attachmentMetadata(picked.value.reference)]
            : [];
      for (const uploaded of uploadedAttachments) {
        const previous = controller.getDraft(target);
        controller.setDraft(target, {
          ...previous,
          attachments: [...previous.attachments, uploaded],
        });
      }
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
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
  function setup() {
    overlay.open({
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
      resources={resources}
      suggestions={(state.suggestions ?? []).filter(
        (suggestion) => suggestion.conversation_id === id,
      )}
      ready={contextReady}
      connectionStatus={state.status}
      terminalAvailable={terminalAvailable}
      compactHeading={compactContext}
      agents={delegatedActivity}
      agentsEmpty={agentsEmpty}
      outputs={outputs}
      completedDesignId={completedDesignId}
      writerQueued={writerQueued}
      onCancelWait={() => void action('conversation.stop')}
      onUseOutputInCode={useOutputInCode}
      onAddResource={setup}
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
  const lastUserText = useMemo(() => {
    for (let index = items.length - 1; index >= 0; index -= 1)
      if (items[index].row.role === 'user')
        return items[index].row.blocks
          .map(publicBlockText)
          .filter(Boolean)
          .join('\n');
    return '';
  }, [items]);
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
  const queueSeen =
    Boolean(pendingSteering) ||
    steeringOpen ||
    state.activity.some((record) => QUEUE_EVENTS.has(record.event.type));
  const queueEvent = [...state.activity]
    .reverse()
    .find(
      (record) =>
        record.event.type === 'queue.updated' ||
        record.event.type === 'queue.changed',
    )?.event;
  const queueCount =
    queueEvent?.type === 'queue.updated' || queueEvent?.type === 'queue.changed'
      ? queueEvent.payload.submission_ids.length
      : 0;
  // A stopped run whose transcript ends on the person's own message.
  const unanswered =
    !turnInFlight &&
    !state.history &&
    generation?.status === 'stopped' &&
    items.at(-1)?.row.role === 'user' &&
    Boolean(lastUserText) &&
    pending?.conversation !== id;
  // The queue stays out of the way once it has drained and the run is over.
  const queueVisible =
    queueSeen &&
    (queueCount > 0 ||
      Boolean(pendingSteering) ||
      steeringOpen ||
      turnInFlight);
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
      if (lastUserText) send(lastUserText, true);
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
      description: 'Use this agent profile in this chat',
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
  }, [controller]);
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
      title: 'Conversation context',
      description: '',
      content: contextRail,
    });
  }
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
          onContext={id && compactContext ? openContextSheet : undefined}
          contextDisabled={!contextReady}
        >
          {missingReceipt &&
            (missingReceipt.key === steeringKey ||
              missingReceipt.key === submitKey ||
              missingReceipt.key === resumeKey) && (
              <Button disabled={busy} onClick={reviewMissingReceipt}>
                Check pending receipt
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
                disabled={
                  !sendActionReady ||
                  busy ||
                  !!pendingSubmit ||
                  !!pendingResume ||
                  !!pendingSteering
                }
                onSend={(prompt) => send(prompt)}
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
                        waiting: generation?.status === 'waiting_approval',
                        stopping: generation?.status === 'stopping',
                        startedAt: runStartedAt,
                      }}
                    >
                      {generation?.approval_id &&
                        generation.status === 'waiting_approval' && (
                          <ApprovalCard
                            id={generation.approval_id}
                            hint={
                              approvalEvent?.event.type === 'approval.required'
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
                <Square className="turn-notice-icon" aria-hidden />
                <div className="turn-notice-text">
                  <strong>Stopped before a reply</strong>
                  <span>Your last message wasn't answered.</span>
                </div>
                <div className="turn-notice-actions">
                  <Button
                    variant="secondary"
                    disabled={sendBlocked || !sendActionReady}
                    onClick={retryLast}
                  >
                    Send again
                  </Button>
                </div>
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
                      ? ' An external action may have completed; review it before retrying.'
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
          {generation?.external_outcome === 'uncertain'
            ? ' An external action may have completed; review before retrying.'
            : ''}
        </p>
        {error && (
          <div role="alert" className="chat-error">
            <TriangleAlert aria-hidden />
            <span>{error}</span>
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
          aria-label="Message composer"
          aria-busy={busy || talkBusy}
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          {queueVisible && (
            <details
              className="activity steering-activity composer-queue"
              onToggle={(event) => setSteeringOpen(event.currentTarget.open)}
            >
              <summary>
                <ListPlus aria-hidden />
                <span>Steering queue</span>
                {queueCount > 0 && (
                  <span className="composer-queue-count">
                    {queueCount} queued
                  </span>
                )}
              </summary>
              {steeringOpen && (
                <QueueControls
                  conversationId={id}
                  generationId={generation?.generation_id ?? ''}
                  refreshKey={
                    state.activity
                      .filter((record) => record.event.type === 'queue.changed')
                      .at(-1)?.event.event_id ?? ''
                  }
                  loadPage={(run, cursor, signal) =>
                    controller.queue(id, run, cursor ?? undefined, signal)
                  }
                  onAction={async (type, submission, revision, text) => {
                    const current = await controller.workspaceFor(id);
                    await controller.intent(
                      id,
                      `conversation.queue.${type}`,
                      {
                        submission_id: submission,
                        expected_queue_revision: revision,
                        ...(type === 'edit' ? { text } : {}),
                      },
                      current.revision,
                    );
                  }}
                />
              )}
              {steeringOpen && (
                <SteeringQueue
                  conversationId={id}
                  generationId={generation?.generation_id ?? ''}
                  refreshKey={
                    state.activity
                      .filter((record) =>
                        record.event.type.startsWith('steering.'),
                      )
                      .at(-1)?.event.event_id ?? ''
                  }
                  loadPage={(run, cursor, signal) =>
                    controller.steering(id, run, cursor ?? undefined, signal)
                  }
                />
              )}
            </details>
          )}
          <div className="composer-field">
            {(!!resources.length ||
              !!draft.attachments.length ||
              Boolean(
                composerSnapshot &&
                (composerSnapshot.active_skills.length ||
                  composerSnapshot.suggestions.length),
              )) && (
              <div className="composer-chips">
                {!!resources.length && (
                  <ResourceTargets
                    resources={resources}
                    selected={targetSelection[id] ?? defaultTargetIds}
                    onChange={chooseTarget}
                  />
                )}
                {!!draft.attachments.length && (
                  <ul className="composer-attachments" aria-label="Attachments">
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
                {composerSnapshot && (
                  <ComposerSkillChips
                    composer={composerSnapshot}
                    disabled={isRunning || busy || composerBusy}
                    action={skillAction}
                  />
                )}
              </div>
            )}
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
                isRunning ? 'Queue a follow-up…' : 'Message Row-Bot…'
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
                onChoose={(command, token) => void chooseSlash(command, token)}
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
              />
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
                                label: 'Check request receipt',
                                disabled: busy,
                                onSelect: () => void recover(),
                              },
                            ]
                          : []),
                        ...(pendingResume
                          ? [
                              {
                                label: 'Check resume receipt',
                                disabled: busy,
                                onSelect: () => void resume(true),
                              },
                            ]
                          : []),
                        ...(pendingSteering
                          ? [
                              {
                                label: 'Check queued message',
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
                    Check request receipt
                  </Button>
                )}
                {!compactToolbar && pendingResume && (
                  <Button disabled={busy} onClick={() => void resume(true)}>
                    Check resume receipt
                  </Button>
                )}
                {!compactToolbar && pendingSteering && (
                  <Button disabled={busy} onClick={() => void queueMessage()}>
                    Check queued message
                  </Button>
                )}
                <ContextUsage usage={state.workspace?.context_usage} />
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
                                  resource_revision: resource.resource_revision,
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
                {!isRunning &&
                  state.workspace &&
                  state.status === 'ready' &&
                  !sendActionReady && (
                    <Button
                      className="composer-setup-model"
                      onClick={() => navigate('/settings/models')}
                    >
                      Set up a model
                    </Button>
                  )}
                <span className="composer-primary-slot">
                  {running ? (
                    <Button
                      variant="secondary"
                      iconOnly
                      className="composer-stop"
                      aria-label="Stop"
                      title="Stop"
                      disabled={!generation.can_stop}
                      onClick={() => void action('conversation.stop')}
                    >
                      <Square size={14} aria-hidden />
                    </Button>
                  ) : (
                    <Button
                      type="submit"
                      variant="primary"
                      iconOnly
                      className="composer-send"
                      aria-label="Send"
                      aria-describedby={
                        composerStateReason
                          ? 'message-composer-state'
                          : undefined
                      }
                      disabled={
                        sendBlocked ||
                        !draft.text.trim() ||
                        state.status !== 'ready' ||
                        !sendActionReady
                      }
                    >
                      <ArrowUp size={18} aria-hidden />
                    </Button>
                  )}
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
              state.draftStatus === 'conflict' || state.draftStatus === 'failed'
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
      {!compactContext && contextRail}
    </div>
  );
}
