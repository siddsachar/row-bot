import { useEffect, useSyncExternalStore } from 'react';
import { Button, ErrorState } from '../../ui/primitives';
import ArtifactDesignControls from './ArtifactDesignControls';
import type { ArtifactDesignSession } from './artifact-design-sessions';

export type ArtifactDesignPanelProps = {
  session: ArtifactDesignSession;
  resourceRevision: string;
  pageId: string;
  selectedElementId?: string;
  onSelectElement: (elementId: string) => void;
  visible: boolean;
  onDraftText: (text: string) => void;
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
    <section
      className="studio-section stack"
      aria-label="Artifact design panel"
    >
      {state.notice && <p role="status">{state.notice}</p>}
      {attempt && (
        <div
          className="capability-section stack"
          role="group"
          aria-label="Original design command"
        >
          <p>
            {['preparing', 'pending'].includes(attempt.status)
              ? 'The original design command is in progress.'
              : 'The original design command is not confirmed. New changes are paused to protect the saved design.'}
          </p>
          <p>Command {attempt.commandId}</p>
          <p>
            Recovery checks this exact command. Unconfirmed saved effects are
            not automatically repeated.
          </p>
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
        onSelectElement={props.onSelectElement}
        onDraftText={(text) => {
          session.guard();
          props.onDraftText(text);
        }}
      />
    </section>
  );
}
