import {
  type ReactNode,
  type RefObject,
  useEffect,
  useRef,
  useState,
} from 'react';
import {
  ArrowDown,
  ArrowLeftRight,
  ArrowUp,
  AudioLines,
  Check,
  CloudRain,
  Film,
  Image as Picture,
  MessageSquare,
  MonitorPlay,
  Music,
  RefreshCw,
  Send,
  Sparkles,
  Trash2,
  Type,
  UserRound,
  X,
  Zap,
  ZoomIn,
} from 'lucide-react';
import type {
  VideoPanelRequest,
  VideoScene,
  VideoSceneWrite,
} from '../../api/types';
import { useRuntime } from '../../runtime';
import {
  Disclosure,
  IconButton,
  Menu,
  Select,
  Toolbar,
  ToolbarSeparator,
} from '../../ui/primitives';
import { acquire, release } from './media-urls';
import {
  AMBIENCE,
  EFFECTS,
  FOOTAGE,
  MAX_EFFECTS,
  SPEEDS,
  type Footage,
  effectWords,
  footageOf,
  isPlanned,
  lengthWords,
  sceneSeconds,
} from './video-words';

/** An ambience runs under the scene (a cloud); a one-shot plays as it starts (a spark). */
function effectIcon(effect: string) {
  return AMBIENCE.has(effect) ? (
    <CloudRain size={12} aria-hidden />
  ) : (
    <Zap size={12} aria-hidden />
  );
}

/**
 * A scene's sound (C6): who says its line (a character, in its own voice; empty
 * for the narrator) and its effects and ambience, each removed with its own
 * button, added from one menu.
 */
function SceneSound({
  scene,
  number,
  speakers,
  editable,
  onChange,
}: {
  scene: VideoSceneWrite;
  number: number;
  /** The datalist of the video's characters, for who says the line. */
  speakers: string;
  editable: boolean;
  onChange: (patch: Partial<VideoSceneWrite>) => void;
}) {
  const effects = scene.effects ?? [];
  const missing = (Object.keys(EFFECTS) as (keyof typeof EFFECTS)[]).filter(
    (effect) => !effects.includes(effect),
  );
  return (
    <div className="video-scene-sound">
      <label className="video-scene-speaker">
        <UserRound size={14} aria-hidden />
        <input
          className="input"
          list={speakers}
          aria-label={`Scene ${number}: who says its line`}
          placeholder="Narrator"
          maxLength={40}
          value={scene.speaker ?? ''}
          disabled={!editable}
          onChange={(event) => onChange({ speaker: event.target.value })}
        />
      </label>
      {effects.length > 0 && (
        <ul
          className="video-scene-effects"
          aria-label={`Scene ${number} sound effects`}
        >
          {effects.map((effect) => (
            <li
              key={effect}
              className="video-scene-effect"
              data-ambience={AMBIENCE.has(effect) ? 'true' : undefined}
            >
              {effectIcon(effect)}
              <span>{effectWords(effect)}</span>
              {editable && (
                <IconButton
                  size="sm"
                  label={`Remove ${effectWords(effect)} from scene ${number}`}
                  onClick={() =>
                    onChange({
                      effects: effects.filter((item) => item !== effect),
                    })
                  }
                >
                  <X size={12} aria-hidden />
                </IconButton>
              )}
            </li>
          ))}
        </ul>
      )}
      {editable && effects.length < MAX_EFFECTS && (
        <Menu
          label={`Add a sound to scene ${number}`}
          hint={`Add a sound to scene ${number}`}
          iconOnly
          variant="ghost"
          className="icon-action icon-action-sm"
          actions={missing.map((effect, index) => ({
            label: EFFECTS[effect],
            description: AMBIENCE.has(effect)
              ? 'Under the whole scene'
              : 'As the scene starts',
            icon: effectIcon(effect),
            separatorBefore:
              index > 0 &&
              AMBIENCE.has(effect) !== AMBIENCE.has(missing[index - 1]),
            onSelect: () => onChange({ effects: [...effects, effect] }),
          }))}
        >
          <AudioLines size={16} aria-hidden />
        </Menu>
      )}
    </div>
  );
}

const FOOTAGE_ICONS: Record<Footage, ReactNode> = {
  clip: <Film size={14} aria-hidden />,
  picture: <Picture size={14} aria-hidden />,
  ai: <Sparkles size={14} aria-hidden />,
  card: <Type size={14} aria-hidden />,
  screen: <MonitorPlay size={14} aria-hidden />,
};

/** Unsent comments by scene, so one outlives the panel moving between side and full screen. */
const comments = new Map<string, string>();

