/**
 * Something outside the inspector asks it to show one file (the details
 * card's project notes). The request waits until that folder's inspector is
 * open and has read its summary, then opens the file in the Files tab.
 */
export type WorkspaceFileRequest = { resourceId: string; path: string };

type Listener = (request: WorkspaceFileRequest) => void;

const pending = new Map<string, string>();
const listeners = new Set<Listener>();

export function requestWorkspaceFile(request: WorkspaceFileRequest) {
  pending.set(request.resourceId, request.path);
  for (const listener of listeners) listener(request);
}

/** The file asked for in this folder, once: the asker reads it after. */
export function takeWorkspaceFile(resourceId: string): string | undefined {
  const path = pending.get(resourceId);
  pending.delete(resourceId);
  return path;
}

export function onWorkspaceFileRequest(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
