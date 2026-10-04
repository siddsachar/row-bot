import type { ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import Advanced from './Advanced';
import ItemPage from './ItemPage';
import Library from './Library';

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
  let id = item;
  try {
    // The router decodes once; an id that still carries escapes is decoded here.
    if (/%[0-9A-F]{2}/i.test(item)) id = decodeURIComponent(item);
  } catch {
    id = item;
  }
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
        : `/settings/${kind === 'app' ? 'apps' : 'skills'}/${encodeURIComponent(id)}`;
    return (
      <section className="stack" aria-label="Advanced settings">
        <Link className="settings-link app-back" to={back}>
          <ArrowLeft size={14} aria-hidden /> Back
        </Link>
        {editor(kind, id)}
      </section>
    );
  }
  return <ItemPage kind={kind} param={id} />;
}
