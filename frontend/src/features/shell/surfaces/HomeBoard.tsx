import type { CSSProperties } from 'react';
import { useEffect, useState } from 'react';
import { MessageSquare, PinOff } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { clientError } from '../../../api/errors';
import { useOptionalRuntime } from '../../../runtime';
import { useNotify } from '../../../ui/overlays';
import { IconButton } from '../../../ui/primitives';
import { reloadBoard, unpinSurface, useBoard } from './board-store';
import { SurfaceView } from './SurfaceView';

/**
 * The Home Board (generative UI, G5): the interfaces the person pinned from
 * their chats, each the latest drawing in its chat. What Row-Bot fills in on
 * this computer is read again while Home is shown (no model, no tokens); a
 * button takes the person to its chat and sends the press there.
 */
export function HomeBoard({ order = 1 }: { order?: number }) {
  const runtime = useOptionalRuntime();
  const board = useBoard();
  const navigate = useNavigate();
  const notify = useNotify();
  const [busy, setBusy] = useState('');
  // Home opening reads it again: a chat may have drawn a pinned surface anew.
  useEffect(() => reloadBoard(runtime?.controller), [runtime?.controller]);
  if (!runtime || !board?.length) return null;
  const open = (conversationId: string) =>
    navigate(`/conversations/${encodeURIComponent(conversationId)}`);
  const unpin = async (pinId: string) => {
    setBusy(pinId);
    try {
      await unpinSurface(runtime.controller, pinId);
    } catch (cause) {
      notify(clientError(cause).message, 'warning');
    } finally {
      setBusy('');
    }
  };
  return (
    <section
      className="overview-section overview-cell genui-board"
      style={{ '--overview-order': order } as CSSProperties}
      aria-labelledby="overview-board"
    >
      <header className="overview-section-title">
        <h3 id="overview-board">Board</h3>
      </header>
      <ul className="genui-board-grid" aria-label="Pinned">
        {board.map((item) => (
          <li
            key={item.pin_id}
            className="genui-board-card"
            data-available={item.available ? 'true' : 'false'}
          >
            <header className="genui-board-head">
              <span className="genui-board-names">
                <strong>{item.title}</strong>
                {item.conversation_title && (
                  <small>{item.conversation_title}</small>
                )}
              </span>
              <IconButton
                size="sm"
                variant="ghost"
                label="Open its chat"
                onClick={() => open(item.conversation_id)}
              >
                <MessageSquare size={14} aria-hidden />
              </IconButton>
              <IconButton
                size="sm"
                variant="ghost"
                label="Unpin from Home"
                disabled={busy === item.pin_id}
                onClick={() => void unpin(item.pin_id)}
              >
                <PinOff size={14} aria-hidden />
              </IconButton>
            </header>
            {item.available && item.surface_json && item.state_key ? (
              <SurfaceView
                json={item.surface_json}
                blockId={`board:${item.pin_id}`}
                stateKey={item.state_key}
                target={{
                  conversationId: item.conversation_id,
                  messageId: item.message_id ?? undefined,
                }}
                place="board"
                onPress={() => open(item.conversation_id)}
              />
            ) : (
              <p className="genui-board-gone">
                The reply that drew this is no longer in its chat.
              </p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
