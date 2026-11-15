/**
 * A video project's card asks the workspace to open the Video panel beside
 * its chat (5.3.1 B3): the side panel on desktop, full screen on a phone.
 * Cards render outside the workspace's React tree, so this is a small
 * module-level channel, as for pages. A request for a chat that isn't open
 * yet waits until the workspace opens that chat.
 */
export type VideoRequest = {
  conversationId: string;
  projectId: string;
  title: string;
};
type Listener = (request: VideoRequest) => boolean;

const listeners = new Set<Listener>();
let waiting: VideoRequest | null = null;

export const VIDEO_PANEL_KIND = 'video.project';

/** True when a workspace showing that chat opened the video. */
export function requestVideoPanel(request: VideoRequest): boolean {
  for (const listener of listeners) if (listener(request)) return true;
  waiting = request;
  return false;
}

/** The video asked for this chat before it was open, once. */
export function takeWaitingVideo(conversationId: string): VideoRequest | null {
  if (waiting?.conversationId !== conversationId) return null;
  const found = waiting;
  waiting = null;
  return found;
}

export function onVideoRequest(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
