import { EmptyState } from '../../../ui/primitives';
import { useLatestSurface } from './surface-store';
import { SurfaceView } from './SurfaceView';

/**
 * A surface beside its chat ("Open bigger"): the latest reply's drawing of
 * this interface, so one Row-Bot redraws shows here as it lands. It is the
 * same surface as in the chat: an edit in either shows in both.
 */
export function SurfacePanel({
  conversationId,
  surfaceId,
}: {
  conversationId: string;
  surfaceId: string;
}) {
  const found = useLatestSurface(surfaceId);
  if (!found)
    return (
      <EmptyState title="Interface not loaded">
        Scroll back in the chat to the reply that drew it, then open it again.
      </EmptyState>
    );
  return (
    <div className="genui-panel">
      <SurfaceView
        json={found.block.surface_json}
        blockId={found.block.id}
        target={{ conversationId, messageId: found.messageId }}
        inPanel
      />
    </div>
  );
}
