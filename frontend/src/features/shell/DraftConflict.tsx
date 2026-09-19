import { useEffect, useState } from 'react';
import type { DraftView } from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Button, Skeleton } from '../../ui/primitives';

export default function DraftConflict({ id }: { id: string }) {
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const [saved, setSaved] = useState<DraftView | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    void controller
      .savedDraft(id, abort.signal)
      .then(setSaved)
      .catch((cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      });
    return () => abort.abort();
  }, [controller, id]);
  async function choose(local: boolean) {
    if (!saved || busy) return;
    setBusy(true);
    try {
      await controller.resolveDraft(id, saved.revision, local);
      overlay.close();
    } catch (cause) {
      setError(clientError(cause).message);
      setBusy(false);
    }
  }
  return (
    <div className="stack draft-conflict" aria-busy={busy}>
      {saved ? (
        <>
          <div className="draft-comparison-grid">
            <section aria-labelledby="saved-draft-heading">
              <h3 id="saved-draft-heading">Saved in another client</h3>
              <pre className="draft-comparison">
                {saved.text || '(Empty draft)'}
              </pre>
              <p className="muted">
                {saved.attachments.length} saved attachments
              </p>
            </section>
            <section aria-labelledby="local-draft-heading">
              <h3 id="local-draft-heading">Your draft on this page</h3>
              <pre className="draft-comparison">
                {controller.getDraft(id).text || '(Empty draft)'}
              </pre>
            </section>
          </div>
          <div
            className="button-row draft-conflict-actions"
            role="group"
            aria-label="Choose a draft"
          >
            <Button disabled={busy} onClick={() => void choose(false)}>
              Use saved draft
            </Button>
            <Button disabled={busy} onClick={() => void choose(true)}>
              Replace saved draft with mine
            </Button>
          </div>
        </>
      ) : (
        <Skeleton label="Loading saved draft" />
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
