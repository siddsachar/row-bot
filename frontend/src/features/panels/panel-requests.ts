/**
 * A panel asks the workspace to open another resource's panel (a design it
 * just duplicated), or to close a resource's panels (a design it just
 * deleted). Panels can render inside overlays, outside the workspace's React
 * tree, so this is a small module-level channel.
 */
export type ResourcePanelRequest = {
  conversationId: string;
  resourceRef: string;
  /** Close every panel showing this resource instead of opening one. */
  close?: boolean;
};

type Listener = (request: ResourcePanelRequest) => void;

const listeners = new Set<Listener>();

/** True when a workspace is listening (and will try to open or close it). */
export function requestResourcePanel(request: ResourcePanelRequest): boolean {
  for (const listener of listeners) listener(request);
  return listeners.size > 0;
}

export function onResourcePanelRequest(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
