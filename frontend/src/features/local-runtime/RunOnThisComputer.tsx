import { useState } from 'react';
import {
  Check,
  Cpu,
  Download,
  Loader2,
  Pause,
  SlidersHorizontal,
} from 'lucide-react';
import type { LocalModelOption, LocalModelRuntimeView } from '../../api/types';
import { clientError } from '../../api/errors';
import { usePlace } from '../../api/place';
import type { ClientController } from '../../api/controller';
import { Button, IconButton } from '../../ui/primitives';
import { useOptionalController } from './useLocalModelRuntime';
import {
  OLDER_NOTE,
  factLine,
  failureWords,
  fitWords,
  modelLabel,
  pickedModel,
  remainingWords,
  sizeWords,
} from './words';

type Props = {
  view: LocalModelRuntimeView | null;
  /** Bytes per second seen so far (for "about 4 min"). */
  speed: number | null;
  /** Show an action's answer at once (and keep reading while it downloads). */
  update: (view: LocalModelRuntimeView | null | undefined) => void;
  /** Setup: use a downloaded model (it becomes the default and is tested). */
  onPick?: (modelRef: string, label: string) => void;
  /** Settings pages pass their controller; Setup reads the runtime's. */
  controller?: ClientController;
};

/**
 * Download with Row-Bot (E1): the details of its row among the other ways to
 * run locally. One model that fits this computer (Qwen3.8 27B at the build
 * that fits, or a smaller older model), downloaded with one press of a button
 * that names the size, then run by Row-Bot itself.
 */
export default function RunOnThisComputer({
  view,
  speed,
  update,
  onPick,
  controller: given,
}: Props) {
  const fromRuntime = useOptionalController();
  const controller = (given ?? fromRuntime) as ClientController;
  const place = usePlace();
  const [chosen, setChosen] = useState<string | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!view)
    return (
      <p className="first-run-waiting" role="status">
        <Loader2 size={14} className="spin" aria-hidden /> Checking {place.name}
        …
      </p>
    );
  if (!view.on_this_computer)
    return (
      <div className="local-model">
        <p>
          Download models from Row-Bot on the computer it runs on. Its models
          show up here once they’re downloaded.
        </p>
      </div>
    );
  if (view.state === 'unavailable')
    return (
      <div className="local-model">
        <p role="status">
          Row-Bot can’t run a model on this computer yet ({view.computer}). Use
          one of the other ways, or a provider.
        </p>
      </div>
    );

  async function act(
    run: () => Promise<LocalModelRuntimeView | null | undefined>,
  ) {
    setBusy(true);
    setError(null);
    try {
      update(await run());
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  const downloaded = view.models.filter((model) => model.installed);
  const download = view.state === 'downloading' ? view.download : null;
  if (download) {
    const row = view.models.find((model) => model.id === download.model_id);
    const name = row ? modelLabel(row) : 'the model';
    const percent = download.total_bytes
      ? Math.min(100, (download.done_bytes / download.total_bytes) * 100)
      : 0;
    const left = remainingWords(
      download.total_bytes - download.done_bytes,
      speed,
    );
    const checking = download.phase === 'checking';
    const words = checking
      ? `Checking ${name} ${place.on}…`
      : `Downloading ${name} · ${sizeWords(download.done_bytes)} of ${sizeWords(download.total_bytes)}${left ? ` · ${left}` : ''}`;
    return (
      <div className="local-model">
        <div className="local-model-head">
          <div className="local-model-progress">
            <progress
              aria-label={`Downloading ${name}`}
              max={100}
              value={checking ? undefined : percent}
            />
            <p role="status">{words}</p>
          </div>
          <IconButton
            label="Stop download; it resumes later"
            disabled={busy || checking}
            onClick={() => void act(() => controller.stopLocalModelRuntime())}
          >
            <Pause size={14} aria-hidden />
          </IconButton>
        </div>
        <p className="local-model-note">
          You can keep using Row-Bot meanwhile; leaving this page keeps it
          going.
        </p>
        {error && <p role="alert">{error}</p>}
      </div>
    );
  }

  // The chosen model; without a choice, a downloaded one, else the recommendation.
  const model = chosen
    ? pickedModel(view, chosen)
    : (downloaded.find((item) => item.id === view.recommended) ??
      downloaded[0] ??
      pickedModel(view, null));
  const ready = Boolean(model?.installed && view.build_installed);
  const failure = failureWords(view);

  return (
    <div className="local-model">
      <div className="local-model-head">
        {model && ready ? (
          <p className="local-model-ready" role="status">
            <Check size={14} aria-hidden /> {modelLabel(model)} is on this
            computer and ready.
          </p>
        ) : model ? (
          <p className="local-model-facts">{factLine(view, model)}</p>
        ) : (
          <p className="local-model-facts">
            <Cpu size={14} aria-hidden /> This computer has little room for a
            model ({view.computer}). A small one can still run, slowly.
          </p>
        )}
        <IconButton
          label="Choose another model"
          pressed={choosing}
          onClick={() => setChoosing((open) => !open)}
        >
          <SlidersHorizontal size={16} aria-hidden />
        </IconButton>
      </div>
      {model?.older && !ready && (
        <p className="local-model-note">{OLDER_NOTE}</p>
      )}
      {failure && <p role="alert">{failure}</p>}
      {error && <p role="alert">{error}</p>}
      {choosing && (
        <ModelChoices
          view={view}
          selected={model?.id ?? null}
          onSelect={(id) => {
            setChosen(id);
            setChoosing(false);
          }}
        />
      )}
      {model && (ready ? onPick : true) && (
        <div className="actions">
          {ready ? (
            <Button
              disabled={busy}
              onClick={() => onPick?.(model.model_ref, modelLabel(model))}
            >
              Use {modelLabel(model)}
            </Button>
          ) : (
            <Button
              disabled={busy || model.fit === 'too_big'}
              onClick={() =>
                void act(() =>
                  controller.installLocalModel(model.id, model.download_bytes),
                )
              }
            >
              <Download size={16} aria-hidden /> Download{' '}
              {sizeWords(model.download_bytes)}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function ModelChoices({
  view,
  selected,
  onSelect,
}: {
  view: LocalModelRuntimeView;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <ul className="local-model-choices" aria-label="Models for this computer">
      {view.models.map((model: LocalModelOption) => (
        <li key={model.id}>
          <button
            type="button"
            className="local-model-choice"
            aria-pressed={model.id === selected}
            disabled={model.fit === 'too_big'}
            onClick={() => onSelect(model.id)}
          >
            <span className="local-model-choice-name">
              <strong>{modelLabel(model)}</strong>
              {model.id === view.recommended && <small>Recommended</small>}
            </span>
            <span className="local-model-choice-facts">
              {model.installed ? 'Downloaded' : sizeWords(model.size_bytes)} ·{' '}
              {fitWords(model.fit)}
              {model.older ? ' · smaller, older generation' : ''}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
