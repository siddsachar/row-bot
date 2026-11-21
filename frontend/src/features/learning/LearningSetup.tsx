import { useEffect, useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { clientError } from '../../api/errors';
import type {
  LearningHardware,
  LearningReceipt,
  LearningSetupChoices,
  LearningSnapshot,
} from '../../api/types';
import { Button } from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';
import type { LearningIO } from './learning-state';
import { MonthlyLimitPicker } from './MonthlyLimit';
import {
  ENGINE_SOURCES,
  ENGINE_TELEMETRY,
  limitMeaning,
  quietHoursWords,
  sizeWords,
} from './words';
import { usePhone } from './use-phone';
import './learning.css';

/** The setup sheet's choices: what is saved now, defaults included (designs/learning-ux.md §1). */
export function defaultChoices(
  snapshot: LearningSnapshot,
): LearningSetupChoices {
  return {
    sources: { ...snapshot.sources },
    apply_mode: snapshot.apply_mode,
    monthly_token_limit: snapshot.spending.limit_tokens,
  };
}

/**
 * Turn on Learning again after it was turned off: one sheet, every choice
 * filled in, a single tap unless something should change. The personal model
 * is set up apart, in Personal model, so turning on never downloads (O195).
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
  /** Learning is on. */
  onTurnedOn: (receipt: LearningReceipt) => void;
  /** A link inside the sheet was followed: close it without turning on. */
  onLeave?: () => void;
}) {
  const snapshot = io.snapshot;
  const [choices, setChoices] = useState<LearningSetupChoices | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const limitId = useId();

  useEffect(() => {
    if (!open) return;
    setError('');
    setChoices(snapshot ? defaultChoices(snapshot) : null);
    // The sheet starts from the saved choices each time it opens.
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
      onTurnedOn(await io.run('turn_on', { setup: choices }));
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  const model = snapshot?.spending.model_name;
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
              <div className="learning-field">
                <label className="learning-label" htmlFor={limitId}>
                  Monthly limit
                </label>
                <MonthlyLimitPicker
                  id={limitId}
                  value={choices.monthly_token_limit}
                  onChange={(limit) => change({ monthly_token_limit: limit })}
                />
              </div>
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
                A personal model is set up on its own, under Personal model.
                Nothing downloads when you turn Learning on.
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
  const personal = io.snapshot?.personal_model;
  const model = personal?.starting_model;
  const name = model?.name ?? 'the starting model';
  const engine =
    hardware?.engine_download_bytes ?? personal?.engine_download_bytes ?? 0;
  const engineNeeded = personal?.engine !== 'installed';
  const modelNeeded = personal?.starting_model_state !== 'downloaded';
  const free = hardware?.free_bytes ?? null;
  const total =
    (engineNeeded ? engine : 0) +
    (modelNeeded ? (model?.download_bytes ?? 0) : 0);
  // The room it needs: the download, the unpacked engine and a margin (Row-Bot works it out).
  const needs = personal?.download_state.needs_bytes || total;
  const parts = [
    engineNeeded ? `The training engine (${sizeWords(engine)})` : '',
    modelNeeded ? `${name} (${sizeWords(model?.download_bytes ?? 0)})` : '',
  ].filter(Boolean);
  return {
    title:
      engineNeeded && !modelNeeded
        ? 'Download the training engine?'
        : !engineNeeded && modelNeeded
          ? `Download ${name}?`
          : `Download the training engine and ${name}?`,
    description: `${parts.join(' and ')}.${
      free === null ? '' : ` ${sizeWords(free)} free on this computer.`
    }`,
    needs,
    free,
  };
}

/** Download or Not now; Download starts the download (the sizes, where from, usage reporting off). */
function DownloadAskBody({
  io,
  hardware,
  onAnswered,
}: {
  io: LearningIO;
  hardware: LearningHardware | null;
  onAnswered: (download: boolean, summary: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const model = io.snapshot?.personal_model.starting_model;
  const { needs, free } = downloadWords(io, hardware);
  const fits = free === null || free >= needs;
  async function answer(download: boolean) {
    setBusy(true);
    setError('');
    try {
      const receipt = await io.run('answer_download', {
        download: download ? 'download' : 'not_now',
      });
      onAnswered(download, receipt.summary);
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
      <p className="learning-muted">
        {ENGINE_SOURCES} {ENGINE_TELEMETRY}
      </p>
      {!fits && free !== null && (
        <p role="alert" className="learning-error">
          Needs {sizeWords(needs)} free; {sizeWords(free)} free.
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
  onAnswered: (download: boolean, summary: string) => void;
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
