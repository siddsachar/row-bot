import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Clapperboard,
  CircleAlert,
  CircleCheck,
  Download,
  Repeat,
  RotateCcw,
  StepBack,
  StepForward,
} from 'lucide-react';
import { clientError } from '../../api/errors';
import type {
  VideoPanelRequest,
  VideoProjectView,
  VideoSceneWrite,
} from '../../api/types';
import {
  type CapabilityResult,
  type SavedFile,
  unavailable,
} from '../../platform/types';
import { useClientSelector, useRuntime } from '../../runtime';
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
import { useNotify } from '../../ui/overlays';
import { shortTime } from '../shell/conversation-groups';
import { announceSaved } from '../shell/save-file';
import { editLive } from '../shell/video-edits';
import { LivePreview } from './LivePreview';
import { acquire, release } from './media-urls';
import { StoryboardCard } from './StoryboardCard';
import { useVoiceChoices, VideoBrandEditor } from './VideoBrandEditor';
import { VideoTimeline } from './VideoTimeline';
import {
  SHAPES,
  clockWords,
  draftOf,
  isPlanned,
  shapeHint,
  isDirty,
  sceneSeconds,
  statusWords,
  versionWords,
} from './video-words';

/** One frame at 30 frames a second: what the frame-step buttons move. */
const FRAME = 1 / 30;
const POLL_MS = 1500;

type Draft = {
  scenes: VideoSceneWrite[];
  captions: boolean;
  brand: boolean;
  voice: string;
};
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

