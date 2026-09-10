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
    <div className="stack">
      {saved ? (
        <>
          <strong>Saved in another client</strong>
          <pre className="draft-comparison">
            {saved.text || '(Empty draft)'}
          </pre>
          <p>{saved.attachments.length} saved attachments</p>
          <strong>Your draft on this page</strong>
          <pre className="draft-comparison">
            {controller.getDraft(id).text || '(Empty draft)'}
          </pre>
          <div className="button-row">
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