/** How a request about one scene reads in the chat: the person's own words, with what Row-Bot needs to find it. */
export function sceneRequest(
  scene: { number: number; sceneId: string },
  video: { title: string; projectId: string },
  words: string,
): string {
  return `Scene ${scene.number} [${scene.sceneId}] of the video "${video.title}" (${video.projectId}): ${words}`;
}

/**
 * A storyboard card's frame (C1): a still of the scene's footage, read by the
 * server only once the card comes near the screen; a made video's scene
 * plays from there on a tap. A card without one shows what it will be.
 */
function SceneFrame({
  projectId,
  version,
  scene,
  shown,
  number,
  playable,
  onSeek,
}: {
  projectId: string;
  version: number;
  scene: VideoSceneWrite;
  shown: VideoScene | undefined;
  number: number;
  playable: boolean;
  onSeek: (at: number) => void;
}) {
  const { controller } = useRuntime();
  const holder = useRef<HTMLElement>(null);
  const [near, setNear] = useState(false);
  const [src, setSrc] = useState('');
  const wanted = Boolean(shown?.has_frame && scene.scene_id);
  useEffect(() => {
    const element = holder.current;
    if (!wanted || near || !element) return;
    if (typeof IntersectionObserver === 'undefined') {
      setNear(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setNear(true);
      },
      { rootMargin: '200px' },
    );
    observer.observe(element);
    return () => observer.disconnect();
    // A made video's scene becomes a button to play from: the new element is the one to watch.
  }, [wanted, near, playable]);
  const key = `frame:${projectId}:${version}:${scene.scene_id}`;
  useEffect(() => {
    if (!wanted || !near || !scene.scene_id) return;
    let live = true;
    void acquire(key, (signal) =>
      controller.videoSceneFrame(projectId, scene.scene_id!, version, signal),
    ).then((url) => {
      if (live) setSrc(url);
    });
    return () => {
      live = false;
      release(key);
    };
  }, [controller, key, near, projectId, scene.scene_id, version, wanted]);

  const footage = footageOf(shown ?? (scene as VideoScene));
  const seconds = sceneSeconds(scene, shown);
  const inside = (
    <>
      {src ? (
        <img src={src} alt="" decoding="async" />
      ) : (
        <span className="video-scene-frame-empty" data-footage={footage}>
          {footage === 'card' && scene.card ? (
            <span className="video-scene-frame-card">{scene.card}</span>
          ) : (
            FOOTAGE_ICONS[footage]
          )}
        </span>
      )}
      <span className="video-scene-frame-type">
        {FOOTAGE_ICONS[footage]}
        <span className="video-scene-frame-label">{FOOTAGE[footage]}</span>
      </span>
      {seconds !== null && (
        <span className="video-scene-frame-length">{lengthWords(seconds)}</span>
      )}
    </>
  );
  const at = shown?.at;
  if (playable && at !== null && at !== undefined)
    return (
      <button
        ref={holder as RefObject<HTMLButtonElement>}
        type="button"
        className="video-scene-frame"
        aria-label={`Play from scene ${number}`}
        onClick={() => onSeek(at)}
      >
        {inside}
      </button>
    );
  return (
    <span
      ref={holder as RefObject<HTMLSpanElement>}
      className="video-scene-frame"
      role="img"
      aria-label={`Scene ${number}: ${FOOTAGE[footage]}`}
    >
      {inside}
    </span>
  );
}

/**
 * One scene of a video as a storyboard card (C1): its frame and footage type,
 * what it shows, its line and who says it, its sound effects, length and music
 * cue. The person edits the line,
 * card and timing here; approves the scene as it is; and asks Row-Bot, in the
 * chat, to change it ("make this one moodier"), swap its footage or make a new
 * take of it.
 */
