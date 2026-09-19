import { useEffect, useRef, useSyncExternalStore } from 'react';
import { Button } from '../../ui/primitives';
import KnowledgeEditor from './KnowledgeEditor';
import KnowledgeRelations from './KnowledgeRelations';
import type { KnowledgeEditorSession } from './KnowledgeEditor';
import type { KnowledgeSessions } from './knowledge-sessions';

function EditorTab({
  session,
  index,
  selected,
  onSelect,
}: {
  session: KnowledgeEditorSession;
  index: number;
  selected: boolean;
  onSelect(): void;
}) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  return (
    <Button aria-pressed={selected} onClick={onSelect}>
      {state.draft.subject || `Knowledge draft ${index + 1}`}
    </Button>
  );
}

function EditorWithRelations({
  owner,
  session,
}: {
  owner: KnowledgeSessions;
  session: KnowledgeEditorSession;
}) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const relations = owner.relations();
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const heading = container.current?.querySelector('h2');
    if (heading) {
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
      heading.scrollIntoView?.({ block: 'start' });
    }
  }, [session]);
  return (
    <div className="stack" ref={container}>
      <KnowledgeEditor session={session} />
      {state.saved?.entity && !relations && (
        <Button onClick={() => owner.openRelations()}>
          Relations and replacement
        </Button>
      )}
      {relations && <KnowledgeRelations session={relations} />}
      <Button onClick={() => owner.close()}>Close knowledge editor</Button>
    </div>
  );
}

export default function KnowledgeEditors({
  owner,
}: {
  owner: KnowledgeSessions;
}) {
  const state = useSyncExternalStore(owner.subscribe, owner.getSnapshot);
  const selected = owner.selected();
  if (!state.active) return null;
  return (
    <section aria-label="Knowledge editing" className="stack">
      <div className="actions">
        <Button onClick={() => owner.open(null)}>Create knowledge</Button>
        {owner.entries().map(([key, session], index) => (
          <EditorTab
            key={key}
            session={session}
            index={index}
            selected={key === state.selected}
            onSelect={() => owner.select(key)}
          />
        ))}
      </div>
      {state.error && <p role="status">{state.error}</p>}
      {selected && <EditorWithRelations owner={owner} session={selected} />}
    </section>
  );
}
