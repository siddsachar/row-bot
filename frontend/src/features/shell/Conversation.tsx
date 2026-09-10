import { memo, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type {
  ApprovalView,
  PanelDescriptor,
  ResourceView,
  TranscriptRow,
  WriteTarget,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useClientState, useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import {
  Button,
  EmptyState,
  Field,
  Input,
  Menu,
  Skeleton,
} from '../../ui/primitives';
import ResourceSetup from './ResourceSetup';
import ComposerControls from './ComposerControls';
import ResourceTargets from './ResourceTargets';
import { Paperclip, ArrowUp } from 'lucide-react';
import SearchConversations from './SearchConversations';
import SteeringQueue from './SteeringQueue';
import DraftConflict from './DraftConflict';
import QueueControls from './QueueControls';
import ContextUsage from './ContextUsage';
import DelegatedActivity from './DelegatedActivity';
import {
  commandReceipts,
  ReceiptStorageError,
  type PendingCommand,
} from './command-receipts';

const EMPTY_ROWS: readonly TranscriptRow[] = [];

const FormattedText = memo(function FormattedText({ text }: { text: string }) {
  const parts = text.split(/(```[^\n]*\n[\s\S]*?(?:```|$))/g);
  return (
    <>
      {parts.map((part, index) =>
        part.startsWith('```') ? (
          <pre className="code-sample" key={index}>
            <code>{part.replace(/^```[^\n]*\n/, '').replace(/```$/, '')}</code>
          </pre>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </>
  );
});

const Message = memo(function Message({
  row,
  conversationId,
}: {
  row: TranscriptRow;
  conversationId: string | null;
}) {
  const { controller } = useRuntime();
  const [expanded, setExpanded] = useState('');
  const [cursor, setCursor] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [previous, setPrevious] = useState<Array<string | undefined>>([]);
  const pageStart = useRef<string | undefined>(undefined);
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
      data-message-id={row.message_id ?? row.id}
      data-row-id={row.id}
      tabIndex={-1}
    >
      <strong className="message-role">
        {row.role === 'user'
          ? 'You'
          : row.role === 'assistant'
            ? 'Row-Bot'
            : 'Tool result'}
      </strong>
      <div className="message-text">
        <FormattedText
          text={expanded || row.blocks.map((block) => block.text).join('\n')}
        />
      </div>
      {!!row.tool_call_ids?.length && (
        <small>
          {row.tool_call_ids.length} tool{' '}
          {row.tool_call_ids.length === 1 ? 'call' : 'calls'}
        </small>
      )}
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
          }}
        >
          Previous message portion
        </Button>
      )}
      {error && <p role="alert">{error}</p>}
    </article>
  );
});

function Approval({ id }: { id: string }) {
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const [view, setView] = useState<ApprovalView | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
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
      overlay.close();
    } catch (cause) {
      setError(clientError(cause).message);
      setBusy(false);
    }
  }
  return (
    <div className="stack">
      {view ? (
        <>
          <p>
            {view.summary || 'Review the pending action before continuing.'}
          </p>
          <small>
            Request {view.id} · policy revision {view.policy_revision}
          </small>
          <div className="button-row">
            <Button
              variant="danger"
              disabled={busy}
              onClick={() => void resolve('reject')}
            >
              Reject action
            </Button>
            <Button
              variant="primary"
              disabled={busy}
              onClick={() => void resolve('approve')}
            >
              Approve action
            </Button>
          </div>
        </>
      ) : (
        <Skeleton label="Loading current approval" />
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}

export function Media({
  reference,
  mime,
}: {
  reference: string;
  mime: string;
}) {
  const { controller } = useRuntime();
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    let owned = '';
    setUrl('');
    setError('');
    void controller
      .download(reference, abort.signal)
      .then((blob) => {
        if (abort.signal.aborted) return;
        owned = URL.createObjectURL(blob);
        setUrl(owned);
      })
      .catch((cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      });
    return () => {
      abort.abort();
      if (owned) URL.revokeObjectURL(owned);
    };
  }, [controller, reference, attempt]);
  return url ? (
    mime.startsWith('image/') ? (
      <img className="message-media" src={url} alt="Generated result" />
    ) : (
      <a href={url} download="result">
        Download generated result
      </a>
    )
  ) : error ? (
    <div role="alert">
      {error}{' '}
      <Button onClick={() => setAttempt((value) => value + 1)}>
        Retry generated result
      </Button>
    </div>
  ) : (
    <p>Loading generated result…</p>
  );
}