export function StoryboardCard({
  projectId,
  title,
  version,
  scene,
  shown,
  number,
  total,
  editable,
  approvable,
  planning,
  changed,
  partial,
  playable,
  busy,
  speakers,
  onChange,
  onMove,
  onRemove,
  onApprove,
  onAsk,
  onSeek,
}: {
  projectId: string;
  title: string;
  version: number;
  scene: VideoSceneWrite;
  /** The id of the datalist naming the video's characters. */
  speakers: string;
  shown: VideoScene | undefined;
  number: number;
  total: number;
  editable: boolean;
  /** A storyboard's saved scene: it can be approved as it is. */
  approvable: boolean;
  /** Its version isn't made yet: nothing is voiced until it is. */
  planning: boolean;
  changed: boolean;
  /** The video was made again from some kept scenes: each card says how it got its footage. */
  partial: boolean;
  playable: boolean;
  busy: boolean;
  onChange: (patch: Partial<VideoSceneWrite>) => void;
  onMove: (by: -1 | 1) => void;
  onRemove: () => void;
  onApprove: (approved: boolean) => void;
  onAsk: (
    text: string,
    action: VideoPanelRequest['action'],
  ) => Promise<boolean>;
  onSeek: (at: number) => void;
}) {
  const kept = `${projectId}:${scene.scene_id}`;
  const [commenting, setOpen] = useState(() => comments.has(kept));
  const [comment, setText] = useState(() => comments.get(kept) ?? '');
  function setComment(text: string) {
    comments.set(kept, text);
    setText(text);
  }
  function setCommenting(open: boolean) {
    if (open) comments.set(kept, comment);
    else comments.delete(kept);
    setOpen(open);
  }
  const field = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (commenting) field.current?.focus();
  }, [commenting]);
  const footage = footageOf(shown ?? (scene as VideoScene));
  const saved = Boolean(shown && scene.scene_id);
  // A piece of a video: its speed and trim. A picture or a card is held for its seconds.
  const cut = Boolean(scene.source) && footage !== 'picture';
  const ask = (words: string, action: VideoPanelRequest['action']) =>
    onAsk(
      sceneRequest(
        { number, sceneId: scene.scene_id ?? '' },
        { title, projectId },
        words,
      ),
      action,
    );
  async function send() {
    const words = comment.trim();
    if (!words) return;
    if (await ask(words, 'comment')) {
      setText('');
      setCommenting(false);
    }
  }
  const approved = Boolean(shown?.approved);
  return (
    <li
      className="video-scene"
      data-changed={changed ? 'true' : undefined}
      data-approved={approvable && approved ? 'true' : undefined}
      data-commenting={commenting ? 'true' : undefined}
    >
      <SceneFrame
        projectId={projectId}
        version={version}
        scene={scene}
        shown={shown}
        number={number}
        playable={playable}
        onSeek={onSeek}
      />
      <div className="video-scene-body">
        <div className="video-scene-meta">
          <strong>Scene {number}</strong>
          {approvable && approved && (
            <span className="video-scene-badge" data-tone="success">
              <Check size={12} aria-hidden />
              Approved
            </span>
          )}
          {partial && shown?.made === 'reused' && (
            <span
              className="video-scene-badge"
              title="Unchanged: reused from the last make"
            >
              Reused
            </span>
          )}
          {partial && shown?.made === 'new' && (
            <span className="video-scene-badge" data-tone="info">
              Made again
            </span>
          )}
          {scene.zoom && (
            <span className="video-scene-badge" title="Zoomed in">
              <ZoomIn size={12} aria-hidden />
              Zoom
            </span>
          )}
          {!planning && shown && !shown.voiced && scene.line && (
            <span className="video-scene-badge">Voiced when made</span>
          )}
        </div>
        {shown?.shows && <p className="video-scene-shows">{shown.shows}</p>}
        {shown && isPlanned(shown) && (
          <p className="video-scene-planned">
            {FOOTAGE[footage]} to make: Row-Bot makes it when you press Make.
          </p>
        )}
        <textarea
          className="input video-scene-line"
          aria-label={`Scene ${number} line`}
          placeholder="Words said over this scene"
          rows={2}
          maxLength={300}
          value={scene.line ?? ''}
          disabled={!editable}
          onChange={(event) => onChange({ line: event.target.value })}
        />
        <SceneSound
          scene={scene}
          number={number}
          speakers={speakers}
          editable={editable}
          onChange={onChange}
        />
        <div className="video-scene-controls">
          {cut ? (
            <Select
              aria-label={`Scene ${number} speed`}
              value={String(scene.speed ?? 1)}
              disabled={!editable}
              onChange={(event) =>
                onChange({ speed: Number(event.target.value) })
              }
            >
              {SPEEDS.map((speed) => (
                <option key={speed} value={speed}>
                  {speed}×
                </option>
              ))}
            </Select>
          ) : (
            // A title card, a picture or footage still to make: only how long it shows.
            <label className="video-scene-seconds">
              <input
                className="input"
                type="number"
                min={0.5}
                step={0.5}
                aria-label={`Scene ${number} lasts, in seconds`}
                value={scene.end ?? (footage === 'card' ? 3 : '')}
                placeholder="Its line"
                disabled={!editable}
                onChange={(event) =>
                  onChange({
                    end:
                      event.target.value === ''
                        ? null
                        : Math.max(0.5, Number(event.target.value) || 3),
                  })
                }
              />
              s
            </label>
          )}
          <input
            className="input video-scene-card"
            aria-label={`Scene ${number} card`}
            placeholder={
              footage === 'card' ? 'Title card' : 'Text on screen (optional)'
            }
            maxLength={120}
            value={scene.card ?? ''}
            disabled={!editable}
            onChange={(event) => onChange({ card: event.target.value })}
          />
        </div>
        {shown?.cue && (
          <p className="video-scene-cue">
            <Music size={12} aria-hidden />
            <span className="sr-only">Music: </span>
            {shown.cue}
          </p>
        )}
        {cut ? (
          <Disclosure summary="Trim" className="video-scene-trim">
            <label>
              From
              <input
                className="input"
                type="number"
                min={0}
                step={0.1}
                aria-label={`Scene ${number} starts at, seconds into ${scene.source}`}
                value={scene.start ?? 0}
                disabled={!editable}
                onChange={(event) =>
                  onChange({
                    start: Math.max(0, Number(event.target.value) || 0),
                  })
                }
              />
            </label>
            <label>
              To
              <input
                className="input"
                type="number"
                min={0}
                step={0.1}
                placeholder="End"
                aria-label={`Scene ${number} ends at, seconds into ${scene.source}`}
                value={scene.end ?? ''}
                disabled={!editable}
                onChange={(event) =>
                  onChange({
                    end:
                      event.target.value === ''
                        ? null
                        : Math.max(0, Number(event.target.value)),
                  })
                }
              />
            </label>
            <span className="video-scene-source">of {scene.source}</span>
          </Disclosure>
        ) : (
          footage === 'card' && (
            <span className="video-scene-source">
              A title card on your brand colour
            </span>
          )
        )}
        <Toolbar label={`Scene ${number}`} className="video-scene-actions">
          {approvable && (
            <IconButton
              size="sm"
              label={
                approved
                  ? `Scene ${number} approved`
                  : `Approve scene ${number}`
              }
              pressed={approved}
              disabled={busy}
              onClick={() => onApprove(!approved)}
            >
              <Check size={16} aria-hidden />
            </IconButton>
          )}
          <IconButton
            size="sm"
            label={`Comment on scene ${number}`}
            pressed={commenting}
            disabled={!saved || !editable}
            onClick={() => setCommenting(!commenting)}
          >
            <MessageSquare size={16} aria-hidden />
          </IconButton>
          <Menu
            label={`Swap scene ${number}'s footage`}
            hint={`Swap scene ${number}'s footage`}
            iconOnly
            variant="ghost"
            className="icon-action icon-action-sm"
            disabled={!saved || !editable || busy}
            actions={(Object.keys(FOOTAGE) as Footage[])
              .filter((kind) => kind !== footage)
              .map((kind) => ({
                label: FOOTAGE[kind],
                icon: FOOTAGE_ICONS[kind],
                onSelect: () =>
                  void ask(
                    `swap its footage for ${kind === 'ai' ? 'an AI shot' : `a ${FOOTAGE[kind].toLowerCase()}`}.`,
                    'swap',
                  ),
              }))}
          >
            <ArrowLeftRight size={16} aria-hidden />
          </Menu>
          {(footage === 'ai' || footage === 'picture') && (
            <IconButton
              size="sm"
              label={`New take of scene ${number}`}
              disabled={!saved || !editable || busy}
              onClick={() =>
                void ask(
                  footage === 'ai'
                    ? 'make a new take of its AI shot.'
                    : 'make a new picture for it.',
                  'new_take',
                )
              }
            >
              <RefreshCw size={16} aria-hidden />
            </IconButton>
          )}
          <ToolbarSeparator />
          <IconButton
            size="sm"
            label={`Move scene ${number} up`}
            disabled={!editable || number === 1}
            onClick={() => onMove(-1)}
          >
            <ArrowUp size={16} aria-hidden />
          </IconButton>
          <IconButton
            size="sm"
            label={`Move scene ${number} down`}
            disabled={!editable || number === total}
            onClick={() => onMove(1)}
          >
            <ArrowDown size={16} aria-hidden />
          </IconButton>
          <IconButton
            size="sm"
            label={`Remove scene ${number}`}
            disabled={!editable || total < 2}
            onClick={onRemove}
          >
            <Trash2 size={16} aria-hidden />
          </IconButton>
        </Toolbar>
        {commenting && (
          <form
            className="video-scene-comment"
            aria-label={`Comment on scene ${number}`}
            onSubmit={(event) => {
              event.preventDefault();
              void send();
            }}
          >
            <textarea
              ref={field}
              className="input"
              aria-label={`What to change in scene ${number}`}
              placeholder="Make this one moodier…"
              rows={2}
              maxLength={500}
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  void send();
                }
                if (event.key === 'Escape') setCommenting(false);
              }}
            />
            <IconButton
              size="sm"
              variant="primary"
              type="submit"
              label="Send to Row-Bot"
              disabled={busy || !comment.trim()}
            >
              <Send size={16} aria-hidden />
            </IconButton>
            <IconButton
              size="sm"
              label="Cancel"
              onClick={() => setCommenting(false)}
            >
              <X size={16} aria-hidden />
            </IconButton>
          </form>
        )}
      </div>
    </li>
  );
}
