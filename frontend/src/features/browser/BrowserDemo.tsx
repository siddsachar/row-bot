import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import {
  KeyRound,
  ListOrdered,
  Mic,
  MicOff,
  Pin,
  Square,
  Undo2,
  X,
} from 'lucide-react';
import type {
  BrowserDemoPage,
  BrowserDemoStep,
  BrowserDemoSummary,
  SkillOfferPage,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Drawer, useNotify, useOverlay } from '../../ui/overlays';
import { Button, Field, IconButton, Input } from '../../ui/primitives';
import SkillDraftSheet from '../skills/SkillDraftSheet';

const IDLE: BrowserDemoSummary = {
  id: '',
  status: 'stopped',
  ended: '',
  steps: 0,
  elapsed_seconds: 0,
};

/** "1:12" for 72 seconds. */
export function clock(seconds: number) {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

const LIMITS: Record<string, string> = {
  step_limit: 'Stopped at 200 steps, the most one demo keeps.',
  time_limit: 'Stopped at 15 minutes, the longest one demo runs.',
};

const NARRATION: Record<string, string> = {
  narration_no_model: 'Install a speech model in Settings › Voice to narrate',
  narration_no_ffmpeg: "Narration needs Row-Bot's audio support",
  narration_unavailable: "Narration isn't available right now",
};

/** Why "Show Row-Bot how" can't start now, or "" when it can. */
export function demoBlocked({
  page,
  acting,
  incognito,
}: {
  page: boolean;
  acting: boolean;
  incognito: boolean;
}) {
  if (incognito)
    return 'Not in an incognito chat: Row-Bot keeps nothing from it';
  if (!page) return 'Open a page first, then show Row-Bot how';
  if (acting) return 'Wait for Row-Bot to finish in the browser';
  return '';
}

/**
 * The microphone while showing Row-Bot how: off by default, one clip per
 * time it's on, each uploaded with when it started in the demo and
 * transcribed on this computer only.
 */
function useNarration(
  send: (audio: Blob, offset: number) => Promise<unknown>,
  onError: (message: string) => void,
) {
  const clip = useRef<{
    recorder: MediaRecorder;
    stream: MediaStream;
    chunks: Blob[];
    offset: number;
  } | null>(null);
  const sending = useRef<Promise<unknown>[]>([]);
  const [on, setOn] = useState(false);
  const [uploading, setUploading] = useState(0);

  const stop = useCallback(
    () =>
      new Promise<void>((resolve) => {
        const current = clip.current;
        clip.current = null;
        setOn(false);
        if (!current) return resolve();
        current.recorder.onstop = () => {
          current.stream.getTracks().forEach((track) => track.stop());
          const audio = new Blob(current.chunks, {
            type: current.recorder.mimeType || 'audio/webm',
          });
          if (!audio.size) return resolve();
          setUploading((count) => count + 1);
          const upload = send(audio, current.offset)
            .catch((cause: unknown) => onError(clientError(cause).message))
            .finally(() => setUploading((count) => count - 1));
          sending.current.push(upload);
          resolve();
        };
        current.recorder.stop();
      }),
    [onError, send],
  );

  const start = useCallback(
    async (offset: number) => {
      if (
        !navigator.mediaDevices?.getUserMedia ||
        typeof MediaRecorder === 'undefined'
      ) {
        onError("This browser can't record sound.");
        return;
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: true,
        });
        const mimeType = [
          'audio/webm;codecs=opus',
          'audio/ogg;codecs=opus',
          'audio/mp4',
        ].find((mime) => MediaRecorder.isTypeSupported(mime));
        const recorder = new MediaRecorder(
          stream,
          mimeType ? { mimeType } : undefined,
        );
        const chunks: Blob[] = [];
        recorder.ondataavailable = (event) => {
          if (event.data.size) chunks.push(event.data);
        };
        recorder.start(1000);
        clip.current = { recorder, stream, chunks, offset };
        setOn(true);
      } catch {
        onError("Row-Bot couldn't use the microphone. Check its permission.");
      }
    },
    [onError],
  );

  /** Stop the microphone and wait for every clip to be transcribed. */
  const settle = useCallback(async () => {
    await stop();
    await Promise.allSettled(sending.current);
    sending.current = [];
  }, [stop]);

  useEffect(
    () => () => {
      const current = clip.current;
      clip.current = null;
      current?.stream.getTracks().forEach((track) => track.stop());
    },
    [],
  );
  return { on, uploading, start, stop, settle };
}

