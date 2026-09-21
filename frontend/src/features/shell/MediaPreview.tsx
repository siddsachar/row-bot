import { useCallback, useEffect, useRef, useState } from 'react';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Button } from '../../ui/primitives';

type PreviewKind = 'image' | 'audio' | 'video' | 'download';
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
function mediaType(value: string) {
  return value.split(';', 1)[0].trim().toLowerCase();
}
function previewKind(declared: string, actual: string): PreviewKind {
  const mime = mediaType(declared);
  if (actual && mediaType(actual) !== mime) return 'download';
  if (images.has(mime)) return 'image';
  if (audio.has(mime)) return 'audio';
  if (video.has(mime)) return 'video';
  return 'download';
}

/** The controller retains download authentication, size and cancellation policy. */
export function MediaPreview({
  reference,
  mime,
  label,
  downloadName = 'result',
}: {
  reference: string;
  mime: string;
  label?: string;
  downloadName?: string;
}) {
  const accessibleLabel = label || 'Generated result';
  const displayLabel = label || 'generated result';
  const { controller } = useRuntime();
  const [result, setResult] = useState<{
    reference: string;
    mime: string;
    url: string;
    kind: PreviewKind;
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
    void controller
      .download(reference, abort.signal)
      .then((blob) => {
        if (abort.signal.aborted) return;
        owned = URL.createObjectURL(blob);
        setResult({
          reference,
          mime,
          url: owned,
          kind: previewKind(mime, blob.type),
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
      <div role="alert" className="media-preview-error">
        {failure} {retry}
      </div>
    ) : (
      <p role="status" className="media-preview-status">
        Loading {displayLabel}…
      </p>
    );

  const decodeFailed = failedUrl === current.url;
  const onError = () => setFailedUrl(current.url);
  return (
    <div
      className="stack media-preview"
      role="group"
      aria-label={accessibleLabel}
    >
      {!decodeFailed && current.kind === 'image' && (
        <img
          className="message-media"
          src={current.url}
          alt={accessibleLabel}
          onError={onError}
        />
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
      {decodeFailed && (
        <div role="alert" className="media-preview-error">
          This generated result could not be previewed. Download it or retry.{' '}
          {retry}
        </div>
      )}
      <a
        className="media-preview-download"
        href={current.url}
        download={downloadName}
      >
        Download {displayLabel}
      </a>
    </div>
  );
}
