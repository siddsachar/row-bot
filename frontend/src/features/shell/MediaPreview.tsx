import { useCallback, useEffect, useRef, useState } from 'react';
import { Download, File as FileIcon, Maximize2 } from 'lucide-react';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Button, IconButton } from '../../ui/primitives';
import { CodeBlock, languageForFile } from './CodeBlock';

type PreviewKind = 'image' | 'audio' | 'video' | 'pdf' | 'text' | 'download';
const images = new Set([
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/webp',
  'image/avif',
  'image/bmp',
]);
const audio = new Set([
  'audio/mpeg',
  'audio/mp4',
  'audio/ogg',
  'audio/wav',
  'audio/x-wav',
  'audio/webm',
  'audio/flac',
  'audio/aac',
]);
const video = new Set([
  'video/mp4',
  'video/webm',
  'video/ogg',
  'video/quicktime',
]);
const textTypes = new Set([
  'application/json',
  'application/xml',
  'application/yaml',
  'application/x-yaml',
  'application/javascript',
  'application/typescript',
  'application/x-sh',
  'application/sql',
  'application/toml',
]);
/** Text previews stay bounded; larger files keep the download card. */
const TEXT_LIMIT = 256 * 1024;

function mediaType(value: string) {
  return value.split(';', 1)[0].trim().toLowerCase();
}
function previewKind(declared: string, actual: string): PreviewKind {
  const mime = mediaType(declared);
  if (actual && mediaType(actual) !== mime) return 'download';
  if (images.has(mime)) return 'image';
  if (audio.has(mime)) return 'audio';
  if (video.has(mime)) return 'video';
  if (mime === 'application/pdf') return 'pdf';
  // HTML stays download-only: it is never rendered, even as source.
  if ((mime.startsWith('text/') && mime !== 'text/html') || textTypes.has(mime))
    return 'text';
  return 'download';
}

export function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

// Results are immutable per reference, so a settled download is kept for the
// session (bounded) and reused when the same result mounts again, for example
// when a live preview becomes the settled one. Retry always downloads again.
const RECENT_LIMIT = 24;
const RECENT_BYTES = 96 * 1024 * 1024;
const recent = new WeakMap<object, Map<string, Blob>>();
function remembered(owner: object, reference: string) {
  const entries = recent.get(owner);
  const blob = entries?.get(reference);
  if (entries && blob) {
    entries.delete(reference);
    entries.set(reference, blob);
  }
  return blob;
}
function remember(owner: object, reference: string, blob: Blob) {
  let entries = recent.get(owner);
  if (!entries) recent.set(owner, (entries = new Map()));
  entries.delete(reference);
  if (blob.size > RECENT_BYTES) return;
  entries.set(reference, blob);
  let total = [...entries.values()].reduce((sum, item) => sum + item.size, 0);
  for (const [key, value] of entries) {
    if (entries.size <= RECENT_LIMIT && total <= RECENT_BYTES) break;
    entries.delete(key);
    total -= value.size;
  }
}
/** Tests and sign-out: drop remembered results for a controller. */
export function forgetMediaPreviews(owner: object) {
  recent.delete(owner);
}

async function isPdf(blob: Blob) {
  const head = new Uint8Array(await blob.slice(0, 5).arrayBuffer());
  return String.fromCharCode(...head) === '%PDF-';
}

/**
 * Local media from the authenticated controller, embedded by type: images
 * with a full-size view, native audio/video players, an inline PDF viewer,
 * highlighted text, and a file card for anything else. The controller keeps
 * download authentication, size and cancellation policy.
 */
