import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from 'react';
import type { VideoScene, VideoSceneWrite } from '../../api/types';
import { lengthWords } from './video-words';

/** Shortest a scene may be trimmed to, in seconds of its own video. */
const SHORTEST = 0.2;
const CARD_SECONDS = 3;

/** A scene's span in its own video: its end filled from the made version when it runs to the end. */
export function span(
  scene: VideoSceneWrite,
  made: VideoScene | undefined,
): [number, number] {
  const start = scene.start ?? 0;
  if (scene.end !== null && scene.end !== undefined) return [start, scene.end];
  if (made?.length !== null && made?.length !== undefined)
    return [start, made.start + made.length * made.speed];
  return [start, start + 3];
}

/** How long a scene lasts on the video. */
export function sceneLength(
  scene: VideoSceneWrite,
  made: VideoScene | undefined,
): number {
  const [start, end] = span(scene, made);
  return Math.max(SHORTEST, end - start) / (scene.speed ?? 1);
}

type Drag =
  | { kind: 'start' | 'end'; index: number; x: number; from: number }
  | { kind: 'move'; index: number; x: number };

/**
 * The timeline (desktop, 5.3.1 B3): the video's scenes as blocks on tracks
 * (video, voice lines, title cards, narration and music), sized by their length. Drag a
 * block's edges to trim it, drag a block to move it, click to play from it,
 * and zoom the timeline. Keys: Space plays, J and L step back and on, K
 * pauses, I and O set the selected scene's start and end at the playhead, S
 * splits it there, Delete removes it, Ctrl+Z undoes.
 */
