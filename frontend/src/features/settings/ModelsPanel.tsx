import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Brain,
  Camera,
  Eye,
  GitBranch,
  Image,
  Network,
  RefreshCw,
  Video,
} from 'lucide-react';
import type { ClientController } from '../../api/controller';
import type {
  AgentRuntimeSettingsState,
  CachedModelPage,
  DefaultModelSnapshot,
  ModelsSettingsState,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, Field, Input, Select, Skeleton } from '../../ui/primitives';
import { type DefaultModelSession } from './DefaultModelSettings';
import { useProviderSettingsValue } from './provider-settings-sessions';
import ModelCatalog from './ModelCatalog';

type Surface = 'chat' | 'vision' | 'image' | 'video' | 'voice';
type Media = 'vision' | 'image' | 'video';
type PinPending = {
  commandId: string;
  operation: 'provider.model.pin' | 'provider.model.unpin';
};
const agentFields: {
  key: keyof Omit<AgentRuntimeSettingsState, 'schema_version'>;
  label: string;
  help: string;
}[] = [
  {
    key: 'max_iterations',
    label: 'Maximum work rounds',
    help: 'Maximum model-and-tool rounds in one run.',
  },
  {
    key: 'max_spawn_depth',
    label: 'Maximum nested agent levels',
    help: 'One level allows children but not grandchildren.',
  },
  {
    key: 'max_concurrent_children',
    label: 'Active children per parent',
    help: 'Extra children wait in the queue.',
  },
  {
    key: 'max_active_children_global',
    label: 'Active children across the app',
    help: 'Application-wide child concurrency cap.',
  },
  {
    key: 'child_timeout_seconds',
    label: 'Child active-time limit (seconds; 0 disables)',
    help: 'Queue time is not counted.',
  },
];
const contextPresets = {
  local: [16384, 32768, 65536, 131072, 262144],
  provider: [16384, 32768, 65536, 131072, 262144, 524288, 1048576],
};
function selectionParts(ref: string) {
  const match = /^model:([^:]+):(.+)$/.exec(ref);
  if (!match) throw { code: 'invalid_model_selection' };
  return { provider_id: match[1], model_id: match[2] };
}
function fieldsFrom(settings: AgentRuntimeSettingsState) {
  return Object.fromEntries(
    agentFields.map(({ key }) => [key, String(settings[key])]),
  ) as Record<string, string>;
}

