import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  CircleStop,
  Download,
  HardDrive,
  Plus,
  Server,
  Trash2,
  Wrench,
} from 'lucide-react';
import type { LocalModelRuntimeView, ProviderLiveCard } from '../../api/types';
import { clientError } from '../../api/errors';
import type { ClientController } from '../../api/controller';
import { useOverlay } from '../../ui/overlays';
import { Button, IconButton } from '../../ui/primitives';
import { SettingsGroup, SettingsItem, StatusLine } from '../settings/anatomy';
import LocalWays from './LocalWays';
import RunOnThisComputer from './RunOnThisComputer';
import { useLocalModelRuntime } from './useLocalModelRuntime';
import { modelLabel, readsWords, repairWords, sizeWords } from './words';

const MANAGED = 'custom_openai_llamacpp';

/** One status line for Row-Bot's download: what runs, what is downloaded, what it takes on disk. */
function statusWords(view: LocalModelRuntimeView): {
  tone: 'success' | 'accent' | 'neutral' | 'danger';
  text: string;
} {
  const downloaded = view.models.filter((model) => model.installed);
  const disk = `${sizeWords(view.disk_bytes)} on disk`;
  const count = `${downloaded.length} model${downloaded.length === 1 ? '' : 's'}`;
  if (view.state === 'downloading' && view.download) {
    const share = view.download.total_bytes
      ? Math.floor((view.download.done_bytes / view.download.total_bytes) * 100)
      : 0;
    return { tone: 'accent', text: `Downloading · ${share}%` };
  }
  if (view.state === 'running')
    return { tone: 'success', text: `Running · ${count} · ${disk}` };
  if (downloaded.length && view.build_installed)
    return {
      tone: 'success',
      text: `Ready · ${count} · ${disk}. It starts when a chat needs it.`,
    };
  if (view.state === 'failed')
    return { tone: 'danger', text: 'The last download didn’t finish' };
  return { tone: 'neutral', text: `Nothing ready yet · ${disk}` };
}

/** The connected runtimes on this computer (Ollama, LM Studio, vLLM, llama.cpp servers), read once. */
function useLocalRuntimes(controller: ClientController) {
  const [cards, setCards] = useState<ProviderLiveCard[] | null>(null);
  useEffect(() => {
    let stopped = false;
    void (async () => {
      try {
        const snapshot = await controller.liveProviderStatus();
        if (!stopped && snapshot) setCards(snapshot.providers);
      } catch {
        /* Shown once they can be read. */
      }
    })();
    return () => {
      stopped = true;
    };
  }, [controller]);
  return cards?.filter(
    (card) =>
      card.billing === 'local' &&
      card.configured &&
      card.provider_id !== MANAGED &&
      !card.provider_id.includes('row-bot-personal'),
  );
}

/**
 * Settings › Models › Local: every way models run on this computer, as equal
 * rows. Connected runtimes are listed with their models; Row-Bot's own
 * download is one entry, with Stop, Repair and Remove. Add offers every way.
 */
