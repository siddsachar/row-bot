import { useEffect, useRef, useState } from 'react';
import {
  File as FileIcon,
  FileAudio,
  FileImage,
  FileText,
  FileVideo,
  RotateCcw,
  TriangleAlert,
  X,
  type LucideIcon,
} from 'lucide-react';
import { clientError } from '../../api/errors';
import type { AttachmentView } from '../../api/types';
import { useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Hint, IconButton } from '../../ui/primitives';
import { fileKind, formatBytes, MediaPreview } from './MediaPreview';

/** A file on its way to the draft: shown as a tile until it is uploaded. */
export type AttachmentUpload = {
  key: string;
  conversation: string;
  file: File;
  sent: number;
  error?: string;
};

/**
 * Uploads picked, dropped or pasted files one after another, keeping each
 * one's progress and failure for its tile. An uploaded file joins the
 * conversation's draft; removing a tile cancels its upload.
 */
export function useAttachmentUploads() {
  const { controller } = useRuntime();
  const [uploads, setUploads] = useState<AttachmentUpload[]>([]);
  const cancels = useRef(new Map<string, AbortController>());
  const change = (key: string, patch: Partial<AttachmentUpload>) =>
    setUploads((all) =>
      all.map((item) => (item.key === key ? { ...item, ...patch } : item)),
    );
  async function run(item: AttachmentUpload) {
    const cancel = cancels.current.get(item.key);
    if (!cancel || cancel.signal.aborted) return;
    try {
      const uploaded = await controller.upload(
        item.conversation,
        item.file,
        cancel.signal,
        (sent) => change(item.key, { sent }),
      );
      const previous = controller.getDraft(item.conversation);
      controller.setDraft(item.conversation, {
        ...previous,
        attachments: [...previous.attachments, uploaded],
      });
      cancels.current.delete(item.key);
      setUploads((all) => all.filter((entry) => entry.key !== item.key));
    } catch (cause) {
      if (!cancel.signal.aborted)
        change(item.key, { sent: 0, error: clientError(cause).message });
    }
  }
  async function add(conversation: string, files: readonly File[]) {
    const items = files.map((file) => ({
      key: crypto.randomUUID(),
      conversation,
      file,
      sent: 0,
    }));
    for (const item of items)
      cancels.current.set(item.key, new AbortController());
    setUploads((all) => [...all, ...items]);
    for (const item of items) await run(item);
  }
  async function retry(item: AttachmentUpload) {
    cancels.current.set(item.key, new AbortController());
    change(item.key, { sent: 0, error: undefined });
    await run(item);
  }
  function dismiss(key: string) {
    cancels.current.get(key)?.abort();
    cancels.current.delete(key);
    setUploads((all) => all.filter((item) => item.key !== key));
  }
  return { uploads, add, retry, dismiss };
}

/** Only these become pictures: the server keeps other images as files. */
const PICTURES = new Set(['image/png', 'image/jpeg']);
const pictured = (mime: string) =>
  PICTURES.has(mime.split(';', 1)[0].trim().toLowerCase());

function typeIcon(mime: string): LucideIcon {
  const type = mime.split(';', 1)[0].trim().toLowerCase();
  if (type.startsWith('image/')) return FileImage;
  if (type.startsWith('audio/')) return FileAudio;
  if (type.startsWith('video/')) return FileVideo;
  if (type === 'application/pdf' || type.startsWith('text/')) return FileText;
  return FileIcon;
}

/** "a-very-long-report-na….pdf": the start and the extension stay. */
export function shortName(name: string, max = 24) {
  if (name.length <= max) return name;
  const dot = name.lastIndexOf('.');
  const extension = dot > 0 && name.length - dot <= 8 ? name.slice(dot) : '';
  return `${name.slice(0, max - extension.length - 1).trimEnd()}…${extension}`;
}

/**
 * The tile's picture: the picked file itself while it uploads, then the
 * server's small thumbnail, so a large photo is never downloaded for a tile.
 * Object URLs are revoked when the tile goes.
 */
function usePicture(mime: string, file?: File, reference?: string) {
  const { controller } = useRuntime();
  const wanted = pictured(mime);
  const [url, setUrl] = useState('');
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!wanted) return;
    let owned = '';
    if (file) {
      owned = URL.createObjectURL(file);
      setUrl(owned);
      return () => URL.revokeObjectURL(owned);
    }
    if (!reference) return;
    const abort = new AbortController();
    controller.attachmentThumbnail(reference, abort.signal).then(
      (blob) => {
        if (abort.signal.aborted) return;
        owned = URL.createObjectURL(blob);
        setUrl(owned);
      },
      () => {
        if (!abort.signal.aborted) setFailed(true);
      },
    );
    return () => {
      abort.abort();
      if (owned) URL.revokeObjectURL(owned);
    };
  }, [controller, file, reference, wanted]);
  /** `shown`: draw the tile as a picture (loading until `url` arrives). */
  return { shown: wanted && !failed, url };
}

