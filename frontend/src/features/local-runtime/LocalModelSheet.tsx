import { useEffect, useId, useState } from 'react';
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
  IconButton,
  Segmented,
  Select,
  SettingRow,
  Skeleton,
} from '../../ui/primitives';
import { StatusLine } from '../settings/anatomy';
import { sizeWords } from './words';

type Thinking = LocalModelSettingsView['thinking']['value'];

/** "64K" for 65,536 tokens. */
export function windowWords(tokens: number): string {
  return `${Math.round(tokens / 1024)}K`;
}

/** "about 2.3 GB on your card" for a context cache, where Row-Bot knows it. */
function memoryWords(
  bytes: number | null | undefined,
  place: LocalModelSettingsView['context']['memory_place'],
): string {
  if (!bytes || !place) return '';
  return `about ${sizeWords(bytes)} ${place === 'card' ? 'on your card' : 'in memory'}`;
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
  const contextId = useId();
  const replyId = useId();
  const reloadId = useId();
  const [view, setView] = useState<LocalModelSettingsView | null>(null);
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [reloadAt, setReloadAt] = useState<number | null>(null);
  const [attempt, setAttempt] = useState(0);

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
        ...change,
      });
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
  const reading = context?.current
    ? `Reads ${windowWords(context.current)} now${
        context.chosen != null && chosen?.memory_bytes
          ? ` · ${memoryWords(chosen.memory_bytes, context.memory_place)}`
          : context.chosen == null && context.automatic_memory_bytes
            ? ` · ${memoryWords(context.automatic_memory_bytes, context.memory_place)}`
            : ''
      }`
    : '';
  const automaticReply = view?.reply_limit.automatic
    ? `Automatic · up to ${windowWords(view.reply_limit.automatic)}`
    : 'Automatic · the server’s own limit';
  const where = place.kind === 'this_computer' ? 'this computer' : place.name;

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
              htmlFor={contextId}
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
                <Select
                  id={contextId}
                  value={context.chosen ?? 'auto'}
                  disabled={busy}
                  data-initial-focus
                  onChange={(event) =>
                    void save({
                      context:
                        event.target.value === 'auto'
                          ? null
                          : Number(event.target.value),
                    })
                  }
                >
                  <option value="auto">
                    {context.automatic
                      ? `Automatic · ${windowWords(context.automatic)}`
                      : 'Automatic'}
                  </option>
                  {context.choices.map((choice) => (
                    <option key={choice.tokens} value={choice.tokens}>
                      {windowWords(choice.tokens)}
                      {choice.slow ? ' · slow first reply on long chats' : ''}
                    </option>
                  ))}
                </Select>
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
                  <Select
                    id={reloadId}
                    aria-label={`Size to load it at in ${view.engine_name}`}
                    value={reloadAt ?? ''}
                    disabled={busy}
                    onChange={(event) =>
                      setReloadAt(Number(event.target.value))
                    }
                  >
                    {reloadChoices.map((size) => (
                      <option key={size} value={size}>
                        {windowWords(size)}
                      </option>
                    ))}
                  </Select>
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
              description="Automatic lets the model decide. A chat’s own choice still wins."
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
            defaultOpen={view.reply_limit.value != null}
          >
            <SettingRow
              label="Reply length"
              htmlFor={replyId}
              description="The most one reply may write. A limit stops a reply that runs on."
              control={
                <Select
                  id={replyId}
                  value={view.reply_limit.value ?? 'auto'}
                  disabled={busy}
                  onChange={(event) =>
                    void save({
                      reply_limit:
                        event.target.value === 'auto'
                          ? null
                          : Number(event.target.value),
                    })
                  }
                >
                  <option value="auto">{automaticReply}</option>
                  {view.reply_limit.choices.map((size) => (
                    <option key={size} value={size}>
                      {windowWords(size)}
                    </option>
                  ))}
                </Select>
              }
            />
          </Disclosure>
          <p className="local-model-sheet-message" role="status">
            {message}
          </p>
        </div>
      )}
    </ModalTask>
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
