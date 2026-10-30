import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowDown,
  ArrowUp,
  CircleAlert,
  CircleCheck,
  Download,
  Repeat,
  RotateCcw,
  StepBack,
  StepForward,
  Trash2,
  ZoomIn,
} from 'lucide-react';
import { clientError } from '../../api/errors';
import type {
  VideoProjectView,
  VideoScene,
  VideoSceneWrite,
} from '../../api/types';
import {
  type CapabilityResult,
  type SavedFile,
  unavailable,
} from '../../platform/types';
import { useRuntime } from '../../runtime';
import {
  Button,
  Disclosure,
  ErrorState,
  IconButton,
  Segmented,
  Select,
  Skeleton,
  StatusDot,
  Toggle,
} from '../../ui/primitives';
import { shortTime } from '../shell/conversation-groups';
import { editLive } from '../shell/video-edits';
import { LivePreview } from './LivePreview';
import { acquire, release } from './media-urls';
import { VideoBrandEditor } from './VideoBrandEditor';
import { VideoTimeline } from './VideoTimeline';
import {
  SHAPES,
  SPEEDS,
  draftOf,
  shapeHint,
  isDirty,
  lengthWords,
  statusWords,
  versionWords,
} from './video-words';

/** One frame at 30 frames a second: what the frame-step buttons move. */
const FRAME = 1 / 30;
const POLL_MS = 1500;

type Draft = { scenes: VideoSceneWrite[]; captions: boolean; brand: boolean };
/** Unsaved edits per project and version, so they outlive the panel moving between side and full screen. */
const drafts = new Map<string, Draft>();
const WIDE = '(min-width: 768px)';

