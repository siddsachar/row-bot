import { Link, useSearchParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { InlineEmpty, Tabs } from '../../ui/primitives';
import { request } from '../api';
import { useDirectory } from '../directory';
import { Loaded, useLoad } from '../load';
import { Page } from '../page';
import type { SpaceList } from '../types';
import { Documents } from './knowledge/Documents';
import { Entities } from './knowledge/Entities';
import { Facts } from './knowledge/Facts';
import { ModelNotices } from './knowledge/parts';
import { Spaces } from './knowledge/Spaces';
import { spaceSummary } from './knowledge/words';

const TABS = ['documents', 'facts', 'graph'];

/** One space: its documents, facts and knowledge graph, each a tab. */
function SpaceView({ id }: { id: string }) {
  const { nameOf } = useDirectory();
  const [params, setParams] = useSearchParams();
  const tab = TABS.includes(params.get('tab') ?? '')
    ? (params.get('tab') as string)
    : 'documents';
  const spaces = useLoad(() => request<SpaceList>('/api/admin/brain/spaces'));
  return (
    <>
      <Link className="button ghost console-back" to="/knowledge">
        <ArrowLeft size={16} aria-hidden />
        All spaces
      </Link>
      <Loaded load={spaces} label="Loading the space" what="the space">
        {({ spaces: list, max_upload_bytes: maxBytes }) => {
          const space = list.find((item) => item.id === id);
          if (!space)
            return (
              <InlineEmpty>That space is no longer on the server.</InlineEmpty>
            );
          return (
            <>
              <header className="console-section-header">
                <h2>{space.name}</h2>
                <p>{spaceSummary(space, nameOf)}</p>
              </header>
              <Tabs
                label={space.name}
                value={tab}
                onChange={(value) =>
                  setParams({ space: id, tab: value }, { replace: true })
                }
                items={[
                  {
                    id: 'documents',
                    label: 'Documents',
                    content: <Documents space={space} maxBytes={maxBytes} />,
                  },
                  {
                    id: 'facts',
                    label: 'Facts',
                    content: <Facts space={space} />,
                  },
                  {
                    id: 'graph',
                    label: 'Knowledge graph',
                    content: (
                      <Entities
                        space={space}
                        onSpaceChange={(changed) =>
                          spaces.update((data) => ({
                            ...data,
                            spaces: data.spaces.map((item) =>
                              item.id === changed.id ? changed : item,
                            ),
                          }))
                        }
                      />
                    ),
                  },
                ]}
              />
            </>
          );
        }}
      </Loaded>
    </>
  );
}

/**
 * The company brain (admins): spaces and who reads and owns them, then in a
 * space its documents, facts and the knowledge Row-Bot found in them.
 */
export default function KnowledgePage() {
  const [params] = useSearchParams();
  const space = params.get('space');
  return (
    <Page
      title="Knowledge"
      description="The company brain: spaces of company documents and facts, who reads them, and what Row-Bot finds in them."
    >
      <ModelNotices />
      {space ? <SpaceView key={space} id={space} /> : <Spaces />}
    </Page>
  );
}
