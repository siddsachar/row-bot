import { useEffect, useState } from 'react';
import { Eye, EyeOff, Star } from 'lucide-react';
import type { ClientController } from '../../api/controller';
import type {
  LocalRuntimeModel,
  LocalRuntimeSnapshot,
  ModelPickerOption,
  ModelVisibilityMutation,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { markModels } from '../../api/model-visibility';
import { Button, Disclosure, IconButton, Skeleton } from '../../ui/primitives';
import { useNotify } from '../../ui/overlays';
import { SettingsGroup } from './anatomy';

type Action = ModelVisibilityMutation['action'];

/**
 * Settings › Models › On this computer (F10): the local models, each with a
 * star (favourites come first in every model list) and Hide (it waits under
 * More models and still works). Choosing happens in the pickers; this only
 * lists and manages them.
 */
export default function LocalModelSettings({
  controller,
  runtime,
  pickerOptions,
  onChanged,
}: {
  controller: ClientController;
  /** The local runtime as Setup reads it: undefined while it loads, null when it can't be read. */
  runtime: LocalRuntimeSnapshot | null | undefined;
  /** The Brain picker's list, for hidden models that aren't local. */
  pickerOptions: readonly ModelPickerOption[];
  /** The pickers on this page re-read their order. */
  onChanged: () => void;
}) {
  const notify = useNotify();
  const [models, setModels] = useState<LocalRuntimeModel[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => setModels(runtime?.models ?? []), [runtime]);

  const local = new Set(models.map((model) => model.model_ref));
  // Hidden anywhere: the local models and the picker's other models.
  const hiddenElsewhere = pickerOptions.filter(
    (option) => option.hidden && !local.has(option.selection_ref),
  ).length;
  const favourites = models
    .filter((model) => model.favourite_rank != null)
    .sort((a, b) => (a.favourite_rank ?? 0) - (b.favourite_rank ?? 0));
  const shown = [
    ...favourites,
    ...models.filter((model) => model.favourite_rank == null && !model.hidden),
  ];
  const hidden = models.filter((model) => model.hidden);
  const hiddenTotal = hidden.length + hiddenElsewhere;

  async function change(
    action: Action,
    model?: LocalRuntimeModel,
    undoing = false,
  ) {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const saved = await controller.updateModelVisibility(
        model
          ? { action, model_ref: model.model_ref }
          : { action, model_ref: null },
      );
      setModels((current) => markModels(current, saved));
      onChanged();
      if (undoing) return;
      if (action === 'hide' && model)
        notify(`${model.name} is hidden`, undefined, {
          label: 'Undo',
          onAction: () =>
            void change(
              model.favourite_rank != null ? 'favourite' : 'show',
              model,
              true,
            ),
        });
      else if (action === 'show_all') notify('Every model is shown again');
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  if ((runtime === null || runtime?.state === 'not_installed') && !hiddenTotal)
    return null;
  const counts = [
    favourites.length
      ? `${favourites.length} favourite${favourites.length === 1 ? '' : 's'}`
      : '',
    hiddenTotal ? `${hiddenTotal} hidden` : '',
  ].filter(Boolean);
  const row = (model: LocalRuntimeModel) => {
    const favourite = model.favourite_rank != null;
    const meta = [
      favourite ? 'Listed first' : '',
      model.hidden ? 'Under More models' : '',
      model.agent_ready === false ? 'Chat only' : '',
    ].filter(Boolean);
    return (
      <li className="settings-divided" key={model.model_ref}>
        <div className="settings-catalog-row-text">
          <strong>{model.name}</strong>
          {meta.length > 0 && <small>{meta.join(' · ')}</small>}
        </div>
        <div className="settings-model-row-actions">
          <IconButton
            size="sm"
            label={
              favourite
                ? `Remove ${model.name} from favourites`
                : `Add ${model.name} to favourites`
            }
            pressed={favourite}
            className="settings-model-favourite"
            disabled={busy}
            onClick={() =>
              void change(favourite ? 'unfavourite' : 'favourite', model)
            }
          >
            <Star
              size={14}
              fill={favourite ? 'currentColor' : 'none'}
              aria-hidden
            />
          </IconButton>
          <IconButton
            size="sm"
            label={model.hidden ? `Show ${model.name}` : `Hide ${model.name}`}
            disabled={busy}
            onClick={() => void change(model.hidden ? 'show' : 'hide', model)}
          >
            {/* The eye shows the state (open: listed), like a layer's eye. */}
            {model.hidden ? (
              <EyeOff size={14} aria-hidden />
            ) : (
              <Eye size={14} aria-hidden />
            )}
          </IconButton>
        </div>
      </li>
    );
  };
  return (
    <SettingsGroup
      title="On this computer"
      anchor="local-models"
      note="Favourites come first in every model list. Hidden models wait under More models and still work."
      meta={
        counts.length > 0 || hiddenTotal > 0 ? (
          <>
            {counts.length > 0 && <span>{counts.join(' · ')}</span>}
            {hiddenTotal > 0 && (
              <>
                <span className="settings-status-sep" aria-hidden>
                  ·
                </span>
                <Button
                  variant="ghost"
                  className="settings-link"
                  disabled={busy}
                  onClick={() => void change('show_all')}
                >
                  Show all hidden
                </Button>
              </>
            )}
          </>
        ) : undefined
      }
    >
      {error && (
        <p role="alert" className="settings-divided settings-catalog-note">
          {error}
        </p>
      )}
      {runtime === undefined ? (
        <div className="settings-divided settings-catalog-note">
          <Skeleton label="Looking for models on this computer" />
        </div>
      ) : runtime?.state !== 'running' ? (
        <p className="settings-divided settings-catalog-note">
          {runtime?.state === 'installed'
            ? 'Ollama isn’t running. Start it to see the models on this computer.'
            : 'No models on this computer.'}
        </p>
      ) : !models.length ? (
        <p className="settings-divided settings-catalog-note">
          Ollama has no models yet.
        </p>
      ) : (
        <>
          {shown.length > 0 && (
            <ul
              className="settings-model-row-list"
              aria-label="Models on this computer"
            >
              {shown.map(row)}
            </ul>
          )}
          {hidden.length > 0 && (
            <Disclosure
              className="settings-group-disclosure settings-divided"
              summary={`Hidden (${hidden.length})`}
              defaultOpen={!shown.length}
            >
              <ul
                className="settings-model-row-list"
                aria-label="Hidden models"
              >
                {hidden.map(row)}
              </ul>
            </Disclosure>
          )}
        </>
      )}
    </SettingsGroup>
  );
}
