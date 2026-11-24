import { useEffect, useState } from 'react';
import { RotateCw, SlidersHorizontal } from 'lucide-react';
import type { ClientController } from '../../api/controller';
import type {
  LocalModelSettingsMutation,
  LocalModelSettingsView,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { usePlace } from '../../api/place';
import { ModalTask } from '../../ui/overlays';
import {
  Button,
  Disclosure,
  ErrorState,
  Field,
  IconButton,
  Input,
  Menu,
  Segmented,
  SettingRow,
  Skeleton,
} from '../../ui/primitives';
import { StatusLine } from '../settings/anatomy';

type Thinking = LocalModelSettingsView['thinking']['value'];
type Sampling = LocalModelSettingsView['sampling'];
type SamplingField = Sampling['fields'][number];
type SamplingValues = NonNullable<Sampling['custom']>;

/** Each sampling value's name, range and step, in the order the sheet shows them. */
const SAMPLING_FIELDS: Record<
  SamplingField,
  {
    label: string;
    /** A shorter visible label where the full one would wrap in a narrow field. */
    visible?: string;
    short: string;
    min: number;
    max: number;
    step: number;
  }
> = {
  temperature: {
    label: 'Temperature',
    short: 'temperature',
    min: 0,
    max: 2,
    step: 0.05,
  },
  top_p: { label: 'Top-p', short: 'top-p', min: 0, max: 1, step: 0.05 },
  top_k: { label: 'Top-k', short: 'top-k', min: 0, max: 1000, step: 1 },
  min_p: { label: 'Min-p', short: 'min-p', min: 0, max: 1, step: 0.01 },
  presence_penalty: {
    label: 'Presence penalty',
    visible: 'Presence',
    short: 'presence penalty',
    min: -2,
    max: 2,
    step: 0.1,
  },
};

/** "temperature 0.7 · top-p 0.8 · top-k 20" for the values a reply is sent. */
function samplingWords(values: SamplingValues, fields: SamplingField[]) {
  return fields
    .filter((field) => values[field] != null)
    .map((field) => `${SAMPLING_FIELDS[field].short} ${values[field]}`)
    .join(' · ');
}

/** The values a reply gets unless a chat chooses: the card's for thinking or not. */
function cardValues(sampling: Sampling): SamplingValues | null {
  if (!sampling.card) return null;
  return sampling.thinks ? sampling.card.thinking : sampling.card.non_thinking;
}

/** "64K" for 65,536 tokens. */
export function windowWords(tokens: number): string {
  return `${Math.round(tokens / 1024)}K`;
}

/** Memory as the computer reports it, kept on one line: "8.1 GB" (or "12 GB" for a card). */
function gigabytes(bytes: number, digits = 1): string {
  const value = bytes / 2 ** 30;
  return `${digits ? value.toFixed(digits).replace(/\.0$/, '') : Math.round(value)}\u00a0GB`;
}

/**
 * What the model and its context take loaded, out of what this computer has:
 * "model and context about 8.1 GB of your card's 12 GB", where Row-Bot knows it.
 */
function memoryWords(
  bytes: number | null | undefined,
  context: LocalModelSettingsView['context'],
): string {
  if (!bytes || !context.memory_place) return '';
  const room = context.room_bytes
    ? context.memory_place === 'card'
      ? ` of your card’s ${gigabytes(context.room_bytes, 0)}`
      : ` of this computer’s ${gigabytes(context.room_bytes, 0)}`
    : '';
  return `model and context about ${gigabytes(bytes)}${room}`;
}

type SizeOption = { value: string; label: string; description?: string };

/** A size chosen from a short list, in the app's own menu (a sheet on a phone). */
function SizePicker({
  label,
  value,
  options,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  options: SizeOption[];
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  const current = options.find((option) => option.value === value);
  return (
    <Menu
      label={`${label}: ${current?.label ?? ''}`}
      className="local-model-sheet-picker"
      disabled={disabled}
      actions={options.map((option) => ({
        label: option.label,
        description: option.description,
        selected: option.value === value,
        onSelect: () => {
          if (option.value !== value) onChange(option.value);
        },
      }))}
    >
      <span className="local-model-sheet-picker-value">{current?.label}</span>
    </Menu>
  );
}

/** Where a server the person runs sets its window, by its kind. */
function serverWindowHelp(view: LocalModelSettingsView): string {
  switch (view.profile) {
    case 'lmstudio':
      return 'Load it at another size here, or change Context Length in LM Studio.';
    case 'llama_cpp':
      return 'Change it where you start llama-server, with --ctx-size.';
    case 'vllm':
      return 'Change it where you start vLLM, with --max-model-len.';
    case 'sglang':
      return 'Change it where you start SGLang, with --context-length.';
    default:
      return `Change it in ${view.engine_name}’s own settings.`;
  }
}

/** What Automatic means for this model's Thinking, in words. */
function thinkingHelp(automatic: boolean | null | undefined): string {
  const chat = 'A chat’s own choice still wins.';
  if (automatic == null) return `Automatic lets the model decide. ${chat}`;
  return `Automatic: ${automatic ? 'on' : 'off'} for this model. ${chat}`;
}

/** What a save means for the next reply, in words. */
function appliedWords(view: LocalModelSettingsView): string {
  if (view.applied === 'when_idle')
    return 'Saved. It applies once the reply being written finishes.';
  return 'Saved. It applies from the next message.';
}

/** Settings › Models with a model's settings sheet open over it. */
export function modelSettingsPath(modelRef: string): string {
  return `/settings/models?model-settings=${encodeURIComponent(modelRef)}`;
}

/** Whether a model has its own settings sheet: one on this computer, never a personal one. */
export function hasLocalModelSettings(
  modelRef: string | undefined,
  local: boolean,
): boolean {
  return (
    !!modelRef &&
    local &&
    !modelRef.includes('row-bot-personal') &&
    (modelRef.startsWith('model:ollama:') ||
      modelRef.startsWith('model:custom_openai_'))
  );
}

/**
 * One local model's settings (U1): how much it reads, whether it thinks, and
 * (under More) how long a reply may be. Each choice saves at once. Where
 * Row-Bot runs the engine (Ollama, its own llama.cpp) the window is chosen
 * here from the sizes that fit; a server the person runs shows what it loaded
 * and where to change it (LM Studio can load it again from here).
 */
export function LocalModelSheet({
  controller,
  modelRef,
  open,
  onOpenChange,
}: {
  controller: ClientController;
  modelRef: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const place = usePlace();
  const [view, setView] = useState<LocalModelSettingsView | null>(null);
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [reloadAt, setReloadAt] = useState<number | null>(null);
  const [attempt, setAttempt] = useState(0);
  // Custom chosen for a model without card values: the fields show before anything is saved.
  const [customPending, setCustomPending] = useState(false);

  useEffect(() => {
    if (!open) return;
    const abort = new AbortController();
    setFailure('');
    setMessage('');
    controller.localModelSettings(modelRef, abort.signal).then(
      (next) => {
        if (abort.signal.aborted) return;
        setView(next ?? null);
        // The next size up from what it loaded is the likely ask.
        const sizes = next?.context.reload_choices ?? [];
        const loaded = next?.context.loaded ?? next?.context.current ?? 0;
        setReloadAt(sizes.find((size) => size > loaded) ?? sizes[0] ?? null);
      },
      (cause) => {
        if (!abort.signal.aborted) setFailure(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [controller, modelRef, open, attempt]);

  async function save(change: Partial<LocalModelSettingsMutation>) {
    if (!view) return;
    setBusy(true);
    setMessage('');
    try {
      const next = await controller.saveLocalModelSettings({
        model_ref: view.model_ref,
        context: view.context.chosen ?? null,
        thinking: view.thinking.value,
        reply_limit: view.reply_limit.value ?? null,
        sampling: view.sampling.value,
        sampling_values: view.sampling.custom ?? null,
        ...change,
      });
      setCustomPending(false);
      setView(next);
      setMessage(appliedWords(next));
    } catch (cause) {
      setMessage(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  async function reload() {
    if (!view || reloadAt == null) return;
    setBusy(true);
    setMessage(
      `Loading it in ${view.engine_name} at ${windowWords(reloadAt)}…`,
    );
    try {
      const next = await controller.reloadLocalModelInLmStudio({
        model_ref: view.model_ref,
        context: reloadAt,
      });
      setView(next);
      setMessage(next.notice ?? '');
    } catch (cause) {
      setMessage(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  const context = view?.context;
  const reloadChoices = context?.reload_choices ?? [];
  const chosen = context?.choices.find(
    (choice) => choice.tokens === context.chosen,
  );
  const memory = context
    ? memoryWords(
        context.chosen != null
          ? chosen?.memory_bytes
          : context.automatic_memory_bytes,
        context,
      )
    : '';
  const reading = context?.current
    ? `Reads ${windowWords(context.current)}${memory ? ` · ${memory}` : ''}`
    : '';
  const automaticReply = view?.reply_limit.automatic
    ? `Automatic · up to ${windowWords(view.reply_limit.automatic)} tokens`
    : 'Automatic · the server’s own limit';
  const where = place.kind === 'this_computer' ? 'this computer' : place.name;
  const sampling = view?.sampling;
  const samplingChoice = customPending ? 'custom' : (sampling?.value ?? 'auto');

  function chooseSampling(value: string) {
    if (!sampling) return;
    if (value !== 'custom') {
      setCustomPending(false);
      void save({
        sampling: value as Sampling['value'],
        sampling_values: null,
      });
      return;
    }
    // Custom starts from the values a reply gets now, where the card has them.
    const start = cardValues(sampling);
    if (start) void save({ sampling: 'custom', sampling_values: start });
    else setCustomPending(true);
  }

  return (
    <ModalTask
      open={open}
      onOpenChange={onOpenChange}
      kind="sheet"
      className="local-model-sheet"
      title={view?.name ?? 'Model settings'}
      description={
        view
          ? `Runs in ${view.engine_name} on ${where}.`
          : 'How this model runs on this computer.'
      }
    >
      {/* Focus lands here on open: no ring for a click or tap, one for the keyboard (B1). */}
      <div className="local-model-sheet-focus" tabIndex={-1} data-initial-focus>
        {failure ? (
          <ErrorState
            title="These settings can’t be shown"
            action={
              <Button onClick={() => setAttempt((value) => value + 1)}>
                Try again
              </Button>
            }
          >
            {failure}
          </ErrorState>
        ) : !view || !context ? (
          <Skeleton label="Reading the model’s settings" />
        ) : (
          <div className="local-model-sheet-body">
            {context.editable ? (
              <SettingRow
                label="Context"
                description={
                  <>
                    How much of the chat it reads at once. A larger context
                    remembers more and takes more memory.
                    {reading && (
                      <StatusLine tone={chosen?.slow ? 'warning' : undefined}>
                        {chosen?.slow
                          ? `${reading} · slow first reply on long chats`
                          : reading}
                      </StatusLine>
                    )}
                  </>
                }
                control={
                  <SizePicker
                    label="Context"
                    value={String(context.chosen ?? 'auto')}
                    disabled={busy}
                    onChange={(value) =>
                      void save({
                        context: value === 'auto' ? null : Number(value),
                      })
                    }
                    options={[
                      {
                        value: 'auto',
                        label: context.automatic
                          ? `Automatic · ${windowWords(context.automatic)}`
                          : 'Automatic',
                      },
                      ...context.choices.map((choice) => ({
                        value: String(choice.tokens),
                        label: windowWords(choice.tokens),
                        description: [
                          choice.memory_bytes
                            ? `About ${gigabytes(choice.memory_bytes)}`
                            : '',
                          choice.slow ? 'slow first reply on long chats' : '',
                        ]
                          .filter(Boolean)
                          .join(' · '),
                      })),
                    ]}
                  />
                }
              />
            ) : (
              <SettingRow
                label="Context"
                description={
                  <>
                    {context.loaded
                      ? `${view.engine_name} loaded it at ${windowWords(context.loaded)}. `
                      : `${view.engine_name} doesn’t say; Row-Bot reads ${windowWords(context.current ?? 32768)}. `}
                    {serverWindowHelp(view)}
                  </>
                }
                control={
                  reloadChoices.length > 0 && (
                    <SizePicker
                      label={`Size to load it at in ${view.engine_name}`}
                      value={String(reloadAt ?? '')}
                      disabled={busy}
                      onChange={(value) => setReloadAt(Number(value))}
                      options={reloadChoices.map((size) => ({
                        value: String(size),
                        label: windowWords(size),
                      }))}
                    />
                  )
                }
              />
            )}
            {!context.editable &&
              reloadChoices.length > 0 &&
              reloadAt != null && (
                <div className="local-model-sheet-reload">
                  <Button disabled={busy} onClick={() => void reload()}>
                    <RotateCw size={16} aria-hidden />
                    Reload in {view.engine_name} at {windowWords(reloadAt)}
                  </Button>
                </div>
              )}
            {view.thinking.available && (
              <SettingRow
                label="Thinking"
                description={thinkingHelp(view.thinking.automatic)}
                control={
                  <Segmented<Thinking>
                    label="Thinking"
                    size="sm"
                    value={view.thinking.value}
                    onChange={(value) => void save({ thinking: value })}
                    options={[
                      { value: 'auto', label: 'Automatic', disabled: busy },
                      { value: 'on', label: 'On', disabled: busy },
                      { value: 'off', label: 'Off', disabled: busy },
                    ]}
                  />
                }
              />
            )}
            <Disclosure
              summary="More"
              className="local-model-sheet-more"
              defaultOpen={
                view.reply_limit.value != null || view.sampling.value !== 'auto'
              }
            >
              <SettingRow
                label="Reply length"
                description="The longest reply it may write, in tokens (a token is about three quarters of a word). A limit stops a reply that runs on."
                control={
                  <SizePicker
                    label="Reply length"
                    value={String(view.reply_limit.value ?? 'auto')}
                    disabled={busy}
                    onChange={(value) =>
                      void save({
                        reply_limit: value === 'auto' ? null : Number(value),
                      })
                    }
                    options={[
                      { value: 'auto', label: automaticReply },
                      ...view.reply_limit.choices.map((size) => ({
                        value: String(size),
                        label: `${windowWords(size)} tokens`,
                        description: `About ${(Math.round((size * 0.75) / 1000) * 1000).toLocaleString('en')} words`,
                      })),
                    ]}
                  />
                }
              />
              {sampling && (
                <SettingRow
                  label="Sampling"
                  description={
                    <>
                      How freely it picks each word.
                      {samplingChoice === 'auto' && sampling.card ? (
                        view.thinking.available ? (
                          <>
                            <StatusLine>
                              {`Thinking: ${samplingWords(sampling.card.thinking, sampling.fields)}`}
                            </StatusLine>
                            <StatusLine>
                              {`Not thinking: ${samplingWords(sampling.card.non_thinking, sampling.fields)}`}
                            </StatusLine>
                          </>
                        ) : (
                          <StatusLine>
                            {samplingWords(
                              sampling.card.thinking,
                              sampling.fields,
                            )}
                          </StatusLine>
                        )
                      ) : samplingChoice === 'custom' ? (
                        <StatusLine>
                          Your values, whether it thinks or not.
                        </StatusLine>
                      ) : (
                        <StatusLine>
                          {`${view.engine_name} uses its own values.`}
                        </StatusLine>
                      )}
                    </>
                  }
                  control={
                    <SizePicker
                      label="Sampling"
                      value={samplingChoice}
                      disabled={busy}
                      onChange={chooseSampling}
                      options={[
                        sampling.card
                          ? {
                              value: 'auto',
                              label: 'Model card',
                              description: 'The values its makers recommend',
                            }
                          : {
                              value: 'auto',
                              label: 'Automatic',
                              description: `${view.engine_name} decides`,
                            },
                        ...(sampling.card
                          ? [
                              {
                                value: 'engine',
                                label: 'Engine’s own',
                                description: `${view.engine_name} decides`,
                              },
                            ]
                          : []),
                        {
                          value: 'custom',
                          label: 'Custom',
                          description: 'Values you set',
                        },
                      ]}
                    />
                  }
                />
              )}
              {sampling && samplingChoice === 'custom' && (
                <SamplingFields
                  key={JSON.stringify(sampling.custom ?? {})}
                  sampling={sampling}
                  disabled={busy}
                  onProblem={setMessage}
                  onSave={(values) =>
                    void save({ sampling: 'custom', sampling_values: values })
                  }
                />
              )}
            </Disclosure>
            <p className="local-model-sheet-message" role="status">
              {message}
            </p>
          </div>
        )}
      </div>
    </ModalTask>
  );
}

/**
 * The person's own sampling values, one field each. A field saves when it is
 * left or Enter is pressed; an empty one is the engine's own.
 */
function SamplingFields({
  sampling,
  disabled,
  onSave,
  onProblem,
}: {
  sampling: Sampling;
  disabled: boolean;
  onSave: (values: SamplingValues) => void;
  onProblem: (words: string) => void;
}) {
  const start = sampling.custom ?? {};
  const [draft, setDraft] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      sampling.fields.map((field) => [field, String(start[field] ?? '')]),
    ),
  );

  function commit() {
    const values: SamplingValues = {};
    for (const field of sampling.fields) {
      const text = (draft[field] ?? '').trim();
      if (!text) continue;
      const value = Number(text);
      const { label, min, max } = SAMPLING_FIELDS[field];
      if (
        !Number.isFinite(value) ||
        value < min ||
        value > max ||
        (field === 'top_k' && !Number.isInteger(value))
      ) {
        onProblem(`${label} is between ${min} and ${max}.`);
        return;
      }
      values[field] = value;
    }
    if (Object.keys(values).length === 0) {
      onProblem('Enter a value, or choose Automatic.');
      return;
    }
    const changed = sampling.fields.some(
      (field) => (values[field] ?? null) !== (sampling.custom?.[field] ?? null),
    );
    if (changed) onSave(values);
  }

  return (
    <div className="local-model-sheet-sampling">
      {sampling.fields.map((field) => {
        const { label, visible, min, max, step } = SAMPLING_FIELDS[field];
        return (
          <Field key={field} label={visible ?? label}>
            <Input
              type="number"
              inputMode="decimal"
              min={min}
              max={max}
              step={step}
              placeholder="Engine’s own"
              aria-label={visible ? label : undefined}
              value={draft[field] ?? ''}
              disabled={disabled}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  [field]: event.target.value,
                }))
              }
              onBlur={commit}
              onKeyDown={(event) => {
                if (event.key === 'Enter') commit();
              }}
            />
          </Field>
        );
      })}
    </div>
  );
}

/** The sliders button that opens a model's settings sheet, for a row. */
export function LocalModelSettingsButton({
  controller,
  modelRef,
  name,
  size,
  onClosed,
}: {
  controller: ClientController;
  modelRef: string;
  name: string;
  size?: 'sm';
  /** The sheet closed (a list showing what the model reads reads it again). */
  onClosed?: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <IconButton
        label={`Settings for ${name}`}
        size={size}
        onClick={() => setOpen(true)}
      >
        <SlidersHorizontal size={size ? 14 : 16} aria-hidden />
      </IconButton>
      {open && (
        <LocalModelSheet
          controller={controller}
          modelRef={modelRef}
          open={open}
          onOpenChange={(next) => {
            setOpen(next);
            if (!next) onClosed?.();
          }}
        />
      )}
    </>
  );
}