/** One recorded step, with its example value and "always this". */
function StepRow({
  step,
  index,
  busy,
  onAlways,
}: {
  step: BrowserDemoStep;
  index: number;
  busy: boolean;
  onAlways?: (step: BrowserDemoStep, always: boolean) => void;
}) {
  return (
    <li className="browser-demo-step" data-secret={step.secret || undefined}>
      <span className="browser-demo-step-number" aria-hidden>
        {index + 1}
      </span>
      <span className="browser-demo-step-words">
        <span className="browser-demo-step-text">{step.text}</span>
        {step.site && step.kind !== 'open' && (
          <span className="browser-demo-step-site">{step.site}</span>
        )}
        {step.value && !step.secret && (
          <span className="browser-demo-value">
            <code title={step.value}>{step.value}</code>
            <span className="browser-demo-value-kind">
              {step.always ? 'Always this' : 'Input: asked each time'}
            </span>
            {onAlways && (
              <IconButton
                size="sm"
                pressed={step.always}
                disabled={busy}
                label={
                  step.always
                    ? 'Make this an input again'
                    : `Always type “${step.value}”`
                }
                onClick={() => onAlways(step, !step.always)}
              >
                <Pin size={14} aria-hidden />
              </IconButton>
            )}
          </span>
        )}
        {step.secret && (
          <span className="browser-demo-step-note">
            <KeyRound size={13} aria-hidden />
            {step.secret === 'sign_in' || step.secret === 'hidden'
              ? 'Not kept: you’ll do this part yourself'
              : `Not kept (${step.secret_text}): you’ll do this part yourself`}
          </span>
        )}
        {step.unsafe && (
          <span className="browser-demo-step-note">
            Its label was left out: it looked like instructions
          </span>
        )}
        {step.narration && (
          <q className="browser-demo-step-said">{step.narration}</q>
        )}
      </span>
    </li>
  );
}

export function DemoSteps({
  steps,
  busy = false,
  onAlways,
}: {
  steps: BrowserDemoStep[];
  busy?: boolean;
  onAlways?: (step: BrowserDemoStep, always: boolean) => void;
}) {
  if (!steps.length)
    return (
      <p className="browser-demo-empty">
        No steps yet. Click, type and open pages as you normally would.
      </p>
    );
  return (
    <ol className="browser-demo-steps" aria-label="Steps so far">
      {steps.map((step, index) => (
        <StepRow
          key={step.id}
          step={step}
          index={index}
          busy={busy}
          onAlways={onAlways}
        />
      ))}
    </ol>
  );
}

/**
 * Show Row-Bot how (C3) in the Browser pane: while the person does a task,
 * the bar says it's recording (steps and time) with Done, Undo last step,
 * Discard, the microphone and the steps so far. Done asks what it's for, then
 * drafts a skill into C1's review sheet, where Approve leads to Try it.
 */
