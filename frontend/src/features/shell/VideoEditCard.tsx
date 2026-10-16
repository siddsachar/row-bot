import { useContext, useEffect, useState } from 'react';
import { Download, Images, Scissors, Square } from 'lucide-react';
import { clientError } from '../../api/errors';
import { Button, Disclosure, Skeleton, StatusDot } from '../../ui/primitives';
import { MediaPreview } from './MediaPreview';
import { useNow } from './background-processes';
import { clockWords } from './recordings';
import {
  VideoEditIoContext,
  editLive,
  editSeconds,
  editStatus,
  editTitle,
  failureWords,
  useVideoEdits,
} from './video-edits';

/**
 * A video edit a step started (F3), in the conversation: while it is made,
 * what it does with its progress and Stop (first "Getting ffmpeg" with the
 * download's progress, the first time only); then the edited video with a
 * player, or the stills as one contact sheet, saved to the conversation's
 * files; or why there is none. Which ffmpeg made it is one tap away.
 */
export function VideoEditCard({
  editId,
  conversationId,
}: {
  editId: string;
  conversationId?: string | null;
}) {
  const io = useContext(VideoEditIoContext);
  const feed = useVideoEdits(io, conversationId ?? undefined);
  const { want } = feed;
  useEffect(() => want(editId), [want, editId]);
  const view = feed.items.find((item) => item.edit_id === editId);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState('');
  const live = view ? editLive(view) : false;
  const now = useNow(live);

  async function stop() {
    if (!io || !view || stopping) return;
    setStopping(true);
    setError('');
    try {
      feed.merge(await io.stop(view.edit_id));
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
        className="transcript-card recording-card video-edit-card"
        data-kind="video_edit"
        data-state={feed.loaded ? 'unavailable' : 'loading'}
        role="group"
        aria-label="Video edit"
      >
        <Scissors className="transcript-card-icon" aria-hidden />
        <div className="transcript-card-text">
          {feed.loaded && !feed.error ? (
            <span>This video edit is no longer listed.</span>
          ) : feed.error ? (
            <span>Couldn&apos;t check this video edit.</span>
          ) : (
            <Skeleton label="Checking the video edit" />
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

  const seconds = editSeconds(view, feed.readAt, now);
  const status = editStatus(view, seconds);
  const title = editTitle(view);
  const Icon =
    view.status === 'getting_ffmpeg'
      ? Download
      : view.kind === 'frames'
        ? Images
        : Scissors;
  const why = failureWords(view);
  const showProgress =
    live && view.progress !== null && view.status !== 'saving';
  const ffmpegDetail =
    view.status === 'failed' && view.problem === 'edit_failed';
  return (
    <div
      className="transcript-card recording-card video-edit-card"
      data-kind="video_edit"
      data-state={view.status}
      role="group"
      aria-label={`${title}: ${status.label}`}
    >
      <Icon className="transcript-card-icon" aria-hidden />
      <div className="transcript-card-text recording-card-text">
        <strong>{title}</strong>
        <span className="recording-card-meta">
          <StatusDot
            tone={status.tone}
            pulse={status.pulse}
            label={status.label}
            showLabel
          />
          {view.status === 'getting_ffmpeg' ? (
            <span>From PyPI, once</span>
          ) : live ? (
            <span>{clockWords(seconds)}</span>
          ) : view.status === 'saved' ? (
            <span>Saved to this chat</span>
          ) : null}
        </span>
        {showProgress && (
          <progress
            className="video-edit-progress"
            aria-label={
              view.status === 'getting_ffmpeg'
                ? 'ffmpeg download'
                : 'Edit progress'
            }
            max={1}
            value={view.progress ?? 0}
          />
        )}
        {why && <span className="video-edit-why">{why}</span>}
      </div>
      {live && (
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
            label={
              view.kind === 'frames'
                ? `Contact sheet of ${view.frame_count} stills`
                : title
            }
            downloadName={
              view.kind === 'frames'
                ? 'stills-contact-sheet.jpg'
                : view.output_name || 'edited-video.mp4'
            }
            size={view.size_bytes ?? undefined}
          />
        </div>
      )}
      {!live && (view.ffmpeg || ffmpegDetail) && (
        <Disclosure summary="Details" className="video-edit-details">
          <dl>
            {ffmpegDetail && view.detail && (
              <>
                <dt>What happened</dt>
                <dd>{view.detail}</dd>
              </>
            )}
            {view.ffmpeg && (
              <>
                <dt>Made with</dt>
                <dd>{view.ffmpeg}</dd>
              </>
            )}
          </dl>
        </Disclosure>
      )}
    </div>
  );
}
