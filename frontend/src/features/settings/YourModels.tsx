import { useState } from 'react';
import { Check, Star } from 'lucide-react';
import type { ClientController } from '../../api/controller';
import type { ModelPickerOption } from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, IconButton } from '../../ui/primitives';
import { humanizeToken } from '../../ui/format';
import { BillingTag } from '../shell/ModelList';
import { splitModelLabel } from '../shell/model-choices';
import { SettingsGroup } from './anatomy';

type Job = 'chat' | 'vision' | 'image' | 'video';
const jobWords: Record<Job, string> = {
  chat: 'chat',
  vision: 'sees images',
  image: 'makes images',
  video: 'makes video',
};
/** Rows shown before "Show all": a long list stays one search away. */
const FIRST = 8;

type Starred = { option: ModelPickerOption; jobs: Job[] };

/** The starred models across the jobs' pickers, in the order they were starred. */
function starred(pickers: Record<Job, readonly ModelPickerOption[]>) {
  const rows = new Map<string, Starred>();
  for (const job of Object.keys(jobWords) as Job[])
    for (const option of pickers[job]) {
      if (option.favourite_rank == null) continue;
      const row = rows.get(option.selection_ref);
      if (row) row.jobs.push(job);
      else rows.set(option.selection_ref, { option, jobs: [job] });
    }
  return [...rows.values()].sort(
    (a, b) => (a.option.favourite_rank ?? 0) - (b.option.favourite_rank ?? 0),
  );
}

/**
 * Settings › Models › Your models: the starred models, which every picker
 * lists first. A star takes a model out; a provider's sheet or the Catalog
 * adds more.
 */
export default function YourModels({
  controller,
  pickers,
  main,
  onMain,
  onChanged,
  disabled = false,
}: {
  controller: ClientController;
  pickers: Record<Job, readonly ModelPickerOption[]>;
  /** The main model's reference. */
  main: string;
  onMain: (ref: string) => Promise<void>;
  onChanged: () => Promise<void>;
  disabled?: boolean;
}) {
  const [all, setAll] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const rows = starred(pickers);
  const shown = all ? rows : rows.slice(0, FIRST);

  async function run(ref: string, action: () => Promise<unknown>) {
    if (busy) return;
    setBusy(ref);
    setError('');
    try {
      await action();
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }

  return (
    <SettingsGroup
      title="Your models"
      anchor="your-models"
      note="Every picker lists these first."
      meta={rows.length ? <span>{rows.length} starred</span> : undefined}
    >
      {error && (
        <p role="alert" className="settings-divided settings-catalog-note">
          {error}
        </p>
      )}
      {!rows.length ? (
        <p className="settings-divided settings-catalog-note">
          Star models in a provider below, or in the Catalog, to keep them here.
        </p>
      ) : (
        <ul className="settings-model-row-list" aria-label="Your models">
          {shown.map(({ option, jobs }) => {
            const { name, provider } = splitModelLabel(option.label);
            const isMain = option.selection_ref === main;
            const meta = [
              provider || humanizeToken(option.provider_id),
              jobs.map((job) => jobWords[job]).join(', '),
            ];
            return (
              <li className="settings-divided" key={option.selection_ref}>
                <div className="settings-catalog-row-text">
                  <strong title={option.selection_ref}>
                    {name}
                    {isMain && (
                      <span className="settings-catalog-default">
                        Main model
                      </span>
                    )}
                  </strong>
                  <small>
                    {meta.join(' · ')}
                    {!option.available && (
                      <span className="settings-catalog-warning">
                        {' · '}
                        {option.reason || 'Unavailable'}
                      </span>
                    )}
                  </small>
                </div>
                <BillingTag billing={option.billing ?? null} />
                <div className="settings-model-row-actions">
                  {jobs.includes('chat') && (
                    <IconButton
                      size="sm"
                      label={`Make ${name} the main model`}
                      disabled={
                        disabled || isMain || !option.available || !!busy
                      }
                      onClick={() =>
                        void run(option.selection_ref, () =>
                          onMain(option.selection_ref),
                        )
                      }
                    >
                      <Check size={14} aria-hidden />
                    </IconButton>
                  )}
                  <IconButton
                    size="sm"
                    label={`Remove ${name} from your models`}
                    pressed
                    className="settings-model-favourite"
                    disabled={!!busy}
                    onClick={() =>
                      void run(option.selection_ref, async () => {
                        await controller.updateModelVisibility({
                          action: 'unfavourite',
                          model_ref: option.selection_ref,
                        });
                        await onChanged();
                      })
                    }
                  >
                    <Star size={14} fill="currentColor" aria-hidden />
                  </IconButton>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {rows.length > FIRST && (
        <div className="settings-divided settings-catalog-more">
          <Button variant="ghost" onClick={() => setAll((value) => !value)}>
            {all ? 'Show fewer' : `Show all ${rows.length}`}
          </Button>
        </div>
      )}
    </SettingsGroup>
  );
}