export default function BrowserDemo({
  conversationId,
  current,
  signIn,
  chatTitle,
  stage,
}: {
  conversationId: string;
  /** The pane's line; null once the steps are discarded or drafted (the draft's sheet stays). */
  current: BrowserDemoSummary | null;
  signIn: boolean;
  chatTitle: string;
  /** Where the steps panel floats: over the page, not inside it. */
  stage: HTMLElement | null;
}) {
  const summary = current ?? IDLE;
  const { controller } = useRuntime();
  const notify = useNotify();
  const overlay = useOverlay();
  const [page, setPage] = useState<BrowserDemoPage | null>(null);
  const [busy, setBusy] = useState(false);
  const [panel, setPanel] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [purpose, setPurpose] = useState('');
  const [drafting, setDrafting] = useState<string | null>(null);
  const [now, setNow] = useState(() => performance.now());
  const recording = summary.status === 'recording';
  const demo = page?.demo?.id === summary.id ? page.demo : null;
  // The time counts on here between the server's updates of the pane's line.
  const key = `${summary.id}:${summary.elapsed_seconds}:${summary.steps}:${summary.status}`;
  // Marked with the clock as it is now: `now` only moves once a second while recording, and from a
  // bar opened earlier it held the time it opened, so a new recording began that far ahead (M6).
  const [mark, setMark] = useState(() => ({ key, at: performance.now() }));
  if (mark.key !== key) setMark({ key, at: performance.now() });
  useEffect(() => {
    if (!recording) return;
    const timer = setInterval(() => setNow(performance.now()), 1000);
    return () => clearInterval(timer);
  }, [recording]);
  const elapsed = recording
    ? summary.elapsed_seconds + Math.max(0, now - mark.at) / 1000
    : summary.elapsed_seconds;

  const read = useCallback(() => {
    controller.browserDemo(conversationId).then(setPage, () => undefined);
  }, [controller, conversationId]);
  // The steps follow the pane's line: read again whenever a step is added or taken back.
  useEffect(() => {
    if (summary.id) read();
  }, [read, summary.id, summary.steps, summary.status]);

  const fail = useCallback(
    (message: string) => notify(message, 'warning'),
    [notify],
  );
  const narration = useNarration(
    useCallback(
      (audio: Blob, offset: number) =>
        controller
          .sendBrowserDemoNarration(conversationId, summary.id, offset, audio)
          .then(setPage),
      [controller, conversationId, summary.id],
    ),
    fail,
  );

  // Stopped at a limit, or Done in another window: ask what it's for.
  const ended = summary.status === 'stopped' ? summary.ended : '';
  const announced = useRef('');
  useEffect(() => {
    if (!ended || announced.current === summary.id) return;
    announced.current = summary.id;
    void narration.stop();
    if (LIMITS[ended]) notify(LIMITS[ended]);
    setFinishing(true);
    // Once per demo.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ended, summary.id]);

  async function act(work: () => Promise<BrowserDemoPage>) {
    setBusy(true);
    try {
      setPage(await work());
    } catch (cause) {
      notify(clientError(cause).message, 'warning');
    } finally {
      setBusy(false);
    }
  }
  const control = (action: 'stop' | 'undo' | 'discard') =>
    act(() =>
      controller.controlBrowserDemo(conversationId, summary.id, action),
    );
  const always = (step: BrowserDemoStep, value: boolean) =>
    act(() =>
      controller.setBrowserDemoAlways(
        conversationId,
        summary.id,
        step.id,
        value,
      ),
    );
  const done = async () => {
    await narration.stop();
    await control('stop');
  };
  const discard = () =>
    overlay.open({
      kind: 'alert',
      title: 'Discard these steps?',
      description:
        'Row-Bot forgets the steps you showed it here. Nothing was saved.',
      confirmLabel: 'Discard',
      cancelLabel: 'Keep them',
      onConfirm: () => {
        overlay.close();
        void narration.stop().then(() => {
          setFinishing(false);
          void control('discard');
        });
      },
    });

  const narrationReady = page?.narration?.ready ?? false;
  const narrationWhy = page?.narration?.code
    ? (NARRATION[page.narration.code] ?? NARRATION.narration_unavailable)
    : '';
  const steps = demo?.steps ?? [];
  const count = summary.steps;

  return (
    <>
      {current && (
        <div
          className="browser-status browser-demo-bar"
          data-tone="accent"
          data-state={summary.status}
          role="status"
          aria-label="Show Row-Bot how"
        >
          {recording ? (
            <span className="browser-demo-dot" aria-hidden />
          ) : (
            <ListOrdered
              size={16}
              aria-hidden
              className="browser-status-icon"
            />
          )}
          <span className="browser-status-text">
            <strong>
              {recording ? 'Showing Row-Bot how' : 'Your steps are ready'}
            </strong>{' '}
            <span className="browser-demo-meta">
              {count} step{count === 1 ? '' : 's'}
              {recording && <> · {clock(elapsed)}</>}
            </span>
            {narration.uploading > 0 && (
              <span className="browser-demo-meta">
                {' '}
                · transcribing your narration…
              </span>
            )}
          </span>
          <span className="browser-demo-actions">
            {recording && (
              <IconButton
                size="sm"
                pressed={narration.on}
                disabled={!narrationReady}
                label={
                  !narrationReady
                    ? narrationWhy || 'Narration isn’t available'
                    : narration.on
                      ? 'Stop narrating'
                      : 'Narrate: say what you’re doing'
                }
                onClick={() =>
                  narration.on
                    ? void narration.stop()
                    : void narration.start(elapsed)
                }
              >
                {narration.on ? (
                  <Mic size={16} aria-hidden />
                ) : (
                  <MicOff size={16} aria-hidden />
                )}
              </IconButton>
            )}
            <IconButton
              size="sm"
              pressed={panel}
              label={panel ? 'Hide the steps' : 'Steps so far'}
              onClick={() => setPanel((value) => !value)}
            >
              <ListOrdered size={16} aria-hidden />
            </IconButton>
            {recording && (
              <IconButton
                size="sm"
                label="Undo last step"
                disabled={busy || !count}
                onClick={() => void control('undo')}
              >
                <Undo2 size={16} aria-hidden />
              </IconButton>
            )}
            <IconButton
              size="sm"
              label="Discard these steps"
              disabled={busy}
              onClick={discard}
            >
              <X size={16} aria-hidden />
            </IconButton>
            {recording ? (
              <Button
                variant="primary"
                className="small browser-demo-done"
                disabled={busy}
                onClick={() => void done()}
              >
                <Square size={12} aria-hidden fill="currentColor" />
                Done
              </Button>
            ) : (
              <Button
                variant="primary"
                className="small"
                disabled={busy}
                onClick={() => setFinishing(true)}
              >
                Draft skill
              </Button>
            )}
          </span>
        </div>
      )}
      {current && signIn && recording && (
        <div className="browser-status" data-tone="info" role="note">
          <KeyRound size={16} aria-hidden className="browser-status-icon" />
          <span className="browser-status-text">
            <strong>Sign-in step.</strong> Row-Bot won’t see or keep what you
            type here.
          </span>
        </div>
      )}
      {current &&
        panel &&
        stage &&
        createPortal(
          <section className="browser-demo-panel" aria-label="Steps so far">
            <header>
              <h3>Steps so far</h3>
              <IconButton
                size="sm"
                label="Hide the steps"
                onClick={() => setPanel(false)}
              >
                <X size={16} aria-hidden />
              </IconButton>
            </header>
            <DemoSteps steps={steps} busy={busy} onAlways={always} />
          </section>,
          stage,
        )}
      <Drawer
        open={Boolean(current) && finishing && !drafting}
        onOpenChange={setFinishing}
        modal
        title="What is this for?"
        description={`${count} step${count === 1 ? '' : 's'} recorded. Row-Bot turns them into a skill you review before it's saved.`}
        className="skill-draft-sheet browser-demo-finish"
        closeLabel="Back to the browser"
      >
        <form
          className="skill-draft-body browser-demo-finish-body"
          onSubmit={(event) => {
            event.preventDefault();
            void narration.settle().then(() => setDrafting(summary.id));
          }}
        >
          <Field
            label="What is this for? (optional)"
            hint="One line, for example: Download my monthly statement."
          >
            <Input
              value={purpose}
              maxLength={200}
              data-initial-focus
              placeholder="Download my monthly statement"
              onChange={(event) => setPurpose(event.target.value)}
            />
          </Field>
          <section aria-label="What you showed">
            <h3>What you showed</h3>
            <p className="browser-demo-hint">
              What you typed becomes an input, asked for each time. Pin a value
              to always type it as it is.
            </p>
            <DemoSteps steps={steps} busy={busy} onAlways={always} />
          </section>
        </form>
        <div className="skill-draft-actions">
          <Button
            variant="primary"
            disabled={busy || !count || narration.uploading > 0}
            onClick={() =>
              void narration.settle().then(() => setDrafting(summary.id))
            }
          >
            {narration.uploading > 0 ? 'Transcribing…' : 'Draft skill'}
          </Button>
          <Button variant="ghost" onClick={() => setFinishing(false)}>
            Back
          </Button>
        </div>
      </Drawer>
      {drafting && (
        <DemoDraft
          conversationId={conversationId}
          chatTitle={chatTitle}
          demoId={drafting}
          purpose={purpose.trim()}
          onClose={() => {
            setDrafting(null);
            // A draft that couldn't be written keeps the steps: the bar offers it again.
            read();
          }}
        />
      )}
    </>
  );
}

/** C1's review sheet for the skill drafted from a demo, with Try it after Approve. */
function DemoDraft({
  conversationId,
  chatTitle,
  demoId,
  purpose,
  onClose,
}: {
  conversationId: string;
  chatTitle: string;
  demoId: string;
  purpose: string;
  onClose: () => void;
}) {
  const { controller } = useRuntime();
  const navigate = useNavigate();
  const [offers, setOffers] = useState<SkillOfferPage | null>(null);
  const messageId = `demo:${demoId}`;
  return (
    <SkillDraftSheet
      open
      conversationId={conversationId}
      chatTitle={chatTitle}
      messageId={messageId}
      offer={offers?.items.find((item) => item.message_id === messageId)}
      onChanged={setOffers}
      draftingText="Writing the skill from your steps…"
      write={(signal) =>
        controller
          .draftDemoSkill(conversationId, demoId, purpose, signal)
          .then((next) => {
            setOffers(next);
            return next;
          })
      }
      onTry={async (name) => {
        const tried = await controller.trySkill(name);
        navigate(`/conversations/${encodeURIComponent(tried.conversation_id)}`);
      }}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    />
  );
}