export function MediaPreview({
  reference,
  mime,
  label,
  downloadName = 'result',
  caption,
  size,
}: {
  reference: string;
  mime: string;
  label?: string;
  downloadName?: string;
  /** For generated images: the prompt, shown on hover and in the viewer. */
  caption?: string;
  size?: number;
}) {
  const accessibleLabel = label || 'Generated result';
  const displayLabel = label || 'generated result';
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const [result, setResult] = useState<{
    reference: string;
    mime: string;
    url: string;
    kind: PreviewKind;
    text?: string;
    bytes: number;
  } | null>(null);
  const [error, setError] = useState<{
    reference: string;
    mime: string;
    message: string;
  } | null>(null);
  const [failedUrl, setFailedUrl] = useState('');
  const [attempt, setAttempt] = useState(0);
  const player = useRef<HTMLMediaElement | null>(null);
  const setPlayer = useCallback((node: HTMLMediaElement | null) => {
    const previous = player.current;
    if (previous && previous !== node) {
      previous.pause();
      previous.removeAttribute('src');
      previous.load();
    }
    player.current = node;
  }, []);

  useEffect(() => {
    const abort = new AbortController();
    let owned = '';
    setResult(null);
    setError(null);
    setFailedUrl('');
    const key = `${mediaType(mime)} ${reference}`;
    const cached = attempt === 0 ? remembered(controller, key) : undefined;
    void (
      cached
        ? Promise.resolve(cached)
        : controller.download(reference, abort.signal)
    )
      .then(async (blob) => {
        if (abort.signal.aborted) return;
        if (!cached) remember(controller, key, blob);
        let kind = previewKind(mime, blob.type);
        let body: Blob = blob;
        let text: string | undefined;
        if (kind === 'pdf') {
          // A PDF frame only ever shows bytes that are a PDF, typed as one.
          if (await isPdf(blob))
            body = new Blob([blob], { type: 'application/pdf' });
          else kind = 'download';
        } else if (kind === 'text') {
          if (blob.size <= TEXT_LIMIT) text = await blob.text();
          else kind = 'download';
        }
        if (abort.signal.aborted) return;
        owned = URL.createObjectURL(body);
        setResult({
          reference,
          mime,
          url: owned,
          kind,
          text,
          bytes: blob.size,
        });
      })
      .catch((cause: unknown) => {
        if (!abort.signal.aborted)
          setError({ reference, mime, message: clientError(cause).message });
      });
    return () => {
      abort.abort();
      if (owned) URL.revokeObjectURL(owned);
    };
  }, [controller, reference, mime, attempt]);

  const current =
    result?.reference === reference && result.mime === mime ? result : null;
  const failure =
    error?.reference === reference && error.mime === mime ? error.message : '';
  const retry = (
    <Button onClick={() => setAttempt((value) => value + 1)}>
      {label ? 'Retry preview' : 'Retry generated result'}
    </Button>
  );
  if (!current)
    return failure ? (
      <div
        role="alert"
        className="media-preview-error"
        data-media-ref={reference}
      >
        {failure} {retry}
      </div>
    ) : (
      <p
        role="status"
        className="media-preview-status"
        data-kind={previewKind(mime, '')}
        data-media-ref={reference}
      >
        Loading {displayLabel}…
      </p>
    );

  const decodeFailed = failedUrl === current.url;
  const onError = () => setFailedUrl(current.url);
  const download = (
    <a
      className="media-preview-download"
      href={current.url}
      download={downloadName}
      aria-label={`Download ${displayLabel}`}
      title={`Download ${displayLabel}`}
    >
      <Download aria-hidden />
      <span className="media-preview-download-text">Download</span>
    </a>
  );
  const openViewer = () =>
    overlay.open({
      title: accessibleLabel,
      description: caption ?? '',
      className: 'media-lightbox',
      content: (
        <figure className="media-lightbox-figure">
          <img src={current.url} alt={accessibleLabel} />
          <a
            className="button secondary"
            href={current.url}
            download={downloadName}
          >
            <Download size={16} aria-hidden /> Download
          </a>
        </figure>
      ),
    });
  return (
    <div
      className="media-preview"
      role="group"
      aria-label={accessibleLabel}
      data-media-ref={reference}
      data-kind={current.kind}
      data-failed={decodeFailed ? 'true' : undefined}
    >
      {!decodeFailed && current.kind === 'image' && (
        <div className="media-image-frame">
          <img
            className="message-media"
            src={current.url}
            alt={accessibleLabel}
            onError={onError}
            onClick={openViewer}
          />
          {caption && (
            <span className="media-image-caption" aria-hidden>
              {caption}
            </span>
          )}
          <span className="media-image-tools">
            <IconButton
              size="sm"
              label={`View ${displayLabel} full size`}
              onClick={openViewer}
            >
              <Maximize2 size={15} aria-hidden />
            </IconButton>
            {download}
          </span>
        </div>
      )}
      {!decodeFailed && current.kind === 'audio' && (
        <audio
          ref={setPlayer}
          className="message-media"
          src={current.url}
          controls
          preload="metadata"
          aria-label="Generated audio result"
          onError={onError}
        >
          Your browser cannot play this audio. Use the download below.
        </audio>
      )}
      {!decodeFailed && current.kind === 'video' && (
        <video
          ref={setPlayer}
          className="message-media"
          src={current.url}
          controls
          preload="metadata"
          playsInline
          aria-label="Generated video result"
          onError={onError}
        >
          Your browser cannot play this video. Use the download below.
        </video>
      )}
      {!decodeFailed && current.kind === 'pdf' && (
        <iframe
          className="media-pdf"
          src={current.url}
          title={`${accessibleLabel} (PDF)`}
          referrerPolicy="no-referrer"
        />
      )}
      {!decodeFailed && current.kind === 'text' && (
        <CodeBlock
          text={current.text ?? ''}
          language={languageForFile(downloadName, current.mime)}
          title={label}
        />
      )}
      {!decodeFailed && current.kind === 'download' && (
        <div className="media-file-card">
          <span className="media-file-icon" aria-hidden>
            <FileIcon />
          </span>
          <span className="media-file-text">
            <strong>{label || 'Generated file'}</strong>
            <small>
              {formatBytes(size ?? current.bytes)} ·{' '}
              {mediaType(current.mime) || 'file'}
            </small>
          </span>
        </div>
      )}
      {decodeFailed && (
        <div role="alert" className="media-preview-error">
          This generated result could not be previewed. Download it or retry.{' '}
          {retry}
        </div>
      )}
      {(current.kind !== 'image' || decodeFailed) && (
        <div className="media-preview-actions">{download}</div>
      )}
    </div>
  );
}
