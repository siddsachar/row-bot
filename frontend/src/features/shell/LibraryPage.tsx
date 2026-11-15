import { useSearchParams } from 'react-router-dom';
import { Clapperboard, LayoutTemplate, MessagesSquare } from 'lucide-react';
import { Segmented } from '../../ui/primitives';
import LibraryPages from '../pages/LibraryPages';
import ConversationLibrary from './ConversationLibrary';
import LibraryMedia from './LibraryMedia';

type Shown = 'conversations' | 'pages' | 'media';

/**
 * Every conversation in one place: one search for titles and messages, a
 * type filter, and bulk actions. The sidebar and ⌘K cover quick switching.
 * Pages lists every page Row-Bot made, with its chat (N5). Media holds every
 * picture, video and sound from the chats (5.3.1 B3).
 */
export default function LibraryPage() {
  const [params, setParams] = useSearchParams();
  const show = params.get('show');
  const shown: Shown =
    show === 'pages' || show === 'media' ? show : 'conversations';
  return (
    <div className="library-page">
      <header className="library-header">
        <h1>Library</h1>
        <Segmented
          size="sm"
          label="Show in the library"
          value={shown}
          onChange={(value) =>
            setParams(value === 'conversations' ? {} : { show: value }, {
              replace: true,
            })
          }
          options={[
            {
              value: 'conversations',
              label: 'Conversations',
              icon: <MessagesSquare size={15} aria-hidden />,
            },
            {
              value: 'pages',
              label: 'Pages',
              icon: <LayoutTemplate size={15} aria-hidden />,
            },
            {
              value: 'media',
              label: 'Media',
              icon: <Clapperboard size={15} aria-hidden />,
            },
          ]}
        />
      </header>
      {shown === 'pages' ? (
        <LibraryPages />
      ) : shown === 'media' ? (
        <LibraryMedia />
      ) : (
        <ConversationLibrary />
      )}
    </div>
  );
}
