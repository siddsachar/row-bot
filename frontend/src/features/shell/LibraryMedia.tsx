import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { Film, Image, Music, RefreshCw, Search, Tag } from 'lucide-react';
import { clientError } from '../../api/errors';
import type { MediaItem } from '../../api/types';
import { useRuntime } from '../../runtime';
import {
  Button,
  EmptyState,
  ErrorState,
  IconButton,
  Input,
  Segmented,
  Skeleton,
} from '../../ui/primitives';
import { absoluteTime } from '../../ui/format';
import { formatBytes } from './MediaPreview';
import { shortTime } from './conversation-groups';

type Kind = '' | MediaItem['kind'];

const ICONS = { image: Image, video: Film, audio: Music } as const;

/** A picture's own thumbnail; a video or a sound its kind's icon. */
function Thumb({ item }: { item: MediaItem }) {
  const { controller } = useRuntime();
  const [src, setSrc] = useState('');
  useEffect(() => {
    if (item.kind !== 'image') return;
    const abort = new AbortController();
    let url = '';
    controller.attachmentThumbnail(item.ref, abort.signal).then(
      (blob) => {
        if (abort.signal.aborted || !blob) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      },
      () => undefined,
    );
    return () => {
      abort.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [controller, item.kind, item.ref]);
  const Icon = ICONS[item.kind];
  return src ? (
    <img className="media-library-thumb" src={src} alt="" />
  ) : (
    <span className="media-library-thumb media-library-icon" aria-hidden>
      <Icon size={22} />
    </span>
  );
}

/** Edit an item's tags in place: comma-separated, saved on Enter or blur. */
function Tags({
  item,
  onSaved,
}: {
  item: MediaItem;
  onSaved: (tags: string[]) => void;
}) {
  const { controller } = useRuntime();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState((item.tags ?? []).join(', '));
  const [error, setError] = useState('');
  async function save(event?: FormEvent) {
    event?.preventDefault();
    const tags = text
      .split(',')
      .map((tag) => tag.trim())
      .filter(Boolean);
    try {
      const saved = await controller.setMediaTags(item.ref, tags);
      onSaved(saved?.tags ?? tags);
      setEditing(false);
      setError('');
    } catch (cause) {
      setError(clientError(cause).message);
    }
  }
  if (editing)
    return (
      <form className="media-library-tags-edit" onSubmit={save}>
        <Input
          aria-label={`Tags for ${item.name}`}
          value={text}
          autoFocus
          placeholder="x post, demo"
          onChange={(event) => setText(event.target.value)}
          onBlur={() => void save()}
          onKeyDown={(event) => {
            if (event.key === 'Escape') setEditing(false);
          }}
        />
        {error && (
          <span role="alert" className="media-library-error">
            {error}
          </span>
        )}
      </form>
    );
  return (
    <span className="media-library-tags">
      {(item.tags ?? []).map((tag) => (
        <span key={tag} className="media-library-tag">
          {tag}
        </span>
      ))}
      <IconButton
        size="sm"
        label={
          (item.tags ?? []).length
            ? `Edit tags for ${item.name}`
            : `Add tags to ${item.name}`
        }
        onClick={() => {
          setText((item.tags ?? []).join(', '));
          setEditing(true);
        }}
      >
        <Tag size={14} aria-hidden />
      </IconButton>
    </span>
  );
}

/**
 * Library › Media (5.3.1 B3): every picture, video and sound in the chats,
 * newest first, with search, a kind filter and tags. Each opens its chat,
 * and Row-Bot finds them too (media_find), so a workflow can reuse them.
 */
export default function LibraryMedia() {
  const { controller } = useRuntime();
  const [items, setItems] = useState<MediaItem[] | null>(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<Kind>('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    setError('');
    const timer = window.setTimeout(() => {
      controller.media({ query: query.trim(), kind }, abort.signal).then(
        (page) => {
          if (!abort.signal.aborted) setItems(page?.items ?? []);
        },
        (cause) => {
          if (!abort.signal.aborted) setError(clientError(cause).message);
        },
      );
    }, 200);
    return () => {
      abort.abort();
      window.clearTimeout(timer);
    };
  }, [controller, query, kind, attempt]);

  const now = new Date();
  return (
    <section className="media-library" aria-label="Media">
      <div className="media-library-bar">
        <label className="media-library-search">
          <Search size={15} aria-hidden />
          <Input
            type="search"
            aria-label="Search media"
            placeholder="Search names, tags and chats"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <Segmented
          size="sm"
          label="Kind of media"
          value={kind || 'all'}
          onChange={(value) => setKind(value === 'all' ? '' : (value as Kind))}
          options={[
            { value: 'all', label: 'All' },
            { value: 'image', label: 'Pictures' },
            { value: 'video', label: 'Videos' },
            { value: 'audio', label: 'Sound' },
          ]}
        />
        <IconButton
          label="Refresh media"
          onClick={() => setAttempt((n) => n + 1)}
        >
          <RefreshCw size={16} aria-hidden />
        </IconButton>
      </div>
      {error ? (
        <ErrorState
          title="Media didn't load"
          action={
            <Button onClick={() => setAttempt((n) => n + 1)}>Try again</Button>
          }
        >
          {error}
        </ErrorState>
      ) : !items ? (
        <Skeleton label="Loading media" />
      ) : items.length === 0 ? (
        query || kind ? (
          <EmptyState title="Nothing matches">
            Try other words, or another kind.
          </EmptyState>
        ) : (
          <EmptyState title="No media yet">
            Recordings, video edits, voice lines, music and pictures from your
            chats show here, to find and use again in any chat or workflow.
          </EmptyState>
        )
      ) : (
        <ul className="media-library-list" aria-label="Media items">
          {items.map((item) => (
            <li key={item.ref} className="media-library-row">
              <Link
                className="media-library-link"
                to={`/conversations/${encodeURIComponent(item.conversation_id)}`}
                aria-label={`${item.name}, in ${item.conversation_title || 'a chat'}`}
              >
                <Thumb item={item} />
                <span className="media-library-text">
                  <span className="media-library-name">{item.name}</span>
                  <span className="media-library-meta">
                    {formatBytes(item.size)} · In{' '}
                    {item.conversation_title || 'a chat'}
                  </span>
                </span>
              </Link>
              <Tags
                item={item}
                onSaved={(tags) =>
                  setItems((list) =>
                    (list ?? []).map((entry) =>
                      entry.ref === item.ref ? { ...entry, tags } : entry,
                    ),
                  )
                }
              />
              <time
                className="library-row-date"
                dateTime={item.added_at}
                title={absoluteTime(item.added_at)}
              >
                {shortTime(item.added_at, now)}
              </time>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
