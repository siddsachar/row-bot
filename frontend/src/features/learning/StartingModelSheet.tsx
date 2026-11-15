import { useEffect, useId, useState } from 'react';
import { clientError } from '../../api/errors';
import type { LearningHardware, LearningStartingModel } from '../../api/types';
import { Button } from '../../ui/primitives';
import { ModalTask, useNotify } from '../../ui/overlays';
import type { LearningFields, LearningIO } from './learning-state';
import { ENGINE_SOURCES, ENGINE_TELEMETRY, sizeWords } from './words';
import { usePhone } from './use-phone';
import { OtherModels } from './OtherModels';
import './learning.css';

type Step = 'choose' | 'ask';

/**
 * Settings › Learning › Starting model › Change… (O124): the certified starting models this graphics card
 * trains, the one setup would pick marked Recommended. Choosing one asks first (its download and the free
 * space), then it becomes the starting model, its download starts, and the next version trains from it.
 * Versions already made stay as they are. ``offer`` opens it at the ask for that model (the Personal model
 * view's one-time "stronger model" line).
 */
export function StartingModelSheet({
  open,
  onOpenChange,
  io,
  offer,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  io: LearningIO;
  offer?: LearningStartingModel | null;
}) {
  const phone = usePhone();
  const notify = useNotify();
  const name = useId();
  const model = io.snapshot?.personal_model;
  const current = model?.starting_model.id ?? '';
  const [hardware, setHardware] = useState<LearningHardware | null>(null);
  const [checkFailed, setCheckFailed] = useState(false);
  const [choice, setChoice] = useState(current);
  const [step, setStep] = useState<Step>('choose');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // A model chosen under Other models becomes the starting model inside this sheet: the radios follow it.
  useEffect(() => setChoice(current), [current]);

  const check = () => {
    setHardware(null);
    setCheckFailed(false);
    io.hardware().then(setHardware, () => setCheckFailed(true));
  };
  useEffect(() => {
    if (!open) return;
    setError('');
    setChoice(offer?.id ?? current);
    setStep(offer ? 'ask' : 'choose');
    check();
    // Each opening reads the computer once (it doesn't change while the sheet is open).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const models = hardware?.starting_models ?? [];
  const recommended = models[0]?.id;
  const chosen =
    models.find((item) => item.id === choice) ??
    (offer && offer.id === choice ? offer : undefined);
  const engineNeeded = model?.engine !== 'installed';
  const engineBytes =
    hardware?.engine_download_bytes ?? model?.engine_download_bytes ?? 0;
  const needs =
    (chosen?.download_bytes ?? 0) + (engineNeeded ? engineBytes : 0);
  const free = hardware?.free_bytes ?? null;
  const fits = free === null || free >= needs;

  async function change() {
    if (!chosen) return;
    setBusy(true);
    setError('');
    try {
      const receipt = await io.run('change_starting_model', {
        model: chosen.id as LearningFields['model'],
      });
      onOpenChange(false);
      notify(receipt.summary);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  const failure = error && (
    <p role="alert" className="learning-error">
      {error}
    </p>
  );

  let body;
  if (checkFailed)
    body = (
      <div className="learning-sheet">
        <p role="alert" className="learning-error">
          Row-Bot couldn’t check this computer’s graphics card.
        </p>
        <div className="learning-sheet-actions">
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" onClick={check} data-initial-focus>
            Try again
          </Button>
        </div>
      </div>
    );
  else if (!hardware) body = <p role="status">Checking this computer…</p>;
  else if (!models.length)
    body = (
      <div className="learning-sheet">
        <p>
          {hardware.gpu
            ? `${hardware.gpu.label} can’t train a personal model.`
            : 'This computer can’t train a personal model.'}
        </p>
        <div className="learning-sheet-actions">
          <Button onClick={() => onOpenChange(false)} data-initial-focus>
            Close
          </Button>
        </div>
      </div>
    );
  else if (step === 'choose')
    body = (
      <div className="learning-sheet">
        <fieldset className="learning-fieldset">
          <legend className="learning-label">Recommended</legend>
          {models.map((item) => (
            <label key={item.id} className="check-field learning-check">
              <input
                type="radio"
                name={name}
                value={item.id}
                checked={choice === item.id}
                onChange={() => setChoice(item.id)}
              />
              <span>
                <span>
                  {item.name}
                  {item.id === current
                    ? ' · in use'
                    : item.id === recommended
                      ? ' · recommended'
                      : ''}
                </span>
                <small>
                  {item.licence} ·{' '}
                  {item.id === current &&
                  model?.starting_model_state === 'downloaded'
                    ? `on this computer (${sizeWords(model.starting_model_bytes)})`
                    : `${sizeWords(item.download_bytes)} download`}
                </small>
              </span>
            </label>
          ))}
        </fieldset>
        <OtherModels io={io} />
        <p className="learning-muted">
          Your next version trains from the model you choose. Versions you have
          stay as they are, each compared with the model it started from.
        </p>
        {failure}
        <div className="learning-sheet-actions">
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!chosen || choice === current}
            onClick={() => setStep('ask')}
            data-initial-focus
          >
            {chosen && choice !== current ? `Use ${chosen.name}…` : 'Use…'}
          </Button>
        </div>
      </div>
    );
  else
    body = (
      <div className="learning-sheet">
        <p>
          {`Row-Bot downloads ${chosen?.name ?? 'the model'} (${sizeWords(chosen?.download_bytes ?? 0)}) from Hugging Face${engineNeeded ? ` and the training engine (${sizeWords(engineBytes)})` : ''}, then trains your next version from it in quiet hours.`}
          {free === null ? '' : ` ${sizeWords(free)} free on this computer.`}
        </p>
        <p className="learning-muted">
          {`${chosen?.name ?? 'It'} is ${chosen?.licence ?? 'openly licensed'}. ${
            model?.starting_model_state === 'downloaded'
              ? `${model.starting_model.name} stays on this computer until you delete it, and your`
              : 'Your'
          } versions stay as they are.`}
          {engineNeeded ? ` ${ENGINE_SOURCES} ${ENGINE_TELEMETRY}` : ''}
        </p>
        {!fits && free !== null && (
          <p role="alert" className="learning-error">
            Needs {sizeWords(needs)} free; {sizeWords(free)} free. Free some
            space, then try again.
          </p>
        )}
        {failure}
        <div className="learning-sheet-actions">
          <Button disabled={busy} onClick={() => setStep('choose')}>
            {offer ? 'Other models' : 'Back'}
          </Button>
          <Button
            variant="primary"
            disabled={busy || !fits || !chosen}
            onClick={() => void change()}
            data-initial-focus
          >
            {busy ? 'Switching…' : 'Download and switch'}
          </Button>
        </div>
      </div>
    );

  return (
    <ModalTask
      open={open}
      onOpenChange={onOpenChange}
      title={
        step === 'ask' && chosen
          ? `Switch to ${chosen.name}?`
          : 'Starting model'
      }
      description={
        step === 'ask'
          ? 'Your next version trains from it. Nothing downloads until you say so.'
          : 'The open model your personal model is trained from.'
      }
      kind={phone ? 'sheet' : 'dialog'}
      className="learning-starting-model"
    >
      {body}
    </ModalTask>
  );
}
