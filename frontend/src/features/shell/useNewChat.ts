import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { clientError } from '../../api/errors';
import { useClientSelector, useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { commandReceipts, ReceiptStorageError } from './command-receipts';
import {
  startVoiceOnOpen,
  takeHomeDraft,
  unusedChat,
  type HomeVoice,
} from './new-chat';
import { primeVoicePlayback } from '../voice/voice-audio';

// Refusals at the door, before any command runs: this window isn't signed in, its address isn't allowed,
// or it has no session.
const REFUSED_UNRUN = new Set([
  'sign_in_required',
  'origin_rejected',
  'authentication_required',
]);

/** Mount once in the persistent shell. Every New chat entry shares this owner. */
export default function useNewChat() {
  const state = { handshake: useClientSelector((value) => value.handshake) };
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const navigate = useNavigate();
  const location = useLocation();
  const routeKey = useRef(location.key);
  useLayoutEffect(() => {
    routeKey.current = location.key;
  }, [location.key]);
  const key = state.handshake
    ? `row-bot.new-chat.${state.handshake.instance_id}`
    : '';
  const [pending, setPending] = useState<string | null>(null);
  const [creatingChat, setCreatingChat] = useState(false);
  const [error, setError] = useState('');
  const [missing, setMissing] = useState<{
    key: string;
    commandId: string;
  } | null>(null);
  const [focusConversationId, setFocusConversationId] = useState<string | null>(
    null,
  );
  const [firstPrompt, setFirstPrompt] = useState<{
    conversationId: string;
    text: string;
  } | null>(null);
  const operation = useRef(false);
  const alive = useRef(true);
  // The chats New chat made here; one never used opens again (one server).
  const started = useRef(new Set<string>());
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    setPending(null);
    setMissing(null);
    setError('');
    setFocusConversationId(null);
    setFirstPrompt(null);
    started.current = new Set();
    if (!key) return;
    try {
      setPending(commandReceipts.read(key)?.commandId ?? null);
    } catch (cause) {
      setError(
        cause instanceof ReceiptStorageError
          ? cause.message
          : clientError(cause).message,
      );
    }
  }, [key]);

  /**
   * Starts a chat. `firstMessage` is sent once the chat is ready unless
   * `send` is false, when it only waits in the composer as a draft.
   * `incognito` starts one that nothing is read from for memory (N11).
   * `voice` starts dictation or Talk in it once it opens (Home's mic and Talk, D3).
   */
  async function newChat(
    firstMessage = '',
    profile?: { id: string; display_name: string },
    {
      send = true,
      incognito = false,
      voice,
    }: { send?: boolean; incognito?: boolean; voice?: HomeVoice } = {},
  ) {
    if (
      operation.current ||
      !key ||
      controller.getSnapshot().status !== 'ready'
    )
      return;
    operation.current = true;
    // Talk from Home: its playback starts in this tap (Safari and iPhone need one).
    if (voice === 'talk') primeVoicePlayback();
    setFirstPrompt(null);
    setCreatingChat(true);
    // Typed in the open chat while this one is on its way: it moves to the new
    // chat's composer, and the open chat keeps the draft it had (F17).
    const from = controller.getSnapshot().selectedConversationId;
    const before = from ? controller.getDraft(from) : null;
    const selection = controller.getSelectionVersion();
    const instance = state.handshake?.instance_id;
    const session = state.handshake?.client_session_id;
    const route = routeKey.current;
    const current = () =>
      alive.current &&
      controller.getSelectionVersion() === selection &&
      routeKey.current === route &&
      controller.getSnapshot().handshake?.instance_id === instance &&
      controller.getSnapshot().handshake?.client_session_id === session;
    let reserved: string | null = null;
    // The receipt this press saved, never one an earlier press left.
    let fresh: string | null = null;
    const opened = (conversationId: string) => {
      setFocusConversationId(conversationId);
      if (voice) startVoiceOnOpen(conversationId, voice);
      // Typed on Home while the chat was on its way: it waits in the new chat's composer (M12). A message
      // asked from Home is that text, already on its way.
      const typed = takeHomeDraft();
      if (
        typed.trim() &&
        !firstMessage.trim() &&
        !controller.getDraft(conversationId).text.trim()
      )
        controller.setDraft(conversationId, { text: typed, attachments: [] });
      if (from && before && from !== conversationId && !firstMessage.trim()) {
        const now = controller.getDraft(from);
        if (
          now.text !== before.text &&
          now.text.trim() &&
          !controller.getDraft(conversationId).text.trim()
        ) {
          controller.setDraft(conversationId, now);
          controller.setDraft(from, before);
        }
      }
      if (firstMessage.trim() && !send) {
        controller.setDraft(conversationId, {
          text: firstMessage,
          attachments: [],
        });
      } else if (firstMessage.trim()) {
        controller.setDraft(conversationId, {
          text: firstMessage.trim(),
          attachments: [],
        });
        setFirstPrompt({ conversationId, text: firstMessage.trim() });
      }
      if (controller.getSnapshot().selectedConversationId !== conversationId)
        void controller.selectConversation(conversationId);
      navigate(`/conversations/${conversationId}`);
      setError('');
    };
    try {
      const saved = commandReceipts.read(key);
      // A chat New chat made here and nobody used opens again instead of
      // another empty one; the open chat's messages count before its name
      // changes in the list.
      const snapshot = controller.getSnapshot();
      const shown = snapshot.projection?.rows.length
        ? snapshot.selectedConversationId
        : null;
      // An unused chat is never reused as an incognito one, nor the other way round.
      const unused =
        saved || profile || incognito
          ? null
          : unusedChat(
              snapshot.conversations.filter(
                (row) =>
                  started.current.has(row.id) &&
                  row.id !== shown &&
                  !row.incognito,
              ),
              (id) => controller.getDraft(id),
            );
      if (unused) {
        opened(unused);
        return;
      }
      const identity = saved?.commandId ?? crypto.randomUUID();
      // A window that can only look is refused before anything runs, so it keeps nothing to check after
      // signing in (RM2).
      if (saved || snapshot.handshake?.may_act !== false) {
        if (!saved) {
          commandReceipts.reserve(key, {
            commandId: identity,
            steeringId: null,
          });
          fresh = identity;
        }
        reserved = identity;
        setPending(identity);
      }
      const result = saved
        ? await controller.receipt(identity)
        : await controller.intent(
            null,
            'conversation.create',
            profile
              ? {
                  title: `${profile.display_name} chat`,
                  agent_profile_id: profile.id,
                }
              : incognito
                ? { incognito: true }
                : {},
            '0',
            identity,
          );
      if (result.command_id !== identity) throw { code: 'operation_uncertain' };
      if (
        result.status === 'completed' &&
        result.conversation_id &&
        current()
      ) {
        commandReceipts.clear(key, identity);
        setPending(null);
        setMissing(null);
        if (saved && incognito)
          // The new chat confirmed here was started as an everyday one: it is unused, so it turns incognito
          // before it opens, never the other way round.
          await controller.intent(
            result.conversation_id,
            'conversation.incognito',
            { incognito: true },
            result.revision ?? '0',
          );
        if (!profile && !incognito) started.current.add(result.conversation_id);
        opened(result.conversation_id);
      } else if (current()) {
        if (result.status === 'rejected')
          setMissing({ key, commandId: identity });
        setError(
          "Row-Bot couldn't confirm the new chat was created. Check it before starting another.",
        );
      }
    } catch (cause) {
      // Refused at the door (signed out, not allowed): the create never ran, so nothing is left to check.
      if (fresh && REFUSED_UNRUN.has(clientError(cause).code)) {
        try {
          commandReceipts.clear(key, fresh);
          reserved = null;
          if (alive.current) setPending(null);
        } catch {
          // Storage refused the clear: the receipt stays checkable, as before.
        }
      }
      if (current()) {
        if (clientError(cause).code === 'not_found' && reserved)
          setMissing({ key, commandId: reserved });
        setError(
          cause instanceof ReceiptStorageError
            ? cause.message
            : clientError(cause).message,
        );
      }
    } finally {
      operation.current = false;
      if (alive.current) setCreatingChat(false);
    }
  }
  function reviewMissingReceipt() {
    if (!missing || missing.key !== key) return;
    const saved = missing;
    const selection = controller.getSelectionVersion();
    const instance = state.handshake?.instance_id;
    const session = state.handshake?.client_session_id;
    overlay.open({
      kind: 'alert',
      title: 'Stop checking this new chat?',
      description:
        "Row-Bot can't find what happened to it, so it may still appear. Look at your conversations first: starting another could make two. Nothing is created now.",
      confirmLabel: 'Stop checking',
      onConfirm: () => {
        if (
          !alive.current ||
          controller.getSelectionVersion() !== selection ||
          controller.getSnapshot().handshake?.instance_id !== instance ||
          controller.getSnapshot().handshake?.client_session_id !== session
        )
          return;
        try {
          commandReceipts.clear(saved.key, saved.commandId);
          setPending(null);
          setMissing(null);
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
  return {
    newChat,
    creatingChat,
    pending,
    error,
    reviewMissingReceipt,
    canReview: missing?.key === key,
    focusConversationId,
    firstPrompt,
    onFirstPromptConsumed: (conversationId: string) =>
      setFirstPrompt((value) =>
        value?.conversationId === conversationId ? null : value,
      ),
    onComposerFocused: () => setFocusConversationId(null),
  };
}
