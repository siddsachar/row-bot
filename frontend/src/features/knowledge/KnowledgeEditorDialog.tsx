import { useEffect, useRef, useSyncExternalStore } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { Button } from '../../ui/primitives';
import KnowledgeEditor, {
  type KnowledgeEditorSession,
} from './KnowledgeEditor';
import KnowledgeRelations from './KnowledgeRelations';
import type { KnowledgeSessions } from './knowledge-sessions';

function EditorDialogBody({
  owner,
  session,
  onMutation,
}: {
  owner: KnowledgeSessions;
  session: KnowledgeEditorSession;
  onMutation?(): void;
}) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const relations = owner.relations();
  const priorRevision = useRef<string | null>(null);
  const revision = state.saved?.entity?.revision ?? null;
  useEffect(() => {
    if (priorRevision.current && revision && priorRevision.current !== revision)
      onMutation?.();
    priorRevision.current = revision;
  }, [revision, onMutation]);
  return (
    <div className="stack">
      <KnowledgeEditor session={session} />
      {state.saved?.entity && !relations && (
        <Button onClick={() => owner.openRelations()}>
          Relations and replacement
        </Button>
      )}
      {relations && <KnowledgeRelations session={relations} />}
    </div>
  );
}

/** Route-independent, single-instance editor overlay backed by the auth-owned session. */
export default function KnowledgeEditorDialog({
  owner,
  onMutation,
}: {
  owner: KnowledgeSessions;
  onMutation?(): void;
}) {
  const state = useSyncExternalStore(owner.subscribe, owner.getSnapshot);
  const selected = owner.selected();
  const returnFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (selected || typeof document === 'undefined') return;
    const remember = (event: FocusEvent) => {
      if (event.target instanceof HTMLElement)
        returnFocus.current = event.target;
    };
    if (document.activeElement instanceof HTMLElement)
      returnFocus.current = document.activeElement;
    document.addEventListener('focusin', remember);
    return () => document.removeEventListener('focusin', remember);
  }, [selected]);
  if (!state.active) return null;
  return (
    <Dialog.Root
      open={Boolean(selected)}
      onOpenChange={(open) => {
        if (!open) {
          const target = returnFocus.current;
          owner.close();
          queueMicrotask(() => target?.focus());
        }
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="overlay-backdrop" />
        <Dialog.Content
          className="dialog knowledge-editor-dialog"
          aria-describedby="knowledge-editor-description"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            returnFocus.current?.focus();
            returnFocus.current = null;
          }}
        >
          <header className="dialog-header">
            <div>
              <Dialog.Title className="dialog-title">
                Edit knowledge
              </Dialog.Title>
              <Dialog.Description
                className="dialog-description"
                id="knowledge-editor-description"
              >
                Update fields, lifecycle, provenance, and reviewed relations.
                Drafts and pending receipts remain available if this dialog
                closes.
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <Button
                iconOnly
                variant="ghost"
                aria-label="Close knowledge editor"
              >
                <X size={20} aria-hidden />
              </Button>
            </Dialog.Close>
          </header>
          <div className="dialog-body">
            {state.error && <p role="status">{state.error}</p>}
            {selected && (
              <EditorDialogBody
                owner={owner}
                session={selected}
                onMutation={onMutation}
              />
            )}
          </div>
          <footer className="dialog-footer">
            <Dialog.Close asChild>
              <Button>Close</Button>
            </Dialog.Close>
          </footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