export default function LocalModelsSettings({
  controller,
}: {
  controller: ClientController;
}) {
  const overlay = useOverlay();
  const local = useLocalModelRuntime(true, 0, controller);
  const runtimes = useLocalRuntimes(controller);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const view = local.view;
  if (!view && !runtimes) return null;
  const downloaded = view?.models.filter((model) => model.installed) ?? [];
  const here = view?.on_this_computer ?? false;
  const managedShown =
    !!view &&
    (downloaded.length > 0 ||
      view.state === 'downloading' ||
      view.state === 'failed' ||
      view.disk_bytes > 0);

  async function act(
    run: () => Promise<LocalModelRuntimeView | null | undefined>,
    done?: (next: LocalModelRuntimeView) => string | null,
  ) {
    setBusy(true);
    setMessage(null);
    try {
      const next = await run();
      local.update(next);
      if (next && done) setMessage(done(next));
    } catch (cause) {
      setMessage(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  const freed = (next: LocalModelRuntimeView) =>
    next.freed_bytes
      ? `Removed. ${sizeWords(next.freed_bytes)} freed.`
      : 'Removed.';

  function confirmRemove(model?: {
    id: string;
    name: string;
    quantization: string;
    older: boolean;
    size_bytes: number;
  }) {
    overlay.open({
      kind: 'alert',
      title: model
        ? `Remove ${modelLabel(model)}?`
        : 'Remove Row-Bot’s download and its models?',
      description: model
        ? `Row-Bot deletes its file and frees about ${sizeWords(model.size_bytes)}. You can download it again any time; your chats are kept.`
        : `Row-Bot stops the model and deletes llama.cpp and ${downloaded.length} model${downloaded.length === 1 ? '' : 's'}, freeing ${sizeWords(view?.disk_bytes ?? 0)}. Other local runtimes aren’t touched. You can download them again any time; your chats are kept.`,
      confirmLabel: 'Remove',
      cancelLabel: 'Keep',
      onConfirm: () => {
        overlay.close();
        void act(() => controller.removeLocalModelRuntime(model?.id), freed);
      },
    });
  }

  const update = downloaded.find((model) => model.download_bytes > 0);
  const managed = view && managedShown && (
    <SettingsItem
      label="Download with Row-Bot (llama.cpp)"
      help={`${view.computer} · ${view.acceleration_label}`}
      icon={<HardDrive size={16} aria-hidden />}
      tone="2"
      bind={false}
      status={
        <StatusLine tone={statusWords(view).tone}>
          {statusWords(view).text}
        </StatusLine>
      }
      control={
        here && (
          <span className="local-model-settings-actions">
            {view.state === 'running' && (
              <IconButton
                label="Stop the model now (it starts again when a chat needs it)"
                disabled={busy}
                onClick={() =>
                  void act(() => controller.stopLocalModelRuntime())
                }
              >
                <CircleStop size={16} aria-hidden />
              </IconButton>
            )}
            <IconButton
              label={repairWords(view.acceleration === 'metal')}
              disabled={busy || view.state === 'downloading'}
              onClick={() =>
                void act(
                  () => controller.repairLocalModelRuntime(),
                  (next) =>
                    `Checked: ${next.computer} · ${next.acceleration_label}.`,
                )
              }
            >
              <Wrench size={16} aria-hidden />
            </IconButton>
            <IconButton
              label="Remove Row-Bot’s download and its models"
              variant="danger"
              disabled={busy}
              onClick={() => confirmRemove()}
            >
              <Trash2 size={16} aria-hidden />
            </IconButton>
          </span>
        )
      }
    >
      {update && here && (
        <div className="local-model-settings-update">
          <span>A newer llama.cpp build comes with this Row-Bot release.</span>
          <Button
            disabled={busy}
            onClick={() =>
              void act(() =>
                controller.installLocalModel(update.id, update.download_bytes),
              )
            }
          >
            <Download size={16} aria-hidden /> Update (
            {sizeWords(update.download_bytes)})
          </Button>
        </div>
      )}
      {downloaded.length > 0 && (
        <ul
          className="local-model-settings-list"
          aria-label="Models Row-Bot downloaded"
        >
          {downloaded.map((model) => (
            <li key={model.id}>
              <span>
                <strong>{modelLabel(model)}</strong>
                <small>
                  {sizeWords(model.size_bytes)} ·{' '}
                  {readsWords(model.context_tokens)}
                </small>
              </span>
              {here && (
                <IconButton
                  label={`Remove ${modelLabel(model)}`}
                  size="sm"
                  disabled={busy}
                  onClick={() => confirmRemove(model)}
                >
                  <Trash2 size={14} aria-hidden />
                </IconButton>
              )}
            </li>
          ))}
        </ul>
      )}
      {view.state === 'downloading' && here && (
        <div className="local-model-settings-add">
          <RunOnThisComputer
            controller={controller}
            view={view}
            speed={local.speed}
            update={local.update}
          />
        </div>
      )}
    </SettingsItem>
  );

  return (
    <SettingsGroup
      title="Local"
      anchor="local-models"
      note="Models that run on this computer: private and free."
      meta={
        <IconButton
          label="Add a way to run models locally"
          pressed={adding}
          onClick={() => setAdding((open) => !open)}
        >
          <Plus size={16} aria-hidden />
        </IconButton>
      }
    >
      {(runtimes ?? []).map((card) => (
        <SettingsItem
          key={card.provider_id}
          label={card.display_name}
          help={
            card.model_count != null
              ? `${card.model_count} model${card.model_count === 1 ? '' : 's'}`
              : 'On this computer'
          }
          icon={<Server size={16} aria-hidden />}
          tone="neutral"
          bind={false}
          status={<StatusLine tone="success">Connected</StatusLine>}
          control={
            <Link
              className="button ghost small"
              to="/settings/providers"
              aria-label={`Manage ${card.display_name} in Providers`}
            >
              Manage
            </Link>
          }
        />
      ))}
      {managed}
      {message && (
        <p className="local-model-settings-message" role="status">
          {message}
        </p>
      )}
      {!(runtimes ?? []).length && !managed && (
        <SettingsItem
          label="Nothing connected yet"
          help="Add Ollama, LM Studio, vLLM or a llama.cpp server, or let Row-Bot download a model."
          bind={false}
          control={
            <Button onClick={() => setAdding(true)}>
              <Plus size={16} aria-hidden /> Add
            </Button>
          }
        />
      )}
      {adding && (
        <div className="local-model-settings-add">
          <LocalWays
            download={
              <RunOnThisComputer
                controller={controller}
                view={view}
                speed={local.speed}
                update={local.update}
              />
            }
          />
        </div>
      )}
    </SettingsGroup>
  );
}