export default function Conversation({
  onPanel,
  focusConversationId,
  onComposerFocused,
}: {
  onPanel: (panel: PanelDescriptor) => void;
  focusConversationId?: string | null;
  onComposerFocused?: () => void;
}) {
  const state = useClientState();
  const { controller, platform } = useRuntime();
  const overlay = useOverlay();
  const navigate = useNavigate();
  const id = state.selectedConversationId;
  const historyReady =
    Boolean(id) && !state.loadingConversation && state.conversation?.id === id;
  const draft = controller.getDraft(id ?? 'new');
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
  const transcriptRef = useRef<HTMLDivElement>(null);
  const transcriptContentRef = useRef<HTMLDivElement>(null);
  const followingLatest = useRef(true);
  const scrollOwner = useRef<string | null>(null);
  const wasHistory = useRef(false);
  const [showLatest, setShowLatest] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const generation = state.projection?.generation;
  const running = generation && !generation.quiesced;
  const controls = state.workspace?.controls;
  const resources = state.workspace?.resources ?? [];
  const rows = (state.history ?? state.projection)?.rows ?? EMPTY_ROWS;
  function scrollToLatest() {
    const transcript = transcriptRef.current;
    if (transcript)
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
    if (!state.history && !state.loadingConversation && followingLatest.current)
      scrollToLatest();
  }, [id, rows, state.history, state.loadingConversation, pending]);
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
  }, [id, state.history, state.loadingConversation]);
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
          setPending(null);
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
  function send() {
    if (
      !id ||
      !draft.text.trim() ||
      pendingSteering ||
      pendingSubmit ||
      pendingResume
    )
      return;
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
    const targets: WriteTarget[] = resources
      .filter((r) => (targetSelection[id] ?? []).includes(r.binding.binding_id))
      .map((r) => ({
        kind: r.binding.kind as 'artifact' | 'workspace',
        binding_id: r.binding.binding_id,
        resource_id: r.binding.resource_id,
        binding_revision: r.binding.revision,
        resource_revision: r.resource_revision,
      }));
    const text = draft.text;
    const selectedVersion = controller.getSelectionVersion();
    if (targets.length)
      overlay.open({
        kind: 'alert',
        title: 'Confirm resource targets',
        description: `This message may change ${resources
          .filter((r) =>
            targets.some((t) => t.binding_id === r.binding.binding_id),
          )
          .map((r) => r.title)
          .join(' and ')}. The selection stays fixed for this request.`,
        confirmLabel: 'Send with these targets',
        onConfirm: () => {
          overlay.close();
          void dispatch(text, targets, draft, selectedVersion);
        },
      });
    else void dispatch(text, [], draft, selectedVersion);
  }
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
      picked = await platform.selectFile();
    if (picked.status !== 'ok' || !('files' in picked.value)) return;
    setBusy(true);
    try {
      for (const file of picked.value.files) {
        const uploaded = await controller.upload(target, file);
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
  }
  function setup() {
    overlay.open({
      title: 'Add resource',
      description:
        'Add a Deck or an existing coding folder to this conversation.',
      content: <ResourceSetup conversationId={id} onPanel={onPanel} />,
    });
  }
  async function recover() {
    if (pendingSubmit) await dispatch();
  }
  return (
    <div className="chat-workspace">
      <header className="conversation-heading">
        <div>
          <span className="eyebrow">Conversation</span>
          <h1>{state.conversation?.title || 'Start a conversation'}</h1>
        </div>
        <div className="button-row">
          {missingReceipt &&
            (missingReceipt.key === steeringKey ||
              missingReceipt.key === submitKey ||
              missingReceipt.key === resumeKey) && (
              <Button disabled={busy} onClick={reviewMissingReceipt}>
                Review pending receipt
              </Button>
            )}
          {id && <Button onClick={setup}>Add resource</Button>}
          {id && (
            <Button
              onClick={() =>
                overlay.open({
                  title: 'Find in conversation',
                  description: 'Search the complete conversation history.',
                  content: <SearchConversations conversationId={id} />,
                })
              }
            >
              Find
            </Button>
          )}
          {id && (
            <Menu
              label="Conversation actions"
              actions={[
                {
                  label: state.conversation?.pinned ? 'Unpin' : 'Pin',
                  onSelect: () => {
                    void controller
                      .intent(
                        id,
                        'conversation.pin',
                        { pinned: !state.conversation?.pinned },
                        state.conversation!.revision,
                      )
                      .catch((e) => setError(clientError(e).message));
                  },
                },
                {
                  label: 'Rename',
                  onSelect: () =>
                    overlay.open({
                      title: 'Rename conversation',
                      description: 'Give this conversation a name.',
                      content: (
                        <Rename
                          id={id}
                          title={state.conversation?.title ?? ''}
                          revision={state.conversation!.revision}
                        />
                      ),
                    }),
                },
                {
                  label: 'Delete conversation',
                  onSelect: () =>
                    overlay.open({
                      kind: 'alert',
                      title: 'Delete conversation?',
                      description:
                        'This removes its history. Bound resources are retained. Running work must stop before deletion completes.',
                      confirmLabel: 'Delete conversation',
                      onConfirm: () => {
                        overlay.close();
                        void controller
                          .intent(
                            id,
                            'conversation.delete',
                            {},
                            state.conversation!.revision,
                          )
                          .then((r) => {
                            if (r.status === 'DeleteCompleted') navigate('/');
                            else
                              setError(
                                'Deletion is waiting for running work to stop. Review and try again.',
                              );
                          })
                          .catch((e) => setError(clientError(e).message));
                      },
                    }),
                },
              ]}
            />
          )}
        </div>
      </header>
      {!!resources.length && (
        <div
          className="resource-chips"
          role="group"
          aria-label="Bound resources"
        >
          {resources.map((resource) => (
            <div key={resource.binding.binding_id} className="resource-chip">
              <Button onClick={() => resourcePanel(resource)}>
                {resource.title}
              </Button>
              <Menu
                label={`Actions for ${resource.title}`}
                actions={[
                  {
                    label: 'Unbind resource',
                    onSelect: () =>
                      overlay.open({
                        kind: 'alert',
                        title: 'Unbind resource?',
                        description:
                          'Remove this relationship. The resource and its original conversation remain saved.',
                        confirmLabel: 'Unbind',
                        onConfirm: () => {
                          overlay.close();
                          void controller
                            .intent(
                              id!,
                              'conversation.unbind',
                              { binding_id: resource.binding.binding_id },
                              state.conversation!.revision,
                            )
                            .catch((e) => setError(clientError(e).message));
                        },
                      }),
                  },
                ]}
              />
            </div>
          ))}
        </div>
      )}
      <div className="history-controls">
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
        <div ref={transcriptContentRef} style={{ display: 'flow-root' }}>
          {state.loadingConversation ? (
            <Skeleton label="Opening conversation" />
          ) : rows.length ? (
            rows.map((row) => (
              <Message key={`${id}:${row.id}`} row={row} conversationId={id} />
            ))
          ) : (
            <EmptyState
              title={
                id
                  ? 'What would you like to work on?'
                  : 'A place for your ideas'
              }
            >
              Start with a message. Resources can be added whenever you need
              them.
            </EmptyState>
          )}
          {pending?.conversation === id &&
            !rows.some((row) => row.message_id === pending.id) && (
              <article
                className="message message-user"
                data-message-id={pending.id}
              >
                <strong>You</strong>
                <div className="message-text">{pending.text}</div>
                <small>Awaiting confirmation</small>
              </article>
            )}
        </div>
      </div>
      {id && (
        <DelegatedActivity
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
      )}
      {id && (
        <details
          className="activity"
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
                  .filter((record) => record.event.type.startsWith('steering.'))
                  .at(-1)?.event.event_id ?? ''
              }
              loadPage={(run, cursor, signal) =>
                controller.steering(id, run, cursor ?? undefined, signal)
              }
            />
          )}
        </details>
      )}
      {!!state.activity.length && (
        <details className="activity">
          <summary>Activity ({state.activity.length})</summary>
          <ol>
            {state.activity.map((record) => (
              <li key={record.event.event_id}>
                {record.event.type === 'tool.activity' ? (
                  `${record.event.payload.state === 'tool_call' ? 'Using' : 'Completed'} ${record.event.payload.tool_name || 'tool'}`
                ) : record.event.type === 'media.available' ? (
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
      {generation?.approval_id && generation.status === 'waiting_approval' && (
        <Button
          variant="primary"
          onClick={() =>
            overlay.open({
              title: 'Approval required',
              description:
                'Review the current server request and its consequences.',
              content: <Approval id={generation.approval_id!} />,
            })
          }
        >
          Review approval
        </Button>
      )}
      {error && (
        <div role="alert" className="chat-error">
          {error}
        </div>
      )}
      {id && (
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          {!!resources.length && (
            <ResourceTargets
              resources={resources}
              selected={targetSelection[id] ?? []}
              onChange={(kind, bindingId) =>
                setTargets((previous) => ({
                  ...previous,
                  [id]: [
                    ...(previous[id] ?? []).filter(
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
          {draft.attachments.map((a) => (
            <div key={a.attachment_ref} className="attachment-chip">
              {a.name}
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
            </div>
          ))}
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
            onChange={(e) =>
              controller.setDraft(id, { ...draft, text: e.target.value })
            }
            onKeyDown={(e) => {
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
          <div className="composer-toolbar">
            <ComposerControls
              key={id}
              disabled={Boolean(running) || busy}
              onError={setError}
            />
            <div className="composer-actions">
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
              {running ? (
                <>
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
                  <Button
                    variant="danger"
                    disabled={!generation.can_stop}
                    onClick={() => void action('conversation.stop')}
                  >
                    Stop
                  </Button>
                </>
              ) : (
                <>
                  <Button
                    type="submit"
                    variant="primary"
                    iconOnly
                    aria-label="Send"
                    disabled={
                      busy ||
                      Boolean(pendingSteering) ||
                      Boolean(pendingSubmit) ||
                      Boolean(pendingResume) ||
                      !draft.text.trim() ||
                      !state.workspace?.actions.find((a) => a.action === 'send')
                        ?.ready
                    }
                  >
                    <ArrowUp size={20} aria-hidden />
                  </Button>
                  {generation?.status === 'interrupted' && (
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
                </>
              )}
            </div>
          </div>
          <small role="status">
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
                  title: 'Review draft conflict',
                  description:
                    'Choose which draft to keep. Messages are unchanged.',
                  content: <DraftConflict id={id} />,
                })
              }
            >
              Review draft conflict
            </Button>
          )}
          {state.draftStatus === 'failed' && (
            <Button onClick={() => void controller.retryDraft(id)}>
              Retry saving draft
            </Button>
          )}
          <ContextUsage usage={state.workspace?.context_usage} />
          {!state.workspace?.actions.find((a) => a.action === 'send')
            ?.ready && (
            <small>
              Choose a configured model to send. You can still create or open
              resources.
            </small>
          )}
        </form>
      )}
    </div>
  );
}

function Rename({
  id,
  title,
  revision,
}: {
  id: string;
  title: string;
  revision: string;
}) {
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const [name, setName] = useState(title);
  const [error, setError] = useState('');
  return (
    <form
      className="stack"
      onSubmit={(e) => {
        e.preventDefault();
        void controller
          .intent(id, 'conversation.rename', { title: name }, revision)
          .then(() => overlay.close())
          .catch((e) => setError(clientError(e).message));
      }}
    >
      <Field label="Conversation name">
        <Input
          value={name}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
          data-initial-focus
        />
      </Field>
      <Button type="submit" disabled={!name.trim()}>
        Save name
      </Button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
