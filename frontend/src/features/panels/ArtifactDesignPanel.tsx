import { useEffect, useSyncExternalStore } from 'react';
import { Button, ErrorState } from '../../ui/primitives';
import ArtifactDesignControls, {
  type DesignControlsView,
} from './ArtifactDesignControls';
import type { ArtifactDesignSession } from './artifact-design-sessions';

export type ArtifactDesignPanelProps = {
  session: ArtifactDesignSession;
  resourceRevision: string;
  pageId: string;
  selectedElementId?: string;
  onSelectElement: (elementId: string) => void;
  visible: boolean;
  onDraftText: (text: string) => void;
  /** Which inspector section to show; all of them when omitted. */
  view?: DesignControlsView;
};

export default function ArtifactDesignPanel(props: ArtifactDesignPanelProps) {
  const { session, visible } = props;
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  useEffect(() => {
    if (!visible) session.hide();
    return () => session.hide();
  }, [session, visible]);
  if (!visible) return null;
  if (state.revoked)
    return (
      <ErrorState title="Design access changed">
        This retained draft is unavailable in the current binding.
      </ErrorState>
    );
  const attempt = state.attempt;
  return (
    <div className="design-controls-host">
      {state.notice && (
        <p className="inspector-status" role="status">
          {state.notice}
        </p>
      )}
      {attempt && (
        <div
          className="inspector-callout"
          role="group"
          aria-label="Original design command"
        >
          <p>
            {['preparing', 'pending'].includes(attempt.status)
              ? 'The original design change is still in progress.'
              : 'The original design change is not confirmed. New changes wait so the saved design stays safe.'}{' '}
            Checking looks up this exact change; it is never repeated
            automatically.
          </p>
          <p className="muted">Change {attempt.commandId}</p>
          <div className="action-cluster">
            <Button
              disabled={
                state.recovering ||
                ['preparing', 'pending'].includes(attempt.status)
              }
              onClick={() => {
                void session.recover().catch(() => undefined);
              }}
            >
              Check original design command
            </Button>
            {attempt.status === 'rejected' && (
              <Button onClick={session.dismissRejection}>
                Dismiss rejected command
              </Button>
            )}
          </div>
        </div>
      )}
      <ArtifactDesignControls
        view={props.view}
        resourceId={session.scope.resource_id}
        resourceRevision={props.resourceRevision}
        pageId={props.pageId}
        selectedElementId={props.selectedElementId}
        visible={visible}
        session={session.form}
        blocked={Boolean(attempt)}
        load={session.load}
        review={session.review}
        apply={session.apply}
        upload={session.upload}
        mutatePreset={session.mutatePreset}
        draftFix={session.draftFix}
        suggestBrand={session.suggestBrand}
        onSelectElement={props.onSelectElement}
        onDraftText={(text) => {
          session.guard();
          props.onDraftText(text);
        }}
      />
    </div>
  );
}
