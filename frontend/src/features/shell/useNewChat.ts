import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { clientError } from '../../api/errors';
import { useClientSelector, useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { commandReceipts, ReceiptStorageError } from './command-receipts';

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
  const operation = useRef(false);
  const alive = useRef(true);
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

  async function newChat() {
    if (
      operation.current ||
      !key ||
      controller.getSnapshot().status !== 'ready'
    )
      return;
    operation.current = true;
    setCreatingChat(true);
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
    try {
      const saved = commandReceipts.read(key);
      const identity = saved?.commandId ?? crypto.randomUUID();
      if (!saved)
        commandReceipts.reserve(key, { commandId: identity, steeringId: null });
      reserved = identity;
      setPending(identity);
      const result = saved
        ? await controller.receipt(identity)
        : await controller.intent(
            null,
            'conversation.create',
            {},
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
        setFocusConversationId(result.conversation_id);
        if (
          controller.getSnapshot().selectedConversationId !==
          result.conversation_id
        )
          void controller.selectConversation(result.conversation_id);
        navigate(`/conversations/${result.conversation_id}`);
        setError('');
      } else if (current()) {
        if (result.status === 'rejected')
          setMissing({ key, commandId: identity });
        setError(
          'The new conversation receipt is retained. Check it before starting another.',
        );
      }
    } catch (cause) {
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
      title: 'Clear the pending receipt?',
      description:
        'The receipt was rejected or could not be found. An unavailable action may still finish. Clear this record only after reviewing the conversation; sending again could create a duplicate. This does not resend an action.',
      confirmLabel: 'Clear pending receipt',
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
    onComposerFocused: () => setFocusConversationId(null),
  };
}
