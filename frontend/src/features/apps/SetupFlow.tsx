import { useEffect, useRef, useState, type FormEvent } from 'react';
import {
  AlertCircle,
  Ban,
  Check,
  Circle,
  CircleDot,
  LoaderCircle,
  ShieldCheck,
} from 'lucide-react';
import { useRuntime } from '../../runtime';
import { clientError } from '../../api/errors';
import type {
  InstallPlan,
  PlanContinueRequest,
  PlanStep,
} from '../../api/types';
import {
  Button,
  Disclosure,
  Field,
  Input,
  Select,
  Toggle,
} from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';
import AccessSheet from './AccessSheet';

const VERBS: Record<string, string> = {
  connect: 'Connect',
  add: 'Add',
  turn_on: 'Turn on',
  update: 'Update',
  turn_off: 'Turn off',
  remove: 'Remove',
  access: 'Save',
};

type Target = { itemId: string; revision: string; name: string };

/** One plan at a time: review, consent, then the server runs it while this polls. */
export function usePlan(
  target: Target,
  onChanged: (plan: InstallPlan) => void,
) {
  const { controller } = useRuntime();
  const [plan, setPlan] = useState<InstallPlan | null>(null);
  const [consent, setConsent] = useState<InstallPlan | null>(null);
  const [label, setLabel] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const signingIn = useRef(false);
  const failures = useRef(0);
  const [missed, setMissed] = useState(0);
  const changed = useRef(onChanged);
  changed.current = onChanged;
  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError('');
    try {
      await work();
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  };
  const review = (intent: string, action = '', cleanup = false) =>
    run(async () => {
      setNotice('');
      const value = await controller.reviewInstallPlan({
        item_id: target.itemId,
        revision: target.revision,
        intent: intent as InstallPlan['intent'],
        cleanup,
      });
      if (action) setLabel(action);
      if (value.plan_id) setPlan(value);
      else setConsent(value);
    });
  const confirm = () =>
    run(async () => {
      if (!consent) return;
      const started = await controller.startInstallPlan({
        plan_id: crypto.randomUUID(),
        item_id: target.itemId,
        revision: target.revision,
        intent: consent.intent,
        digest: consent.digest,
        consent_token: consent.consent_token ?? '',
        cleanup: consent.consent.cleanup,
      });
      setConsent(null);
      setPlan(started);
    });
  /** The access sheet is its own consent: review and start in one step. */
  const apply = (intent: string, choice: PlanContinueRequest) =>
    run(async () => {
      const value = await controller.reviewInstallPlan({
        item_id: target.itemId,
        revision: target.revision,
        intent: intent as InstallPlan['intent'],
      });
      setPlan(
        value.plan_id
          ? value
          : await controller.startInstallPlan({
              plan_id: crypto.randomUUID(),
              item_id: target.itemId,
              revision: target.revision,
              intent: value.intent,
              digest: value.digest,
              consent_token: value.consent_token ?? '',
              preset: choice.preset,
              overrides: choice.overrides ?? {},
              tools_digest: choice.tools_digest,
            }),
      );
    });
  const resume = (body: PlanContinueRequest = {}) =>
    run(async () => {
      if (plan?.plan_id)
        setPlan(await controller.continueInstallPlan(plan.plan_id, body));
    });
  const cancel = () =>
    run(async () => {
      if (plan?.plan_id)
        setPlan(await controller.cancelInstallPlan(plan.plan_id));
    });
  const planId = plan?.plan_id;
  const state = plan?.state;
  const pause = plan?.pause;
  useEffect(() => {
    if (!planId) return;
    if (pause === 'sign_in') signingIn.current = true;
    if (state === 'running' || (state === 'paused' && pause === 'sign_in')) {
      const timer = setTimeout(
        () => {
          controller.installPlan(planId).then(
            (value) => {
              failures.current = 0;
              setError('');
              setPlan(value);
            },
            (cause) => {
              // A missed look is not the end of the plan: say so and look again a little later.
              failures.current += 1;
              setError(clientError(cause).message);
              setMissed((count) => count + 1);
            },
          );
        },
        failures.current ? 3000 : 750,
      );
      return () => clearTimeout(timer);
    }
    if (state === 'paused' && pause === 'resume' && signingIn.current) {
      // The browser sign-in finished; the rest of the agreed plan carries on.
      signingIn.current = false;
      void resume();
    }
    if (state === 'completed') {
      // Done: say so once and give the page back its next action.
      setNotice(plan!.message);
      setPlan(null);
      changed.current(plan!);
    } else if (state && !['running', 'paused', 'uncertain'].includes(state))
      changed.current(plan!);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planId, state, pause, plan, controller, missed]);
  return {
    plan,
    consent,
    label,
    busy,
    error,
    review,
    confirm,
    apply,
    resume,
    cancel,
    notice,
    dismissConsent: () => setConsent(null),
    clear: () => setPlan(null),
  };
}

