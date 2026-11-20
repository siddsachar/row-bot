import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import {
  CircleCheck,
  CircleDashed,
  CircleMinus,
  CircleStop,
  CircleX,
  ExternalLink,
  LoaderCircle,
  RotateCw,
  Search,
  Trash2,
} from 'lucide-react';
import { clientError } from '../../api/errors';
import { useNotify } from '../../ui/overlays';
import type { LearningCheckedModel, LearningOpenModel } from '../../api/types';
import {
  Button,
  IconButton,
  Input,
  Progress,
  StatusDot,
  type Tone,
} from '../../ui/primitives';
import type { LearningIO } from './learning-state';
import { plural, sizeWords } from './words';
import './learning.css';

/** The check's four parts, as the person reads them (C1 Part 4). */
const CHECKS: Record<string, string> = {
  fits: 'Fits the graphics card',
  steps: 'Learns: a few training steps',
  export: 'Runs on Row-Bot’s own server',
  tools: 'Calls Row-Bot’s tools',
};
const ITEM_STATES: Record<string, { words: string; icon: typeof CircleCheck }> =
  {
    waiting: { words: 'Waiting', icon: CircleDashed },
    running: { words: 'Checking…', icon: LoaderCircle },
    passed: { words: 'Passed', icon: CircleCheck },
    failed: { words: 'Didn’t pass', icon: CircleX },
    skipped: { words: 'Not checked', icon: CircleMinus },
  };
const STATES: Record<string, { label: string; tone: Tone }> = {
  not_checked: { label: 'Not checked', tone: 'neutral' },
  downloading: { label: 'Downloading', tone: 'info' },
  checking: { label: 'Checking', tone: 'info' },
  passed: { label: 'Passed', tone: 'success' },
  failed: { label: 'Didn’t pass', tone: 'danger' },
  stopped: { label: 'Stopped', tone: 'neutral' },
};

type Where = 'this_computer' | 'hugging_face';

/** What Other models are, in a sentence or three (the picker, and the Hugging Face sheet's description). */
export const OTHER_MODELS_WORDS =
  'Any open model on Hugging Face. Row-Bot checks it on this computer before you can choose it: that it fits, learns, runs here and calls Row-Bot’s tools. Other models aren’t certified.';

function sizeOf(model: { params_b?: number | null }) {
  return model.params_b ? `${model.params_b.toLocaleString()}B` : '';
}

function line(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(' · ');
}

/** Where a passed model trains, in words: "Trains on this computer and on Hugging Face". */
function whereWords(model: LearningCheckedModel) {
  if (model.trains_here && model.hugging_face_flavor)
    return 'Trains on this computer and on Hugging Face';
  if (model.trains_here) return 'Trains on this computer';
  if (model.hugging_face_flavor)
    return 'Trains on Hugging Face: too big to train on this graphics card';
  return '';
}

/**
 * Settings › Starting model's second level (C1 Part 4, O138): any open model on Hugging Face. Search, see what
 * each model is, its licence and whether Row-Bot can try it; check one on this computer (its licence agreed in
 * the same ask when it has terms); then see what was checked and use it where Learning trains. Unsupported kinds
 * are said plainly, never tried; a gated model links to Hugging Face; a model this card can't run afterwards is
 * never offered (O131). Other models aren't certified, and say so wherever they appear.
 */
