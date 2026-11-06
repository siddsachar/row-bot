import { useContext, useEffect, useState } from 'react';
import {
  Download,
  File as FileIcon,
  MonitorSmartphone,
  Send,
} from 'lucide-react';
import type { DeliveryView } from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, Skeleton, StatusDot } from '../../ui/primitives';
import { fileKind, formatBytes } from './MediaPreview';
import { saveProblem, useSaveFile } from './save-file';
import {
  DeliveryIoContext,
  deliveryAdvice,
  deliveryExpired,
  deliveryPlace,
  deliveryStatus,
  deliveryTitle,
  sendInsteadLabel,
} from './deliveries';

/**
 * Files a step sent (F7), in the conversation. Sent to the person's devices:
 * each file with Download, on every device they're signed in on, until the
 * week runs out. Sent to a chat app: where it went. One that didn't go says
 * why in plain words, with one way out: send it to your devices instead.
 */
export function DeliveryCard({
  deliveryId,
  nested = false,
}: {
  deliveryId: string;
  /** Shown after the card of the send it replaced (never followed again). */
  nested?: boolean;
}) {
  const io = useContext(DeliveryIoContext);
  const [view, setView] = useState<DeliveryView | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState<number | 'devices' | null>(null);
  const [error, setError] = useState('');
  const saveFile = useSaveFile();

  useEffect(() => {
    if (!io) return;
    const abort = new AbortController();
    setFailed(false);
    io.load(deliveryId, abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) setView(value);
      },
      () => {
        if (!abort.signal.aborted) setFailed(true);
      },
    );
    return () => abort.abort();
  }, [io, deliveryId, attempt]);

  async function download(index: number) {
    if (!io || !view || !saveFile || busy !== null) return;
    setBusy(index);
    setError('');
    const result = await saveFile({
      kind: 'delivery',
      delivery_id: view.delivery_id,
      index,
    });
    setBusy(null);
    setError(saveProblem(result, 'the file'));
    if (
      result.status === 'unavailable' &&
      result.reason === 'sent_file_expired'
    )
      setAttempt((value) => value + 1);
  }

  async function sendToDevices() {
    if (!io || !view || busy !== null) return;
    setBusy('devices');
    setError('');
    try {
      const copy = await io.sendToDevices(view.delivery_id);
      setView({
        ...view,
        sent_instead: copy.delivery_id,
        can_send_to_devices: false,
      });
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(null);
    }
  }

  if (!io) return null;
  if (!view)
    return (
      <div
        className="transcript-card delivery-card"
        data-kind="delivery"
        data-state={failed ? 'unavailable' : 'loading'}
        role="group"
        aria-label="Sent files"
      >
        <Send className="transcript-card-icon" aria-hidden />
        <div className="transcript-card-text">
          {failed ? (
            <span>Couldn&apos;t check these files.</span>
          ) : (
            <Skeleton label="Checking the sent files" />
          )}
        </div>
        {failed && (
          <div className="transcript-card-actions">
            <Button
              variant="ghost"
              onClick={() => setAttempt((value) => value + 1)}
            >
              Try again
            </Button>
          </div>
        )}
      </div>
    );

  const now = Date.now();
  const title = deliveryTitle(view, now);
  const status = deliveryStatus(view, now);
  const place = deliveryPlace(view, now);
  const advice = deliveryAdvice(view, now);
  const expired = deliveryExpired(view, now);
  const anySent = view.files.some((file) => file.sent);
  const Icon = view.target === 'devices' ? MonitorSmartphone : Send;
  const state = expired
    ? 'expired'
    : view.status === 'failed'
      ? 'failed'
      : view.status;
  const card = (
    <div
      className="transcript-card recording-card delivery-card"
      data-kind="delivery"
      data-target={view.target}
      data-state={state}
      role="group"
      aria-label={`${title}: ${status.label}`}
    >
      <Icon className="transcript-card-icon" aria-hidden />
      <div className="transcript-card-text recording-card-text">
        <strong>{title}</strong>
        <span className="recording-card-meta">
          <StatusDot tone={status.tone} label={status.label} showLabel />
          {place && <span>{place}</span>}
        </span>
        {advice && <span className="delivery-advice">{advice}</span>}
      </div>
      <ul
        className="delivery-files"
        aria-label={view.files.length === 1 ? 'File' : 'Files'}
      >
        {view.files.map((file) => (
          <li
            key={file.index}
            className="delivery-file"
            data-sent={file.sent || undefined}
          >
            <span className="media-file-icon" aria-hidden>
              <FileIcon />
            </span>
            <span className="media-file-text">
              <strong title={file.name}>{file.name}</strong>
              <small>
                {formatBytes(file.size_bytes)} ·{' '}
                {fileKind(file.mime_type, file.name)}
                {!file.sent && anySent ? ' · Not sent' : ''}
              </small>
            </span>
            {file.downloadable && !expired && (
              <Button
                className="small delivery-download"
                aria-label={`Download ${file.name}`}
                disabled={busy !== null}
                onClick={() => void download(file.index)}
              >
                <Download size={14} aria-hidden />
                <span className="delivery-download-text">
                  {busy === file.index ? 'Downloading…' : 'Download'}
                </span>
              </Button>
            )}
          </li>
        ))}
      </ul>
      {view.can_send_to_devices && !view.sent_instead && (
        <div className="transcript-card-actions delivery-actions">
          <Button
            variant="primary"
            disabled={busy !== null}
            onClick={() => void sendToDevices()}
          >
            <MonitorSmartphone size={14} aria-hidden />
            {busy === 'devices' ? 'Sending…' : sendInsteadLabel(view)}
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="transcript-card-error">
          {error}
        </p>
      )}
    </div>
  );
  // What was sent to the devices instead follows as a card of its own.
  return view.sent_instead && !nested ? (
    <>
      {card}
      <DeliveryCard deliveryId={view.sent_instead} nested />
    </>
  ) : (
    card
  );
}