export type PlanControl = ReturnType<typeof usePlan>;

const stepIcons = {
  done: Check,
  running: LoaderCircle,
  waiting: CircleDot,
  failed: AlertCircle,
  unsupported: Ban,
  pending: Circle,
  skipped: Circle,
};

/** Every step type renders the same way: title, state and message. */
export function Stepper({ steps }: { steps: PlanStep[] }) {
  return (
    <ol className="plan-steps" aria-label="Setup steps">
      {steps
        .filter((step) => step.type !== 'consent')
        .map((step) => {
          const Icon = stepIcons[step.state] ?? Circle;
          return (
            <li key={step.id} data-state={step.state}>
              <Icon size={16} aria-hidden />
              <span>
                <strong>{step.title}</strong>
                <span className="visually-hidden">: {step.state}</span>
                {step.message && <small>{step.message}</small>}
              </span>
            </li>
          );
        })}
    </ol>
  );
}

function InputsForm({
  step,
  busy,
  onSubmit,
}: {
  step: PlanStep;
  busy: boolean;
  onSubmit: (values: Record<string, string>) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const inputs = step.inputs ?? [];
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit(
      Object.fromEntries(
        inputs.map((input) => [input.key, values[input.key] ?? input.default]),
      ),
    );
    setValues({});
  };
  const secret = inputs.some((input) => input.secret);
  const plain = inputs.some((input) => !input.secret);
  return (
    <form className="plan-wait stack" onSubmit={submit}>
      {inputs.map((input, index) => {
        const value = values[input.key] ?? input.default;
        const change = (next: string) =>
          setValues({ ...values, [input.key]: next });
        const choices =
          input.format === 'boolean' && !input.choices.length
            ? ['true', 'false']
            : input.choices;
        return (
          <Field
            key={input.key}
            label={input.required ? input.label : `${input.label} (optional)`}
            hint={input.description || undefined}
          >
            {choices.length ? (
              <Select
                data-initial-focus={index === 0 ? true : undefined}
                required={input.required}
                value={value}
                onChange={(event) => change(event.target.value)}
              >
                {!input.required && <option value="">Not set</option>}
                {choices.map((choice) => (
                  <option key={choice} value={choice}>
                    {input.format === 'boolean'
                      ? choice === 'true'
                        ? 'Yes'
                        : 'No'
                      : choice}
                  </option>
                ))}
              </Select>
            ) : (
              <Input
                type={input.secret ? 'password' : 'text'}
                inputMode={input.format === 'number' ? 'decimal' : undefined}
                autoComplete="off"
                spellCheck={false}
                data-initial-focus={index === 0 ? true : undefined}
                required={input.required}
                value={value}
                onChange={(event) => change(event.target.value)}
              />
            )}
            {input.help_url && (
              <a href={input.help_url} target="_blank" rel="noreferrer">
                Where do I get this?
              </a>
            )}
          </Field>
        );
      })}
      <p className="settings-help">
        {secret && plain
          ? 'Keys are saved only in your system keychain; other settings stay with this app.'
          : secret
            ? 'Saved only in your system keychain.'
            : 'Saved with this app on this computer.'}
      </p>
      <div className="app-dialog-actions">
        <Button type="submit" variant="primary" disabled={busy}>
          Continue
        </Button>
      </div>
    </form>
  );
}

