import { useEffect, useId, useState } from 'react';
import { clientError } from '../../api/errors';
import type {
  LearningHuggingFace,
  LearningHuggingFaceQuote,
} from '../../api/types';
import { ModalTask, useNotify, useOverlay } from '../../ui/overlays';
import { Button, Select } from '../../ui/primitives';
import type { LearningIO } from './learning-state';
import { usePhone } from './use-phone';
import {
  durationWords,
  HF_ACCESS,
  HF_BILLING_URL,
  HF_CONNECTED_APPS_URL,
  HF_JOIN_URL,
  HF_NEEDS,
  moneyWords,
  plural,
  sizeWords,
} from './words';
import './learning.css';

function External({ href, children }: { href: string; children: string }) {
  return (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  );
}

/** "Qwen3.8 27B costs about $3.10 (about 1 h 40 min on an NVIDIA L40S)": a typical run, for the guided start. */
function typicalWords(quote: LearningHuggingFaceQuote | null | undefined) {
  if (!quote) return '';
  return `A typical run of ${quote.model_name} costs about ${moneyWords(quote.cost_usd)} (${durationWords(quote.seconds)} on ${quote.flavor_label}).`;
}

/**
 * Sign in with Hugging Face (O134): one button; Hugging Face's own page opens with Row-Bot's code filled in,
 * the person clicks Authorize there (on this computer or any phone), and Row-Bot carries on by itself.
 */
export function HuggingFaceSignIn({
  io,
  hugging,
  compact = false,
}: {
  io: LearningIO;
  hugging: LearningHuggingFace;
  compact?: boolean;
}) {
  const notify = useNotify();
  const overlay = useOverlay();
  const [busy, setBusy] = useState(false);
  const sign = hugging.sign_in;

  async function run(
    action: 'hf_sign_in' | 'hf_cancel_sign_in' | 'hf_sign_out',
  ) {
    setBusy(true);
    try {
      const receipt = await io.run(action);
      const url = receipt.snapshot.personal_model.hugging_face?.sign_in.url;
      if (action === 'hf_sign_in' && url)
        window.open(url, '_blank', 'noopener');
      if (action !== 'hf_sign_in') notify(receipt.summary);
    } catch (cause) {
      notify(clientError(cause).message, 'danger');
    } finally {
      setBusy(false);
    }
  }

  if (!hugging.available)
    return (
      <p className="learning-muted">
        Training on Hugging Face needs Row-Bot’s full install, which includes
        Hugging Face’s own library.
      </p>
    );
  if (sign.state === 'signing_in')
    return (
      <div className="learning-stack-tight" aria-live="polite">
        <p>
          Sign in on Hugging Face and click <strong>Authorize</strong>. If it
          asks for a code, enter{' '}
          <strong className="learning-code">{sign.code}</strong>.
        </p>
        <p className="learning-muted">
          Waiting for you to authorize… This works from a phone too: open the
          link there.
        </p>
        <div className="learning-actions-inline">
          {sign.url && (
            <a
              className="button small primary"
              href={sign.url}
              target="_blank"
              rel="noreferrer noopener"
            >
              Open Hugging Face
            </a>
          )}
          <Button
            className="small"
            variant="ghost"
            disabled={busy}
            onClick={() => void run('hf_cancel_sign_in')}
          >
            Cancel
          </Button>
        </div>
      </div>
    );
  if (sign.state === 'signed_in')
    return (
      <div className="learning-stack-tight">
        <p>
          Signed in to Hugging Face as <strong>{sign.user || 'you'}</strong>.
        </p>
        {!compact && <p className="learning-muted">{HF_ACCESS}</p>}
        <div className="learning-actions-inline">
          <Button
            className="small"
            variant="ghost"
            disabled={busy}
            onClick={(event) => {
              const opener = event.currentTarget;
              overlay.open({
                kind: 'alert',
                title: 'Sign out of Hugging Face?',
                description:
                  'Row-Bot removes its sign-in from this computer’s keychain and won’t train there until you sign in again. You can also remove Row-Bot’s access on Hugging Face, in Settings › Connected apps.',
                confirmLabel: 'Sign out',
                returnFocusTo: opener,
                onConfirm: () => void run('hf_sign_out'),
              });
            }}
          >
            Sign out
          </Button>
          {!compact && (
            <External href={HF_CONNECTED_APPS_URL}>Connected apps</External>
          )}
        </div>
      </div>
    );
  return (
    <div className="learning-stack-tight">
      {sign.failure && (
        <p role="alert" className="learning-error">
          {sign.failure === 'denied'
            ? 'Sign-in didn’t finish: Hugging Face says it wasn’t authorized.'
            : sign.failure === 'expired'
              ? 'Sign-in didn’t finish in time. Try again.'
              : 'Hugging Face didn’t accept the sign-in. Try again.'}
        </p>
      )}
      {sign.state === 'expired' && (
        <p>
          Your Hugging Face sign-in has run out. Sign in again to train there.
        </p>
      )}
      <div className="learning-actions-inline">
        <Button
          variant="primary"
          className={compact ? 'small' : undefined}
          disabled={busy}
          onClick={() => void run('hf_sign_in')}
        >
          {busy ? 'Opening Hugging Face…' : 'Sign in with Hugging Face'}
        </Button>
        {!compact && <External href={HF_JOIN_URL}>Create an account</External>}
      </div>
    </div>
  );
}

