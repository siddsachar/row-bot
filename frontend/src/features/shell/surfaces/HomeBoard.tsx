import type { CSSProperties } from 'react';
import { useEffect, useState } from 'react';
import { MessageSquare, PinOff } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type { BoardView } from '../../../api/types';
import { clientError } from '../../../api/errors';
import { useOptionalRuntime } from '../../../runtime';
import { useNotify } from '../../../ui/overlays';
import { IconButton } from '../../../ui/primitives';
import { reloadBoard, unpinSurface, useBoard } from './board-store';
import { SurfaceView } from './SurfaceView';

type BoardItem = BoardView['items'][number];

/**
 * The Home Board (generative UI, G5): the interfaces the person pinned from
 * their chats, each the latest drawing in its chat. What Row-Bot fills in on
 * this computer is read again while Home is shown (no model, no tokens); a
 * button takes the person to its chat and sends the press there.
 */
export function HomeBoard({ order = 1 }: { order?: number }) {
  const runtime = useOptionalRuntime();
  const board = useBoard();
  // Home opening reads it again: a chat may have drawn a pinned surface anew.
  useEffect(() => reloadBoard(runtime?.controller), [runtime?.controller]);
  if (!runtime || !board?.length) return null;
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
          <li key={item.pin_id}>
            <BoardCard item={item} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * One pinned interface, as on Home: its name and chat, Open its chat and
 * Unpin, and the surface itself, live. A chat shows the same card when
 * Row-Bot is asked for a pinned interface by name (board_show).
 */
export function BoardCard({ item }: { item: BoardItem }) {
  const runtime = useOptionalRuntime();
  const navigate = useNavigate();
  const notify = useNotify();
  const [busy, setBusy] = useState(false);
  const open = () =>
    navigate(`/conversations/${encodeURIComponent(item.conversation_id)}`);
  const unpin = async () => {
    setBusy(true);
    try {
      await unpinSurface(runtime?.controller, item.pin_id);
    } catch (cause) {
      notify(clientError(cause).message, 'warning');
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      className="genui-board-card"
      data-available={item.available ? 'true' : 'false'}
    >
      <header className="genui-board-head">
        <span className="genui-board-names">
          <strong>{item.title}</strong>
          {item.conversation_title && <small>{item.conversation_title}</small>}
        </span>
        <IconButton
          size="sm"
          variant="ghost"
          label="Open its chat"
          onClick={open}
        >
          <MessageSquare size={14} aria-hidden />
        </IconButton>
        <IconButton
          size="sm"
          variant="ghost"
          label="Unpin from Home"
          disabled={busy}
          onClick={() => void unpin()}
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
          onPress={open}
        />
      ) : (
        <p className="genui-board-gone">
          The reply that drew this is no longer in its chat.
        </p>
      )}
    </div>
  );
}

/** A pinned interface that board_show named in a chat: the Board's card, read by its pin. */
export function PinnedSurfaceCard({ pinId }: { pinId: string }) {
  const board = useBoard();
  if (board === null) return null;
  const item = board.find((entry) => entry.pin_id === pinId);
  if (!item)
    return (
      <p className="genui-board-gone">
        That interface isn’t pinned to Home any more.
      </p>
    );
  return <BoardCard item={item} />;
}