/** The plan's progress and the one thing it is waiting for, if anything. */
export function PlanProgress({
  control,
  name,
}: {
  control: PlanControl;
  name: string;
}) {
  const { platform } = useRuntime();
  const { plan, busy } = control;
  const opened = useRef('');
  const step = plan?.steps.find((item) => item.id === plan.current_step);
  const url = plan?.pause === 'sign_in' ? step?.sign_in?.authorization_url : '';
  useEffect(() => {
    if (url && opened.current !== url) {
      opened.current = url;
      void platform.openExternal(url);
    }
  }, [url, platform]);
  if (!plan) return null;
  if (plan.state === 'uncertain')
    return (
      <div className="app-banner" role="status">
        <LoaderCircle size={16} aria-hidden />
        <span>Finishing your last change…</span>
        <Button disabled={busy} onClick={() => void control.resume()}>
          Retry
        </Button>
      </div>
    );
  const done = ['completed', 'failed', 'cancelled', 'expired'].includes(
    plan.state,
  );
  return (
    <section
      className="plan-progress"
      aria-live="polite"
      aria-busy={plan.state === 'running'}
    >
      <Stepper steps={plan.steps} />
      {plan.message && (
        <p role={plan.state === 'failed' ? 'alert' : 'status'}>
          {plan.message}
        </p>
      )}
      {plan.pause === 'inputs' && step && (
        <InputsForm
          step={step}
          busy={busy}
          onSubmit={(inputs) => void control.resume({ inputs })}
        />
      )}
      {plan.pause === 'sign_in' && (
        <div className="plan-wait">
          <p>
            Finish signing in to {name} in your browser. This page updates by
            itself.
          </p>
          {url && (
            <Button onClick={() => void platform.openExternal(url)}>
              Open the sign-in page again
            </Button>
          )}
        </div>
      )}
      {plan.pause === 'resume' && (
        <Button
          variant="primary"
          disabled={busy}
          onClick={() => void control.resume()}
        >
          Continue
        </Button>
      )}
      {plan.pause === 'digest_changed' && (
        <Button
          variant="primary"
          disabled={busy}
          onClick={() => void control.review(plan.intent)}
        >
          Review again
        </Button>
      )}
      <div className="app-dialog-actions">
        {!done && plan.state !== 'running' && (
          <Button disabled={busy} onClick={() => void control.cancel()}>
            Stop
          </Button>
        )}
        {done && (
          <Button onClick={control.clear}>
            {plan.state === 'completed' ? 'Done' : 'Close'}
          </Button>
        )}
        {plan.state === 'failed' && (
          <Button
            variant="primary"
            onClick={() => void control.review(plan.intent)}
          >
            Try again
          </Button>
        )}
      </div>
      <AccessSheet
        open={plan.pause === 'access' && Boolean(step?.access)}
        name={name}
        access={step?.access ?? null}
        change={plan.intent === 'access'}
        busy={busy}
        onCancel={() => void control.cancel()}
        onAllow={(choice) => void control.resume(choice)}
      />
    </section>
  );
}

const SAFE = [
  'Nothing runs or connects until you agree.',
  'Keys are kept in your system keychain, never in settings files.',
  'Deleting, sending, paying and unknown actions always ask first.',
  'You can turn it off or remove it at any time; your data stays unless you choose to delete it.',
];

export function hostOf(place: string) {
  try {
    return new URL(place).host || place;
  } catch {
    return place;
  }
}