/** The model Hugging Face trains (O130, O131): certified first; only models this computer can run after. */
export function HuggingFaceModels({
  io,
  hugging,
}: {
  io: LearningIO;
  hugging: LearningHuggingFace;
}) {
  const notify = useNotify();
  const name = useId();
  const [busy, setBusy] = useState(false);
  const models = hugging.models ?? [];
  if (models.length === 0)
    return (
      <p className="learning-muted">
        No model Row-Bot trains on Hugging Face could run on this computer
        afterwards: personal models run here on an NVIDIA graphics card with 8
        GB or more.
      </p>
    );

  async function choose(model: string) {
    setBusy(true);
    try {
      notify((await io.run('hf_choose_model', { model })).summary);
    } catch (cause) {
      notify(clientError(cause).message, 'danger');
    } finally {
      setBusy(false);
    }
  }

  return (
    <fieldset className="learning-fieldset" disabled={busy}>
      <legend className="learning-label">Model to train</legend>
      {models.map((model) => (
        <label key={model.id} className="check-field learning-check">
          <input
            type="radio"
            name={name}
            value={model.id}
            checked={hugging.model === model.id}
            onChange={() => void choose(model.id)}
          />
          <span>
            <span>
              {model.name} · {model.licence}
              {model.recommended ? ' · Recommended' : ''}
            </span>
            {model.runs_here_gb ? (
              <small>
                Runs on this computer after: needs about{' '}
                {Math.ceil(model.runs_here_gb)} GB of the graphics card ·{' '}
                {sizeWords(model.download_bytes)} download
              </small>
            ) : null}
          </span>
        </label>
      ))}
    </fieldset>
  );
}

/**
 * The quote before a run (O133): GPU, time, price, what is uploaded and where, and that it is deleted after;
 * the first run also sets the monthly cap (O132). Start is the person's agreement to this price, on this
 * device (D28): the command carries the price they saw.
 */
