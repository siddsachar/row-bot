import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform } from '../../platform';
import { nativeConversationId } from '../../platform';

/**
 * Main windows tell the native host which conversation they show; the
 * desktop Buddy follows it. Browsers have no host, so publishing stops after
 * the first refusal. Returns the unbind function.
 */
export function publishBuddyTarget(
  controller: Pick<ClientController, 'getSnapshot' | 'subscribe'>,
  platform: Pick<ClientPlatform, 'publishBuddyTarget'>,
): () => void {
  let published: string | null = null;
  let active = true;
  const publish = () => {
    const id = controller.getSnapshot().selectedConversationId;
    if (!active || !id || id === published || !nativeConversationId(id)) return;
    published = id;
    void platform.publishBuddyTarget(id).then((result) => {
      if (result.status === 'ok') return;
      // Let the next selection try again after a transient failure.
      if (published === id) published = null;
      if (
        result.status === 'unavailable' &&
        result.reason === 'buddy_target_requires_native'
      )
        active = false;
    });
  };
  const unsubscribe = controller.subscribe(publish);
  publish();
  return () => {
    active = false;
    unsubscribe();
  };
}

/**
 * "Open full thread" in the desktop Buddy: the host brings this window
 * forward and asks it (a DOM event carrying only an id) to open the
 * conversation. The route checks access like any other navigation.
 */
export function OpenConversationRequests() {
  const navigate = useNavigate();
  useEffect(() => {
    const open = (event: Event) => {
      const id = (event as CustomEvent<unknown>).detail;
      if (nativeConversationId(id))
        navigate(`/conversations/${encodeURIComponent(id)}`);
    };
    window.addEventListener('row-bot-open-conversation', open);
    return () => window.removeEventListener('row-bot-open-conversation', open);
  }, [navigate]);
  return null;
}
