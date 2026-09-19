import type { ProviderStatusSnapshot } from '../../api/types';

export default function CatalogStatus({
  generated_at,
  freshness,
}: Pick<ProviderStatusSnapshot, 'generated_at' | 'freshness'>) {
  return (
    <p className="muted" role="status">
      {freshness === 'unavailable'
        ? 'No dated catalog snapshot is available.'
        : freshness === 'stale'
          ? 'Saved catalog may be out of date.'
          : 'Saved catalog is current.'}
      {generated_at != null && Number.isFinite(generated_at) && (
        <> Last saved {new Date(generated_at * 1000).toLocaleString()}.</>
      )}{' '}
      Account access and runtime readiness have not been checked.
    </p>
  );
}
