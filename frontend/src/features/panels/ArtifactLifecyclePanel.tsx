import { useEffect, useRef, useState } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { Info } from 'lucide-react';
import { Button, IconButton, StatusDot } from '../../ui/primitives';

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

export type LifecycleView = 'presentation' | 'export' | 'sharing';

export type ArtifactLifecyclePanelProps = {
  resourceId: string;
  resourceRevision: string;
  visible: boolean;
  load: (
    resourceId: string,
    resourceRevision: string,
    signal: AbortSignal,
  ) => Promise<ArtifactLifecycleState>;
};

const groups: Record<LifecycleView, string[]> = {
  presentation: ['presentation', 'thumbnails'],
  export: ['export.html', 'export.pdf', 'export.png', 'export.pptx'],
  sharing: ['publish.local', 'publish.remote', 'share.channel', 'share.x'],
};

export type DesignLifecycle = {
  state: ArtifactLifecycleState | null;
  error: string;
  /** Whether any operation of a view can run; true while unknown. */
  available: (view: LifecycleView) => boolean;
  retry: () => void;
};

/**
 * Reads what the saved design can do (present, export, share) once per saved
 * revision while the panel shows it. Nothing polls; Retry re-reads.
 */
export function useDesignLifecycle({
  load,
  resourceId,
  resourceRevision,
  visible,
}: ArtifactLifecyclePanelProps): DesignLifecycle {
  const [state, setState] = useState<ArtifactLifecycleState | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    request.current?.abort();
    setState(null);
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
            ? 'The saved design changed while its options were read.'
            : 'What this design can do could not be read.',
        );
      });
    return () => controller.abort();
  }, [load, reload, resourceId, resourceRevision, visible]);
  const current =
    state?.resource_id === resourceId &&
    state.resource_revision === resourceRevision
      ? state
      : null;
  return {
    state: current,
    error,
    available: (view) =>
      !current ||
      current.capabilities.some(
        (item) =>
          groups[view].includes(item.id) && item.state !== 'unavailable',
      ),
    retry: () => setReload((value) => value + 1),
  };
}

const stateWords: Record<
  ArtifactLifecycleCapability['state'],
  { text: string; tone: 'success' | 'neutral' | 'warning' }
> = {
  ready: { text: 'Ready', tone: 'success' },
  check_on_use: { text: 'Checked when used', tone: 'neutral' },
  unavailable: { text: 'Unavailable', tone: 'warning' },
};

/** ⓘ in the design top bar: capabilities and review requirements. */
export function DesignCapabilities({
  lifecycle,
}: {
  lifecycle: DesignLifecycle;
}) {
  const { state, error } = lifecycle;
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <IconButton size="sm" label="Design capabilities">
          <Info size={15} aria-hidden />
        </IconButton>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          className="popover design-capabilities"
          aria-label="Design capabilities"
          align="end"
          sideOffset={8}
          collisionPadding={12}
        >
          <p className="design-capabilities-title">
            Capabilities and review requirements
          </p>
          {error ? (
            <div className="design-capabilities-error">
              <p>{error}</p>
              <Button onClick={lifecycle.retry}>Retry</Button>
            </div>
          ) : !state ? (
            <p className="muted">Reading what this design can do…</p>
          ) : (
            <ul
              className="design-capabilities-list"
              aria-label="Lifecycle availability"
            >
              {state.capabilities.map((item) => {
                const words = stateWords[item.state];
                return (
                  <li key={item.id}>
                    <StatusDot tone={words.tone} label={words.text} />
                    <span className="design-capabilities-name">
                      {item.label}
                    </span>
                    <span className="design-capabilities-state">
                      {words.text}
                      {item.review_required ? ' · Review required' : ''}
                    </span>
                    {item.detail && (
                      <span className="design-capabilities-detail">
                        {item.detail}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          <p className="design-capabilities-note">
            Publishing and delivery require a separate review. Exports and
            downloads stay on this device.
          </p>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
