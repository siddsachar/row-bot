import { useState } from 'react';
import { createPortal } from 'react-dom';
import { useSearchParams } from 'react-router-dom';
import {
  Clapperboard,
  LayoutTemplate,
  ListChecks,
  MessagesSquare,
  MoreHorizontal,
  RefreshCw,
  SquarePen,
} from 'lucide-react';
import { usePhone } from '../../ui/on-your-computer';
import { IconButton, Menu, Segmented } from '../../ui/primitives';
import LibraryPages from '../pages/LibraryPages';
import ConversationLibrary from './ConversationLibrary';
import LibraryFiles from './LibraryFiles';
import LibraryMedia from './LibraryMedia';
import { useWorkspaceActions } from './workspace-actions';
import '../../ui/styles/phone-chats.css';

type Shown = 'conversations' | 'pages' | 'media';

/**
 * Every conversation in one place: one search for titles and messages, a
 * type filter, and bulk actions. The sidebar and ⌘K cover quick switching.
 * Pages lists every page Row-Bot made, with its chat (N5). Media holds every
 * picture, video and sound from the chats (5.3.1 B3). A phone shows Chats and
 * Files instead (D10).
 */
export default function LibraryPage() {
  const [params, setParams] = useSearchParams();
  const phone = usePhone();
  if (phone) return <PhoneChats />;
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

/**
 * The phone's Chats tab (D10 A): Chats | Files, with New chat and ⋯ (Select,
 * Refresh) in the header. Library links to Pages and Media open Files.
 */
function PhoneChats() {
  const [params, setParams] = useSearchParams();
  const actions = useWorkspaceActions();
  const [selecting, setSelecting] = useState(false);
  // Refresh reads the list again by mounting it afresh.
  const [reads, setReads] = useState(0);
  const show = params.get('show');
  const files = show === 'files' || show === 'pages' || show === 'media';
  const slot = actions?.phoneHeaderSlot;
  return (
    <div className="library-page phone-chats">
      <Segmented
        label="Show"
        className="phone-chats-switch"
        value={files ? 'files' : 'chats'}
        onChange={(value) => {
          setSelecting(false);
          setParams(value === 'files' ? { show: 'files' } : {}, {
            replace: true,
          });
        }}
        options={[
          { value: 'chats', label: 'Chats' },
          { value: 'files', label: 'Files' },
        ]}
      />
      {files ? (
        <LibraryFiles
          key={reads}
          initialKind={
            show === 'pages' ? 'page' : show === 'media' ? 'media' : 'all'
          }
        />
      ) : (
        <ConversationLibrary
          key={reads}
          selecting={selecting}
          onSelectingChange={setSelecting}
        />
      )}
      {slot &&
        createPortal(
          <>
            <IconButton
              size="sm"
              label="New chat"
              onClick={() => actions?.newChat?.()}
            >
              <SquarePen size={20} aria-hidden />
            </IconButton>
            <Menu
              label="More for Chats"
              iconOnly
              variant="ghost"
              className="icon-action icon-action-sm"
              actions={[
                ...(files
                  ? []
                  : [
                      {
                        label: 'Select',
                        icon: <ListChecks size={16} />,
                        onSelect: () => setSelecting(true),
                      },
                    ]),
                {
                  label: 'Refresh',
                  icon: <RefreshCw size={16} />,
                  onSelect: () => setReads((count) => count + 1),
                },
              ]}
            >
              <MoreHorizontal size={20} aria-hidden />
            </Menu>
          </>,
          slot,
        )}
    </div>
  );
}