function Tile({
  name,
  size,
  mime,
  file,
  reference,
  sent,
  error,
  retryDisabled,
  onOpen,
  onRetry,
  onRemove,
}: {
  name: string;
  size: number;
  mime: string;
  /** A file still uploading, else the uploaded attachment's reference. */
  file?: File;
  reference?: string;
  sent?: number;
  error?: string;
  retryDisabled?: boolean;
  onOpen?: () => void;
  onRetry?: () => void;
  onRemove: () => void;
}) {
  const picture = usePicture(mime, file, reference);
  const uploading = sent !== undefined && error === undefined;
  const state =
    error !== undefined ? 'failed' : uploading ? 'uploading' : 'ready';
  const asPicture = picture.shown && state !== 'failed';
  const Icon = state === 'failed' ? TriangleAlert : typeIcon(mime);
  const percent = uploading ? Math.round((100 * sent) / Math.max(1, size)) : 0;
  const face = (
    <>
      {asPicture ? (
        picture.url ? (
          <img className="attachment-tile-picture" src={picture.url} alt="" />
        ) : (
          <span className="attachment-tile-picture" aria-hidden />
        )
      ) : (
        <>
          <span className="attachment-tile-icon" aria-hidden>
            <Icon />
          </span>
          <span className="attachment-tile-text">
            <span className="attachment-tile-name">{shortName(name)}</span>
            <small title={error}>
              {error ?? `${formatBytes(size)} · ${fileKind(mime, name)}`}
            </small>
          </span>
        </>
      )}
      {uploading && (
        <span
          className="attachment-tile-progress"
          role="progressbar"
          aria-label={`Uploading ${name}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
        >
          <span style={{ inlineSize: `${percent}%` }} />
        </span>
      )}
    </>
  );
  return (
    <li
      className="attachment-tile"
      data-kind={asPicture ? 'picture' : 'file'}
      data-state={state}
    >
      {onOpen ? (
        <Hint label={`${name} · ${formatBytes(size)}`}>
          <button
            type="button"
            className="attachment-tile-face"
            aria-label={`Preview ${name}`}
            onClick={onOpen}
          >
            {face}
          </button>
        </Hint>
      ) : (
        <span className="attachment-tile-face">{face}</span>
      )}
      {error !== undefined && (
        <>
          <span className="sr-only" role="alert">
            {`Couldn’t upload ${name}. ${error}`}
          </span>
          {onRetry && (
            <IconButton
              size="sm"
              className="attachment-tile-retry"
              label={`Retry ${name}`}
              disabled={retryDisabled}
              onClick={onRetry}
            >
              <RotateCcw aria-hidden />
            </IconButton>
          )}
        </>
      )}
      <IconButton
        size="sm"
        className="attachment-tile-remove"
        label={`Remove ${name}`}
        onClick={onRemove}
      >
        <X aria-hidden />
      </IconButton>
    </li>
  );
}

/**
 * The composer's attachments as small tiles (B232): pictures show a
 * thumbnail, other files a type icon, the name and the size. Uploads show
 * their progress or failure; a tile opens the attachment's preview.
 */
export function ComposerAttachments({
  attachments,
  uploads,
  retryDisabled = false,
  onRemove,
  onRetry,
  onDismiss,
}: {
  attachments: readonly AttachmentView[];
  uploads: readonly AttachmentUpload[];
  /** While other files upload, a failed one waits to be retried. */
  retryDisabled?: boolean;
  onRemove: (reference: string) => void;
  onRetry: (upload: AttachmentUpload) => void;
  onDismiss: (key: string) => void;
}) {
  const overlay = useOverlay();
  if (!attachments.length && !uploads.length) return null;
  return (
    <ul className="composer-attachments" aria-label="Attachments">
      {attachments.map((item) => (
        <Tile
          key={item.attachment_ref}
          name={item.name}
          size={item.size_bytes}
          mime={item.mime_type}
          reference={item.attachment_ref}
          onOpen={() =>
            overlay.open({
              title: item.name,
              description: `${formatBytes(item.size_bytes)} · ${fileKind(item.mime_type, item.name)}`,
              content: (
                <MediaPreview
                  reference={item.attachment_ref}
                  mime={item.mime_type}
                  label={item.name}
                  downloadName={item.name}
                  size={item.size_bytes}
                />
              ),
            })
          }
          onRemove={() => onRemove(item.attachment_ref)}
        />
      ))}
      {uploads.map((item) => (
        <Tile
          key={item.key}
          name={item.file.name}
          size={item.file.size}
          mime={item.file.type}
          file={item.file}
          sent={item.sent}
          error={item.error}
          retryDisabled={retryDisabled}
          onRetry={() => onRetry(item)}
          onRemove={() => onDismiss(item.key)}
        />
      ))}
    </ul>
  );
}
