import { useEffect, useState } from 'react';
import { Star } from 'lucide-react';
import type { ClientController } from '../../api/controller';
import type {
  LocalRuntimeModel,
  LocalRuntimeSnapshot,
  ModelVisibilityMutation,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { markModels } from '../../api/model-visibility';
import { IconButton, Skeleton } from '../../ui/primitives';
import { SettingsGroup } from './anatomy';

/**
 * Settings › Models › On this computer: the local models, each with a star
 * (in your models, so every picker lists it), as in the Catalog. Choosing
 * happens in the pickers; this only lists and manages them.
 */
export default function LocalModelSettings({
  controller,
  runtime,
  onChanged,
}: {
  controller: ClientController;
  /** The local runtime as Setup reads it: undefined while it loads, null when it can't be read. */
  runtime: LocalRuntimeSnapshot | null | undefined;
  /** The pickers on this page re-read their order. */
  onChanged: () => void;
}) {
  const [models, setModels] = useState<LocalRuntimeModel[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => setModels(runtime?.models ?? []), [runtime]);

  const favourites = models
    .filter((model) => model.favourite_rank != null)
    .sort((a, b) => (a.favourite_rank ?? 0) - (b.favourite_rank ?? 0));
  const shown = [
    ...favourites,
    ...models.filter((model) => model.favourite_rank == null),
  ];

  async function change(
    action: ModelVisibilityMutation['action'],
    model: LocalRuntimeModel,
  ) {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const saved = await controller.updateModelVisibility({
        action,
        model_ref: model.model_ref,
      });
      setModels((current) => markModels(current, saved));
      onChanged();
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  if (runtime === null || runtime?.state === 'not_installed') return null;
  const row = (model: LocalRuntimeModel) => {
    const starred = model.favourite_rank != null;
    const meta = [
      starred ? 'In your models' : '',
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
              starred
                ? `Remove ${model.name} from your models`
                : `Add ${model.name} to your models`
            }
            pressed={starred}
            className="settings-model-favourite"
            disabled={busy}
            onClick={() =>
              void change(starred ? 'unfavourite' : 'favourite', model)
            }
          >
            <Star
              size={14}
              fill={starred ? 'currentColor' : 'none'}
              aria-hidden
            />
          </IconButton>
        </div>
      </li>
    );
  };
  return (
    <SettingsGroup
      title="On this computer"
      anchor="local-models"
      note="Starred models are your models: every picker lists them."
      meta={
        favourites.length > 0 ? (
          <span>{`${favourites.length} starred`}</span>
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
          Ollama isn’t running. Start it to see the models on this computer.
        </p>
      ) : !models.length ? (
        <p className="settings-divided settings-catalog-note">
          Ollama has no models yet.
        </p>
      ) : (
        <ul
          className="settings-model-row-list"
          aria-label="Models on this computer"
        >
          {shown.map(row)}
        </ul>
      )}
    </SettingsGroup>
  );
}