export function VideoTimeline({
  scenes,
  made,
  narration = '',
  music,
  url,
  playhead,
  disabled,
  onChange,
  onUndo,
  onSeek,
  onPlay,
  onPause,
}: {
  scenes: VideoSceneWrite[];
  made: VideoScene[];
  /** One voice file said over the whole video, instead of each scene's line. */
  narration?: string;
  music: string;
  url: string;
  /** Seconds into the made video, or null when the edits moved things since. */
  playhead: number | null;
  disabled: boolean;
  onChange: (scenes: VideoSceneWrite[]) => void;
  onUndo: () => void;
  onSeek: (at: number) => void;
  onPlay: () => void;
  onPause: () => void;
}) {
  // Pixels per second: the whole video fits the panel until the person zooms.
  const [zoomed, setZoomed] = useState<number | null>(null);
  const [fit, setFit] = useState(60);
  const scroller = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState(0);
  const [drag, setDrag] = useState<Drag | null>(null);
  const preview = useRef<VideoSceneWrite[] | null>(null);
  const [, redraw] = useState(0);
  const madeOf = (scene: VideoSceneWrite) =>
    made.find((item) => item.scene_id === scene.scene_id);
  const shownScenes = preview.current ?? scenes;
  const lengths = shownScenes.map((scene) => sceneLength(scene, madeOf(scene)));
  const starts = lengths.map((_, index) =>
    lengths.slice(0, index).reduce((sum, value) => sum + value, 0),
  );
  const total = lengths.reduce((sum, value) => sum + value, 0);
  const scale = zoomed ?? fit;
  useEffect(() => {
    const box = scroller.current;
    if (!box || total <= 0) return;
    const measure = () =>
      setFit(Math.min(200, Math.max(20, (box.clientWidth - 80) / total)));
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(box);
    return () => observer.disconnect();
  }, [total]);
  useEffect(() => {
    if (selected >= scenes.length) setSelected(Math.max(0, scenes.length - 1));
  }, [scenes.length, selected]);

  function begin(event: PointerEvent, next: Drag) {
    if (disabled) return;
    event.preventDefault();
    event.stopPropagation();
    (event.currentTarget as Element).setPointerCapture?.(event.pointerId);
    preview.current = scenes;
    setSelected(next.index);
    setDrag(next);
  }
  function moveTo(event: PointerEvent) {
    if (!drag) return;
    const seconds = (event.clientX - drag.x) / scale;
    const base = scenes;
    if (drag.kind === 'move') {
      const centre = starts[drag.index] + lengths[drag.index] / 2 + seconds;
      let target = base.length - 1;
      for (let index = 0; index < base.length; index += 1)
        if (centre < starts[index] + lengths[index] / 2) {
          target = index;
          break;
        }
      const next = [...base];
      const [scene] = next.splice(drag.index, 1);
      next.splice(target, 0, scene);
      preview.current = next;
    } else {
      const scene = base[drag.index];
      const [start, end] = span(scene, madeOf(scene));
      const shift = Math.round(seconds * (scene.speed ?? 1) * 10) / 10;
      const next = [...base];
      next[drag.index] =
        drag.kind === 'start'
          ? {
              ...scene,
              start: Math.min(end - SHORTEST, Math.max(0, drag.from + shift)),
              end,
            }
          : {
              ...scene,
              start,
              end: Math.max(start + SHORTEST, drag.from + shift),
            };
      preview.current = next;
    }
    redraw((value) => value + 1);
  }
  function end() {
    if (!drag) return;
    const next = preview.current;
    preview.current = null;
    setDrag(null);
    if (next && JSON.stringify(next) !== JSON.stringify(scenes)) {
      if (drag.kind === 'move') setSelected(next.indexOf(scenes[drag.index]));
      onChange(next);
    }
  }

  /** The selected scene's own time at the playhead, when the playhead is inside it. */
  function sourceAt(): number | null {
    const scene = scenes[selected];
    const shown = scene && madeOf(scene);
    if (
      !scene ||
      !shown ||
      playhead === null ||
      shown.at === null ||
      shown.length === null
    )
      return null;
    const into = playhead - (shown.at ?? 0);
    if (into <= 0 || into >= (shown.length ?? 0)) return null;
    return (
      Math.round(((scene.start ?? 0) + into * (scene.speed ?? 1)) * 10) / 10
    );
  }
  function key(event: KeyboardEvent) {
    if (event.target !== event.currentTarget) return;
    const scene = scenes[selected];
    const at = sourceAt();
    const replace = (index: number, value: VideoSceneWrite[]) => {
      const next = [...scenes];
      next.splice(index, 1, ...value);
      onChange(next);
    };
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
      event.preventDefault();
      onUndo();
    } else if (event.key === ' ' || event.key === 'k' || event.key === 'K') {
      event.preventDefault();
      if (event.key === ' ') onPlay();
      else onPause();
    } else if (event.key === 'j' || event.key === 'J')
      onSeek(Math.max(0, (playhead ?? 0) - 1));
    else if (event.key === 'l' || event.key === 'L')
      onSeek((playhead ?? 0) + 1);
    else if (event.key === 'ArrowLeft') setSelected(Math.max(0, selected - 1));
    else if (event.key === 'ArrowRight')
      setSelected(Math.min(scenes.length - 1, selected + 1));
    else if (disabled || !scene) return;
    else if ((event.key === 'i' || event.key === 'I') && at !== null)
      replace(selected, [{ ...scene, start: at }]);
    else if ((event.key === 'o' || event.key === 'O') && at !== null)
      replace(selected, [{ ...scene, end: at }]);
    else if ((event.key === 's' || event.key === 'S') && at !== null) {
      const [, finish] = span(scene, madeOf(scene));
      replace(selected, [
        { ...scene, end: at },
        { ...scene, scene_id: '', start: at, end: finish, card: '', line: '' },
      ]);
    } else if (
      (event.key === 'Delete' || event.key === 'Backspace') &&
      scenes.length > 1
    ) {
      event.preventDefault();
      replace(selected, []);
    } else return;
  }

  const width = Math.max(total * scale, 320);
  const at = (seconds: number) => `${seconds * scale}px`;
  return (
    <div className="video-timeline">
      <div className="video-timeline-tools">
        <span className="video-timeline-total">{lengthWords(total)}</span>
        <label className="video-timeline-zoom">
          Zoom
          <input
            type="range"
            min={20}
            max={200}
            step={10}
            value={Math.round(scale)}
            aria-label="Timeline zoom"
            onChange={(event) => setZoomed(Number(event.target.value))}
          />
        </label>
      </div>
      <div
        ref={scroller}
        className="video-timeline-scroll"
        role="application"
        aria-label="Timeline: Space plays, I and O set the start and end, S splits, Delete removes, Ctrl+Z undoes"
        tabIndex={0}
        onKeyDown={key}
      >
        <div className="video-timeline-tracks" style={{ width: `${width}px` }}>
          {playhead !== null && !drag && (
            <span
              className="video-timeline-playhead"
              style={{ left: at(playhead) }}
              aria-hidden
            />
          )}
          <div className="video-track" aria-label="Video">
            <span className="video-track-name">Video</span>
            {shownScenes.map((scene, index) => {
              const shown = madeOf(scene);
              return (
                <div
                  key={scene.scene_id || `new-${index}`}
                  className="video-block"
                  role="button"
                  tabIndex={-1}
                  aria-label={`Scene ${index + 1}, ${lengthWords(lengths[index])}`}
                  aria-pressed={selected === index}
                  data-selected={selected === index ? 'true' : undefined}
                  style={{ left: at(starts[index]), width: at(lengths[index]) }}
                  onPointerDown={(event) =>
                    begin(event, { kind: 'move', index, x: event.clientX })
                  }
                  onPointerMove={moveTo}
                  onPointerUp={end}
                  onPointerCancel={end}
                  onClick={() => {
                    setSelected(index);
                    if (shown?.at !== null && shown?.at !== undefined)
                      onSeek(shown.at);
                  }}
                >
                  {url && shown?.at !== null && shown?.at !== undefined && (
                    <video
                      src={`${url}#t=${(shown.at + 0.05).toFixed(2)}`}
                      preload="metadata"
                      muted
                      tabIndex={-1}
                      aria-hidden
                    />
                  )}
                  <span className="video-block-label">
                    {index + 1}
                    {(scene.speed ?? 1) !== 1 && ` · ${scene.speed}×`}
                  </span>
                  {!disabled && (
                    <>
                      <span
                        className="video-block-edge"
                        data-edge="start"
                        aria-hidden
                        onPointerDown={(event) =>
                          begin(event, {
                            kind: 'start',
                            index,
                            x: event.clientX,
                            from: span(scene, shown)[0],
                          })
                        }
                      />
                      <span
                        className="video-block-edge"
                        data-edge="end"
                        aria-hidden
                        onPointerDown={(event) =>
                          begin(event, {
                            kind: 'end',
                            index,
                            x: event.clientX,
                            from: span(scene, shown)[1],
                          })
                        }
                      />
                    </>
                  )}
                </div>
              );
            })}
          </div>
          {!narration && (
            <div className="video-track" aria-label="Voice">
              <span className="video-track-name">Voice</span>
              {shownScenes.map((scene, index) =>
                scene.line ? (
                  <span
                    key={`line-${scene.scene_id || index}`}
                    className="video-line-block"
                    title={scene.line}
                    style={{
                      left: at(starts[index]),
                      width: at(lengths[index]),
                    }}
                  >
                    {scene.line}
                  </span>
                ) : null,
              )}
            </div>
          )}
          <div className="video-track" aria-label="Cards">
            <span className="video-track-name">Cards</span>
            {shownScenes.map((scene, index) =>
              scene.card ? (
                <span
                  key={`card-${scene.scene_id || index}`}
                  className="video-card-block"
                  title={scene.card}
                  style={{
                    left: at(starts[index]),
                    width: at(Math.min(CARD_SECONDS, lengths[index])),
                  }}
                >
                  {scene.card}
                </span>
              ) : null,
            )}
          </div>
          {narration && (
            <div className="video-track" aria-label="Narration">
              <span className="video-track-name">Narration</span>
              <span
                className="video-music-block"
                title={narration}
                style={{ left: 0, width: at(total) }}
              >
                {narration}
              </span>
            </div>
          )}
          {music && (
            <div className="video-track" aria-label="Music">
              <span className="video-track-name">Music</span>
              <span
                className="video-music-block"
                title={music}
                style={{ left: 0, width: at(total) }}
              >
                {music}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
