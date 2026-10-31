import { useEffect, useMemo, useRef, useState } from 'react';
import { Pause, Play } from 'lucide-react';
import type { VideoScene, VideoSceneWrite } from '../../api/types';
import { useRuntime } from '../../runtime';
import { IconButton } from '../../ui/primitives';
import { acquire, release } from './media-urls';
import { span } from './VideoTimeline';

/** How long a card shows over a scene, in seconds of the video. */
const CARD_SECONDS = 3;
/** A scene with no video to play here (a workspace file) shows as a still card this long, at most. */
const STILL_SECONDS = 3;

type Piece = {
  key: string;
  ref: string | null;
  start: number;
  end: number;
  speed: number;
  zoom: number[] | null;
  card: string;
  line: string;
  voice: string | null;
};

/** The edited scenes as the preview plays them: each with its video and, while its line is unchanged, its voice. */
export function pieces(
  scenes: VideoSceneWrite[],
  shown: VideoScene[],
): Piece[] {
  const sourceRefs = new Map(
    shown.map((scene) => [scene.source, scene.source_ref ?? null]),
  );
  return scenes.map((scene, index) => {
    const made = shown.find((item) => item.scene_id === scene.scene_id);
    const [start, end] = span(scene, made);
    return {
      key: scene.scene_id || `new-${index}`,
      ref: sourceRefs.get(scene.source ?? '') ?? null,
      start,
      end,
      speed: scene.speed ?? 1,
      zoom: scene.zoom ?? null,
      card: scene.card ?? '',
      line: scene.line ?? '',
      voice:
        made && made.voice_ref && made.line === (scene.line ?? '')
          ? made.voice_ref
          : null,
    };
  });
}

/** Punch in on a region: scale the picture up around the region's middle. */
function zoomStyle(zoom: number[] | null) {
  if (!zoom) return undefined;
  const [x, y, width, height] = zoom;
  return {
    transformOrigin: `${(x + width / 2) * 100}% ${(y + height / 2) * 100}%`,
    transform: `scale(${1 / Math.max(width, height, 0.05)})`,
  };
}

/**
 * The live preview (5.3.1 B3): the edited scenes played in the client as
 * they will be made: each scene's own video from its start to its end at its
 * speed, punched in on its zoom, with its card over the first seconds and its
 * line as a caption, and its voice while its line is unchanged. A changed
 * line is said once the video is made. Nothing is rendered until Save.
 */
export function LivePreview({
  scenes,
  shown,
  captions,
}: {
  scenes: VideoSceneWrite[];
  shown: VideoScene[];
  captions: boolean;
}) {
  const { controller } = useRuntime();
  const list = useMemo(() => pieces(scenes, shown), [scenes, shown]);
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const video = useRef<HTMLVideoElement>(null);
  const voice = useRef<HTMLAudioElement>(null);
  const still = useRef(0);

  // The files to play, fetched once each while the set stays the same (typing a line doesn't fetch again).
  const wanted = [...new Set(list.flatMap((piece) => [piece.ref, piece.voice]))]
    .filter((ref): ref is string => Boolean(ref))
    .sort()
    .join('|');
  useEffect(() => {
    if (!wanted) return;
    const refs = wanted.split('|');
    let live = true;
    for (const ref of refs)
      void acquire(ref, (signal) => controller.download(ref, signal)).then(
        (url) => {
          if (live && url) setUrls((current) => ({ ...current, [ref]: url }));
        },
      );
    return () => {
      live = false;
      refs.forEach(release);
    };
  }, [controller, wanted]);

  const piece = list[Math.min(index, list.length - 1)];
  const source = piece?.ref ? urls[piece.ref] : '';
  const voiceUrl = piece?.voice ? urls[piece.voice] : '';

  function next() {
    setElapsed(0);
    if (index + 1 < list.length) setIndex(index + 1);
    else {
      setPlaying(false);
      setIndex(0);
    }
  }

  // Each scene starts from its own start, at its own speed, with its voice.
  useEffect(() => {
    const element = video.current;
    if (element && source) {
      element.currentTime = piece.start;
      element.playbackRate = piece.speed;
      if (playing) void element.play().catch(() => undefined);
    }
    const line = voice.current;
    if (line) {
      line.currentTime = 0;
      if (playing && voiceUrl) void line.play().catch(() => undefined);
      else line.pause();
    }
    window.clearTimeout(still.current);
    if (playing && piece && !source)
      still.current = window.setTimeout(
        next,
        Math.min(STILL_SECONDS, (piece.end - piece.start) / piece.speed) * 1000,
      );
    return () => window.clearTimeout(still.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, source, voiceUrl, playing]);

  if (!piece) return null;
  const cardShown = piece.card && elapsed < CARD_SECONDS;
  return (
    <div className="video-preview" aria-label="Preview of your edits">
      <div className="video-preview-stage">
        {source ? (
          <video
            ref={video}
            src={source}
            muted={Boolean(piece.voice)}
            playsInline
            style={zoomStyle(piece.zoom)}
            onTimeUpdate={(event) => {
              const element = event.currentTarget;
              setElapsed((element.currentTime - piece.start) / piece.speed);
              if (element.currentTime >= piece.end - 0.02) next();
            }}
            onEnded={next}
            aria-label={`Scene ${index + 1}`}
          />
        ) : (
          <div
            className="video-preview-still"
            aria-label={`Scene ${index + 1}`}
          >
            {piece.ref
              ? 'Loading…'
              : 'This scene’s video shows once the video is made.'}
          </div>
        )}
        {voiceUrl && <audio ref={voice} src={voiceUrl} />}
        {cardShown && (
          <span
            className="video-preview-card"
            data-over={piece.ref ? 'picture' : 'card'}
          >
            {piece.card}
          </span>
        )}
        {captions && piece.line && (
          <span className="video-preview-caption">{piece.line}</span>
        )}
      </div>
      <div className="video-preview-bar">
        <IconButton
          size="sm"
          label={playing ? 'Pause the preview' : 'Play the preview'}
          onClick={() => {
            const element = video.current;
            if (playing) {
              element?.pause();
              voice.current?.pause();
            }
            setPlaying(!playing);
          }}
        >
          {playing ? (
            <Pause size={16} aria-hidden />
          ) : (
            <Play size={16} aria-hidden />
          )}
        </IconButton>
        <span>
          Scene {index + 1} of {list.length} · a preview: changed lines are said
          once the video is made
        </span>
      </div>
    </div>
  );
}
