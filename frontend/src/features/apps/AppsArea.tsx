import type { ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import Advanced from './Advanced';
import ItemPage from './ItemPage';
import Library from './Library';
import { idPath } from './parts';

/**
 * Settings › Apps and Settings › Skills: the library, one item's page, or the
 * advanced editor scoped to that one item (`?edit=1`, `skills/new`).
 */
export default function AppsArea({
  kind,
  item,
  editor,
  chat,
}: {
  kind: 'app' | 'skill';
  item: string;
  editor: (kind: 'app' | 'skill', id: string) => ReactNode;
  chat: ReactNode;
}) {
  const [search] = useSearchParams();
  // Item ids travel as `item?id=`; app ids, skill names, `new` and `custom` as the path.
  const id = item === 'item' ? (search.get('id') ?? '') : item;
  if (!id)
    return kind === 'app' && search.get('view') === 'advanced' ? (
      <Advanced chat={chat} />
    ) : (
      <Library kind={kind} />
    );
  if (
    search.get('edit') ||
    (kind === 'skill' && id === 'new') ||
    id === 'custom'
  ) {
    const back =
      id === 'new' || id === 'custom'
        ? `/settings/${kind === 'app' ? 'apps?view=advanced' : 'skills'}`
        : idPath(
            kind,
            kind === 'skill' && !id.includes(':') ? `skill:${id}` : id,
          );
    return (
      <section className="stack" aria-label="Advanced settings">
        <Link className="settings-link app-back" to={back}>
          <ArrowLeft size={14} aria-hidden /> Back
        </Link>
        {editor(kind, id)}
      </section>
    );
  }
  return <ItemPage key={`${kind}:${id}`} kind={kind} param={id} />;
}
