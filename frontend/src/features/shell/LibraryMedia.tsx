import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { LayoutGrid, RefreshCw, Search, Tag } from 'lucide-react';
import { clientError } from '../../api/errors';
import type { OutputView } from '../../api/types';
import { useRuntime } from '../../runtime';
import {
  Button,
  EmptyState,
  ErrorState,
  IconButton,
  Input,
  ListSkeleton,
  Segmented,
} from '../../ui/primitives';
import { absoluteTime } from '../../ui/format';
import { formatBytes } from './MediaPreview';
import { shortTime } from './conversation-groups';
import {
  OUTPUT_ICONS,
  OUTPUT_KIND_WORDS,
  SHOW_OPTIONS,
  outputTitle,
  versionWords,
  type OutputShow,
} from './outputs';

type Kind = '' | OutputView['kind'];

/**
 * A picture's own thumbnail; anything else its kind's icon. A picture is
 * read once it nears the screen: hundreds of them read at once took the
 * server's whole view budget, and the next screen waited behind them.
 */
export function Thumb({ item }: { item: Pick<OutputView, 'ref' | 'kind'> }) {
  const { controller } = useRuntime();
  const [src, setSrc] = useState('');
  const [near, setNear] = useState(false);
  const holder = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const element = holder.current;
    if (item.kind !== 'image' || near || !element) return;
    if (typeof IntersectionObserver === 'undefined') {
      setNear(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setNear(true);
      },
      { rootMargin: '200px' },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [item.kind, near]);
  useEffect(() => {
    if (item.kind !== 'image' || !near) return;
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
  }, [controller, item.kind, item.ref, near]);
  const Icon = OUTPUT_ICONS[item.kind];
  return src ? (
    <img className="media-library-thumb" src={src} alt="" />
  ) : (
    <span
      ref={holder}
      className="media-library-thumb media-library-icon"
      aria-hidden
    >
      <Icon size={22} />
    </span>
  );
}

/** Edit an item's tags in place: comma-separated, saved on Enter or blur. */
function Tags({
  item,
  title,
  onSaved,
}: {
  item: OutputView;
  title: string;
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
          aria-label={`Tags for ${title}`}
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
            ? `Edit tags for ${title}`
            : `Add tags to ${title}`
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

const KIND_OPTIONS: { value: OutputView['kind'] | 'all'; label: string }[] = [
  { value: 'all', label: 'Every kind' },
  { value: 'image', label: 'Pictures' },
  { value: 'video', label: 'Videos' },
  { value: 'audio', label: 'Sound' },
  { value: 'document', label: 'Documents' },
];

const EMPTY: Record<OutputShow, { title: string; body: string }> = {
  made: {
    title: 'Nothing made yet',
    body: 'Finished films, pictures, sounds and documents from your chats show here, to find and use again in any chat or workflow.',
  },
  uploads: {
    title: 'No uploads yet',
    body: 'Files you add to your chats show here.',
  },
  working: {
    title: 'No working files',
    body: 'Recordings, frames, contact sheets, voice lines and drafts Row-Bot makes on the way show here.',
  },
};

/**
 * Library › Media (5.3.1 B3, W3): what the chats made, from the one outputs
 * list the Outputs pane and the phone's Files show too. Finished items by
 * default; Uploads and Working files on request. Search, a kind filter and
 * tags; each opens its chat, and Row-Bot finds them too (media_find).
 */
export default function LibraryMedia() {
  const { controller } = useRuntime();
  const [items, setItems] = useState<OutputView[] | null>(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [show, setShow] = useState<OutputShow>('made');
  const [kind, setKind] = useState<Kind>('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    setError('');
    const timer = window.setTimeout(() => {
      controller
        .outputs(
          { show, query: query.trim(), kind: kind || undefined },
          abort.signal,
        )
        .then(
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
  }, [controller, query, show, kind, attempt]);

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
          label="Show"
          value={show}
          onChange={(value) => {
            setItems(null);
            setShow(value);
          }}
          options={SHOW_OPTIONS}
        />
        <Segmented
          size="sm"
          label="Kind of media"
          value={kind || 'all'}
          onChange={(value) => setKind(value === 'all' ? '' : (value as Kind))}
          options={KIND_OPTIONS.map((option) => {
            const Icon =
              option.value === 'all' ? LayoutGrid : OUTPUT_ICONS[option.value];
            return {
              ...option,
              icon: <Icon size={15} aria-hidden />,
              hideLabel: true,
            };
          })}
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
        <ListSkeleton
          label="Loading media"
          rows={10}
          className="media-library-loading"
        />
      ) : items.length === 0 ? (
        query || kind ? (
          <EmptyState title="Nothing matches">
            Try other words, or another kind.
          </EmptyState>
        ) : (
          <EmptyState title={EMPTY[show].title}>{EMPTY[show].body}</EmptyState>
        )
      ) : (
        <ul className="media-library-list" aria-label="Media items">
          {items.map((item) => {
            const title = outputTitle(item);
            const chat = item.conversation_title || 'a chat';
            return (
              <li
                key={`${item.conversation_id}:${item.id}`}
                className="media-library-row"
              >
                <Link
                  className="media-library-link"
                  to={`/conversations/${encodeURIComponent(item.conversation_id)}`}
                  aria-label={`${title}, in ${chat}`}
                >
                  <Thumb item={item} />
                  <span className="media-library-text">
                    <span className="media-library-name">
                      {title}
                      {item.role === 'draft' && (
                        <span className="media-library-draft">Draft</span>
                      )}
                    </span>
                    <span className="media-library-meta">
                      {[
                        OUTPUT_KIND_WORDS[item.kind],
                        versionWords(item),
                        formatBytes(item.size),
                        `In ${chat}`,
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                  </span>
                </Link>
                <Tags
                  item={item}
                  title={title}
                  onSaved={(tags) =>
                    setItems((list) =>
                      (list ?? []).map((entry) =>
                        entry.id === item.id ? { ...entry, tags } : entry,
                      ),
                    )
                  }
                />
                <time
                  className="library-row-date"
                  dateTime={item.updated_at}
                  title={absoluteTime(item.updated_at)}
                >
                  {shortTime(item.updated_at, now)}
                </time>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