export default function ModelsPanel({
  controller,
  session,
  initialProvider = '',
}: {
  controller: ClientController;
  session: DefaultModelSession;
  initialProvider?: string;
}) {
  const [state, setState] = useState<ModelsSettingsState | null>(null);
  const [snapshot, setSnapshot] =
    useProviderSettingsValue<DefaultModelSnapshot | null>(
      session,
      'snapshot',
      null,
    );
  const [pending, setPending] = useProviderSettingsValue<{
    commandId: string;
  } | null>(session, 'pending', null);
  const [pinPending, setPinPending] =
    useProviderSettingsValue<PinPending | null>(
      session,
      'surfacePending',
      null,
    );
  const [agents, setAgents] = useState<AgentRuntimeSettingsState | null>(null);
  const [agentDraft, setAgentDraft] = useState<Record<string, string>>({});
  const [contextDraft, setContextDraft] = useState('');
  const [customContext, setCustomContext] = useState('');
  const [cameras, setCameras] = useState<number[] | null>(null);
  const [catalogOpen, setCatalogOpen] = useState(!!initialProvider);
  const [catalogRefresh, setCatalogRefresh] = useState(0);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const contextSelectedCap = state?.context.selected_cap;
  const contextPolicyKind = state?.context.policy_kind;

  useEffect(() => {
    const abort = new AbortController();
    void Promise.all([
      controller.modelsSettings(abort.signal),
      controller.defaultModel(abort.signal),
      controller.agentRuntimeSettings(abort.signal),
    ]).then(
      ([models, defaultModel, agentSettings]) => {
        if (abort.signal.aborted) return;
        setState(models);
        if (!session.get('pending', null)) setSnapshot(defaultModel);
        setAgents(agentSettings);
        setAgentDraft(fieldsFrom(agentSettings));
      },
      (cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [controller, session, setSnapshot]);

  useEffect(() => {
    if (!contextPolicyKind) return;
    const current = contextSelectedCap;
    const presets = contextPresets[contextPolicyKind];
    setContextDraft(
      current == null
        ? 'auto'
        : presets.includes(current)
          ? String(current)
          : 'custom',
    );
    setCustomContext(current == null ? '' : String(current));
  }, [contextSelectedCap, contextPolicyKind]);

  async function reload() {
    const updated = await controller.modelsSettings();
    setState(updated);
  }
  async function brainDefault(ref: string) {
    if (!session.active || pending || pinPending || busy)
      throw { code: 'operation_uncertain' };
    setBusy('brain');
    setError('');
    try {
      const current = snapshot ?? (await controller.defaultModel());
      const { provider_id, model_id } = selectionParts(ref);
      const review = await controller.reviewDefaultModel({
        settings_revision: current.revision,
        provider_id,
        model_id,
      });
      if (
        review.settings_revision !== current.revision ||
        review.provider_id !== provider_id ||
        review.model_id !== model_id ||
        !review.nonce
      )
        throw { code: 'revision_conflict' };
      const original = { commandId: crypto.randomUUID() };
      setPending(original);
      try {
        const saved = await session.perform([original, review], () =>
          controller.executeDefaultModel(
            structuredClone(review),
            original.commandId,
          ),
        );
        setSnapshot(saved);
        setPending(null);
        session.resolved();
        await reload();
        setNotice('Brain default saved for future work.');
      } catch (cause) {
        try {
          const receipt = await controller.defaultModelReceipt(
            original.commandId,
          );
          if (receipt.status !== 'uncertain') {
            setSnapshot(receipt.selection);
            setPending(null);
            session.resolved();
            await reload();
            if (receipt.status === 'completed') {
              setNotice('Brain default saved for future work.');
              return;
            }
          }
        } catch {
          /* Keep the original receipt available when the outcome cannot be established. */
        }
        throw cause;
      }
    } catch (cause) {
      setError(clientError(cause).message);
      throw cause;
    } finally {
      setBusy('');
    }
  }
  async function checkBrainReceipt() {
    if (!pending) return;
    setBusy('receipt');
    try {
      const receipt = await controller.defaultModelReceipt(pending.commandId);
      if (receipt.status === 'uncertain')
        setNotice('The original Brain save is still unconfirmed.');
      else {
        setPending(null);
        session.resolved();
        setSnapshot(receipt.selection);
        await reload();
        setNotice(
          receipt.status === 'completed'
            ? 'Brain default saved for future work.'
            : 'The Brain save was rejected.',
        );
      }
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }
  async function pin(
    surface: Surface,
    model: CachedModelPage['items'][number],
  ) {
    if (pending || pinPending || busy) throw { code: 'operation_uncertain' };
    const operation = model.pinned_surfaces.includes(surface)
      ? 'provider.model.unpin'
      : 'provider.model.pin';
    setBusy('pin');
    setError('');
    try {
      const configuration = await controller.providerConfiguration('');
      const fields = {
        provider_id: model.provider_id,
        model_id: model.model_id,
        surface,
      };
      const review = await controller.reviewProviderConfiguration({
        operation,
        configuration_revision: configuration.revision,
        fields,
      });
      if (
        review.operation !== operation ||
        review.configuration_revision !== configuration.revision ||
        !review.nonce
      )
        throw { code: 'revision_conflict' };
      const original: PinPending = {
        commandId: crypto.randomUUID(),
        operation,
      };
      setPinPending(original);
      try {
        await session.perform([original, review], () =>
          controller.executeProviderConfiguration(
            operation,
            configuration.revision,
            fields,
            original.commandId,
            review.nonce,
          ),
        );
        setPinPending(null);
        session.resolved();
      } catch (cause) {
        try {
          const receipt = await controller.providerConfigurationReceipt(
            original.commandId,
          );
          if (receipt.status !== 'uncertain') {
            setPinPending(null);
            session.resolved();
            if (receipt.status === 'completed') return;
          }
        } catch {
          /* Resolve only from the original receipt. */
        }
        throw cause;
      }
    } finally {
      setBusy('');
    }
  }
  async function checkPinReceipt() {
    if (!pinPending) return;
    setBusy('receipt');
    try {
      const result = await controller.providerConfigurationReceipt(
        pinPending.commandId,
      );
      if (result.status === 'uncertain')
        setNotice('The original pin change is still unconfirmed.');
      else {
        setPinPending(null);
        session.resolved();
        await reload();
        setCatalogRefresh((value) => value + 1);
        setNotice(
          result.status === 'completed'
            ? 'Picker updated.'
            : 'Picker change rejected.',
        );
      }
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }
  async function media(
    surface: Media,
    action: 'default' | 'enabled' | 'camera',
    value: string | boolean | number,
  ) {
    const previous = state;
    setBusy(surface);
    setError('');
    if (previous) {
      setState({
        ...previous,
        [surface]: {
          ...previous[surface],
          ...(action === 'enabled'
            ? { enabled: value as boolean }
            : action === 'default'
              ? { current_ref: value as string }
              : {}),
        },
        camera_index:
          action === 'camera' ? (value as number) : previous.camera_index,
      });
    }
    try {
      const body = {
        surface,
        action,
        ...(action === 'default'
          ? { selection_ref: value as string }
          : action === 'enabled'
            ? { enabled: value as boolean }
            : { camera_index: value as number }),
      };
      setState(await controller.updateModelSurface(body));
      setNotice(
        `${surface[0].toUpperCase() + surface.slice(1)} settings saved.`,
      );
    } catch (cause) {
      setError(clientError(cause).message);
      if (previous) setState(previous);
      await reload().catch(() => {});
    } finally {
      setBusy('');
    }
  }
  async function context(cap: number | null) {
    if (!state) return;
    setBusy('context');
    setError('');
    try {
      setState(
        await controller.updateModelContext({
          policy_kind: state.context.policy_kind,
          cap,
        }),
      );
      setNotice('Context setting saved.');
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }
  async function saveAgents(reset = false) {
    if (!agents) return;
    setBusy('agents');
    setError('');
    try {
      let saved: AgentRuntimeSettingsState;
      if (reset) saved = await controller.resetAgentRuntimeSettings();
      else {
        const values = Object.fromEntries(
          agentFields.map(({ key }) => {
            const raw = agentDraft[key]?.trim() ?? '';
            if (
              !/^\d+$/.test(raw) ||
              (!Number(raw) && key !== 'child_timeout_seconds')
            )
              throw new Error(
                `${key.replaceAll('_', ' ')} must be a whole number.`,
              );
            return [key, Number(raw)];
          }),
        );
        saved = await controller.saveAgentRuntimeSettings({
          schema_version: 1,
          ...values,
        } as AgentRuntimeSettingsState);
      }
      setAgents(saved);
      setAgentDraft(fieldsFrom(saved));
      setNotice(
        reset
          ? 'Recommended agent limits restored.'
          : 'Agent limits saved for new runs.',
      );
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }
  async function refreshCatalog() {
    setBusy('refresh');
    setError('');
    try {
      await controller.refreshModelsCatalog();
      setNotice('Refreshing model catalog…');
      let refreshStatus = await controller.liveProviderRefresh();
      for (let attempt = 0; attempt < 40 && refreshStatus.running; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        refreshStatus = await controller.liveProviderRefresh();
      }
      await reload();
      setCatalogRefresh((value) => value + 1);
      setNotice(
        refreshStatus.running
          ? 'Model catalog is still refreshing.'
          : refreshStatus.ok === false
            ? 'Model catalog refresh did not complete. Check your provider connection.'
            : 'Model catalog refreshed.',
      );
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }
  async function refreshCameras() {
    setBusy('cameras');
    try {
      const result = await controller.refreshModelCameras();
      setCameras(result.cameras);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }

  if (!state)
    return (
      <section className="stack" aria-label="Models settings">
        {error ? (
          <p role="alert">{error}</p>
        ) : (
          <Skeleton label="Loading Models settings" />
        )}
      </section>
    );
  const defaults: Partial<Record<Surface, string>> = {
    chat: state.brain.current_ref,
    vision: state.vision.current_ref,
    image: state.image.current_ref,
    video: state.video.current_ref,
  };
  const currentBrain = state.brain.options.find(
    (item) => item.selection_ref === state.brain.current_ref,
  );
  const contextKind = state.context.policy_kind;
  return (
    <div className="stack settings-models-parity" aria-label="Models settings">
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <section
        className="settings-model-defaults-group stack"
        aria-label="Defaults"
      >
        <header className="settings-owner-heading">
          <div>
            <h3>Defaults</h3>
            <p>
              Pickers show pinned catalog choices plus the current default. Pin
              models in the catalog below.
            </p>
          </div>
          <div className="settings-model-heading-actions">
            <span className="status-chip">Catalog-backed</span>
            <Link
              className="button ghost"
              to="/app-v2/settings/providers"
              aria-label="Provider connections"
              title="Provider connections"
            >
              <Network size={16} aria-hidden />
            </Link>
          </div>
        </header>
        <div className="settings-model-role-heading">
          <Brain size={18} aria-hidden />
          <div>
            <h4>Brain</h4>
            <p>Conversation, tool use, memory, and workflows.</p>
          </div>
          <span className="status-chip">
            {!state.brain.current_ref
              ? 'Not set'
              : !currentBrain?.available
                ? 'Unavailable'
                : state.brain.current_ref.startsWith('model:ollama:')
                  ? 'Local'
                  : 'Provider'}
          </span>
        </div>
        <div className="settings-model-selector">
          <Field label="Default model">
            <Select
              value={state.brain.current_ref}
              disabled={!!busy || !!pending || !!pinPending}
              onChange={(event) =>
                void brainDefault(event.target.value).catch(() => {})
              }
            >
              <option value="">Choose a pinned Brain model</option>
              {state.brain.options.map((item) => (
                <option
                  value={item.selection_ref}
                  key={item.selection_ref}
                  disabled={!item.available}
                >
                  {item.available ? item.label : `Unavailable: ${item.label}`}
                </option>
              ))}
            </Select>
          </Field>
          <Button
            variant="ghost"
            aria-label="Refresh model settings"
            onClick={() => void reload()}
          >
            <RefreshCw size={16} aria-hidden />
          </Button>
        </div>
        {state.brain.warning && (
          <p className="settings-model-warning">
            {state.brain.warning.replace('Chat', 'Brain')}
          </p>
        )}
        {state.context.effective_cap && (
          <p className="settings-help">
            Native max{' '}
            {state.context.native_max
              ? Math.round(state.context.native_max / 1000) + 'K'
              : 'unknown'}{' '}
            · effective {Math.round(state.context.effective_cap / 1000)}K{' '}
            {state.context.selected_cap == null ? 'Auto' : 'cap'}
          </p>
        )}
        {pending && (
          <Button onClick={() => void checkBrainReceipt()}>
            Check original Brain save receipt
          </Button>
        )}
        <details className="settings-model-context">
          <summary>Advanced context</summary>
          <p>
            {contextKind === 'local'
              ? 'Local model context controls the requested Ollama allocation.'
              : 'Provider context caps trim requests; configure the server context separately.'}
          </p>
          <Field
            label={
              contextKind === 'local'
                ? 'Local model context'
                : 'Provider context cap'
            }
          >
            <Select
              value={contextDraft}
              disabled={!!busy}
              onChange={(event) => {
                const value = event.target.value;
                setContextDraft(value);
                if (value === 'auto') void context(null);
                else if (value !== 'custom') void context(Number(value));
              }}
            >
              <option value="auto">
                {contextKind === 'local'
                  ? 'Auto - use detected server context'
                  : 'Auto - use provider/model context'}
              </option>
              {contextPresets[contextKind].map((cap) => (
                <option value={cap} key={cap}>
                  {Math.round(cap / 1024)}K
                </option>
              ))}
              <option value="custom">Custom…</option>
            </Select>
          </Field>
          {contextDraft === 'custom' && (
            <div className="settings-model-custom-context">
              <Field label="Custom context tokens">
                <Input
                  type="number"
                  min={16384}
                  max={10000000}
                  value={customContext}
                  onChange={(event) => setCustomContext(event.target.value)}
                />
              </Field>
              <Button
                disabled={
                  !/^\d+$/.test(customContext) || Number(customContext) < 16384
                }
                onClick={() => void context(Number(customContext))}
              >
                Save cap
              </Button>
            </div>
          )}
          {state.context.warning && (
            <p className="settings-model-warning">{state.context.warning}</p>
          )}
        </details>
        {(
          [
            ['vision', Eye, 'Camera and screen capture analysis'],
            ['image', Image, 'Image generation and editing'],
            ['video', Video, 'Video generation and image animation'],
          ] as const
        ).map(([surface, Icon, description]) => {
          const picker = state[surface];
          return (
            <section
              className="settings-model-surface"
              key={surface}
              aria-label={surface}
            >
              <div className="settings-model-role-heading">
                <Icon size={18} aria-hidden />
                <div>
                  <h4>{surface[0].toUpperCase() + surface.slice(1)}</h4>
                  <p>{description}</p>
                </div>
                <label className="settings-model-enabled">
                  <input
                    type="checkbox"
                    checked={picker.enabled === true}
                    disabled={!!busy}
                    onChange={(event) =>
                      void media(surface, 'enabled', event.target.checked)
                    }
                  />{' '}
                  Enabled
                </label>
              </div>
              <Field
                label={`${surface[0].toUpperCase() + surface.slice(1)} model`}
              >
                <Select
                  value={picker.current_ref}
                  disabled={!!busy}
                  onChange={(event) =>
                    void media(surface, 'default', event.target.value)
                  }
                >
                  <option value="">Choose a pinned model</option>
                  {picker.options.map((item) => (
                    <option
                      value={item.selection_ref}
                      key={item.selection_ref}
                      disabled={!item.available}
                    >
                      {item.available
                        ? item.label
                        : `Unavailable: ${item.label}`}
                    </option>
                  ))}
                </Select>
              </Field>
              {picker.warning && (
                <p className="settings-model-warning">{picker.warning}</p>
              )}
              {surface === 'vision' && (
                <div className="settings-model-camera">
                  <Camera size={17} aria-hidden />
                  <Field label="Camera">
                    <Select
                      value={state.camera_index}
                      disabled={!!busy}
                      onChange={(event) =>
                        void media(
                          'vision',
                          'camera',
                          Number(event.target.value),
                        )
                      }
                    >
                      {[
                        ...new Set([state.camera_index, ...(cameras ?? [])]),
                      ].map((camera) => (
                        <option key={camera} value={camera}>
                          Camera {camera}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <span>
                    {cameras == null
                      ? 'Camera list not loaded'
                      : cameras.length
                        ? `${cameras.length} camera(s) detected`
                        : 'No cameras detected'}
                  </span>
                  <Button
                    variant="ghost"
                    aria-label="Refresh camera list"
                    onClick={() => void refreshCameras()}
                  >
                    <RefreshCw size={16} aria-hidden />
                  </Button>
                </div>
              )}
            </section>
          );
        })}
      </section>
      <details className="settings-model-delegation" open>
        <summary>
          <GitBranch size={18} aria-hidden />
          <span>Agent runtime &amp; delegation</span>
        </summary>
        <p>
          Optional limits for long-running work and delegated child agents.
          Changes apply to new runs.
        </p>
        {agents ? (
          <>
            <div className="settings-model-agent-chips">
              {agentFields.map(({ key }) => (
                <span className="status-chip" key={key}>
                  {agents[key]}{' '}
                  {key === 'child_timeout_seconds'
                    ? 'child seconds'
                    : key.replaceAll('_', ' ')}
                </span>
              ))}
            </div>
            <div className="settings-model-agent-grid">
              {agentFields.map(({ key, label, help }) => (
                <Field label={label} hint={help} key={key}>
                  <Input
                    type="number"
                    min={key === 'child_timeout_seconds' ? 0 : 1}
                    max={1000000}
                    step={1}
                    value={agentDraft[key] ?? ''}
                    onChange={(event) =>
                      setAgentDraft((value) => ({
                        ...value,
                        [key]: event.target.value,
                      }))
                    }
                  />
                </Field>
              ))}
            </div>
            <div className="actions">
              <Button
                variant="primary"
                disabled={!!busy}
                onClick={() => void saveAgents()}
              >
                Save
              </Button>
              <Button
                variant="ghost"
                disabled={!!busy}
                onClick={() => void saveAgents(true)}
              >
                Restore recommended defaults
              </Button>
            </div>
          </>
        ) : (
          <Skeleton label="Loading agent limits" />
        )}
      </details>
      <section className="settings-catalog-owner stack" aria-label="Catalog">
        <h3>Catalog</h3>
        <p>Browse or pin models when you need more choices.</p>
        <div className="settings-model-catalog-status">
          <span
            className={`status-chip ${state.freshness === 'fresh' ? 'success' : 'warning'}`}
          >
            {state.freshness === 'fresh'
              ? 'Cached models ready'
              : state.freshness === 'stale'
                ? 'Cached models may be old'
                : 'No cached models'}
          </span>
          <span>
            {state.generated_at
              ? 'Saved catalog available'
              : 'No saved catalog yet'}
          </span>
          <Button
            variant="ghost"
            disabled={busy === 'refresh'}
            onClick={() => void refreshCatalog()}
          >
            <RefreshCw size={16} aria-hidden /> Refresh catalog
          </Button>
        </div>
        <button
          className="settings-disclosure"
          type="button"
          aria-expanded={catalogOpen}
          aria-controls="model-catalog-content"
          onClick={() => setCatalogOpen((value) => !value)}
        >
          Model Catalog <span aria-hidden>{catalogOpen ? '−' : '+'}</span>
        </button>
        {catalogOpen && (
          <div id="model-catalog-content">
            <ModelCatalog
              controller={controller}
              initialProvider={initialProvider}
              refreshSignal={catalogRefresh}
              defaults={defaults}
              onDefault={async (surface, model) => {
                if (!model.pinned_surfaces.includes(surface)) {
                  await pin(surface, model);
                  await reload();
                }
                if (surface === 'chat') await brainDefault(model.selection_ref);
                else if (surface !== 'voice')
                  await media(surface, 'default', model.selection_ref);
              }}
              onPin={pin}
              onChanged={reload}
            />
          </div>
        )}
        {pinPending && (
          <Button onClick={() => void checkPinReceipt()}>
            Check original picker receipt
          </Button>
        )}
      </section>
    </div>
  );
}
