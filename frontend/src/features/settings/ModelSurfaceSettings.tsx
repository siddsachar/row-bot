import {
  Eye,
  GitBranch,
  Image as ImageIcon,
  SlidersHorizontal,
  Video,
} from 'lucide-react';
import { useEffect, useRef, type ComponentType } from 'react';
import { Link } from 'react-router-dom';
import type {
  CachedModelPage,
  DefaultModelSnapshot,
  ProviderConfigurationPage,
  ProviderConfigurationReceipt,
  ProviderConfigurationReview,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, Field, Select, Skeleton } from '../../ui/primitives';
import {
  type ProviderSettingsSession,
  useProviderSettingsValue,
} from './provider-settings-sessions';

type Surface = 'vision' | 'image' | 'video';
type Operation = 'provider.model.pin' | 'provider.model.unpin';
type Fields = { provider_id: string; model_id: string; surface: Surface };
type Reviewed = {
  operation: Operation;
  configurationRevision: string;
  fields: Fields;
  selectionRef: string;
  review: ProviderConfigurationReview;
};
type Pending = Reviewed & { commandId: string };
type SurfaceSelections = Record<Surface, string>;

const surfaces: readonly {
  id: Surface;
  label: string;
  description: string;
  icon: ComponentType<{ size?: number; 'aria-hidden'?: boolean }>;
}[] = [
  {
    id: 'vision',
    label: 'Vision',
    description: 'Camera and screen capture analysis',
    icon: Eye,
  },
  {
    id: 'image',
    label: 'Image',
    description: 'Image generation and editing',
    icon: ImageIcon,
  },
  {
    id: 'video',
    label: 'Video',
    description: 'Video generation and image animation',
    icon: Video,
  },
] as const;
const emptySelections = (): SurfaceSelections => ({
  vision: '',
  image: '',
  video: '',
});
const MAX_CATALOG_PAGES = 4;
const MAX_CATALOG_ROWS = 200;

function supportsSurface(
  model: CachedModelPage['items'][number],
  surface: Surface,
) {
  return (
    model.categories.includes(surface) ||
    model.pinned_surfaces.includes(surface)
  );
}

function updateMembership(
  models: CachedModelPage['items'],
  fields: Fields,
  pinned: boolean,
) {
  return models.map((model) => {
    if (
      model.provider_id !== fields.provider_id ||
      model.model_id !== fields.model_id
    )
      return model;
    const values = new Set(model.pinned_surfaces);
    if (pinned) values.add(fields.surface);
    else values.delete(fields.surface);
    return { ...model, pinned_surfaces: [...values] };
  });
}

export type ModelSurfaceSettingsProps = {
  session: ProviderSettingsSession;
  loadModels: (
    providerId?: string,
    query?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) => Promise<CachedModelPage>;
  loadConfiguration: (
    signal?: AbortSignal,
  ) => Promise<ProviderConfigurationPage>;
  review: (
    operation: Operation,
    revision: string,
    fields: Fields,
    signal?: AbortSignal,
  ) => Promise<ProviderConfigurationReview>;
  apply: (
    operation: Operation,
    revision: string,
    fields: Fields,
    commandId: string,
    review: ProviderConfigurationReview,
  ) => Promise<{ configuration_revision: string }>;
  receipt: (
    commandId: string,
    signal?: AbortSignal,
  ) => Promise<ProviderConfigurationReceipt>;
  onChanged: () => void;
};

