import type { ClientController } from '../../api/controller';

/** Retain one private editor across routes, replacing it on any auth change. */
export function createAuthenticatedEditorOwner<
  T extends { dispose(): void; hasRetained(): boolean },
>(
  controller: Pick<ClientController, 'getSnapshot' | 'subscribe'>,
  create: () => T,
) {
  const identity = () => {
    const h = controller.getSnapshot().handshake;
    return h
      ? JSON.stringify([h.instance_id, h.server_epoch, h.client_session_id])
      : '';
  };
  let auth = identity(),
    current = create(),
    disposed = false;
  const sync = () => {
    if (disposed || identity() === auth) return;
    auth = identity();
    current.dispose();
    current = create();
  };
  const unsubscribe = controller.subscribe(sync);
  return {
    get() {
      sync();
      return !disposed && auth ? current : undefined;
    },
    hasRetained() {
      sync();
      return !disposed && current.hasRetained();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      unsubscribe();
      current.dispose();
    },
  };
}
