import { useEffect, useState } from 'react';
import { Clapperboard } from 'lucide-react';
import type { VideoProjectView } from '../../api/types';
import { useClientSelector, useRuntime } from '../../runtime';
import { Button, StatusDot } from '../../ui/primitives';
import { requestVideoPanel } from './open-video';
import { madeWords, statusWords } from './video-words';

/**
 * A video project a step made or changed (5.3.1 B3), in the chat: its title,
 * the version and whether it's made, with Open, which shows it in the Video
 * panel beside the chat (full screen on a phone). Read again by id.
 */
export function VideoProjectCard({
  projectId,
  conversationId,
}: {
  projectId: string;
  conversationId: string | null | undefined;
}) {
  const { controller } = useRuntime();
  const [view, setView] = useState<VideoProjectView | null>(null);
  const [gone, setGone] = useState(false);
  const revision = useClientSelector((value) =>
    value.projection?.conversation_id === conversationId
      ? value.projection?.checkpoint_revision
      : undefined,
  );
  const working = view?.status === 'voicing' || view?.status === 'rendering';

  useEffect(() => {
    const abort = new AbortController();
    let timer = 0;
    const read = () =>
      controller.videoProject(projectId, undefined, abort.signal).then(
        (value) => {
          if (abort.signal.aborted || !value) return;
          setView(value);
          if (value.status === 'voicing' || value.status === 'rendering')
            timer = window.setTimeout(read, 2000);
        },
        () => {
          if (!abort.signal.aborted) setGone(true);
        },
      );
    void read();
    return () => {
      abort.abort();
      window.clearTimeout(timer);
    };
  }, [controller, projectId, revision]);

  if (gone)
    return (
      <div
        className="transcript-card"
        data-kind="video_project"
        data-state="removed"
        role="group"
        aria-label="Video no longer here"
      >
        <Clapperboard className="transcript-card-icon" aria-hidden />
        <div className="transcript-card-text">
          <span>This video is no longer here.</span>
        </div>
      </div>
    );
  const title = view?.title ?? '';
  const words = view ? statusWords(view) : null;
  return (
    <div
      className="transcript-card video-project-card"
      data-kind="video_project"
      role="group"
      aria-label={title ? `${title} · video` : 'Video'}
      aria-busy={!view}
    >
      <Clapperboard className="transcript-card-icon" aria-hidden />
      <div className="transcript-card-text">
        <span>
          <strong>{title || 'Video'}</strong> · video
        </span>
        {view && words && (
          <span className="recording-card-meta">
            <StatusDot
              tone={words.tone}
              pulse={working}
              label={words.label}
              showLabel
            />
            <span>{madeWords(view)}</span>
          </span>
        )}
      </div>
      <div className="transcript-card-actions">
        <Button
          disabled={!view || !conversationId}
          onClick={() =>
            view &&
            conversationId &&
            requestVideoPanel({
              conversationId,
              projectId,
              title: view.title,
            })
          }
        >
          Open
        </Button>
      </div>
    </div>
  );
}