export function HuggingFaceQuoteSheet({
  open,
  onOpenChange,
  io,
  jobId,
  version,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  io: LearningIO;
  jobId: string;
  version: number | null;
}) {
  const phone = usePhone();
  const notify = useNotify();
  const capId = useId();
  const gpuName = useId();
  const hugging = io.snapshot?.personal_model.hugging_face ?? null;
  const quote = hugging?.quote ?? null;
  const choices = hugging?.cap_choices ?? [10, 20, 50, 100];
  const suggested =
    choices.find((value) => quote && value >= quote.cost_usd * 4) ??
    choices[choices.length - 1];
  const [cap, setCap] = useState<number>(hugging?.cap_usd || suggested || 20);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!open) return;
    setError('');
    setCap(hugging?.cap_usd || suggested || 20);
    // The cap is read once each time the sheet opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const name = version ? `v${version}` : 'your personal model';
  const spent = hugging?.month_spent_usd ?? 0;
  const gpus = quote?.choices ?? [];

  async function chooseGpu(flavor: string) {
    setBusy(true);
    setError('');
    try {
      await io.run('hf_choose_flavor', { flavor });
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }
  const fits = !!quote && spent + quote.cost_usd <= cap;

  async function start() {
    if (!quote) return;
    setBusy(true);
    setError('');
    try {
      const receipt = await io.run('hf_start', {
        job_id: jobId,
        cap_usd: cap,
        quote_cost_cents: Math.round(quote.cost_usd * 100),
      });
      notify(receipt.summary);
      onOpenChange(false);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <ModalTask
      open={open}
      onOpenChange={onOpenChange}
      title={`Train ${name} on Hugging Face?`}
      description="Billed to your Hugging Face account. Nothing is uploaded until you start."
      kind={phone ? 'sheet' : 'dialog'}
      className="learning-hf-quote"
    >
      {!quote ? (
        <div className="learning-sheet">
          <p>Row-Bot is getting the price…</p>
        </div>
      ) : (
        <div className="learning-sheet">
          {gpus.length > 1 && (
            <fieldset className="learning-fieldset" disabled={busy}>
              <legend className="learning-label">
                Graphics card on Hugging Face
              </legend>
              {gpus.map((gpu) => (
                <label key={gpu.flavor} className="check-field learning-check">
                  <input
                    type="radio"
                    name={gpuName}
                    value={gpu.flavor}
                    checked={quote.flavor === gpu.flavor}
                    onChange={() => void chooseGpu(gpu.flavor)}
                  />
                  <span>
                    <span>
                      {gpu.label}
                      {gpu.gpu_memory_gb ? ` (${gpu.gpu_memory_gb} GB)` : ''}
                    </span>
                    <small>
                      {durationWords(gpu.seconds)} · about{' '}
                      {moneyWords(gpu.cost_usd)} ·{' '}
                      {moneyWords(gpu.price_per_hour)} an hour
                    </small>
                  </span>
                </label>
              ))}
              <p className="learning-muted">
                If Hugging Face has none of one kind free, the run waits in its
                queue without charge until one is free.
              </p>
            </fieldset>
          )}
          <dl className="learning-facts" aria-label="The run">
            <div>
              <dt>Graphics card</dt>
              <dd>
                {quote.flavor_label}
                {quote.gpu_memory_gb
                  ? ` (${quote.gpu_memory_gb} GB)`
                  : ''} · {moneyWords(quote.price_per_hour)} an hour
              </dd>
            </div>
            <div>
              <dt>Time</dt>
              <dd>{durationWords(quote.seconds)}</dd>
            </div>
            <div>
              <dt>Price</dt>
              <dd>
                about {moneyWords(quote.cost_usd)} · at most{' '}
                {moneyWords(quote.max_cost_usd)} (Hugging Face stops the run
                there)
              </dd>
            </div>
            <div>
              <dt>Uploads</dt>
              <dd>
                {plural(quote.examples, 'example')} (
                {sizeWords(quote.upload_bytes)}) to a private dataset in your
                account. Deleted once your model is downloaded and checked.
              </dd>
            </div>
            <div>
              <dt>Then</dt>
              <dd>
                {quote.model_name} ({sizeWords(quote.download_bytes)}) downloads
                to this computer and runs here. Row-Bot compares it with what
                you use now and asks before using it.
              </dd>
            </div>
          </dl>
          {!quote.measured && (
            <p className="learning-muted">
              The time and price are estimates: this model’s runs on Hugging
              Face haven’t been measured yet.
            </p>
          )}
          <label className="learning-field" htmlFor={capId}>
            <span className="learning-label">Spend at most</span>
            <Select
              id={capId}
              value={String(cap)}
              aria-describedby={`${capId}-help`}
              onChange={(event) => setCap(Number(event.target.value))}
            >
              {choices.map((value) => (
                <option key={value} value={value}>
                  {moneyWords(value)} a month
                </option>
              ))}
            </Select>
          </label>
          <small id={`${capId}-help`} className="learning-muted">
            {spent > 0 ? `${moneyWords(spent)} spent this month. ` : ''}
            Later runs start in quiet hours on their own while they fit this
            cap.
          </small>
          {!fits && (
            <p role="alert" className="learning-error">
              This run doesn’t fit the cap. Choose a higher one to start it.
            </p>
          )}
          {error && (
            <p role="alert" className="learning-error">
              {error}
            </p>
          )}
          <div className="learning-sheet-actions">
            <Button onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button
              variant="primary"
              disabled={busy || !fits}
              onClick={() => void start()}
              data-initial-focus
            >
              {busy
                ? 'Starting…'
                : `Start · about ${moneyWords(quote.cost_usd)}`}
            </Button>
          </div>
        </div>
      )}
    </ModalTask>
  );
}

/**
 * The Personal model view while it trains on Hugging Face: the guided start for someone new to it, the
 * sign-in, the model, this month's spending and the last run (and that its uploads were deleted).
 */
export function HuggingFaceCard({
  io,
  hugging,
  running = false,
}: {
  io: LearningIO;
  hugging: LearningHuggingFace;
  /** A run is planned or under way: the account line is short, the run's own card says the rest. */
  running?: boolean;
}) {
  const signedIn = hugging.sign_in.state === 'signed_in';
  const last = hugging.last_run;
  return (
    <section className="monitor-section" aria-label="Hugging Face">
      <header className="monitor-section-head">
        <h3>Training on Hugging Face</h3>
      </header>
      <div className="learning-stack">
        {!signedIn && (
          <div className="learning-stack-tight">
            <p>
              Hugging Face is where open models like Qwen are published. It also
              rents graphics cards by the minute, so Row-Bot can train a larger
              personal model there, in your own account, than this computer can.
              The trained model then runs here, on your computer.
            </p>
            <p className="learning-muted">
              {HF_NEEDS} <External href={HF_BILLING_URL}>Billing</External>
            </p>
            {hugging.quote && (
              <p className="learning-muted">
                {typicalWords(hugging.quote)} Row-Bot shows the price before
                every run and never spends more than the monthly cap you choose.
              </p>
            )}
          </div>
        )}
        <HuggingFaceSignIn io={io} hugging={hugging} compact={running} />
        {signedIn && <HuggingFaceModels io={io} hugging={hugging} />}
        {signedIn && (
          <dl className="learning-facts" aria-label="Hugging Face spending">
            <div>
              <dt>This month</dt>
              <dd>
                {moneyWords(hugging.month_spent_usd)}
                {hugging.cap_usd
                  ? ` of your ${moneyWords(hugging.cap_usd)} cap`
                  : ' · you choose a monthly cap with your first run'}
              </dd>
            </div>
            {last && (
              <div>
                <dt>Last run</dt>
                <dd>
                  {last.version ? `v${last.version} · ` : ''}
                  {last.minutes
                    ? `${durationWords(last.minutes * 60).replace('about ', '')} · `
                    : ''}
                  {moneyWords(last.cost_usd)}
                  {last.outcome === 'canceled'
                    ? ' · cancelled'
                    : last.outcome === 'error'
                      ? ' · stopped with an error'
                      : last.outcome === 'not_started'
                        ? ' · didn’t start'
                        : ''}
                  {last.cleaned
                    ? ' · its uploaded examples and files are deleted from Hugging Face'
                    : last.cleanup_failed
                      ? ' · deleting its files on Hugging Face didn’t work yet; Row-Bot tries again'
                      : ''}
                </dd>
              </div>
            )}
          </dl>
        )}
      </div>
    </section>
  );
}
