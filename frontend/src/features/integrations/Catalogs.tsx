import { useEffect, useRef, useState } from 'react';
import { useRuntime } from '../../runtime';
import type {
  IntegrationSourceStatus,
  IntegrationSourceView,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, Field, Toggle } from '../../ui/primitives';
import {
  sourceName,
  sourcesFor,
  useSources,
  type Kind,
  type Source,
} from './discovery';
// An explicit update downloads the latest listings; it runs on the server and is polled here.
function UpdateCatalog({ source, label }: { source: string; label: string }) {
  const { controller } = useRuntime();
  const [state, setState] = useState<'idle' | 'updating' | 'done' | 'failed'>(
    'idle',
  );
  const mounted = useRef(true);
  useEffect(() => () => void (mounted.current = false), []);
  const update = async () => {
    setState('updating');
    try {
      let view: IntegrationSourceView | undefined =
        await controller.updateIntegrationSource(source);
      while (mounted.current && view?.catalog?.state === 'updating') {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        const list = await controller.integrationSources();
        view = list?.items.find((item) => item.id === source);
      }
      if (mounted.current)
        setState(view?.catalog?.state === 'done' ? 'done' : 'failed');
    } catch {
      if (mounted.current) setState('failed');
    }
  };
  return (
    <>
      <Button
        onClick={() => void update()}
        disabled={state === 'updating'}
        aria-label={`Update ${label}`}
      >
        {state === 'updating' ? 'Updating…' : 'Update'}
      </Button>
      {state === 'done' && <p role="status">Updated.</p>}
      {state === 'failed' && (
        <p role="alert">
          This catalog couldn&apos;t be updated. Its saved listings are still
          available.
        </p>
      )}
    </>
  );
}

export default function Catalogs({
  kind,
  disabled,
  onChange,
  onBack,
}: {
  kind: Kind;
  disabled: Source[];
  onChange: (source: Source, enabled: boolean) => void;
  onBack: () => void;
}) {
  const { controller } = useRuntime();
  const { sources: known, error: sourcesError } = useSources();
  const [sources, setSources] = useState<IntegrationSourceStatus[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!known.length) return;
    const abort = new AbortController();
    void controller
      .searchIntegrations(
        { kind, sources: sourcesFor(kind, known), refresh: false },
        abort.signal,
      )
      .then((page) => {
        if (!abort.signal.aborted) setSources(page.sources);
      })
      .catch((e) => {
        if (!abort.signal.aborted) setError(clientError(e).message);
      });
    return () => abort.abort();
  }, [controller, kind, known]);
  return (
    <section className="stack integration-catalogs" aria-label="Catalogs">
      <Button onClick={onBack}>Back to integrations</Button>
      <h2>Catalogs</h2>
      <p>
        Choose catalogs for this device. Search sends your query to enabled
        public catalogs; Update downloads a catalog&apos;s latest listings.
      </p>
      {(error || sourcesError) && (
        <p role="alert">
          Catalog information unavailable: {error || sourcesError}
        </p>
      )}
      {sources.map((source) => (
        <section
          className="integration-catalog stack"
          key={source.source}
          aria-label={sourceName(source.source)}
        >
          <Field label={sourceName(source.source)} layout="row">
            <Toggle
              label={sourceName(source.source)}
              disabled={source.eligibility !== 'eligible'}
              checked={
                source.eligibility === 'eligible' &&
                !disabled.includes(source.source as Source)
              }
              onChange={(e) =>
                onChange(source.source as Source, e.target.checked)
              }
            />
          </Field>
          <p>
            {source.status === 'empty' && source.eligibility === 'eligible'
              ? 'No saved results. Use Search in Discover to look for current listings.'
              : source.message}
          </p>
          <p>
            {source.eligibility === 'eligible' ? 'Eligible' : 'Unavailable'} ·{' '}
            {source.access === 'snapshot'
              ? 'Local snapshot'
              : source.access === 'public'
                ? 'Public catalog'
                : source.access === 'local'
                  ? 'Local metadata'
                  : 'Access not implemented'}{' '}
            · {source.status}
          </p>
          <p>
            {source.fetched_at
              ? `Saved ${new Date(source.fetched_at * 1000).toLocaleString()} · ${Math.max(0, Math.floor((Date.now() / 1000 - source.fetched_at) / 86400))} days old`
              : 'Acquisition time not supplied'}
          </p>
          {source.truncated && (
            <p>Bounded catalog sample; more entries may exist upstream.</p>
          )}
          {known.some(
            (view) =>
              view.id === source.source &&
              view.network === 'explicit' &&
              view.eligibility === 'eligible',
          ) && (
            <UpdateCatalog
              source={source.source}
              label={sourceName(source.source)}
            />
          )}
          {source.snapshot_digest && (
            <details>
              <summary>Snapshot provenance</summary>
              <p>API {source.snapshot_version}</p>
              <code>{source.snapshot_digest}</code>
            </details>
          )}
        </section>
      ))}
    </section>
  );
}