export function OtherModels({
  io,
  titled = true,
}: {
  io: LearningIO;
  /** Its own heading and words (false: the sheet it fills says them). */
  titled?: boolean;
}) {
  const notify = useNotify();
  const searchId = useId();
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<LearningOpenModel[] | null>(null);
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState<LearningOpenModel | null>(null);
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState('');
  const personal = io.snapshot?.personal_model;
  const checked = personal?.other_models ?? [];
  const where: Where =
    personal?.train_where === 'hugging_face' ? 'hugging_face' : 'this_computer';
  const engineReady = personal?.engine === 'installed';
  const running = checked.find(
    (item) => item.state === 'downloading' || item.state === 'checking',
  );
  const byId = new Map(checked.map((item) => [item.id, item]));

  async function search() {
    if (query.trim().length < 2) return;
    setBusy(true);
    setFailure('');
    try {
      const result = await io.models(query.trim());
      setFound(result.models ?? []);
      if (result.failure)
        setFailure(
          result.failure === 'unreachable'
            ? 'Row-Bot couldn’t reach Hugging Face. Check your connection, then try again.'
            : 'Hugging Face didn’t answer as expected. Try again.',
        );
    } catch (cause) {
      setFailure(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  async function run(
    action:
      | 'check_model'
      | 'stop_model_check'
      | 'forget_model'
      | 'change_starting_model'
      | 'hf_choose_model',
    model: string,
    licenceOk = false,
  ) {
    setBusy(true);
    setError('');
    try {
      const receipt = await io.run(action, {
        model,
        ...(licenceOk ? { licence_ok: true } : {}),
      });
      notify(receipt.summary);
      setAsking(null);
      setAgreed(false);
      return true;
    } catch (cause) {
      setError(clientError(cause).message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="learning-stack" aria-label="Other models">
      <div className="learning-stack-tight">
        {titled && (
          <>
            <h3 className="learning-label learning-models-heading">
              Other models
            </h3>
            <p className="learning-muted">{OTHER_MODELS_WORDS}</p>
          </>
        )}
        {!engineReady && (
          <p className="learning-muted" role="note">
            To check a model, first download the training engine
            {personal?.engine_download_bytes
              ? ` (${sizeWords(personal.engine_download_bytes)})`
              : ''}
            : Settings › Learning › Training engine.
          </p>
        )}
      </div>
      {error && (
        <p role="alert" className="learning-error">
          {error}
        </p>
      )}
      {checked.length > 0 && (
        <ul className="learning-models" aria-label="Checked models">
          {checked.map((model) => (
            <CheckedModel
              key={model.id}
              model={model}
              busy={busy}
              where={where}
              engineReady={engineReady}
              otherRunning={!!running && running.id !== model.id}
              inUse={
                personal?.starting_model.id === model.id ||
                personal?.hugging_face?.model === model.id
              }
              onRun={(action) => run(action, model.id)}
            />
          ))}
        </ul>
      )}
      <form
        className="learning-model-search"
        role="search"
        aria-label="Search Hugging Face"
        onSubmit={(event) => {
          event.preventDefault();
          void search();
        }}
      >
        <label className="visually-hidden" htmlFor={searchId}>
          Search Hugging Face
        </label>
        <Input
          id={searchId}
          type="search"
          value={query}
          placeholder="Search Hugging Face…"
          onChange={(event) => setQuery(event.target.value)}
        />
        <IconButton
          type="submit"
          label="Search"
          variant="secondary"
          disabled={busy || query.trim().length < 2}
        >
          <Search size={16} aria-hidden />
        </IconButton>
      </form>
      {failure && (
        <p role="alert" className="learning-error">
          {failure}
        </p>
      )}
      {asking && (
        <CheckAsk
          model={asking}
          agreed={agreed}
          onAgree={setAgreed}
          busy={busy}
          onCancel={() => {
            setAsking(null);
            setAgreed(false);
          }}
          onCheck={() =>
            void run('check_model', asking.id, asking.licence_kind === 'terms')
          }
        />
      )}
      {found && !asking && (
        <ul className="learning-models" aria-label="Search results">
          {found.length === 0 && (
            <li className="learning-model learning-muted">
              No chat models found. Try another name.
            </li>
          )}
          {found.map((model) => (
            <SearchResult
              key={model.id}
              model={model}
              checked={byId.get(model.id)}
              canCheck={!busy && !running && engineReady}
              onCheck={() => setAsking(model)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function SearchResult({
  model,
  checked,
  canCheck,
  onCheck,
}: {
  model: LearningOpenModel;
  checked?: LearningCheckedModel;
  canCheck: boolean;
  onCheck: () => void;
}) {
  let note: string | null = null;
  let action = null;
  if (model.unsupported) note = model.unsupported;
  else if (model.licence_kind === 'gated') {
    note = 'Gated: Hugging Face asks you to request access first.';
    action = (
      <a
        className="learning-model-link"
        href={`https://huggingface.co/${model.repo}`}
        target="_blank"
        rel="noreferrer noopener"
      >
        Request access
        <ExternalLink size={13} aria-hidden />
      </a>
    );
  } else if (!model.runs_here)
    note = `Too big to run on this computer afterwards${
      model.runs_here_gb
        ? ` (it needs about ${Math.ceil(model.runs_here_gb)} GB of the graphics card)`
        : ''
    }.`;
  else if (checked) {
    const state = STATES[checked.state] ?? STATES.not_checked;
    action = <StatusDot tone={state.tone} label={state.label} showLabel />;
  } else
    action = (
      <Button className="small" disabled={!canCheck} onClick={onCheck}>
        Check…
      </Button>
    );
  return (
    <li className="learning-model" aria-label={model.name}>
      <div className="learning-model-head">
        <div className="learning-model-text">
          <strong className="learning-model-name">{model.name}</strong>
          <span className="learning-model-meta">
            {line(sizeOf(model), model.licence, model.repo)}
          </span>
        </div>
        {action}
      </div>
      {note && <p className="learning-model-note">{note}</p>}
    </li>
  );
}

function CheckAsk({
  model,
  agreed,
  onAgree,
  busy,
  onCancel,
  onCheck,
}: {
  model: LearningOpenModel;
  agreed: boolean;
  onAgree: (value: boolean) => void;
  busy: boolean;
  onCancel: () => void;
  onCheck: () => void;
}) {
  const terms = model.licence_kind === 'terms';
  const ask = useAskInView();
  return (
    <div
      ref={ask}
      className="learning-model-ask"
      role="group"
      aria-label={`Check ${model.name}?`}
    >
      <p>
        <strong>Check {model.name}?</strong>
      </p>
      <p>
        Row-Bot downloads it from Hugging Face, trains it a few steps on this
        computer’s graphics card, converts it to run here and asks it to call
        Row-Bot’s tools on a short sample. That takes a while and uses the
        graphics card. Nothing is used until you choose it.
      </p>
      {terms && (
        <div className="learning-stack-tight">
          <p>
            {model.name} uses the {model.licence}. Its key terms:
          </p>
          <ul className="learning-list">
            {(model.terms ?? []).map((term) => (
              <li key={term}>{term}</li>
            ))}
          </ul>
          <label className="check-field learning-check">
            <input
              type="checkbox"
              checked={agreed}
              onChange={(event) => onAgree(event.target.checked)}
            />
            <span>
              <span>
                I agree to the {model.licence} (
                <a
                  href={model.licence_url}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  read it
                </a>
                )
              </span>
            </span>
          </label>
        </div>
      )}
      <div className="learning-actions-inline learning-model-ask-actions">
        <Button className="small" disabled={busy} onClick={onCancel}>
          Back
        </Button>
        <Button
          className="small"
          variant="primary"
          disabled={busy || (terms && !agreed)}
          onClick={onCheck}
        >
          Check
        </Button>
      </div>
    </div>
  );
}

function CheckedModel({
  model,
  busy,
  where,
  engineReady,
  otherRunning,
  inUse,
  onRun,
}: {
  model: LearningCheckedModel;
  busy: boolean;
  where: Where;
  engineReady: boolean;
  otherRunning: boolean;
  inUse: boolean;
  onRun: (
    action:
      | 'check_model'
      | 'stop_model_check'
      | 'forget_model'
      | 'change_starting_model'
      | 'hf_choose_model',
  ) => Promise<boolean>;
}) {
  const [confirming, setConfirming] = useState(false);
  const active = model.state === 'downloading' || model.state === 'checking';
  const state = STATES[model.state] ?? STATES.not_checked;
  const passed = (model.sample ?? []).filter((item) => item.passed).length;
  const onHuggingFace = where === 'hugging_face';
  const usable =
    model.state === 'passed' &&
    (onHuggingFace ? !!model.hugging_face_flavor : !!model.trains_here);
  const unusableHere =
    model.state === 'passed' &&
    !usable &&
    (onHuggingFace
      ? 'It can’t train on Hugging Face: no single card there holds it.'
      : 'Too big to train on this graphics card. Choose Hugging Face in Where to train to train it there.');
  return (
    <li className="learning-model" aria-label={model.name}>
      <div className="learning-model-head">
        <div className="learning-model-text">
          <span className="learning-model-title">
            <strong className="learning-model-name">{model.name}</strong>
            <StatusDot
              tone={state.tone}
              label={state.label}
              showLabel
              pulse={active}
            />
          </span>
          <span className="learning-model-meta">
            {line(
              sizeOf(model),
              model.licence,
              'not certified',
              model.state === 'passed' && whereWords(model),
            )}
          </span>
        </div>
        <span className="learning-model-actions">
          {inUse && (
            <span className="learning-model-in-use">In use for training</span>
          )}
          {usable && !inUse && !confirming && (
            <Button
              className="small"
              disabled={busy}
              onClick={() => setConfirming(true)}
            >
              {onHuggingFace ? 'Use on Hugging Face…' : 'Use for training…'}
            </Button>
          )}
          {(model.state === 'failed' || model.state === 'stopped') &&
            !inUse && (
              <IconButton
                size="sm"
                label={`Check ${model.name} again`}
                disabled={busy || otherRunning || !engineReady}
                onClick={() => void onRun('check_model')}
              >
                <RotateCw size={15} aria-hidden />
              </IconButton>
            )}
          {active ? (
            <IconButton
              size="sm"
              label="Stop the check"
              disabled={busy}
              onClick={() => void onRun('stop_model_check')}
            >
              <CircleStop size={15} aria-hidden />
            </IconButton>
          ) : (
            !inUse && (
              <IconButton
                size="sm"
                label={`Remove ${model.name}`}
                disabled={busy}
                onClick={() => void onRun('forget_model')}
              >
                <Trash2 size={15} aria-hidden />
              </IconButton>
            )
          )}
        </span>
      </div>
      {model.state === 'downloading' && model.download_bytes > 0 && (
        <Progress
          label={
            model.downloaded_bytes > 0
              ? `Downloading: ${sizeWords(model.downloaded_bytes)} of ${sizeWords(model.download_bytes)}`
              : `Downloading ${sizeWords(model.download_bytes)}`
          }
          value={Math.round(
            (model.downloaded_bytes / model.download_bytes) * 100,
          )}
        />
      )}
      <ul className="learning-model-checks" aria-label="What was checked">
        {model.items.map((item) => {
          const shown = ITEM_STATES[item.state] ?? ITEM_STATES.waiting;
          const Icon = shown.icon;
          return (
            <li key={item.id} data-state={item.state}>
              <Icon
                size={15}
                aria-hidden
                className="learning-model-check-icon"
              />
              <span>
                <span className="learning-model-check-name">
                  {CHECKS[item.id]}
                </span>
                {item.words ? (
                  <>
                    <span className="visually-hidden">: {shown.words}.</span>
                    <span className="learning-model-check-words">
                      {' '}
                      {item.words}
                    </span>
                  </>
                ) : (
                  <span className="learning-model-check-words">
                    : {shown.words}
                  </span>
                )}
              </span>
            </li>
          );
        })}
      </ul>
      {(model.sample ?? []).length > 0 && (
        <details className="learning-model-sample">
          <summary>
            The tool sample: {passed} of{' '}
            {plural((model.sample ?? []).length, 'task')} right
          </summary>
          <ul className="learning-list">
            {(model.sample ?? []).map((item) => (
              <li key={item.prompt}>
                {item.passed ? 'Right' : 'Wrong'}: {item.prompt}
              </li>
            ))}
          </ul>
        </details>
      )}
      {unusableHere && <p className="learning-model-note">{unusableHere}</p>}
      {model.state === 'stopped' && (
        <p className="learning-model-note">
          What it downloaded stays: checking it again carries on from there.
        </p>
      )}
      {confirming && (
        <UseAsk name={model.name}>
          <p>
            {onHuggingFace
              ? `Your next version trains from ${model.name} on Hugging Face. It isn’t certified: Row-Bot checked it on this computer, and its time and price are estimates until a run measures them.`
              : `Your next version trains from ${model.name}. It isn’t certified: Row-Bot checked it on this computer.`}{' '}
            Versions you have stay as they are.
          </p>
          <div className="learning-actions-inline learning-model-ask-actions">
            <Button
              className="small"
              disabled={busy}
              onClick={() => setConfirming(false)}
            >
              Back
            </Button>
            <Button
              className="small"
              variant="primary"
              disabled={busy}
              onClick={() =>
                void onRun(
                  onHuggingFace ? 'hf_choose_model' : 'change_starting_model',
                ).then((done) => done && setConfirming(false))
              }
            >
              Use {model.name}
            </Button>
          </div>
        </UseAsk>
      )}
    </li>
  );
}

/** An ask that opens inside a long list scrolls into view (on a phone it would open below the fold). */
function useAskInView() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollIntoView?.({ block: 'nearest' });
  }, []);
  return ref;
}

function UseAsk({ name, children }: { name: string; children: ReactNode }) {
  const ask = useAskInView();
  return (
    <div
      ref={ask}
      className="learning-model-ask"
      role="group"
      aria-label={`Use ${name}?`}
    >
      {children}
    </div>
  );
}
