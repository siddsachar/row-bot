import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';
import { GraduationCap } from 'lucide-react';
import { clientError } from '../../api/errors';
import type { LearningConversation } from '../../api/types';
import type { MenuAction } from '../../ui/primitives';
import { useOverlay } from '../../ui/overlays';
import { learningIsOn, useLearning, type LearningIO } from './learning-state';
import TeachSheet, { type TeachAnswer } from './TeachSheet';

/** What a Teach is about: a reply (its message id) or a failed step (its tool call id). */
export type TeachTarget = { messageId?: string; toolCallId?: string };

/** Opens Teach for this chat; null when the chat offers no Teach (D27). */
export const TeachContext = createContext<
  ((target: TeachTarget) => void) | null
>(null);

export function useTeach() {
  return useContext(TeachContext);
}

export const TEACH_ICON = GraduationCap;

/**
 * The open chat's Learning controls: Teach while Learning is on and uses this chat,
 * and the sheet it opens. Nothing at all without Learning (preview) or while off.
 */
export function useChatTeach(conversationId: string | null): {
  teach: ((target: TeachTarget) => void) | null;
  sheet: ReactNode;
  state: LearningConversation['state'] | null;
} {
  const io = useLearning();
  const on = learningIsOn(io);
  const revision = io?.snapshot?.revision ?? '';
  // The shell's own overlays (a chat always has them).
  const { notify } = useOverlay();
  const [state, setState] = useState<LearningConversation | null>(null);
  const [target, setTarget] = useState<TeachTarget | null>(null);
  useEffect(() => {
    if (!io || !on || !conversationId) {
      setState(null);
      return;
    }
    let live = true;
    io.conversation(conversationId).then(
      (value) => live && setState(value),
      () => live && setState(null),
    );
    return () => {
      live = false;
    };
    // Read again when the conversation or Learning's saved state changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, on, revision]);
  const current = state?.conversation_id === conversationId ? state : null;
  const teach =
    io && current?.state === 'learning'
      ? (next: TeachTarget) => setTarget(next)
      : null;
  async function send(answer: TeachAnswer) {
    if (!io || !conversationId || !target) return;
    const receipt = await io.run('teach', {
      conversation_id: conversationId,
      message_id: target.messageId ?? '',
      tool_call_id: target.toolCallId ?? '',
      category: answer.category,
      note: answer.note,
    });
    setTarget(null);
    const signal = receipt.signal_id;
    notify(
      'Thanks. Row-Bot will learn from this.',
      undefined,
      signal
        ? {
            label: 'Undo',
            onAction: () =>
              void io.run('undo_teach', { signal_id: signal }).then(
                () => notify('Removed. Row-Bot won’t learn from it.'),
                (cause: unknown) =>
                  notify(clientError(cause).message, 'danger'),
              ),
          }
        : undefined,
    );
  }
  return {
    teach,
    state: current?.state ?? null,
    sheet: io ? (
      <TeachSheet
        open={target !== null}
        onOpenChange={(open) => {
          if (!open) setTarget(null);
        }}
        step={Boolean(target?.toolCallId)}
        onSend={send}
      />
    ) : null,
  };
}

/** Switch a chat off from Learning (with a confirm: it deletes what was recorded) or back on. */
function useOptOut(io: LearningIO | null) {
  const overlay = useOverlay();
  return useCallback(
    (
      conversation: { id: string; title: string },
      optedOut: boolean,
      opener?: HTMLElement | null,
    ) => {
      if (!io) return;
      const run = (action: 'opt_out' | 'opt_in') =>
        void io.run(action, { conversation_id: conversation.id }).then(
          (receipt) => overlay.notify(receipt.summary),
          (cause: unknown) =>
            overlay.notify(clientError(cause).message, 'danger'),
        );
      if (optedOut) {
        run('opt_in');
        return;
      }
      overlay.open({
        kind: 'alert',
        title: 'Don’t learn from this chat?',
        description:
          'Row-Bot won’t record anything more from this chat. This also deletes what Learning recorded from this chat. You can switch it back on from this menu.',
        confirmLabel: 'Don’t learn from this chat',
        returnFocusTo: opener ?? null,
        onConfirm: () => run('opt_out'),
      });
    },
    [io, overlay],
  );
}

/**
 * "Don't learn from this chat", a check item for a chat's menu while Learning
 * is on; null otherwise. Ticked while the chat is switched off.
 */
export function useLearningChatMenu(): (conversation: {
  id: string;
  title: string;
}) => MenuAction | null {
  const io = useLearning();
  const on = learningIsOn(io);
  const optOut = useOptOut(io);
  const switchedOff = io?.snapshot?.opted_out_conversations;
  return useCallback(
    (conversation) => {
      if (!on) return null;
      const checked = Boolean(switchedOff?.includes(conversation.id));
      return {
        label: 'Don’t learn from this chat',
        icon: <GraduationCap size={16} />,
        checked,
        onSelect: (opener) => optOut(conversation, checked, opener),
      };
    },
    [on, optOut, switchedOff],
  );
}
