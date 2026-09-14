import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Button, ErrorState, Skeleton } from '../../ui/primitives';

export type ArtifactLifecycleCapability = {
  id:
    | 'presentation'
    | 'thumbnails'
    | 'export.html'
    | 'export.pdf'
    | 'export.png'
    | 'export.pptx'
    | 'publish.local'
    | 'publish.remote'
    | 'share.channel'
    | 'share.x';
  label: string;
  state: 'ready' | 'check_on_use' | 'unavailable';
  detail: string;
  review_required: boolean;
};

export type ArtifactLifecycleState = {
  resource_id: string;
  resource_revision: string;
  mode: 'deck' | 'document' | 'landing' | 'app_mockup' | 'storyboard';
  page_count: number;
  capabilities: ArtifactLifecycleCapability[];
};

type LifecycleView = 'presentation' | 'export' | 'sharing';

export type ArtifactLifecyclePanelProps = {
  resourceId: string;
  resourceRevision: string;
  visible: boolean;
  load: (
    resourceId: string,
    resourceRevision: string,
    signal: AbortSignal,
  ) => Promise<ArtifactLifecycleState>;
  renderPresentation: (state: ArtifactLifecycleState) => ReactNode;
  renderExport: (state: ArtifactLifecycleState) => ReactNode;
  renderSharing: (state: ArtifactLifecycleState) => ReactNode;
};

const groups: Record<LifecycleView, string[]> = {
  presentation: ['presentation', 'thumbnails'],
  export: ['export.html', 'export.pdf', 'export.png', 'export.pptx'],
  sharing: ['publish.local', 'publish.remote', 'share.channel', 'share.x'],
};

export default function ArtifactLifecyclePanel(
  props: ArtifactLifecyclePanelProps,
) {
  const { load, resourceId, resourceRevision, visible } = props;
  const [state, setState] = useState<ArtifactLifecycleState | null>(null);
  const [active, setActive] = useState<LifecycleView | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const request = useRef<AbortController | null>(null);

  useEffect(() => {
    request.current?.abort();
    setState(null);
    setActive(null);
    setError('');
    if (!visible) return;
    const controller = new AbortController();
    request.current = controller;
    void load(resourceId, resourceRevision, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        if (
          value.resource_id !== resourceId ||
          value.resource_revision !== resourceRevision
        )
          throw { code: 'resource_revision_conflict' };
        setState(value);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        const code =
          typeof reason === 'object' && reason !== null && 'code' in reason
            ? String(reason.code)
            : '';
        setError(
          code === 'resource_revision_conflict'
            ? 'The saved design changed. Reload its current lifecycle options.'
            : 'Lifecycle options are unavailable for this design.',
        );
      });
    return () => controller.abort();
  }, [load, reload, resourceId, resourceRevision, visible]);

  const availability = useMemo(() => {
    const result = new Map<LifecycleView, boolean>();
    for (const view of Object.keys(groups) as LifecycleView[])
      result.set(
        view,
        !!state?.capabilities.some(
          (item) =>
            groups[view].includes(item.id) && item.state !== 'unavailable',
        ),
      );
    return result;
  }, [state]);

  if (!visible) return null;
  return (
    <section className="studio-section stack" aria-label="Design lifecycle">
      <header className="capability-header">
        <div>
          <h3>Present, export and share</h3>
          <p>
            Work from this exact saved version. Publishing and delivery always
            require a separate review.
          </p>
        </div>
      </header>
      {!state && !error && <Skeleton label="Loading design lifecycle" />}
      {error && (
        <ErrorState
          title="Design lifecycle unavailable"
          action={
            <Button onClick={() => setReload((value) => value + 1)}>
              Reload
            </Button>
          }
        >
          {error}
        </ErrorState>
      )}
      {state && (
        <>
          <div
            className="panel-toolbar action-cluster"
            role="toolbar"
            aria-label="Design lifecycle views"
          >
            {(
              [
                ['presentation', 'Present'],
                ['export', 'Export'],
                ['sharing', 'Share'],
              ] as const
            ).map(([view, label]) => (
              <Button
                key={view}
                disabled={!availability.get(view)}
                aria-pressed={active === view}
                onClick={() =>
                  setActive((current) => (current === view ? null : view))
                }
              >
                {label}
              </Button>
            ))}
            <Button onClick={() => setReload((value) => value + 1)}>
              Refresh options
            </Button>
          </div>
          <ul
            className="capability-summary"
            aria-label="Lifecycle availability"
          >
            {state.capabilities.map((item) => (
              <li key={item.id}>
                <strong>{item.label}:</strong>{' '}
                {item.state === 'ready'
                  ? 'Ready.'
                  : item.state === 'check_on_use'
                    ? 'Checked when used.'
                    : 'Unavailable.'}{' '}
                {item.detail}
              </li>
            ))}
          </ul>
          {active === 'presentation' && props.renderPresentation(state)}
          {active === 'export' && props.renderExport(state)}
          {active === 'sharing' && props.renderSharing(state)}
        </>
      )}
    </section>
  );
}
