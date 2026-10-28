import { useId, useState } from 'react';
import { clientError } from '../../api/errors';
import type { LearningCheckedModel, LearningOpenModel } from '../../api/types';
import { Button, Field, Input, Progress } from '../../ui/primitives';
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
const STATE_WORDS: Record<string, string> = {
  waiting: 'Waiting',
  running: 'Checking…',
  passed: 'Passed',
  failed: 'Didn’t pass',
  skipped: 'Not checked',
};

function sizeOf(model: { params_b?: number | null }) {
  return model.params_b ? `${model.params_b.toLocaleString()}B` : '';
}

/**
 * Settings › Starting model's second level (C1 Part 4, O138): any open model on Hugging Face. Search, see what
 * each model is, its licence and whether Row-Bot can try it; check one on this computer (its licence agreed in
 * the same ask when it has terms); then see what was checked. Unsupported kinds are said plainly, never tried;
 * a gated model links to Hugging Face; a model this card can't run afterwards is never offered (O131).
 */
export function OtherModels({ io }: { io: LearningIO }) {
  const searchId = useId();
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<LearningOpenModel[] | null>(null);
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState<LearningOpenModel | null>(null);
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState('');
  const checked = io.snapshot?.personal_model?.other_models ?? [];
  const running = checked.find(
    (item) => item.state === 'downloading' || item.state === 'checking',
  );

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
      | 'change_starting_model',
    model: string,
    licenceOk = false,
  ) {
    setBusy(true);
    setError('');
    try {
      await io.run(action, {
        model,
        ...(licenceOk ? { licence_ok: true } : {}),
      });
      setAsking(null);
      setAgreed(false);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="learning-stack-tight" aria-label="Other models">
      <h3 className="learning-label">Other models</h3>
      <p className="learning-muted">
        Any open model on Hugging Face. Row-Bot checks it on this computer
        before it can be chosen: that it fits, learns, runs here and calls
        Row-Bot’s tools. Other models aren’t certified.
      </p>
      <form
        className="learning-actions-inline"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          void search();
        }}
      >
        <Field label="Search Hugging Face">
          <Input
            id={searchId}
            type="search"
            value={query}
            placeholder="Qwen, Gemma, Llama…"
            onChange={(event) => setQuery(event.target.value)}
          />
        </Field>
        <Button type="submit" disabled={busy || query.trim().length < 2}>
          Search
        </Button>
      </form>
      {failure && (
        <p role="alert" className="learning-error">
          {failure}
        </p>
      )}
      {error && (
        <p role="alert" className="learning-error">
          {error}
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
        <ul className="learning-list" aria-label="Search results">
          {found.length === 0 && <li>No chat models found.</li>}
          {found.map((model) => (
            <li key={model.id} className="learning-stack-tight">
              <span>
                <strong>{model.name}</strong>
                {[sizeOf(model), model.licence].filter(Boolean).length
                  ? ` · ${[sizeOf(model), model.licence].filter(Boolean).join(' · ')}`
                  : ''}
              </span>
              <small className="learning-muted">{model.repo}</small>
              {model.unsupported ? (
                <small>{model.unsupported}</small>
              ) : model.licence_kind === 'gated' ? (
                <small>
                  Gated on Hugging Face: request access there.{' '}
                  <a
                    href={`https://huggingface.co/${model.repo}`}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    Open on Hugging Face
                  </a>
                </small>
              ) : !model.runs_here ? (
                <small>
                  Too big to run on this computer afterwards
                  {model.runs_here_gb
                    ? ` (about ${Math.ceil(model.runs_here_gb)} GB of the graphics card)`
                    : ''}
                  .
                </small>
              ) : (
                <span className="learning-actions-inline">
                  <Button
                    className="small"
                    disabled={busy || !!running}
                    onClick={() => setAsking(model)}
                  >
                    Check…
                  </Button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {checked.length > 0 && (
        <ul className="learning-list" aria-label="Checked models">
          {checked.map((model) => (
            <CheckedModel
              key={model.id}
              model={model}
              busy={busy}
              onStop={() => void run('stop_model_check', model.id)}
              onForget={() => void run('forget_model', model.id)}
              onUse={() => void run('change_starting_model', model.id)}
              inUse={
                io.snapshot?.personal_model?.starting_model.id === model.id
              }
            />
          ))}
        </ul>
      )}
    </section>
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
  return (
    <div
      className="learning-sheet"
      role="group"
      aria-label={`Check ${model.name}?`}
    >
      <p>
        <strong>Check {model.name}?</strong> Row-Bot downloads it from Hugging
        Face, trains it a few steps on this computer’s graphics card, converts
        it to run here and asks it to call Row-Bot’s tools on a short sample.
        That takes a while and uses the graphics card. Nothing is used until you
        choose it.
      </p>
      {terms && (
        <div className="learning-stack-tight">
          <p>
            {model.name} uses {model.licence}. Its key terms:
          </p>
          <ul>
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
              I agree to {model.licence} (
              <a href={model.licence_url} target="_blank" rel="noreferrer">
                read it
              </a>
              )
            </span>
          </label>
        </div>
      )}
      <div className="learning-sheet-actions">
        <Button disabled={busy} onClick={onCancel}>
          Back
        </Button>
        <Button
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
  onStop,
  onForget,
  onUse,
  inUse,
}: {
  model: LearningCheckedModel;
  busy: boolean;
  onStop: () => void;
  onForget: () => void;
  onUse: () => void;
  inUse: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  const active = model.state === 'downloading' || model.state === 'checking';
  const headline =
    model.state === 'downloading'
      ? `Downloading ${model.name} to check it…`
      : model.state === 'checking'
        ? `Checking ${model.name} on this computer…`
        : model.state === 'passed'
          ? `${model.name} passed its check`
          : model.state === 'failed'
            ? `${model.name} didn’t pass its check`
            : model.state === 'stopped'
              ? `${model.name}’s check was stopped`
              : model.name;
  const where = [
    model.trains_here ? 'trains on this computer' : '',
    model.hugging_face_flavor ? 'trains on Hugging Face' : '',
  ].filter(Boolean);
  const passed = (model.sample ?? []).filter((item) => item.passed).length;
  return (
    <li className="learning-stack-tight" aria-label={model.name}>
      <strong>{headline}</strong>
      <small className="learning-muted">
        {[sizeOf(model), model.licence, 'not certified']
          .filter(Boolean)
          .join(' · ')}
        {model.state === 'passed' && where.length
          ? `. It ${where.join(' and ')}.`
          : ''}
      </small>
      {model.state === 'downloading' && model.download_bytes > 0 && (
        <Progress
          label={`${sizeWords(model.downloaded_bytes)} of ${sizeWords(model.download_bytes)}`}
          value={Math.round(
            (model.downloaded_bytes / model.download_bytes) * 100,
          )}
        />
      )}
      <ul aria-label="What was checked">
        {model.items.map((item) => (
          <li key={item.id}>
            {CHECKS[item.id]}: {STATE_WORDS[item.state]}
            {item.words ? `. ${item.words}` : ''}
          </li>
        ))}
      </ul>
      {(model.sample ?? []).length > 0 && (
        <details>
          <summary>
            The tool sample: {passed} of{' '}
            {plural((model.sample ?? []).length, 'task')} right
          </summary>
          <ul>
            {(model.sample ?? []).map((item) => (
              <li key={item.prompt}>
                {item.passed ? 'Right' : 'Wrong'}: {item.prompt}
              </li>
            ))}
          </ul>
        </details>
      )}
      {confirming && (
        <p>
          Your next version trains from {model.name}. It isn’t certified:
          Row-Bot checked it on this computer. Versions you have stay as they
          are.
        </p>
      )}
      <span className="learning-actions-inline">
        {model.state === 'passed' && model.trains_here && !inUse && (
          <Button
            className="small"
            variant={confirming ? 'primary' : 'secondary'}
            disabled={busy}
            onClick={() => (confirming ? onUse() : setConfirming(true))}
          >
            {confirming ? `Use ${model.name}` : 'Use for training…'}
          </Button>
        )}
        {inUse && <small>In use for training</small>}
        {active ? (
          <Button className="small" disabled={busy} onClick={onStop}>
            Stop
          </Button>
        ) : (
          <Button className="small" disabled={busy} onClick={onForget}>
            Remove
          </Button>
        )}
      </span>
    </li>
  );
}
