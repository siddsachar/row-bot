import { useState } from 'react';
import { Clock, Film, ImageIcon, VolumeX, X } from 'lucide-react';
import type { XPostApproval, XPostEdit } from '../../api/types';
import { Button, IconButton } from '../../ui/primitives';
import { useOverlay } from '../../ui/overlays';
import { formatBytes, openMediaViewer } from './MediaPreview';

type XPost = XPostApproval;

const TARGET: Record<XPost['action'], string> = {
  post: '',
  reply: 'Replying to',
  quote: 'Quoting',
  delete: 'Deleting',
};

/**
 * Exactly what a post, reply, quote or delete on X sends, and as which
 * account (N7): the account, the post it answers or quotes as a link, the
 * full text (never cut) and each file by name, size and the start of its
 * SHA-256. The approval is bound to all of it: any change asks again.
 */
export default function XPostDetails({ post }: { post: XPost }) {
  const text = post.action === 'delete' ? post.target_text : post.text;
  return (
    <div className="x-post-card">
      <p className="x-post-account">
        <span className="x-post-handle">@{post.username}</span>
        {post.name && <span>{post.name}</span>}
        <span className="x-post-id">ID {post.account_id}</span>
      </p>
      {post.target_url && TARGET[post.action] && (
        <p className="x-post-target">
          {TARGET[post.action]}{' '}
          <a href={post.target_url} target="_blank" rel="noopener noreferrer">
            {post.target_url.replace(/^https:\/\//, '')}
          </a>
        </p>
      )}
      {text ? (
        <p className="x-post-text">{text}</p>
      ) : (
        post.action === 'delete' && (
          <p className="x-post-muted">X didn’t say what this post says.</p>
        )
      )}
      <MediaList media={post.media ?? []} />
      {post.post_at && <Slot at={post.post_at} />}
    </div>
  );
}

/** "Goes out at 1:00 PM on Wed 29 Oct": approving schedules it for then (G3). */
export function Slot({ at }: { at: string }) {
  const when = new Date(at);
  if (Number.isNaN(when.getTime())) return null;
  return (
    <p className="x-post-slot">
      <Clock size={14} aria-hidden />
      Goes out at{' '}
      {when.toLocaleTimeString(undefined, {
        hour: 'numeric',
        minute: '2-digit',
      })}{' '}
      on{' '}
      {when.toLocaleDateString(undefined, {
        weekday: 'short',
        day: 'numeric',
        month: 'short',
      })}
    </p>
  );
}

type Media = NonNullable<XPost['media']>[number];

/** A post's file as a small picture (or a film or picture icon), a button
 * that opens it full size when it can be opened. */
export function MediaTile({
  item,
  onOpen,
}: {
  item: Pick<Media, 'name' | 'kind' | 'thumbnail'>;
  onOpen?: () => void;
}) {
  const tile = item.thumbnail ? (
    <img
      src={item.thumbnail}
      alt={onOpen ? '' : `Preview of ${item.name}`}
      width={56}
      height={56}
    />
  ) : (
    <span className="x-post-media-icon" aria-hidden>
      {item.kind === 'video' ? <Film size={18} /> : <ImageIcon size={18} />}
    </span>
  );
  if (!onOpen) return tile;
  return (
    <button
      type="button"
      className="x-post-media-open"
      aria-label={`Open ${item.name}`}
      title={`Open ${item.name}`}
      onClick={onOpen}
    >
      {tile}
    </button>
  );
}

function MediaList({
  media,
  onRemove,
}: {
  media: Media[];
  onRemove?: (item: Media) => void;
}) {
  const overlay = useOverlay();
  if (!media.length) return null;
  return (
    <ul
      className="x-post-media"
      aria-label={
        media.length === 1 ? 'Attached file' : `${media.length} attached files`
      }
    >
      {media.map((item) => (
        <li key={`${item.name}-${item.sha256}`}>
          <MediaTile
            item={item}
            // A file from Library › Media opens full size, to check before it
            // goes; a file on this computer shows only its facts.
            onOpen={
              item.ref && item.mime
                ? () =>
                    openMediaViewer(overlay, {
                      reference: item.ref ?? '',
                      mime: item.mime ?? '',
                      name: item.name,
                      size: item.size,
                    })
                : undefined
            }
          />
          <span className="x-post-media-facts">
            <span className="x-post-media-name">{item.name}</span>
            <span>
              {formatBytes(item.size)} ·{' '}
              <abbr title="The start of the file’s SHA-256 fingerprint">
                SHA-256
              </abbr>{' '}
              <code>{item.sha256}</code>
            </span>
            {item.silent && (
              // A silent video plays silent on X too: said before it's approved.
              <span className="x-post-media-silent">
                <VolumeX size={14} aria-hidden />
                No sound
              </span>
            )}
          </span>
          {onRemove && (
            <IconButton
              size="sm"
              label={`Remove ${item.name}`}
              className="x-post-media-remove"
              onClick={() => onRemove(item)}
            >
              <X size={16} aria-hidden />
            </IconButton>
          )}
        </li>
      ))}
    </ul>
  );
}

const LINK = /https?:\/\/\S+/gi;

/** One character's weight as X counts it: Latin and common punctuation 1, anything else 2. */
function weigh(text: string): number {
  let total = 0;
  for (const character of text.normalize('NFC')) {
    const code = character.codePointAt(0) ?? 0;
    total +=
      code <= 4351 ||
      (code >= 8192 && code <= 8205) ||
      (code >= 8208 && code <= 8223) ||
      (code >= 8242 && code <= 8247)
        ? 1
        : 2;
  }
  return total;
}

/** A post's length as X counts it (G1): each link 23, wide characters 2. */
export function xLength(text: string): number {
  let total = 0;
  let position = 0;
  for (const match of text.matchAll(LINK)) {
    total += weigh(text.slice(position, match.index)) + 23;
    position = (match.index ?? 0) + match[0].length;
  }
  return total + weigh(text.slice(position));
}

/**
 * Edit a post on its card (G2): its text and which files go with it. Saving
 * approves nothing: Row-Bot asks again with the edited post, on a new card.
 */
export function XPostEditor({
  post,
  busy,
  onSave,
  onCancel,
}: {
  post: XPost;
  busy: boolean;
  onSave: (edit: XPostEdit) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState(post.text ?? '');
  const [media, setMedia] = useState(post.media ?? []);
  const length = xLength(text);
  const limit = post.limit ?? 0;
  const over = limit > 0 && length > limit;
  const empty = !text.trim() && (post.action !== 'post' || !media.length);
  const unchanged =
    text === (post.text ?? '') && media.length === (post.media ?? []).length;
  const ready = !busy && !over && !empty && !unchanged;
  const save = () => {
    if (ready) onSave({ text, media: media.map((item) => item.sha256) });
  };
  return (
    <div className="x-post-card x-post-edit">
      <p className="x-post-account">
        <span className="x-post-handle">@{post.username}</span>
        {post.name && <span>{post.name}</span>}
      </p>
      <textarea
        className="input x-post-edit-text"
        aria-label={`Edit the post as @${post.username}`}
        aria-describedby="x-post-edit-count"
        maxLength={25000}
        rows={5}
        value={text}
        disabled={busy}
        autoFocus
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onCancel();
          if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            save();
          }
        }}
      />
      <small
        id="x-post-edit-count"
        className="x-post-edit-count"
        data-over={over || undefined}
      >
        {limit
          ? over
            ? `${(length - limit).toLocaleString()} over the ${limit.toLocaleString()} @${post.username} can post`
            : `${length.toLocaleString()} of ${limit.toLocaleString()}`
          : `${length.toLocaleString()} characters as X counts them`}
      </small>
      <MediaList
        media={media}
        onRemove={(item) =>
          setMedia((items) => items.filter((kept) => kept !== item))
        }
      />
      {post.post_at && <Slot at={post.post_at} />}
      <div className="x-post-edit-actions">
        <Button variant="ghost" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="primary" disabled={!ready} onClick={save}>
          Save edits
        </Button>
      </div>
    </div>
  );
}
