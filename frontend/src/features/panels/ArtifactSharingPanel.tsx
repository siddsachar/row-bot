import { useEffect, useState } from 'react';
import type { ArtifactShareChannels } from '../../api/types';
import { Button, ErrorState } from '../../ui/primitives';
import ArtifactSharing, { type ArtifactSharingProps } from './ArtifactSharing';

export type ArtifactSharingPanelProps = Omit<
  ArtifactSharingProps,
  'channels'
> & {
  loadChannels: (
    cursor?: string,
    signal?: AbortSignal,
  ) => Promise<ArtifactShareChannels>;
};

export default function ArtifactSharingPanel({
  loadChannels,
  ...props
}: ArtifactSharingPanelProps) {
  const [page, setPage] = useState<ArtifactShareChannels | null>(null);
  const [cursor, setCursor] = useState<string>();
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    if (!props.visible) return;
    const abort = new AbortController();
    setLoading(true);
    setError(false);
    loadChannels(cursor, abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) {
          setPage(value);
          setLoading(false);
        }
      },
      () => {
        if (!abort.signal.aborted) {
          setError(true);
          setLoading(false);
        }
      },
    );
    return () => abort.abort();
  }, [props.visible, cursor, loadChannels, reload]);
  return (
    <div className="stack" hidden={!props.visible}>
      {error && (
        <ErrorState title="Registered channels unavailable">
          Reload the channel list. Saved destinations are retained.
        </ErrorState>
      )}
      <div className="actions">
        <Button
          disabled={loading}
          onClick={() => {
            setCursor(undefined);
            setPage(null);
            setReload((value) => value + 1);
          }}
        >
          First channel page
        </Button>
        {page?.next_cursor && (
          <Button
            disabled={loading}
            onClick={() => setCursor(page.next_cursor!)}
          >
            Next channel page
          </Button>
        )}
      </div>
      <ArtifactSharing {...props} channels={page?.items ?? []} />
    </div>
  );
}