export default function ModelSurfaceSettings({
  session,
  loadModels,
  loadConfiguration,
  review: reviewChange,
  apply,
  receipt: readReceipt,
  onChanged,
}: ModelSurfaceSettingsProps) {
  const [models, setModels] = useProviderSettingsValue<
    CachedModelPage['items']
  >(session, 'surfaceModels', []);
  const [configurationRevision, setConfigurationRevision] =
    useProviderSettingsValue(session, 'surfaceConfigurationRevision', '');
  const [selection, setSelection] = useProviderSettingsValue<SurfaceSelections>(
    session,
    'surfaceSelection',
    emptySelections(),
  );
  const [, setReviewed] = useProviderSettingsValue<Reviewed | null>(
    session,
    'surfaceReviewed',
    null,
  );
  const [pending, setPending] = useProviderSettingsValue<Pending | null>(
    session,
    'surfacePending',
    null,
  );
  const [busy, setBusy] = useProviderSettingsValue(session, 'surfaceBusy', '');
  const [error, setError] = useProviderSettingsValue(
    session,
    'surfaceError',
    '',
  );
  const [notice, setNotice] = useProviderSettingsValue(
    session,
    'surfaceNotice',
    '',
  );
  const [truncated, setTruncated] = useProviderSettingsValue(
    session,
    'surfaceTruncated',
    false,
  );
  const [defaultSnapshot] =
    useProviderSettingsValue<DefaultModelSnapshot | null>(
      session,
      'snapshot',
      null,
    );
  const epoch = useRef(0);
  const defaultBusy = session.get('busy', '');
  const defaultPending = session.get('pending', null);
  const locked =
    !session.active || !!busy || !!pending || !!defaultBusy || !!defaultPending;

  async function load() {
    if (!session.active || session.get('surfaceBusy', '')) return;
    const abort = session.read();
    const ticket = ++epoch.current;
    setBusy('load');
    setError('');
    try {
      const [configuration, first] = await Promise.all([
        loadConfiguration(abort.signal),
        loadModels(undefined, '', undefined, abort.signal),
      ]);
      let rows = [...first.items];
      let cursor = first.next_cursor ?? undefined;
      let pageCount = 1;
      while (
        cursor &&
        pageCount < MAX_CATALOG_PAGES &&
        rows.length < MAX_CATALOG_ROWS
      ) {
        const next = await loadModels(undefined, '', cursor, abort.signal);
        if (next.revision !== first.revision) throw { code: 'cursor_expired' };
        rows = [...rows, ...next.items].slice(0, MAX_CATALOG_ROWS);
        cursor = next.next_cursor ?? undefined;
        pageCount += 1;
      }
      if (abort.signal.aborted || ticket !== epoch.current || !session.active)
        return;
      const nextSelection = { ...selection };
      for (const surface of surfaces) {
        const available = rows.filter((model) =>
          supportsSurface(model, surface.id),
        );
        if (
          !nextSelection[surface.id] ||
          !available.some(
            (model) => model.selection_ref === nextSelection[surface.id],
          )
        )
          nextSelection[surface.id] =
            available.find((model) =>
              model.pinned_surfaces.includes(surface.id),
            )?.selection_ref ?? '';
      }
      setModels(rows);
      setConfigurationRevision(configuration.revision);
      setSelection(nextSelection);
      setTruncated(Boolean(cursor));
      setReviewed(null);
    } catch (cause) {
      if (!abort.signal.aborted) setError(clientError(cause).message);
    } finally {
      session.finishRead(abort);
      if (ticket === epoch.current) setBusy('');
    }
  }

  useEffect(() => {
    if (
      !session.get<CachedModelPage['items']>('surfaceModels', []).length &&
      !session.get('surfaceBusy', '')
    )
      void load();
    return () => {
      epoch.current += 1;
    };
    // The authenticated owner retains reviews and pending receipts on route changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session]);

  function choose(surface: Surface, selectionRef: string) {
    if (locked) return;
    setSelection((current) => ({ ...current, [surface]: selectionRef }));
    setReviewed(null);
    setError('');
    setNotice('');
  }

  function selected(surface: Surface) {
    return models.find((model) => model.selection_ref === selection[surface]);
  }

  async function review(surface: Surface) {
    const model = selected(surface);
    if (
      locked ||
      session.get('surfaceBusy', '') ||
      !configurationRevision ||
      !model
    )
      return;
    const abort = session.read();
    const fields: Fields = {
      provider_id: model.provider_id,
      model_id: model.model_id,
      surface,
    };
    const operation: Operation = model.pinned_surfaces.includes(surface)
      ? 'provider.model.unpin'
      : 'provider.model.pin';
    setBusy('review');
    setReviewed(null);
    setError('');
    setNotice('');
    let approved: Reviewed | null = null;
    try {
      const result = await reviewChange(
        operation,
        configurationRevision,
        fields,
        abort.signal,
      );
      if (
        result.operation !== operation ||
        result.configuration_revision !== configurationRevision ||
        !result.nonce
      )
        throw { code: 'revision_conflict' };
      if (!abort.signal.aborted && session.active) {
        approved = {
          operation,
          configurationRevision,
          fields,
          selectionRef: model.selection_ref,
          review: structuredClone(result),
        };
        setReviewed(approved);
      }
    } catch (cause) {
      if (!abort.signal.aborted) setError(clientError(cause).message);
    } finally {
      session.finishRead(abort);
      setBusy('');
    }
    if (approved) await confirm(approved);
  }

  function settle(change: Reviewed, revision: string) {
    const pinned = change.operation === 'provider.model.pin';
    setModels((current) => updateMembership(current, change.fields, pinned));
    setConfigurationRevision(revision);
    setReviewed(null);
    setPending(null);
    session.resolved();
    setNotice(
      `${change.fields.surface[0].toUpperCase()}${change.fields.surface.slice(1)} picker membership saved. No provider or model was started.`,
    );
    onChanged();
  }

  async function confirm(approved?: Reviewed) {
    const captured =
      approved ?? session.get<Reviewed | null>('surfaceReviewed', null);
    if (locked || !captured) return;
    const original: Pending = {
      ...structuredClone(captured),
      commandId: crypto.randomUUID(),
    };
    setPending(original);
    setReviewed(null);
    setBusy('save');
    setError('');
    try {
      const result = await session.perform([original], () =>
        apply(
          original.operation,
          original.configurationRevision,
          structuredClone(original.fields),
          original.commandId,
          structuredClone(original.review),
        ),
      );
      if (session.active) settle(original, result.configuration_revision);
    } catch (cause) {
      setError(clientError(cause).message);
      setNotice(
        'The original outcome is unconfirmed. Check its receipt; this change will not be sent again.',
      );
    } finally {
      setBusy('');
    }
  }

  async function receipt() {
    if (!pending || busy || !session.active) return;
    const abort = session.read();
    setBusy('receipt');
    setError('');
    try {
      const value = await readReceipt(pending.commandId, abort.signal);
      if (abort.signal.aborted || !session.active) return;
      if (value.command_id !== pending.commandId)
        throw { code: 'operation_uncertain' };
      if (value.status === 'uncertain')
        setNotice(
          'The original outcome remains unconfirmed. No request was replayed.',
        );
      else if (value.status === 'completed')
        settle(pending, value.configuration_revision);
      else {
        setPending(null);
        session.resolved();
        setNotice(
          'The original picker change was rejected. Reload saved choices before trying again.',
        );
      }
    } catch (cause) {
      if (!abort.signal.aborted) setError(clientError(cause).message);
    } finally {
      session.finishRead(abort);
      setBusy('');
    }
  }

  const defaultModel = models.find(
    (model) => model.selection_ref === defaultSnapshot?.selection_ref,
  );

  return (
    <div
      className="stack settings-model-surface-owner"
      role="group"
      aria-label="Vision, image, and video model pickers"
      aria-busy={!!busy}
    >
      <details className="settings-model-secondary">
        <summary>
          <SlidersHorizontal size={17} aria-hidden />
          Advanced context
        </summary>
        <div className="settings-model-secondary-content">
          <p>
            {defaultModel?.context_window
              ? `Saved catalog context window: ${defaultModel.context_window.toLocaleString()} tokens.`
              : 'The selected Brain model has no saved context-window value in the local catalog.'}
          </p>
          <p>
            Provider and model policy owns the effective context. Custom
            endpoint caps can be reviewed under{' '}
            <Link className="settings-inline-action" to="/settings/providers">
              Provider connections
            </Link>
            .
          </p>
        </div>
      </details>
      {busy === 'load' && <Skeleton label="Loading saved model pickers" />}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {models.length > 0 && (
        <div className="settings-model-surface-list">
          {surfaces.map(({ id, label, description, icon: Icon }) => {
            const available = models.filter((model) =>
              supportsSurface(model, id),
            );
            const current = selected(id);
            const pinned = available.filter((model) =>
              model.pinned_surfaces.includes(id),
            );
            return (
              <section
                className="settings-model-surface stack"
                aria-labelledby={`settings-model-${id}`}
                key={id}
              >
                <header className="settings-model-surface-heading">
                  <Icon size={19} aria-hidden />
                  <div>
                    <h4 id={`settings-model-${id}`}>{label}</h4>
                    <p>{description}</p>
                  </div>
                  <div className="settings-model-surface-state">
                    <span className="status-chip">{pinned.length} pinned</span>
                    <span>
                      <span
                        className="settings-provider-readiness-dot is-unknown"
                        aria-hidden
                      />
                      Runtime not checked
                    </span>
                  </div>
                </header>
                {available.length ? (
                  <>
                    <Field
                      label={`${label} pinned choice`}
                      hint="This controls saved picker membership, not the active runtime default."
                    >
                      <Select
                        aria-label={`${label} pinned choice`}
                        value={selection[id]}
                        disabled={locked}
                        onChange={(event) => choose(id, event.target.value)}
                      >
                        <option value="">Choose a saved catalog model</option>
                        {available.map((model) => (
                          <option
                            value={model.selection_ref}
                            key={model.selection_ref}
                          >
                            {model.display_name} · {model.provider_display_name}
                            {model.pinned_surfaces.includes(id)
                              ? ' · pinned'
                              : ''}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    {current && (
                      <p className="settings-model-picker-state">
                        <strong>Saved picker:</strong>{' '}
                        {current.provider_display_name} ·{' '}
                        {current.installed === true
                          ? 'installed locally'
                          : current.installed === false
                            ? 'not installed locally'
                            : 'installation state unknown'}
                      </p>
                    )}
                    <div className="actions">
                      <Button
                        disabled={locked || !current}
                        onClick={() => void review(id)}
                      >
                        {current?.pinned_surfaces.includes(id)
                          ? 'Remove'
                          : 'Add'}{' '}
                        {label} picker choice
                      </Button>
                    </div>
                  </>
                ) : (
                  <p className="settings-help">
                    No saved {label.toLowerCase()} models are available. Use the
                    catalog below after a provider catalog has been saved.
                  </p>
                )}
              </section>
            );
          })}
        </div>
      )}
      {!busy && !models.length && !error && (
        <p className="settings-help">
          No saved Vision, Image, or Video catalog choices are available.
        </p>
      )}
      {truncated && (
        <p className="settings-help">
          Showing the first {MAX_CATALOG_ROWS} saved models. Search the catalog
          below to manage additional choices.
        </p>
      )}
      <div className="actions">
        {pending && (
          <Button
            disabled={!!busy || !session.active}
            onClick={() => void receipt()}
          >
            Check original picker receipt
          </Button>
        )}
        <Button disabled={locked} onClick={() => void load()}>
          Reload saved picker choices
        </Button>
      </div>
      <details className="settings-model-delegation">
        <summary>
          <GitBranch size={19} aria-hidden />
          <span>
            <h4>Agent runtime &amp; delegation</h4>
            <small>Concurrency, delegation, and context compaction</small>
          </span>
          <span className="status-chip warning">Unavailable</span>
        </summary>
        <p>
          Existing application-wide limits are not exposed by the typed Settings
          owner, so this client does not imitate editable values or save
          actions.
        </p>
      </details>
    </div>
  );
}
