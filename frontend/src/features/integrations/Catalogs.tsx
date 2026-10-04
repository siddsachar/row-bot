import { useEffect, useState } from 'react';
import { useRuntime } from '../../runtime';
import type { IntegrationSourceStatus } from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, Field, Toggle } from '../../ui/primitives';
import {
  sourceName,
  sourcesFor,
  useSources,
  type Kind,
  type Source,
} from './discovery';
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
  const known = useSources();
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
        Choose catalogs for this device. Opening this view only reads local
        metadata. Search sends your query to enabled public catalogs; snapshots
        stay local.
      </p>
      {error && <p role="alert">Catalog information unavailable: {error}</p>}
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
