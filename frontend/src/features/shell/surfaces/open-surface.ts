/**
 * A surface's "Open bigger" asks the workspace to show it beside its chat:
 * the side panel on desktop, full screen on a phone. Surfaces render outside
 * the workspace's React tree, so this is a small module-level channel, like
 * a page's.
 */
export type SurfaceRequest = {
  conversationId: string;
  surfaceId: string;
  title: string;
};
type Listener = (request: SurfaceRequest) => boolean;

const listeners = new Set<Listener>();

export const SURFACE_PANEL_KIND = 'surface.view';

/** True when a workspace showing that chat opened the surface. */
export function requestSurfacePanel(request: SurfaceRequest): boolean {
  for (const listener of listeners) if (listener(request)) return true;
  return false;
}

export function onSurfaceRequest(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