function facts(plan: InstallPlan, name: string) {
  const types = new Map(plan.steps.map((step) => [step.type, step.state]));
  const consent = plan.consent;
  const lines: string[] = [];
  if (plan.intent === 'turn_off')
    lines.push('Row-Bot stops using it. Its settings and saved keys are kept.');
  if (plan.intent === 'remove' && consent.cleanup)
    lines.push('Its saved keys and data are deleted too.');
  if (plan.intent === 'update')
    lines.push(
      `Downloads the newest version from ${consent.downloads[0] ?? 'its source'} and checks it first.`,
    );
  if (['connect', 'add', 'fix', 'turn_on'].includes(plan.intent)) {
    for (const place of consent.destinations)
      lines.push(`What you ask goes to ${hostOf(place)}.`);
    if (consent.runs_locally && plan.kind !== 'skill')
      lines.push('Runs on this computer.');
    if (consent.downloads.length && plan.intent !== 'turn_on')
      lines.push(`Downloads ${consent.downloads.join(', ')}.`);
    if (types.get('sign_in') === 'pending')
      lines.push(`You sign in to ${name} in your browser.`);
    else if (types.get('sign_in') === 'skipped')
      lines.push(`If ${name} asks, you sign in to it in your browser.`);
    const asks = plan.steps.find((step) => step.type === 'inputs');
    if (asks?.state === 'pending')
      lines.push(
        asks.inputs?.every((input) => input.secret)
          ? 'You paste a key. It is kept in your system keychain.'
          : 'You add a few settings. Any key is kept in your system keychain.',
      );
    if (types.get('access') === 'pending')
      lines.push('Next, you choose what it can do. Changes ask first.');
    if (plan.kind === 'skill' && plan.intent === 'add')
      lines.push(
        'Adds the skill and turns it on. Scripts in it never run when you add it.',
      );
    if (consent.turns_on_mcp) lines.push('This also turns on apps in Row-Bot.');
  }
  return lines;
}

/** One consent per intent: what will happen, then the safety promises once. */
export function ConsentSheet({
  control,
  name,
  cleanupOffered,
}: {
  control: PlanControl;
  name: string;
  cleanupOffered: boolean;
}) {
  const { consent, busy } = control;
  const verb = consent
    ? (VERBS[consent.intent] ?? (control.label || 'Continue'))
    : '';
  const remove = consent?.intent === 'remove';
  const title = consent
    ? VERBS[consent.intent]
      ? `${verb} ${name}`
      : verb
    : '';
  return (
    <ModalTask
      open={Boolean(consent)}
      onOpenChange={(open) => {
        if (!open) control.dismissConsent();
      }}
      title={title}
      description={
        remove
          ? 'Row-Bot stops using it and removes it from this computer.'
          : "Here's what happens next."
      }
    >
      {consent && (
        <div className="stack app-consent">
          {!consent.supported ? (
            <p role="alert">{consent.unsupported_reason}</p>
          ) : (
            <ul className="app-facts">
              {facts(consent, name).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
          {remove && cleanupOffered && (
            <Field label="Also delete saved keys and data" layout="row">
              <Toggle
                label="Also delete saved keys and data"
                checked={consent.consent.cleanup}
                disabled={busy}
                onChange={(event) =>
                  void control.review('remove', '', event.target.checked)
                }
              />
            </Field>
          )}
          {!remove && consent.supported && (
            <Disclosure summary="How Row-Bot keeps this safe">
              <ul className="app-facts">
                {SAFE.map((line) => (
                  <li key={line}>
                    <ShieldCheck size={14} aria-hidden /> {line}
                  </li>
                ))}
              </ul>
            </Disclosure>
          )}
          {control.error && <p role="alert">{control.error}</p>}
          <div className="app-dialog-actions">
            <Button
              data-initial-focus={remove || undefined}
              onClick={control.dismissConsent}
            >
              Cancel
            </Button>
            {consent.supported && (
              <Button
                data-initial-focus={!remove || undefined}
                variant={remove ? 'danger' : 'primary'}
                disabled={busy}
                onClick={() => void control.confirm()}
              >
                {verb}
              </Button>
            )}
          </div>
        </div>
      )}
    </ModalTask>
  );
}
