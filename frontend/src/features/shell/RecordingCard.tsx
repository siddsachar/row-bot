import { useContext, useEffect, useState } from 'react';
import { Monitor, Square, Video } from 'lucide-react';
import { clientError } from '../../api/errors';
import { Button, Skeleton, StatusDot } from '../../ui/primitives';
import { MediaPreview } from './MediaPreview';
import { useNow } from './background-processes';
import {
  RecordingIoContext,
  clockWords,
  recordingLive,
  recordingSeconds,
  recordingStatus,
  recordingSubject,
  stoppedWords,
  useRecordings,
} from './recordings';

/**
 * A recording a step started (F2), in the conversation: while it records,
 * "Recording · 0:42" with Stop; then the video with a player, saved to the
 * conversation's files, or why there is none.
 */
export function RecordingCard({
  recordingId,
  conversationId,
}: {
  recordingId: string;
  conversationId?: string | null;
}) {
  const io = useContext(RecordingIoContext);
  const feed = useRecordings(io, conversationId ?? undefined);
  const { want } = feed;
  useEffect(() => want(recordingId), [want, recordingId]);
  const view = feed.items.find((item) => item.recording_id === recordingId);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState('');
  const live = view ? recordingLive(view) : false;
  const now = useNow(live);

  async function stop() {
    if (!io || !view || stopping) return;
    setStopping(true);
    setError('');
    try {
      feed.merge(await io.stop(view.recording_id));
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setStopping(false);
    }
  }

  if (!io) return null;
  if (!view)
    return (
      <div
        className="transcript-card recording-card"
        data-kind="recording"
        data-state={feed.loaded ? 'unavailable' : 'loading'}
        role="group"
        aria-label="Recording"
      >
        <Video className="transcript-card-icon" aria-hidden />
        <div className="transcript-card-text">
          {feed.loaded && !feed.error ? (
            <span>This recording is no longer listed.</span>
          ) : feed.error ? (
            <span>Couldn&apos;t check this recording.</span>
          ) : (
            <Skeleton label="Checking the recording" />
          )}
        </div>
        {feed.error && (
          <div className="transcript-card-actions">
            <Button variant="ghost" onClick={feed.refresh}>
              Try again
            </Button>
          </div>
        )}
      </div>
    );

  const seconds = recordingSeconds(view, feed.readAt, now);
  const status = recordingStatus(view, seconds);
  const subject = recordingSubject(view);
  const Icon = view.kind === 'screen' ? Monitor : Video;
  const stopped = view.status === 'recording' ? '' : stoppedWords(view);
  return (
    <div
      className="transcript-card recording-card"
      data-kind="recording"
      data-state={view.status}
      role="group"
      aria-label={`${subject}: ${status.label}`}
    >
      <Icon className="transcript-card-icon" aria-hidden />
      <div className="transcript-card-text recording-card-text">
        <strong>
          {view.status === 'recording'
            ? view.kind === 'screen'
              ? 'Recording your screen'
              : "Recording Row-Bot's browser"
            : subject}
        </strong>
        <span className="recording-card-meta">
          <StatusDot
            tone={status.tone}
            pulse={status.pulse}
            label={status.label}
            showLabel
          />
          {view.status === 'recording' ? (
            <span>Stops by itself at {clockWords(view.max_seconds)}</span>
          ) : (
            stopped && <span>{stopped}</span>
          )}
        </span>
      </div>
      {view.status === 'recording' && (
        <div className="transcript-card-actions">
          <Button disabled={stopping} onClick={() => void stop()}>
            <Square size={12} aria-hidden />
            Stop
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="transcript-card-error">
          {error}
        </p>
      )}
      {view.status === 'saved' && view.media_ref && (
        <div className="recording-card-preview">
          <MediaPreview
            reference={view.media_ref}
            mime={view.mime_type ?? 'video/mp4'}
            label={subject}
            downloadName={
              view.kind === 'screen'
                ? 'screen-recording.mp4'
                : 'browser-recording.mp4'
            }
            size={view.size_bytes ?? undefined}
          />
        </div>
      )}
    </div>
  );
}
