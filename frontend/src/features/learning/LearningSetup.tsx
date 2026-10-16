import { useEffect, useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { clientError } from '../../api/errors';
import type {
  LearningHardware,
  LearningReceipt,
  LearningSetupChoices,
  LearningSnapshot,
} from '../../api/types';
import { Button, Select } from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';
import type { LearningIO } from './learning-state';
import {
  limitMeaning,
  limitOptions,
  quietHoursWords,
  sizeWords,
} from './words';
import { usePhone } from './use-phone';
import './learning.css';

/** The setup sheet's choices, every default filled in (designs/learning-ux.md §1). */
export function defaultChoices(
  snapshot: LearningSnapshot,
): LearningSetupChoices {
  return {
    sources: {
      my_chats: true,
      workflows_and_goals: true,
      // The last 30 days of chats: a ticked checkbox (D26).
      past_chats: true,
    },
    apply_mode: 'ask_first',
    monthly_token_limit: snapshot.spending.limit_tokens,
    train_in_quiet_hours: snapshot.personal_model.train_in_quiet_hours,
    train_where: snapshot.personal_model.train_where,
    starting_model: 'qwen3.5-2b',
  };
}

/** What this computer can do, in words, while the check runs and after. */
function HardwareRows({
  hardware,
  error,
  choices,
  onChange,
  onCheckAgain,
}: {
  hardware: LearningHardware | null;
  error: string;
  choices: LearningSetupChoices;
  onChange: (next: Partial<LearningSetupChoices>) => void;
  onCheckAgain: () => void;
}) {
  const modelId = useId();
  if (!hardware && !error)
    return (
      <p className="learning-muted" role="status">
        Checking this computer…
      </p>
    );
  if (!hardware || hardware.state === 'unknown')
    return (
      <div className="learning-stack-tight">
        <p>Row-Bot couldn’t check this computer’s graphics card.</p>
        <div className="learning-actions-inline">
          <Button className="small" onClick={onCheckAgain}>
            Check again
          </Button>
        </div>
      </div>
    );
  if (hardware.state === 'gpu' && hardware.tier) {
    const model = hardware.starting_models[0];
    return (
      <div className="learning-stack-tight">
        <p>
          <span className="learning-label">This computer: </span>
          {hardware.gpu?.label}
        </p>
        <p>{hardware.tier.summary}</p>
        {hardware.larger_tiers.map((tier) => (
          <p className="learning-muted" key={tier.id}>
            {tier.summary}
          </p>
        ))}
        {model && (
          <label className="learning-field" htmlFor={modelId}>
            <span className="learning-label">Starting model</span>
            <Select
              id={modelId}
              value={choices.starting_model}
              disabled={!choices.train_in_quiet_hours}
              onChange={(event) =>
                onChange({
                  starting_model: event.target
                    .value as LearningSetupChoices['starting_model'],
                })
              }
            >
              <option value={model.id}>
                {model.name} · {model.licence} ·{' '}
                {sizeWords(model.download_bytes)} download
              </option>
            </Select>
          </label>
        )}
        <label className="check-field learning-check">
          <input
            type="checkbox"
            checked={choices.train_in_quiet_hours}
            onChange={(event) =>
              onChange({ train_in_quiet_hours: event.target.checked })
            }
          />
          <span>
            Train a personal model during quiet hours
            <small>
              Downloads the training engine (about{' '}
              {sizeWords(hardware.engine_download_bytes)}) and the starting
              model when you turn this on. Asks first.
            </small>
          </span>
        </label>
      </div>
    );
  }
  const hugging = choices.train_where === 'hugging_face';
  return (
    <div className="learning-stack-tight">
      {hardware.gpu && (
        <p>
          <span className="learning-label">This computer: </span>
          {hardware.gpu.label}. Training needs a graphics card with about 8 GB.
        </p>
      )}
      {hugging ? (
        <>
          <p>
            Your personal model will train on Hugging Face, in your own account.
            Each run is priced before it starts, and nothing is uploaded until
            you agree.
          </p>
          <div className="learning-actions-inline">
            <Button
              className="small"
              variant="ghost"
              onClick={() => onChange({ train_where: 'this_computer' })}
            >
              Don’t use Hugging Face
            </Button>
          </div>
        </>
      ) : (
        <>
          <p>
            This computer can’t train a model. You can train on Hugging Face
            (your account, priced before each run) or skip this for now.
          </p>
          <div className="learning-actions-inline">
            <Button
              className="small"
              onClick={() => onChange({ train_where: 'hugging_face' })}
            >
              Use Hugging Face
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Turn on Learning: one sheet, every default filled in, a single tap unless
 * something should change. The personal-model part asks before any download.
 */
export function LearningSetupSheet({
  open,
  onOpenChange,
  io,
  onTurnedOn,
  onLeave,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  io: LearningIO;
  /** Learning is on, and the download was answered (or there was nothing to ask). */
  onTurnedOn: (receipt: LearningReceipt) => void;
  /** A link inside the sheet was followed: close it without turning on. */
  onLeave?: () => void;
}) {
  const snapshot = io.snapshot;
  const [hardware, setHardware] = useState<LearningHardware | null>(null);
  const [hardwareError, setHardwareError] = useState('');
  const [choices, setChoices] = useState<LearningSetupChoices | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // After Turn on: the download ask, in the same sheet (no second dialog to juggle focus).
  const [turnedOn, setTurnedOn] = useState<LearningReceipt | null>(null);
  const limitId = useId();
  const phone = usePhone();

  const check = (refresh = false) => {
    setHardwareError('');
    if (refresh) setHardware(null);
    io.hardware(refresh).then(setHardware, (cause: unknown) =>
      setHardwareError(clientError(cause).message),
    );
  };
  useEffect(() => {
    if (!open) return;
    setError('');
    setTurnedOn(null);
    setChoices(snapshot ? defaultChoices(snapshot) : null);
    check();
    // The sheet reads the computer once each time it opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const change = (next: Partial<LearningSetupChoices>) =>
    setChoices((current) => (current ? { ...current, ...next } : current));
  const setSource = (key: keyof LearningSetupChoices['sources'], on: boolean) =>
    setChoices((current) =>
      current
        ? { ...current, sources: { ...current.sources, [key]: on } }
        : current,
    );

  async function turnOn() {
    if (!choices || busy) return;
    setBusy(true);
    setError('');
    try {
      const receipt = await io.run('turn_on', { setup: choices });
      if (
        choices.train_in_quiet_hours &&
        choices.train_where === 'this_computer' &&
        hardware?.state === 'gpu'
      )
        setTurnedOn(receipt);
      else onTurnedOn(receipt);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  const model = snapshot?.spending.model_name;
  if (turnedOn) {
    const words = downloadWords(io, hardware);
    return (
      <ModalTask
        open={open}
        onOpenChange={(next) => {
          // Closed without an answer: Learning is on, nothing downloads.
          if (!next) onTurnedOn(turnedOn);
        }}
        title={words.title}
        description={words.description}
        kind={phone ? 'sheet' : 'dialog'}
        className="learning-download-ask"
      >
        <DownloadAskBody
          io={io}
          hardware={hardware}
          onAnswered={() => onTurnedOn(turnedOn)}
        />
      </ModalTask>
    );
  }
  return (
    <ModalTask
      open={open}
      onOpenChange={onOpenChange}
      title="Turn on Learning"
      description="Every choice is filled in. Change any you like."
      className="learning-setup"
    >
      {!snapshot || !choices ? (
        <p role="status">Loading Learning…</p>
      ) : (
        <>
          <div className="learning-sheet learning-sheet-scroll">
            <section
              className="learning-section"
              aria-labelledby={`${limitId}-keep`}
            >
              <h3 id={`${limitId}-keep`}>What Row-Bot will keep</h3>
              <p>
                A private record of how it handled your requests: what it saw,
                which tools it used and what happened. It stays on this
                computer, and you can delete it at any time.
              </p>
            </section>
            <section className="learning-section">
              <fieldset className="learning-fieldset">
                <legend>
                  <h3>Learn from</h3>
                </legend>
                <label className="check-field learning-check">
                  <input
                    type="checkbox"
                    checked={choices.sources.my_chats}
                    onChange={(event) =>
                      setSource('my_chats', event.target.checked)
                    }
                    data-initial-focus
                  />
                  <span>
                    My chats
                    <small>
                      Includes your own direct chats on Telegram, Slack and
                      other apps.
                    </small>
                  </span>
                </label>
                <label className="check-field learning-check">
                  <input
                    type="checkbox"
                    checked={choices.sources.workflows_and_goals}
                    onChange={(event) =>
                      setSource('workflows_and_goals', event.target.checked)
                    }
                  />
                  <span>Workflows and goals</span>
                </label>
                <label className="check-field learning-check">
                  <input
                    type="checkbox"
                    checked={choices.sources.past_chats}
                    onChange={(event) =>
                      setSource('past_chats', event.target.checked)
                    }
                  />
                  <span>Also learn from the last 30 days of chats</span>
                </label>
                <p className="learning-muted">
                  Chats with other people are never used. Switch any chat off
                  from its menu.
                </p>
                {snapshot.owner_unset_channels.map((item) => (
                  <p className="learning-muted" key={item.channel}>
                    {item.note}
                  </p>
                ))}
              </fieldset>
            </section>
            <section
              className="learning-section"
              aria-labelledby={`${limitId}-model`}
            >
              <h3 id={`${limitId}-model`}>Personal model</h3>
              <HardwareRows
                hardware={hardware}
                error={hardwareError}
                choices={choices}
                onChange={change}
                onCheckAgain={() => check(true)}
              />
            </section>
            <section className="learning-section">
              <fieldset className="learning-fieldset">
                <legend>
                  <h3>Using changes</h3>
                </legend>
                <label className="check-field learning-check">
                  <input
                    type="radio"
                    name={`${limitId}-apply`}
                    checked={choices.apply_mode === 'ask_first'}
                    onChange={() => change({ apply_mode: 'ask_first' })}
                  />
                  <span>Ask me first</span>
                </label>
                <label className="check-field learning-check">
                  <input
                    type="radio"
                    name={`${limitId}-apply`}
                    checked={choices.apply_mode === 'automatic'}
                    onChange={() => change({ apply_mode: 'automatic' })}
                  />
                  <span>
                    Use changes that pass their tests
                    <small>New personal-model versions always ask.</small>
                  </span>
                </label>
              </fieldset>
            </section>
            <section
              className="learning-section"
              aria-labelledby={`${limitId}-spend`}
            >
              <h3 id={`${limitId}-spend`}>Spending</h3>
              <p>
                Learning uses{' '}
                {model ? `${model} (your default model)` : 'your default model'}{' '}
                to find and test improvements.
              </p>
              <label className="learning-field" htmlFor={limitId}>
                <span className="learning-label">Monthly limit</span>
                <Select
                  id={limitId}
                  value={String(choices.monthly_token_limit)}
                  onChange={(event) =>
                    change({ monthly_token_limit: Number(event.target.value) })
                  }
                >
                  {limitOptions(choices.monthly_token_limit).map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              </label>
              <p className="learning-muted">
                {limitMeaning(choices.monthly_token_limit)}
              </p>
            </section>
            <section
              className="learning-section"
              aria-labelledby={`${limitId}-when`}
            >
              <h3 id={`${limitId}-when`}>When</h3>
              <p>
                Quiet hours,{' '}
                {quietHoursWords(
                  snapshot.quiet_hours.window_start,
                  snapshot.quiet_hours.window_end,
                )}{' '}
                (shared with Dream Cycle).{' '}
                <Link to="/settings/preferences#dream-cycle" onClick={onLeave}>
                  Change
                </Link>
              </p>
              <p className="learning-muted">
                Training on answers from OpenAI, Anthropic or Google models may
                be limited by their terms. Check your provider’s terms.
              </p>
            </section>
            {error && (
              <p role="alert" className="learning-error">
                {error}
              </p>
            )}
          </div>
          <div className="learning-sheet-actions">
            <Button onClick={() => onOpenChange(false)} disabled={busy}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={busy}
              onClick={() => void turnOn()}
            >
              {busy ? 'Turning on…' : 'Turn on Learning'}
            </Button>
          </div>
        </>
      )}
    </ModalTask>
  );
}

/** The download ask's title and line: what downloads, how big, and the free space. */
function downloadWords(io: LearningIO, hardware: LearningHardware | null) {
  const model = io.snapshot?.personal_model.starting_model;
  const name = model?.name ?? 'the starting model';
  const engine =
    hardware?.engine_download_bytes ??
    io.snapshot?.personal_model.engine_download_bytes ??
    0;
  const free = hardware?.free_bytes ?? null;
  return {
    title: `Download the training engine and ${name}?`,
    description: `The training engine (${sizeWords(engine)}) and ${name} (${sizeWords(model?.download_bytes ?? 0)}).${
      free === null ? '' : ` ${sizeWords(free)} free on this computer.`
    }`,
    total: engine + (model?.download_bytes ?? 0),
    free,
  };
}

/** Download or Not now; Download records the choice (the download itself arrives in L2b). */
function DownloadAskBody({
  io,
  hardware,
  onAnswered,
}: {
  io: LearningIO;
  hardware: LearningHardware | null;
  onAnswered: (download: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const model = io.snapshot?.personal_model.starting_model;
  const { total, free } = downloadWords(io, hardware);
  const fits = free === null || free >= total;
  async function answer(download: boolean) {
    setBusy(true);
    setError('');
    try {
      await io.run('answer_download', {
        download: download ? 'download' : 'not_now',
      });
      onAnswered(download);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="learning-sheet">
      <p>
        They’re used only to train your personal model, here on this computer.{' '}
        {model?.name} is {model?.licence}.
      </p>
      {!fits && free !== null && (
        <p role="alert" className="learning-error">
          Needs {sizeWords(total)} free; {sizeWords(free)} free.
        </p>
      )}
      {error && (
        <p role="alert" className="learning-error">
          {error}
        </p>
      )}
      <div className="learning-sheet-actions">
        <Button disabled={busy} onClick={() => void answer(false)}>
          Not now
        </Button>
        <Button
          variant="primary"
          disabled={busy || !fits}
          onClick={() => void answer(true)}
          data-initial-focus
        >
          Download
        </Button>
      </div>
    </div>
  );
}

/**
 * The personal model's download ask on its own (Settings › Learning, the Personal
 * model view): sizes and free space, Download or Not now.
 */
export function DownloadAsk({
  open,
  onOpenChange,
  io,
  hardware,
  onAnswered,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  io: LearningIO;
  hardware: LearningHardware | null;
  onAnswered: (download: boolean) => void;
}) {
  const words = downloadWords(io, hardware);
  const phone = usePhone();
  return (
    <ModalTask
      open={open}
      onOpenChange={onOpenChange}
      title={words.title}
      description={words.description}
      kind={phone ? 'sheet' : 'dialog'}
      className="learning-download-ask"
    >
      <DownloadAskBody io={io} hardware={hardware} onAnswered={onAnswered} />
    </ModalTask>
  );
}
