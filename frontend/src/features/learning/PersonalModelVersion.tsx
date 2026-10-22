import { useEffect, useId, useRef, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { clientError } from '../../api/errors';
import type {
  LearningPersonalModel,
  LearningRuntime,
  LearningVersion,
} from '../../api/types';
import { writeClipboardText } from '../../platform/clipboard';
import { useRuntime } from '../../runtime';
import { ModalTask, useNotify } from '../../ui/overlays';
import { Button } from '../../ui/primitives';
import type { LearningIO } from './learning-state';
import { usePhone } from './use-phone';
import {
  EXPORT_STEPS,
  runtimeName,
  sizeWords,
  VERSION_FAILURES,
} from './words';

type VersionAction = {
  label: string;
  /** make_ready: Try again / Check again; runs_in: open the "Runs in" choice; try_ollama; compare: Compare now. */
  kind: 'make_ready' | 'runs_in' | 'try_ollama' | 'compare';
  primary?: boolean;
};

/** The newest personal-model version in words (designs/learning-ux.md §5, §9). Never a raw state. */
export type VersionView = {
  title: string;
  detail?: string;
  note?: string;
  /** The one command that loads it where the person starts the runtime themselves. */
  command?: string;
  actions: VersionAction[];
  /** For the status region: changes only when the version moves on. */
  announce: string;
};

function trainedWords(version: LearningVersion) {
  return version.examples
    ? `Trained on ${version.examples.toLocaleString()} examples of your work`
    : 'Trained on your work';
}

export function versionView(version: LearningVersion): VersionView {
  const name = `v${version.version}`;
  const where = runtimeName(version.runtime);
  const failure = VERSION_FAILURES[version.failure ?? ''] ?? '';
  const tryOllama: VersionAction[] =
    version.runtime !== 'ollama'
      ? [{ label: 'Try Ollama', kind: 'try_ollama' }]
      : [];
  switch (version.phase) {
    case 'exporting': {
      const step = EXPORT_STEPS[version.step ?? 'merge'] ?? EXPORT_STEPS.merge;
      return {
        title: `Preparing ${name} to run on this computer…`,
        detail: `${step.text} (step ${step.number} of 3). This takes a few minutes.`,
        actions: [],
        announce: `Preparing ${name} to run.`,
      };
    }
    case 'export_failed': {
      const disk =
        version.failure === 'not_enough_disk' &&
        version.needs_bytes != null &&
        version.free_bytes != null
          ? `It needs ${sizeWords(version.needs_bytes)} free; ${sizeWords(version.free_bytes)} free.`
          : '';
      return {
        title: `${name} couldn’t be prepared to run. ${failure}`.trim(),
        detail: [disk, 'What it learned is kept.'].filter(Boolean).join(' '),
        actions: [{ label: 'Try again', kind: 'make_ready', primary: true }],
        announce: `${name} couldn’t be prepared to run.`,
      };
    }
    case 'making_ready':
      return {
        title: `Making ${name} ready in ${where}…`,
        detail:
          'Loading it and checking it can answer and finish a task that uses a tool.',
        actions: [],
        announce: `Making ${name} ready in ${where}.`,
      };
    case 'ready':
      return {
        title: `${name} is ready to compare`,
        detail: `${trainedWords(version)} · runs in ${where}.`,
        note: 'Row-Bot compares it with your main model before asking you to use it. Until then your main model answers everything.',
        actions: [{ label: 'Compare now', kind: 'compare' }],
        announce: `${name} is ready to compare.`,
      };
    case 'waiting_for_you':
      return {
        title: `Load ${name} in ${where}`,
        detail:
          version.failure === 'not_loaded'
            ? `${where} is running, but ${name} isn’t loaded yet. Run this, then check again.`
            : `Start ${where} with this command, then check again. Row-Bot checks every few minutes too.`,
        command: version.command ?? undefined,
        actions: [
          { label: 'Check again', kind: 'make_ready', primary: true },
          ...tryOllama,
        ],
        announce: `Load ${name} in ${where}.`,
      };
    case 'runtime_failed':
    default:
      return {
        title: `${name} couldn’t start in ${where}. Your main model is still in use.`,
        detail: failure || undefined,
        actions: [
          { label: 'Try again', kind: 'make_ready', primary: true },
          ...(tryOllama.length
            ? tryOllama
            : [
                {
                  label: 'Change where it runs',
                  kind: 'runs_in' as const,
                },
              ]),
        ],
        announce: `${name} couldn’t start in ${where}.`,
      };
  }
}

function Command({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const { platform } = useRuntime();
  const id = useId();
  return (
    <div className="learning-command">
      <code id={id} className="learning-command-text">
        {text}
      </code>
      <Button
        variant="ghost"
        className="small"
        aria-describedby={id}
        onClick={() => {
          void writeClipboardText(text, platform.writeClipboard).then(
            setCopied,
          );
        }}
      >
        {copied ? (
          <Check size={14} aria-hidden />
        ) : (
          <Copy size={14} aria-hidden />
        )}
        {copied ? 'Copied' : 'Copy command'}
      </Button>
    </div>
  );
}

/** The newest version on the Personal model view: preparing, making ready, ready, or the way out. */
export function VersionCard({
  io,
  model,
  onRunsIn,
}: {
  io: LearningIO;
  model: LearningPersonalModel;
  onRunsIn: () => void;
}) {
  const notify = useNotify();
  const [busy, setBusy] = useState(false);
  const version = model.version;
  const view = version ? versionView(version) : null;
  const [said, setSaid] = useState('');
  const announced = useRef<string | null>(null);
  const announce = view?.announce ?? '';
  useEffect(() => {
    if (announced.current === null) {
      announced.current = announce;
      return;
    }
    if (announce && announce !== announced.current) {
      announced.current = announce;
      setSaid(announce);
    }
  }, [announce]);
  if (!version || !view) return null;

  async function run(action: VersionAction) {
    if (action.kind === 'runs_in') {
      onRunsIn();
      return;
    }
    setBusy(true);
    try {
      const receipt =
        action.kind === 'try_ollama'
          ? await io.run('choose_runtime', { runtime: 'ollama' })
          : action.kind === 'compare'
            ? await io.run('compare_version', { version: version!.version })
            : await io.run('make_ready');
      notify(receipt.summary);
    } catch (cause) {
      notify(clientError(cause).message, 'danger');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="monitor-section"
      aria-label={`Personal model v${version.version}`}
    >
      <header className="monitor-section-head">
        <h3>Personal model v{version.version}</h3>
      </header>
      <div className="learning-training" data-state={version.phase}>
        <div className="learning-training-head">
          <div className="learning-training-words">
            <p className="learning-training-title">{view.title}</p>
            {view.detail && <p className="learning-muted">{view.detail}</p>}
            {view.note && <p className="learning-muted">{view.note}</p>}
          </div>
          {view.actions.length > 0 && (
            <div className="learning-actions-inline">
              {view.actions.map((item) => (
                <Button
                  key={item.kind}
                  variant={item.primary ? 'primary' : undefined}
                  className="small"
                  disabled={busy}
                  onClick={() => void run(item)}
                >
                  {item.label}
                </Button>
              ))}
            </div>
          )}
        </div>
        {view.command && <Command text={view.command} />}
      </div>
      <p className="visually-hidden" role="status" aria-live="polite">
        {said}
      </p>
    </section>
  );
}

const RUNTIME_NOTES: Record<string, string> = {
  ollama: 'Row-Bot sets it up there for you and checks it.',
  lmstudio:
    'You load it with one command Row-Bot shows; Row-Bot then checks it.',
  llama_cpp:
    'You start llama.cpp’s server with one command Row-Bot shows; Row-Bot then checks it.',
  vllm: 'You start vLLM with one command Row-Bot shows; Row-Bot then checks it.',
};

/** "Runs in": where personal models run (designs/learning-ux.md §5). */
export function RunsInSheet({
  open,
  onOpenChange,
  io,
  model,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  io: LearningIO;
  model: LearningPersonalModel;
}) {
  const phone = usePhone();
  const notify = useNotify();
  const [choice, setChoice] = useState(model.runtime);
  const [busy, setBusy] = useState(false);
  const name = useId();
  useEffect(() => {
    if (open) setChoice(model.runtime);
  }, [open, model.runtime]);
  const runtimes: LearningRuntime[] = model.runtimes?.length
    ? model.runtimes
    : [{ id: 'ollama', name: 'Ollama', automated: true }];

  async function save() {
    setBusy(true);
    try {
      const receipt = await io.run('choose_runtime', { runtime: choice });
      onOpenChange(false);
      notify(receipt.summary);
    } catch (cause) {
      notify(clientError(cause).message, 'danger');
    } finally {
      setBusy(false);
    }
  }

  return (
    <ModalTask
      open={open}
      onOpenChange={onOpenChange}
      title="Where your personal model runs"
      description="Row-Bot uses it only after it passes a check there."
      kind={phone ? 'sheet' : 'dialog'}
    >
      <fieldset className="learning-fieldset">
        <legend className="visually-hidden">Runs in</legend>
        {runtimes.map((runtime) => (
          <label key={runtime.id} className="check-field learning-check">
            <input
              type="radio"
              name={name}
              value={runtime.id}
              checked={choice === runtime.id}
              onChange={() => setChoice(runtime.id)}
            />
            <span>
              <span>
                {runtime.name}
                {runtime.id === 'ollama'
                  ? runtime.installed === false
                    ? ' · not installed'
                    : ' · recommended'
                  : ''}
              </span>
              <small>{RUNTIME_NOTES[runtime.id] ?? ''}</small>
            </span>
          </label>
        ))}
      </fieldset>
      <div className="learning-sheet-actions">
        <Button onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button
          variant="primary"
          disabled={busy || choice === model.runtime}
          onClick={() => void save()}
          data-initial-focus
        >
          Use {runtimeName(choice)}
        </Button>
      </div>
    </ModalTask>
  );
}

/** "Ollama ✓" once the newest version passed its check there. */
export function runsInWords(model: LearningPersonalModel) {
  const where = runtimeName(model.runtime);
  const version = model.version;
  if (version?.phase === 'ready' && version.runtime === model.runtime)
    return `${where} ✓`;
  return where;
}

/** "v1 · 2 GB": main screens never name the file (O54; Export… and Experiment details do). */
export function versionFileWords(version: LearningVersion) {
  return version.file_bytes
    ? `v${version.version} · ${sizeWords(version.file_bytes)}`
    : '';
}