/** True on a desktop-sized window: the timeline is one click away there; a phone edits by scenes. */
function useWide(): boolean {
  const [wide, setWide] = useState(
    () => window.matchMedia?.(WIDE).matches ?? true,
  );
  useEffect(() => {
    const query = window.matchMedia?.(WIDE);
    if (!query) return;
    const update = () => setWide(query.matches);
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  return wide;
}

function SceneThumb({
  url,
  scene,
  number,
  onSeek,
}: {
  url: string;
  scene: VideoScene;
  number: number;
  onSeek: (at: number) => void;
}) {
  const at = scene.at;
  if (!url || at === null || at === undefined)
    return (
      <span className="video-scene-thumb video-scene-thumb-empty" aria-hidden>
        {number}
      </span>
    );
  return (
    <button
      type="button"
      className="video-scene-thumb"
      aria-label={`Play from scene ${number}`}
      onClick={() => onSeek(at)}
    >
      <video
        src={`${url}#t=${(at + 0.05).toFixed(2)}`}
        preload="metadata"
        muted
        playsInline
        tabIndex={-1}
        aria-hidden
      />
    </button>
  );
}

/**
 * A video project beside its chat (5.3.1 B3): the player, then the video as
 * scenes. Each scene's line is edited as text (re-voiced when the video is
 * made), with its speed, its card and its trim; scenes move and go. Saving
 * makes a new version and the video again; every version stays, so going back
 * is one tap. Once made, the review card shows the checks, and Post to X or
 * Save to the workspace go through Row-Bot, with their own approvals.
 */
export default function VideoPanel({
  conversationId,
  projectId,
  visible,
}: {
  conversationId: string;
  projectId: string;
  visible: boolean;
}) {
  const { controller, platform } = useRuntime();
  const [view, setView] = useState<VideoProjectView | null>(null);
  const [shown, setShown] = useState<number | undefined>();
  const [draft, setStoredDraft] = useState<Draft | null>(null);
  const [failed, setFailed] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [url, setUrl] = useState('');
  const [loop, setLoop] = useState(false);
  const [mode, setMode] = useState<'scenes' | 'timeline'>('scenes');
  // The brand kit loads the first time its section opens.
  const [brandOpen, setBrandOpen] = useState(false);
  const [time, setTime] = useState(0);
  const history = useRef<Draft[]>([]);
  const wide = useWide();
  const player = useRef<HTMLVideoElement>(null);

  const draftKey = view ? `${projectId}:${view.number}` : '';
  useEffect(() => {
    if (draftKey) setStoredDraft(drafts.get(draftKey) ?? null);
  }, [draftKey]);
  function setDraft(next: Draft | null) {
    if (!next) history.current = [];
    if (draftKey) {
      if (next) drafts.set(draftKey, next);
      else drafts.delete(draftKey);
    }
    setStoredDraft(next);
  }
  const status = view?.status;
  const working = status === 'voicing' || status === 'rendering';
  useEffect(() => {
    if (!visible) return;
    const abort = new AbortController();
    let timer = 0;
    const read = () =>
      controller.videoProject(projectId, shown, abort.signal).then(
        (next) => {
          if (!next) return;
          setView(next);
          setFailed('');
          if (
            next.status === 'voicing' ||
            next.status === 'rendering' ||
            next.exports.some((item) => editLive(item.edit))
          )
            timer = window.setTimeout(read, POLL_MS);
        },
        (cause) => {
          if (!abort.signal.aborted) setFailed(clientError(cause).message);
        },
      );
    void read();
    return () => {
      abort.abort();
      window.clearTimeout(timer);
    };
  }, [controller, projectId, shown, visible, attempt]);

  const reference = status === 'made' ? (view?.edit?.media_ref ?? '') : '';
  useEffect(() => {
    if (!reference) {
      setUrl('');
      return;
    }
    let live = true;
    void acquire(reference, (signal) =>
      controller.download(reference, signal),
    ).then((next) => {
      if (live) setUrl(next);
    });
    return () => {
      live = false;
      release(reference);
    };
  }, [controller, reference]);

  const scenes = view?.scenes ?? [];
  const edited = draft ?? (view ? draftOf(view) : null);
  const dirty = view && draft ? isDirty(view, draft) : false;
  const changed = useMemo(() => new Set(view?.changed ?? []), [view]);
  const latest = view ? view.number === view.latest : true;

  /** An edit, kept so Ctrl+Z on the timeline can take it back. */
  function edit(next: Draft) {
    if (edited) history.current.push(edited);
    setDraft(next);
  }
  function undo() {
    const previous = history.current.pop();
    if (previous) setDraft(previous);
  }
  function change(index: number, patch: Partial<VideoSceneWrite>) {
    if (!view || !edited) return;
    edit({
      ...edited,
      scenes: edited.scenes.map((scene, at) =>
        at === index ? { ...scene, ...patch } : scene,
      ),
    });
  }
  function move(index: number, by: -1 | 1) {
    if (!edited) return;
    const next = [...edited.scenes];
    const [scene] = next.splice(index, 1);
    next.splice(index + by, 0, scene);
    edit({ ...edited, scenes: next });
  }
  function remove(index: number) {
    if (!edited || edited.scenes.length < 2) return;
    edit({
      ...edited,
      scenes: edited.scenes.filter((_, at) => at !== index),
    });
  }

  async function act(run: () => Promise<VideoProjectView | undefined>) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const next = await run();
      if (next) {
        setView(next);
        setShown(undefined);
        setDraft(null);
      }
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }
  const save = () =>
    view && edited
      ? act(() =>
          controller.changeVideoProject(projectId, {
            base: view.number,
            scenes: edited.scenes,
            captions: edited.captions,
            brand: edited.brand,
            note: 'Edited in the panel',
          }),
        )
      : undefined;
  const restore = (number: number) =>
    act(() => controller.restoreVideoProject(projectId, number));

  async function ask(text: string) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await controller.controlsSettled?.(conversationId);
      const workspace = await controller.workspaceFor(conversationId);
      const selection = workspace.controls.model_selection;
      if (!selection) throw { code: 'model_selection_required' };
      const snapshot = controller.getSnapshot();
      const receipt = await controller.intent(
        conversationId,
        'conversation.submit',
        {
          submission_id: crypto.randomUUID(),
          text,
          attachment_refs: [],
          model_selection: selection,
        },
        snapshot.conversation?.id === conversationId
          ? snapshot.conversation.revision
          : workspace.revision,
      );
      if (receipt.status !== 'accepted' && receipt.status !== 'completed')
        throw { code: receipt.code ?? 'operation_uncertain' };
      setNotice('Asked Row-Bot in the chat.');
      controller.showLatest();
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  // The desktop app's Save dialog (Exports while it reconnects), or a browser download.
  async function download(edit = view?.edit) {
    if (!edit?.media_ref) return;
    const name = edit.output_name || 'video.mp4';
    const result = await platform
      .save(edit.media_ref, name)
      .catch((): CapabilityResult<SavedFile> =>
        unavailable('operation_failed'),
      );
    if (result.status === 'cancelled') return;
    if (result.status !== 'ok') {
      setError(
        result.reason === 'user_gesture_required'
          ? 'Press Download again to save the video.'
          : `Couldn't download ${name}. Try again.`,
      );
      return;
    }
    setError('');
    const saved = result.value;
    if (saved.kind === 'exports')
      setNotice(`Saved ${saved.fileName} to ${saved.folder}.`);
    else if (saved.kind === 'file') setNotice(`Saved ${name}.`);
  }

  function step(by: number) {
    const video = player.current;
    if (!video) return;
    video.pause();
    video.currentTime = Math.max(0, video.currentTime + by);
  }
  function seek(at: number) {
    const video = player.current;
    if (!video) return;
    video.currentTime = at;
    void video.play().catch(() => undefined);
  }

  if (failed && !view)
    return (
      <ErrorState
        title="Couldn't open this video"
        action={
          <Button onClick={() => setAttempt((value) => value + 1)}>
            Try again
          </Button>
        }
      >
        {failed}
      </ErrorState>
    );
  if (!view || !edited)
    return (
      <div className="video-panel">
        <Skeleton label="Opening the video" />
      </div>
    );

  const words = statusWords(view);
  const versionLabel = (number: number) => {
    const item = view.versions.find((entry) => entry.number === number);
    return item ? versionWords(item) : `Version ${number}`;
  };
  return (
    <section className="video-panel" aria-label={`${view.title} video`}>
      <header className="video-panel-header">
        <div className="video-panel-heading">
          <h2>{view.title}</h2>
          <StatusDot
            tone={words.tone}
            pulse={working}
            label={words.label}
            showLabel
          />
        </div>
        <div className="video-panel-versions">
          <Select
            aria-label="Version"
            value={String(view.number)}
            disabled={dirty || busy}
            onChange={(event) => {
              const number = Number(event.target.value);
              setShown(number === view.latest ? undefined : number);
              setDraft(null);
            }}
          >
            {view.versions.map((item) => (
              <option key={item.number} value={item.number}>
                {versionLabel(item.number)}
              </option>
            ))}
          </Select>
          {latest ? (
            view.latest > 1 && (
              <IconButton
                size="sm"
                label="Back to the version before"
                disabled={busy || dirty}
                onClick={() => void restore(view.latest - 1)}
              >
                <RotateCcw size={16} aria-hidden />
              </IconButton>
            )
          ) : (
            <Button disabled={busy} onClick={() => void restore(view.number)}>
              Use this version
            </Button>
          )}
        </div>
      </header>

      <div className="video-player" data-status={view.status}>
        {dirty ? (
          <LivePreview
            scenes={edited.scenes}
            shown={scenes}
            captions={edited.captions}
          />
        ) : url ? (
          <video
            ref={player}
            src={url}
            onTimeUpdate={(event) => setTime(event.currentTarget.currentTime)}
            onSeeked={(event) => setTime(event.currentTarget.currentTime)}
            controls
            playsInline
            loop={loop}
            aria-label={`${view.title}, version ${view.number}`}
          />
        ) : (
          <div className="video-player-empty">
            {working ? (
              <>
                <span>{words.detail}</span>
                {view.edit?.progress !== null &&
                  view.edit?.progress !== undefined && (
                    <progress
                      aria-label="Making the video"
                      max={1}
                      value={view.edit.progress}
                    />
                  )}
              </>
            ) : (
              <span>{words.detail}</span>
            )}
          </div>
        )}
        {url && !dirty && (
          <div
            className="video-player-tools"
            role="toolbar"
            aria-label="Player"
          >
            <IconButton
              size="sm"
              label="Back one frame"
              onClick={() => step(-FRAME)}
            >
              <StepBack size={16} aria-hidden />
            </IconButton>
            <IconButton
              size="sm"
              label="Forward one frame"
              onClick={() => step(FRAME)}
            >
              <StepForward size={16} aria-hidden />
            </IconButton>
            <IconButton
              size="sm"
              label="Loop"
              pressed={loop}
              onClick={() => setLoop((value) => !value)}
            >
              <Repeat size={16} aria-hidden />
            </IconButton>
            <IconButton
              size="sm"
              label="Download"
              onClick={() => void download(view.edit)}
            >
              <Download size={16} aria-hidden />
            </IconButton>
          </div>
        )}
      </div>
      {view.problem && (
        <p className="video-panel-problem" role="alert">
          {view.problem}
        </p>
      )}

      <div className="video-scenes-head">
        {wide ? (
          <Segmented
            size="sm"
            label="Edit the video as"
            value={mode}
            onChange={setMode}
            options={[
              { value: 'scenes', label: 'Scenes' },
              { value: 'timeline', label: 'Timeline' },
            ]}
          />
        ) : (
          <h3>Scenes</h3>
        )}
        <label className="video-captions">
          <Toggle
            label="Captions"
            checked={edited.captions}
            disabled={!latest}
            onChange={(event) =>
              edit({ ...edited, captions: event.target.checked })
            }
          />
          <span aria-hidden>Captions</span>
        </label>
        <label className="video-captions">
          <Toggle
            label="Brand kit"
            checked={edited.brand}
            disabled={!latest}
            onChange={(event) =>
              edit({ ...edited, brand: event.target.checked })
            }
          />
          <span aria-hidden>Brand</span>
        </label>
      </div>
      {wide && mode === 'timeline' && (
        <VideoTimeline
          scenes={edited.scenes}
          made={scenes}
          narration={view.narration}
          music={view.music}
          url={url}
          playhead={url && !dirty ? time : null}
          disabled={!latest}
          onChange={(next) => edit({ ...edited, scenes: next })}
          onUndo={undo}
          onSeek={(at) => {
            const video = player.current;
            if (video) video.currentTime = at;
          }}
          onPlay={() => {
            const video = player.current;
            if (video)
              void (video.paused
                ? video.play().catch(() => undefined)
                : video.pause());
          }}
          onPause={() => player.current?.pause()}
        />
      )}
      <ol
        className="video-scenes"
        aria-label="Scenes"
        hidden={wide && mode === 'timeline'}
      >
        {edited.scenes.map((scene, index) => {
          const number = index + 1;
          const shownScene = scenes.find(
            (item) => item.scene_id === scene.scene_id,
          );
          return (
            <li
              key={scene.scene_id || `new-${index}`}
              className="video-scene"
              data-changed={
                !dirty && scene.scene_id && changed.has(scene.scene_id)
                  ? 'true'
                  : undefined
              }
            >
              {shownScene ? (
                <SceneThumb
                  url={url}
                  scene={shownScene}
                  number={number}
                  onSeek={seek}
                />
              ) : (
                <span
                  className="video-scene-thumb video-scene-thumb-empty"
                  aria-hidden
                >
                  {number}
                </span>
              )}
              <div className="video-scene-body">
                <div className="video-scene-meta">
                  <strong>Scene {number}</strong>
                  {shownScene?.length !== null &&
                    shownScene?.length !== undefined && (
                      <span>{lengthWords(shownScene.length)}</span>
                    )}
                  {scene.zoom && (
                    <span className="video-scene-badge" title="Zoomed in">
                      <ZoomIn size={12} aria-hidden />
                      Zoom
                    </span>
                  )}
                  {shownScene && !shownScene.voiced && scene.line && (
                    <span className="video-scene-badge">Voiced when made</span>
                  )}
                </div>
                <textarea
                  className="input video-scene-line"
                  aria-label={`Scene ${number} line`}
                  placeholder="Words said over this scene"
                  rows={2}
                  maxLength={300}
                  value={scene.line ?? ''}
                  disabled={!latest}
                  onChange={(event) =>
                    change(index, { line: event.target.value })
                  }
                />
                <div className="video-scene-controls">
                  <Select
                    aria-label={`Scene ${number} speed`}
                    value={String(scene.speed ?? 1)}
                    disabled={!latest}
                    onChange={(event) =>
                      change(index, { speed: Number(event.target.value) })
                    }
                  >
                    {SPEEDS.map((speed) => (
                      <option key={speed} value={speed}>
                        {speed}×
                      </option>
                    ))}
                  </Select>
                  <input
                    className="input video-scene-card"
                    aria-label={`Scene ${number} card`}
                    placeholder="Title card"
                    maxLength={120}
                    value={scene.card ?? ''}
                    disabled={!latest}
                    onChange={(event) =>
                      change(index, { card: event.target.value })
                    }
                  />
                  <span className="video-scene-actions">
                    <IconButton
                      size="sm"
                      label={`Move scene ${number} up`}
                      disabled={!latest || index === 0}
                      onClick={() => move(index, -1)}
                    >
                      <ArrowUp size={16} aria-hidden />
                    </IconButton>
                    <IconButton
                      size="sm"
                      label={`Move scene ${number} down`}
                      disabled={!latest || index === edited.scenes.length - 1}
                      onClick={() => move(index, 1)}
                    >
                      <ArrowDown size={16} aria-hidden />
                    </IconButton>
                    <IconButton
                      size="sm"
                      label={`Remove scene ${number}`}
                      disabled={!latest || edited.scenes.length < 2}
                      onClick={() => remove(index)}
                    >
                      <Trash2 size={16} aria-hidden />
                    </IconButton>
                  </span>
                </div>
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
                      disabled={!latest}
                      onChange={(event) =>
                        change(index, {
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
                      disabled={!latest}
                      onChange={(event) =>
                        change(index, {
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
              </div>
            </li>
          );
        })}
      </ol>

      {dirty && (
        <div
          className="video-panel-save"
          role="group"
          aria-label="Unsaved changes"
        >
          <span>Unsaved changes</span>
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => setDraft(null)}
          >
            Discard
          </Button>
          <Button variant="primary" disabled={busy} onClick={() => void save()}>
            Save and make
          </Button>
        </div>
      )}

      {view.status === 'made' && view.checks.length > 0 && (
        <section className="video-review" aria-label="Review">
          <h3>
            {view.checks_passed ? (
              <CircleCheck size={16} aria-hidden />
            ) : (
              <CircleAlert size={16} aria-hidden />
            )}
            {view.checks_passed
              ? 'Ready to share'
              : 'Needs a look before sharing'}
          </h3>
          <ul>
            {view.checks.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <div className="video-review-actions">
            <Button
              disabled={busy}
              onClick={() =>
                void ask(
                  `Save version ${view.number} of the video "${view.title}" (${view.project_id}) to the workspace.`,
                )
              }
            >
              Save to workspace
            </Button>
            <Button
              disabled={busy}
              onClick={() =>
                void ask(
                  `Post version ${view.number} of the video "${view.title}" (${view.project_id}) to X. Show me the post first.`,
                )
              }
            >
              Post to X
            </Button>
          </div>
          <h3>Other shapes</h3>
          <ul className="video-exports" aria-label="Other shapes">
            {SHAPES.filter((item) => item.shape !== view.aspect).map((item) => {
              const made = view.exports.find(
                (entry) => entry.shape === item.shape,
              )?.edit;
              const hint = shapeHint(item, view.edit?.duration_seconds ?? 0);
              return (
                <li key={item.shape} className="video-export">
                  <span className="video-export-name">
                    <strong>{item.label}</strong>
                    <small>{hint}</small>
                  </span>
                  {made && editLive(made) ? (
                    <StatusDot tone="info" pulse label="Making" showLabel />
                  ) : made?.status === 'saved' ? (
                    <IconButton
                      size="sm"
                      label={`Download ${item.label}`}
                      onClick={() => void download(made)}
                    >
                      <Download size={16} aria-hidden />
                    </IconButton>
                  ) : (
                    <Button
                      disabled={busy}
                      onClick={() =>
                        void act(() =>
                          controller.exportVideoProject(projectId, item.shape),
                        )
                      }
                    >
                      {made ? 'Try again' : 'Make'}
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}
      <Disclosure
        summary="Brand kit"
        className="video-brand-disclosure"
        onOpenChange={(open) => open && setBrandOpen(true)}
      >
        {brandOpen && (
          <VideoBrandEditor
            onSaved={() =>
              setNotice(
                'Brand kit saved: save a change or make the video again to use it.',
              )
            }
          />
        )}
      </Disclosure>
      <p className="video-panel-foot" aria-live="polite">
        {error ? (
          <span role="alert" className="video-panel-error">
            {error}
          </span>
        ) : (
          notice ||
          (view.versions[0] &&
            `Version ${view.latest} ${view.versions[0].by === 'you' ? 'by you' : 'by Row-Bot'}, ${shortTime(view.versions[0].at)}`)
        )}
      </p>
    </section>
  );
}
