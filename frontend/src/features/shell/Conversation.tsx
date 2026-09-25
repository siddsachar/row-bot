import {
  memo,
  useEffect,
  useEffectEvent,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { useNavigate } from 'react-router-dom';
import type {
  ApprovalView,
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
import {
  Button,
  CompactAction,
  EmptyState,
  Skeleton,
} from '../../ui/primitives';
import ResourceSetup from './ResourceSetup';
import { MediaPreview as Media } from './MediaPreview';
export { MediaPreview as Media } from './MediaPreview';
import ComposerControls from './ComposerControls';
import VoiceControls from './VoiceControls';
import ConversationVoice from './ConversationVoice';
import ResourceTargets from './ResourceTargets';
import { ArrowUp, Check, Copy, Paperclip, Square } from 'lucide-react';
import SearchConversations from './SearchConversations';
import SteeringQueue from './SteeringQueue';
import ConversationActions from '../settings/ConversationActions';
import DraftConflict from './DraftConflict';
import QueueControls from './QueueControls';
import ContextUsage from './ContextUsage';
import DelegatedActivity from './DelegatedActivity';
import ConversationContextRail from './ConversationContextRail';
import {
  commandReceipts,
  ReceiptStorageError,
  type PendingCommand,
} from './command-receipts';
import SafeMarkdown from './chat-parity-markdown';
import TranscriptTrace from './TranscriptTrace';
import { publicBlockText, TranscriptBlocks } from './TranscriptBlocks';
import SlashPalette, { type SlashPaletteHandle } from './SlashPalette';
import { ComposerSkillChips } from './ComposerSkills';
import { EXAMPLE_LABELS, EXAMPLE_PROMPTS } from './welcome-prompts';

const EMPTY_ROWS: readonly TranscriptRow[] = [];

const Message = memo(function Message({
  row,
  conversationId,
}: {
  row: TranscriptRow;
  conversationId: string | null;
}) {
  const { controller, platform } = useRuntime();
  const [expanded, setExpanded] = useState('');
  const [cursor, setCursor] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const [previous, setPrevious] = useState<Array<string | undefined>>([]);
  const pageStart = useRef<string | undefined>(undefined);
  const author =
    row.role === 'user'
      ? 'You'
      : row.role === 'assistant'
        ? 'Row-Bot'
        : 'Tool result';
  const visibleText =
    expanded || row.blocks.map(publicBlockText).filter(Boolean).join('\n');
  async function copy() {
    try {
      const result = await platform.writeClipboard(visibleText);
      if (result.status !== 'ok') {
        setCopied(false);
        setError('Copy is unavailable in this browser.');
        return;
      }
      setCopied(true);
      setError('');
    } catch {
      setCopied(false);
      setError('The visible message could not be copied.');
    }
  }
  async function copyCode(value: string) {
    try {
      return (await platform.writeClipboard(value)).status === 'ok';
    } catch {
      return false;
    }
  }
  async function more() {
    const identity = conversationId;
    if (!identity || !row.content_ref) return;
    setBusy(true);
    try {
      const page = await controller.messageText(
        identity,
        row.content_ref,
        cursor,
      );
      if (controller.getSnapshot().selectedConversationId !== identity) return;
      if (expanded)
        setPrevious((pages) => [...pages, pageStart.current].slice(-100));
      pageStart.current = cursor;
      setExpanded(
        new TextDecoder().decode(
          Uint8Array.from(atob(page.data), (c) => c.charCodeAt(0)),
        ),
      );
      setCursor(page.next_cursor ?? undefined);
      setError('');
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <article
      className={`message message-${row.role}`}
      aria-label={`${author} message`}
      data-message-id={row.message_id ?? row.id}
      data-row-id={row.id}
      tabIndex={-1}
    >
      <div className="transcript-content">
        <header className="message-meta">
          <strong className="message-role">{author}</strong>
          {!!row.tool_call_ids?.length && (
            <small className="message-tool-count">
              {row.tool_call_ids.length} tool{' '}
              {row.tool_call_ids.length === 1 ? 'call' : 'calls'}
            </small>
          )}
          {row.content_status && row.content_status !== 'inline' && (
            <small className="message-delivery-state">
              {row.content_status === 'lazy'
                ? 'Paged content'
                : 'Oversized content'}
            </small>
          )}
          <CompactAction
            className="message-copy-action"
            label={copied ? 'Copied message' : 'Copy message'}
            onClick={() => void copy()}
          >
            {copied ? (
              <Check aria-hidden="true" />
            ) : (
              <Copy aria-hidden="true" />
            )}
          </CompactAction>
        </header>
        <div className="message-text">
          {expanded ? (
            <SafeMarkdown text={expanded} copyText={copyCode} />
          ) : (
            <TranscriptBlocks blocks={row.blocks} copyText={copyCode} />
          )}
        </div>
        {!!row.traces?.length && conversationId && (
          <TranscriptTrace conversation={conversationId} groups={row.traces} />
        )}
        {((row.content_status === 'lazy' && (!expanded || cursor)) ||
          previous.length > 0) && (
          <div className="message-actions">
            {row.content_status === 'lazy' && (!expanded || cursor) && (
              <Button onClick={() => void more()} disabled={busy}>
                {cursor ? 'Load next content page' : 'Load message content'}
              </Button>
            )}
            {!!previous.length && (
              <Button
                onClick={() => {
                  const start = previous.at(-1);
                  setPrevious((pages) => pages.slice(0, -1));
                  setExpanded('');
                  setCursor(start);
                  setCopied(false);
                }}
              >
                Previous message portion
              </Button>
            )}
          </div>
        )}
        {copied && <small role="status">Visible message copied.</small>}
        {error && <p role="alert">{error}</p>}
      </div>
    </article>
  );
});

function ApprovalDetails({ view }: { view: ApprovalView }) {
  return (
    <div className="approval-detail stack">
      <p>{view.reason || view.summary || 'Approval review required.'}</p>
      <dl>
        <dt>Action</dt>
        <dd>{view.action_label}</dd>
        <dt>Risk</dt>
        <dd>{view.risk_class}</dd>
        <dt>Scope</dt>
        <dd>{view.scope || 'Only this requested action.'}</dd>
        {view.safe_argument_summary && (
          <>
            <dt>Reviewed input</dt>
            <dd>
              <code>{view.safe_argument_summary}</code>
            </dd>
          </>
        )}
        <dt>Expiry</dt>
        <dd>{view.expires_at || 'No server expiry supplied'}</dd>
      </dl>
      <small>
        Request {view.id} · policy revision {view.policy_revision}
      </small>
    </div>
  );
}

function ApprovalBar({
  id,
  hint,
}: {
  id: string;
  hint?: { action_label?: string; reason?: string; risk_class?: string };
}) {
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const [view, setView] = useState<ApprovalView | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [resolution, setResolution] = useState('');
  useEffect(() => {
    const abort = new AbortController();
    void controller
      .approval(id, abort.signal)
      .then(setView)
      .catch((error) => {
        if (!abort.signal.aborted) setError(clientError(error).message);
      });
    return () => abort.abort();
  }, [controller, id]);
  async function resolve(decision: 'approve' | 'reject') {
    if (!view || busy) return;
    setBusy(true);
    try {
      await controller.intent(
        id,
        'approval.resolve',
        { decision, nonce: view.nonce },
        view.revision,
      );
      setResolution(
        decision === 'approve' ? 'Approval submitted.' : 'Rejection submitted.',
      );
    } catch (cause) {
      setError(clientError(cause).message);
      setBusy(false);
    }
  }
  return (
    <aside
      className="approval-bar"
      aria-label={`Approval required for ${view?.action_label || hint?.action_label || 'requested action'}`}
      data-risk={view?.risk_class || hint?.risk_class || 'unknown'}
    >
      {view ? (
        <>
          <div className="approval-bar-context">
            <strong>{view.action_label}</strong>
            <span>{view.reason || view.summary}</span>
            <small>
              Risk: {view.risk_class} · {view.scope}
            </small>
          </div>
          <div className="approval-bar-actions">
            <Button
              variant="danger"
              disabled={busy || Boolean(resolution)}
              onClick={() => void resolve('reject')}
            >
              Reject
            </Button>
            <Button
              disabled={busy || Boolean(resolution)}
              onClick={() =>
                overlay.open({
                  title: `Approval details · ${view.action_label}`,
                  description: 'Review bounded public context for this action.',
                  content: <ApprovalDetails view={view} />,
                })
              }
            >
              Details
            </Button>
            <Button
              variant="primary"
              disabled={busy || Boolean(resolution)}
              onClick={() => void resolve('approve')}
            >
              Approve
            </Button>
          </div>
        </>
      ) : (
        <div className="approval-bar-context">
          <strong>{hint?.action_label || 'Requested action'}</strong>
          <span>{hint?.reason || 'Loading approval context…'}</span>
          <Skeleton label="Loading current approval" />
        </div>
      )}
      {resolution && <p role="status">{resolution}</p>}
      {error && <p role="alert">{error}</p>}
    </aside>
  );
}

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
}) {
  const state = useClientState();
  const { controller, platform, conversationActionsOwner } = useRuntime();
  const overlay = useOverlay();
  const navigate = useNavigate();
  const id = state.selectedConversationId;
  const historyReady =
    Boolean(id) && !state.loadingConversation && state.conversation?.id === id;
  const draft = controller.getDraft(id ?? 'new');
  const voiceScope = controller.dictationScope();
  const [talkBusy, setTalkBusy] = useState(false);
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
  const [composerCursor, setComposerCursor] = useState(0);
  const [composerSnapshot, setComposerSnapshot] =
    useState<ConversationComposer | null>(null);
  const [composerBusy, setComposerBusy] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  useLayoutEffect(() => {
    const composer = composerRef.current;
    if (!composer) return;
    composer.style.height = 'auto';
    composer.style.height = `${Math.min(144, Math.max(54, composer.scrollHeight))}px`;
  }, [draft.text, id]);
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
  const visibleRows = rows.filter(
    (row) => row.role !== 'tool' || !row.trace_parent_id,
  );
  const hasLiveTranscript =
    visibleRows.length > 0 || Boolean(pending) || Boolean(running);
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
  function send(example?: string) {
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
    const targets: WriteTarget[] = (example ? [] : resources)
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
        <header className="conversation-heading">
          <div className="conversation-title-block">
            <span className="eyebrow">Conversation</span>
            <h1 title={state.conversation?.title || 'Start a conversation'}>
              {state.conversation?.title || 'Start a conversation'}
            </h1>
          </div>
          <div
            className="button-row conversation-actions"
            role="group"
            aria-label="Conversation actions"
          >
            {missingReceipt &&
              (missingReceipt.key === steeringKey ||
                missingReceipt.key === submitKey ||
                missingReceipt.key === resumeKey) && (
                <Button disabled={busy} onClick={reviewMissingReceipt}>
                  Check pending receipt
                </Button>
              )}
            {id && compactContext && (
              <Button
                disabled={!contextReady}
                onClick={() =>
                  overlay.open({
                    kind: 'sheet',
                    key: 'conversation-context',
                    title: 'Conversation context',
                    description: '',
                    content: contextRail,
                  })
                }
              >
                Context
              </Button>
            )}
          </div>
        </header>
        <div
          className="history-controls"
          role="group"
          aria-label="Conversation history"
        >
          <Button
            disabled={!historyReady}
            onClick={() =>
              void controller
                .showHistory()
                .catch((e) => setError(clientError(e).message))
            }
          >
            Browse history
          </Button>
          {state.history?.previous_cursor && (
            <Button
              disabled={!historyReady}
              onClick={() =>
                void controller
                  .showHistory(undefined, state.history!.previous_cursor!)
                  .catch((e) => setError(clientError(e).message))
              }
            >
              Earlier messages
            </Button>
          )}
          {state.history?.next_cursor && (
            <Button
              disabled={!historyReady}
              onClick={() =>
                void controller
                  .showHistory(undefined, state.history!.next_cursor!)
                  .catch((e) => setError(clientError(e).message))
              }
            >
              Later messages
            </Button>
          )}
          {(state.history || showLatest) && (
            <Button
              onClick={() => {
                followingLatest.current = true;
                setShowLatest(false);
                if (state.history) controller.showLatest();
                else scrollToLatest();
              }}
            >
              Latest messages
            </Button>
          )}
        </div>
        <div
          role="log"
          aria-label="Conversation"
          aria-live="polite"
          aria-relevant="additions"
          className="transcript"
          ref={transcriptRef}
          tabIndex={0}
          onScroll={(event) => {
            if (state.history || state.loadingConversation) return;
            const transcript = event.currentTarget;
            const following =
              transcript.scrollHeight -
                transcript.clientHeight -
                transcript.scrollTop <=
              24;
            followingLatest.current = following;
            setShowLatest(!following);
          }}
        >
          <div
            className="transcript-content"
            ref={transcriptContentRef}
            style={{ display: 'flow-root' }}
          >
            {state.loadingConversation ? (
              <Skeleton label="Opening conversation" />
            ) : visibleRows.length ? (
              visibleRows.map((row) => (
                <Message
                  key={`${id}:${row.id}`}
                  row={row}
                  conversationId={id}
                />
              ))
            ) : (
              <EmptyState
                title={
                  id
                    ? 'What would you like to work on?'
                    : 'A place for your ideas'
                }
                action={
                  id && (
                    <div className="stack" aria-label="Example prompts">
                      {EXAMPLE_PROMPTS.slice(0, 3).map((prompt, index) => (
                        <Button
                          key={prompt}
                          disabled={
                            !sendActionReady ||
                            busy ||
                            !!pendingSubmit ||
                            !!pendingResume ||
                            !!pendingSteering
                          }
                          onClick={() => send(prompt)}
                        >
                          {EXAMPLE_LABELS[index]}
                        </Button>
                      ))}
                      <details>
                        <summary>More ideas</summary>
                        <div className="stack">
                          {EXAMPLE_PROMPTS.slice(3).map((prompt, index) => (
                            <Button
                              key={prompt}
                              disabled={
                                !sendActionReady ||
                                busy ||
                                !!pendingSubmit ||
                                !!pendingResume ||
                                !!pendingSteering
                              }
                              onClick={() => send(prompt)}
                            >
                              {EXAMPLE_LABELS[index + 3]}
                            </Button>
                          ))}
                        </div>
                      </details>
                    </div>
                  )
                }
              >
                <span className="desktop-empty-hint">
                  Chat, reason, browse, use tools, and work with your local
                  knowledge, workflows, and designs. Settings can be finished
                  anytime.
                </span>
                <span className="mobile-empty-hint">
                  Start with a message. Add a code folder or design anytime.
                </span>
              </EmptyState>
            )}
            {thinkingActive && (
              <details className="trace-group thinking-stub">
                <summary>Thinking</summary>
                <p>Row-Bot is working. Private reasoning is not shown.</p>
              </details>
            )}
            {id && liveTraceGroups.length > 0 && (
              <TranscriptTrace conversation={id} groups={liveTraceGroups} />
            )}
            {generation?.approval_id &&
              generation.status === 'waiting_approval' && (
                <ApprovalBar
                  id={generation.approval_id}
                  hint={
                    approvalEvent?.event.type === 'approval.required'
                      ? approvalEvent.event.payload
                      : undefined
                  }
                />
              )}
            {pending?.conversation === id &&
              !rows.some((row) => row.message_id === pending.id) && (
                <article
                  className="message message-user"
                  aria-label="You message awaiting confirmation"
                  data-message-id={pending.id}
                >
                  <header className="message-meta">
                    <strong className="message-role">You</strong>
                    <small className="message-delivery-state">
                      Awaiting confirmation
                    </small>
                  </header>
                  <div className="message-text">{pending.text}</div>
                </article>
              )}
          </div>
        </div>
        {id && (
          <details
            className="activity steering-activity"
            onToggle={(event) => setSteeringOpen(event.currentTarget.open)}
          >
            <summary>Steering queue</summary>
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
        {!!state.activity.filter(
          (record) => record.event.type !== 'tool.activity',
        ).length && (
          <details className="activity activity-feed">
            <summary>
              Activity (
              {
                state.activity.filter(
                  (record) => record.event.type !== 'tool.activity',
                ).length
              }
              )
            </summary>
            <ol className="activity-list">
              {state.activity
                .filter((record) => record.event.type !== 'tool.activity')
                .map((record) => (
                  <li className="activity-item" key={record.event.event_id}>
                    {record.event.type === 'media.available' ? (
                      <Media
                        reference={record.event.payload.media_ref}
                        mime={record.event.payload.mime_type}
                      />
                    ) : record.event.type === 'agent.activity' ? (
                      <details>
                        <summary>
                          Delegated task: {record.event.payload.status}
                        </summary>
                        <p>Task reference: {record.event.payload.run_id}</p>
                      </details>
                    ) : record.event.type === 'generation.error' ? (
                      'The response was interrupted.'
                    ) : record.event.type === 'queue.updated' ? (
                      `${record.event.payload.submission_ids.length} queued submissions`
                    ) : (
                      record.event.type.replaceAll('.', ' ')
                    )}
                  </li>
                ))}
            </ol>
          </details>
        )}
        {generation && (
          <p role="status" className="run-status">
            {generation.status === 'stopping'
              ? 'Stopping — waiting for the worker to finish.'
              : generation.quiesced
                ? `Work ${generation.status}.`
                : generation.status.replaceAll('_', ' ')}
            {generation.external_outcome === 'uncertain'
              ? ' An external action may have completed; review before retrying.'
              : ''}
          </p>
        )}
        {error && (
          <div role="alert" className="chat-error">
            {error}
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
          {!!resources.length && (
            <ResourceTargets
              resources={resources}
              selected={targetSelection[id] ?? defaultTargetIds}
              onChange={(kind, bindingId) =>
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
                }))
              }
            />
          )}
          {!!draft.attachments.length && (
            <ul className="composer-attachments" aria-label="Attachments">
              {draft.attachments.map((a) => (
                <li key={a.attachment_ref} className="attachment-chip">
                  <span>{a.name}</span>
                  <Button
                    aria-label={`Remove ${a.name}`}
                    onClick={() =>
                      controller.setDraft(id, {
                        ...draft,
                        attachments: draft.attachments.filter(
                          (item) => item.attachment_ref !== a.attachment_ref,
                        ),
                      })
                    }
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {composerSnapshot && (
            <ComposerSkillChips
              composer={composerSnapshot}
              disabled={Boolean(running) || busy || composerBusy}
              action={skillAction}
            />
          )}
          <label className="sr-only" htmlFor="message-composer">
            Message
          </label>
          <textarea
            ref={composerRef}
            id="message-composer"
            className="input message-composer"
            value={draft.text}
            maxLength={200000}
            placeholder="Message Row-Bot…"
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
                slashPaletteRef.current?.key(e)
              ) {
                e.preventDefault();
                return;
              }
              if (
                e.key === 'Enter' &&
                !e.shiftKey &&
                !e.nativeEvent.isComposing &&
                !running
              ) {
                e.preventDefault();
                send();
              }
            }}
          />
          {composerSnapshot && (
            <SlashPalette
              ref={slashPaletteRef}
              text={draft.text}
              cursor={composerCursor}
              commands={composerSnapshot.commands}
              disabled={Boolean(running) || busy || composerBusy}
              onChoose={(command, token) => void chooseSlash(command, token)}
            />
          )}
          <div className="composer-toolbar">
            <ComposerControls
              key={id}
              composer={composerSnapshot ?? undefined}
              onSkillAction={skillAction}
              skillsOpen={skillsOpen}
              onSkillsOpenChange={setSkillsOpen}
              disabled={Boolean(running) || busy || talkBusy || composerBusy}
              onError={setError}
            />
            <div className="composer-actions">
              {voiceScope && (
                <ConversationVoice
                  key={`talk:${voiceScope.clientSessionId}:${voiceScope.serverEpoch}:${voiceScope.conversationId}:${voiceScope.selectionKey}`}
                  compact
                  controller={controller}
                  scope={voiceScope}
                  available={
                    voiceExposure.key === voiceHostKey &&
                    (voiceExposure.talk || voiceExposure.realtime)
                  }
                  unavailableReason={voiceExposure.talkReason}
                  disabled={busy}
                  running={Boolean(running)}
                  onBusy={setTalkBusy}
                  context={
                    controls?.model_selection && state.conversation
                      ? {
                          conversation_revision: state.conversation.revision,
                          model_selection: controls.model_selection,
                          write_targets: resources
                            .filter((resource) =>
                              (
                                targetSelection[id ?? ''] ?? defaultTargetIds
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
                      (targetSelection[id ?? ''] ?? defaultTargetIds).includes(
                        resource.binding.binding_id,
                      ),
                    )
                    .map((resource) => resource.title)}
                />
              )}
              {voiceScope && (
                <VoiceControls
                  compact
                  scope={voiceScope}
                  available={
                    voiceExposure.key === voiceHostKey && voiceExposure.dictate
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
              )}
              <Button
                variant="ghost"
                iconOnly
                aria-label="Attach file"
                disabled={busy}
                onClick={() => void attach()}
              >
                <Paperclip size={18} aria-hidden />
              </Button>
              {pendingSubmit && (
                <Button disabled={busy} onClick={() => void recover()}>
                  Check request receipt
                </Button>
              )}
              {pendingResume && (
                <Button disabled={busy} onClick={() => void resume(true)}>
                  Check resume receipt
                </Button>
              )}
              {pendingSteering && (
                <Button disabled={busy} onClick={() => void queueMessage()}>
                  Check queued message
                </Button>
              )}
              {running && (
                <Button
                  disabled={
                    !draft.text.trim() ||
                    draft.text.length > 16000 ||
                    busy ||
                    Boolean(pendingSteering) ||
                    Boolean(pendingSubmit) ||
                    Boolean(pendingResume)
                  }
                  onClick={() => void action('conversation.steer')}
                >
                  Queue message
                </Button>
              )}
              <span className="composer-primary-slot">
                {running ? (
                  <Button
                    variant="danger"
                    iconOnly
                    aria-label="Stop"
                    title="Stop"
                    disabled={!generation.can_stop}
                    onClick={() => void action('conversation.stop')}
                  >
                    <Square size={16} aria-hidden />
                  </Button>
                ) : (
                  <Button
                    type="submit"
                    variant="primary"
                    iconOnly
                    aria-label="Send"
                    aria-describedby={
                      composerStateReason ? 'message-composer-state' : undefined
                    }
                    disabled={
                      busy ||
                      Boolean(pendingSteering) ||
                      Boolean(pendingSubmit) ||
                      Boolean(pendingResume) ||
                      !draft.text.trim() ||
                      state.status !== 'ready' ||
                      !state.workspace?.actions.find((a) => a.action === 'send')
                        ?.ready
                    }
                  >
                    <ArrowUp size={20} aria-hidden />
                  </Button>
                )}
              </span>
              {!running && generation?.status === 'interrupted' && (
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
            </div>
          </div>
          {composerStateReason && (
            <p
              id="message-composer-state"
              className="composer-state-reason"
              role="status"
            >
              {composerStateReason}
            </p>
          )}
          <div className="composer-status-row">
            <small role="status" className="draft-status">
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
              <Button onClick={() => void controller.retryDraft(id)}>
                Retry saving draft
              </Button>
            )}
          </div>
          <ContextUsage usage={state.workspace?.context_usage} />
        </form>
      )}
      {!compactContext && contextRail}
    </div>
  );
}
