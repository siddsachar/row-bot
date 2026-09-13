import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import type {
  WorkspaceEditableFile,
  WorkspaceEditResult,
} from '../../api/types';
import { Button, ErrorState, Field, Skeleton } from '../../ui/primitives';
import { WorkspaceEditSession } from './workspace-edit-sessions';

export type WorkspaceFileEditorProps = {
  path: string;
  visible: boolean;
  load: (path: string, signal?: AbortSignal) => Promise<WorkspaceEditableFile>;
  save: (
    snapshot: WorkspaceEditableFile,
    content: string,
  ) => Promise<WorkspaceEditResult>;
  close: () => void;
  discard: () => void;
  session?: WorkspaceEditSession;
};

export default function WorkspaceFileEditor(props: WorkspaceFileEditorProps) {
  const callbacks = useRef(props);
  callbacks.current = props;
  const local = useMemo(
    () =>
      new WorkspaceEditSession(props.path, {
        load: (...args) => callbacks.current.load(...args),
        save: (...args) => callbacks.current.save(...args),
      }),
    [props.path],
  );
  const session = props.session ?? local;
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  useEffect(() => () => local.dispose(), [local]);
  useEffect(() => {
    if (props.visible) void session.load();
  }, [session, props.visible]);
  if (!props.visible) return null;
  if (!state.accessible)
    return (
      <ErrorState title="Workspace access changed">
        This retained edit is unavailable in the current binding.
      </ErrorState>
    );
  const { snapshot, draft, busy, loading, uncertain, error, notice, stale } =
    state;
  const editable = snapshot && ['text', 'missing'].includes(snapshot.status);
  return (
    <section
      className="stack"
      aria-label="Workspace file editor"
      aria-busy={busy || loading}
    >
      <h3>Edit {state.path}</h3>
      <p>
        {snapshot?.target === 'sandbox_shadow'
          ? 'Prepared sandbox file'
          : 'Workspace file'}{' '}
        · saves use the reviewed file revision.
      </p>
      {loading && <Skeleton label="Loading complete file" />}
      {error && (
        <ErrorState title="File edit requires attention">{error}</ErrorState>
      )}
      {stale && (
        <p role="status">
          The workspace revision changed. Your draft is retained.{' '}
          {uncertain
            ? 'Check the original save receipt before refreshing.'
            : 'Refresh the file revision before saving.'}
        </p>
      )}
      {snapshot && !editable && (
        <p role="status">
          Inline editing unavailable: {snapshot.code || snapshot.status}. The
          original file is retained.
        </p>
      )}
      {editable && (
        <Field label="File contents">
          <textarea
            className="input code-sample"
            rows={12}
            maxLength={204800}
            value={draft ?? snapshot.content ?? ''}
            disabled={busy || loading || uncertain || stale}
            onChange={(event) => session.setDraft(event.target.value)}
          />
        </Field>
      )}
      {notice && <p role="status">{notice}</p>}
      <div className="actions">
        <Button
          disabled={!editable || busy || loading || (stale && !uncertain)}
          onClick={() => void session.commit()}
        >
          {uncertain ? 'Retry original save' : 'Save file'}
        </Button>
        <Button
          disabled={busy || uncertain}
          onClick={() => void session.load(true)}
        >
          Refresh file revision
        </Button>
        <Button onClick={props.close}>Close editor and keep draft</Button>
        <Button
          disabled={busy || uncertain}
          onClick={() => {
            if (session.canDiscard()) props.discard();
          }}
        >
          Discard draft
        </Button>
      </div>
    </section>
  );
}