/**
 * A video project beside its chat (5.3.1 B3): the player, then the video as
 * storyboard cards (C1). A new video starts as a storyboard: nothing is made
 * until the person presses Make, after approving its scenes or commenting on
 * them (a comment reaches Row-Bot as their message in the chat). Each scene's
 * line is edited as text (re-voiced when the video is made), with its speed,
 * its card and its trim; scenes move and go. Saving makes a new version, and
 * the video again once it was made; every version stays, so going back is one
 * tap. Once made, the review card shows the checks, and Post to X or Save to
 * the workspace go through Row-Bot, with their own approvals.
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
  const notify = useNotify();
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
  const voices = useVoiceChoices();
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
  // A step in the chat may have changed this video (a comment on a card): the panel follows it, unless the person
  // is in the middle of an edit.
  const revision = useClientSelector((value) =>
    value.projection?.conversation_id === conversationId
      ? value.projection?.checkpoint_revision
      : undefined,
  );
  const editing = useRef(false);
  const seenRevision = useRef(revision);
  useEffect(() => {
    if (revision === seenRevision.current) return;
    seenRevision.current = revision;
    if (!editing.current) setAttempt((value) => value + 1);
  }, [revision]);
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
  editing.current = dirty;
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
        // Being made now: read it again until it's made.
        if (next.status === 'voicing' || next.status === 'rendering')
          setAttempt((value) => value + 1);
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
            voice: edited.voice,
            note: 'Edited in the panel',
          }),
        )
      : undefined;
  const restore = (number: number) =>
    act(() => controller.restoreVideoProject(projectId, number));

  /**
   * The person's request, sent to Row-Bot as their message in this chat; a
   * storyboard's request says which of the panel's buttons sent it.
   */
  async function ask(
    text: string,
    action?: VideoPanelRequest['action'],
  ): Promise<boolean> {
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
          ...(action ? { video_panel: { action } } : {}),
        },
        snapshot.conversation?.id === conversationId
          ? snapshot.conversation.revision
          : workspace.revision,
      );
      if (receipt.status !== 'accepted' && receipt.status !== 'completed')
        throw { code: receipt.code ?? 'operation_uncertain' };
      setNotice('Asked Row-Bot in the chat.');
      controller.showLatest();
      return true;
    } catch (cause) {
      setError(clientError(cause).message);
      return false;
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
    announceSaved(notify, result.value, name);
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
  const storyboard = view.status === 'draft' && latest;
  const planned = scenes.filter(isPlanned).length;
  const approved = scenes.filter((scene) => scene.approved).length;
  const lengths = edited.scenes.map((scene) =>
    sceneSeconds(
      scene,
      scenes.find((item) => item.scene_id === scene.scene_id),
    ),
  );
  const total = lengths.every((value) => value !== null)
    ? lengths.reduce<number>((sum, value) => sum + (value ?? 0), 0)
    : null;
  const makes = scenes.map((scene) => scene.made ?? '');
  const partial = view.status === 'made' && makes.includes('reused');
  // Footage still to make (an AI shot) is Row-Bot's: Make asks it in the chat.
  const make = () =>
    planned
      ? void ask(
          `Make the video "${view.title}" (${view.project_id}) from its storyboard, version ${view.number}.`,
          'make',
        )
      : void act(() => controller.makeVideoProject?.(projectId));
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

      {view.logline && <p className="video-panel-logline">{view.logline}</p>}
      {storyboard && !dirty ? (
        <section className="video-storyboard-bar" aria-label="Storyboard">
          <div className="video-storyboard-sum">
            <strong>Storyboard</strong>
            <span>
              {scenes.length} scenes
              {total !== null && ` · about ${clockWords(total)}`}
              {` · ${approved} of ${scenes.length} approved`}
            </span>
            {planned > 0 && (
              <small>
                {planned === 1
                  ? '1 scene needs its footage: Row-Bot makes it first.'
                  : `${planned} scenes need their footage: Row-Bot makes it first.`}
              </small>
            )}
          </div>
          <Button variant="primary" disabled={busy} onClick={make}>
            <Clapperboard size={16} aria-hidden />
            Make video
          </Button>
        </section>
      ) : (
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
      )}
      {partial && (
        <p className="video-panel-made">
          {`Made again: ${makes.filter((made) => made === 'new').length} changed scene${makes.filter((made) => made === 'new').length === 1 ? '' : 's'} drawn, ${makes.filter((made) => made === 'reused').length} reused.`}
        </p>
      )}
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
              { value: 'scenes', label: 'Storyboard' },
              { value: 'timeline', label: 'Timeline' },
            ]}
          />
        ) : (
          <h3>Storyboard</h3>
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
        <Select
          aria-label="Voice for this video"
          className="video-voice"
          value={edited.voice}
          disabled={!latest}
          onChange={(event) => edit({ ...edited, voice: event.target.value })}
        >
          <option value="">Brand kit's voice</option>
          {voices.map((voice) => (
            <option key={voice.value} value={voice.value}>
              {voice.label}
            </option>
          ))}
        </Select>
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
        aria-label="Storyboard"
        hidden={wide && mode === 'timeline'}
      >
        {edited.scenes.map((scene, index) => (
          <StoryboardCard
            key={scene.scene_id || `new-${index}`}
            projectId={projectId}
            title={view.title}
            version={view.number}
            scene={scene}
            shown={scenes.find((item) => item.scene_id === scene.scene_id)}
            number={index + 1}
            total={edited.scenes.length}
            editable={latest}
            approvable={storyboard && !dirty}
            planning={view.status === 'draft'}
            changed={
              !dirty && Boolean(scene.scene_id) && changed.has(scene.scene_id!)
            }
            partial={partial && !dirty}
            playable={Boolean(url) && !dirty}
            busy={busy}
            onChange={(patch) => change(index, patch)}
            onMove={(by) => move(index, by)}
            onRemove={() => remove(index)}
            onApprove={(yes) =>
              void act(() =>
                controller.approveVideoScenes(projectId, {
                  base: view.number,
                  scene_ids: [scene.scene_id!],
                  approved: yes,
                }),
              )
            }
            onAsk={ask}
            onSeek={seek}
          />
        ))}
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
            {view.status === 'draft' ? 'Save' : 'Save and make'}
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
