import { useEffect, useId, useState, type ComponentType } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Brain,
  Clapperboard,
  Download,
  Eye,
  Image,
  Network,
  RefreshCw,
} from 'lucide-react';
import type { ClientController } from '../../api/controller';
import type {
  AgentRuntimeSettingsState,
  CachedModelPage,
  DefaultModelSnapshot,
  ModelPickerOption,
  ModelsSettingsState,
} from '../../api/types';
import { clientError } from '../../api/errors';
import {
  Button,
  Disclosure,
  Input,
  Segmented,
  Select,
  Skeleton,
  Toggle,
} from '../../ui/primitives';
import { useNotify } from '../../ui/overlays';
import { relativeTime } from '../../ui/format';
import {
  SettingsAdvanced,
  SettingsGroup,
  SettingsItem,
  SettingsPageMenu,
  SettingsStatus,
  StatusLine,
} from './anatomy';
import { type DefaultModelSession } from './DefaultModelSettings';
import { useProviderSettingsValue } from './provider-settings-sessions';
import ModelCatalog from './ModelCatalog';
import DefaultModelPicker from './DefaultModelPicker';
import { modelRefName, splitModelLabel } from '../shell/model-choices';

type Surface = 'chat' | 'vision' | 'image' | 'video' | 'voice';
type Media = 'vision' | 'image' | 'video';
type Icon = ComponentType<{ size?: number; 'aria-hidden'?: boolean }>;
type PinPending = {
  commandId: string;
  operation: 'provider.model.pin' | 'provider.model.unpin';
};
/** Limits for long work, in plain words; the saved fields are unchanged. */
const agentFields: {
  key: keyof Omit<AgentRuntimeSettingsState, 'schema_version'>;
  label: string;
  help: string;
}[] = [
  {
    key: 'max_iterations',
    label: 'Steps per run',
    help: 'Model-and-tool rounds one run may take.',
  },
  {
    key: 'max_spawn_depth',
    label: 'Helper levels',
    help: '1 lets helpers work but not start helpers of their own.',
  },
  {
    key: 'max_concurrent_children',
    label: 'Helpers at a time, per agent',
    help: 'More wait in line.',
  },
  {
    key: 'max_active_children_global',
    label: 'Helpers at a time, in total',
    help: 'Across the whole app.',
  },
  {
    key: 'child_timeout_seconds',
    label: 'Helper time limit (seconds)',
    help: '0 means no limit. Time waiting in line doesn’t count.',
  },
  {
    key: 'goal_max_turns',
    label: 'Goal turn limit',
    help: '0 means no limit. New goals start with this; each goal can change it.',
  },
];
/** Fields where 0 switches the limit off. */
const ZERO_OFF = new Set(['child_timeout_seconds', 'goal_max_turns']);
/** The smallest reading limit the server accepts. */
const MIN_CONTEXT = 16384;
const media: {
  surface: Media;
  label: string;
  help: string;
  icon: Icon;
  tone: '2' | '3' | '4';
}[] = [
  {
    surface: 'vision',
    label: 'Vision',
    help: 'Reads images, screenshots and your camera.',
    icon: Eye,
    tone: '2',
  },
  {
    surface: 'image',
    label: 'Image',
    help: 'Makes and edits pictures.',
    icon: Image,
    tone: '3',
  },
  {
    surface: 'video',
    label: 'Video',
    help: 'Makes short clips and animates pictures.',
    icon: Clapperboard,
    tone: '4',
  },
];
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
/** A model's name without its provider ("GPT-5.5 - ChatGPT" → "GPT-5.5"). */
function optionName(options: readonly ModelPickerOption[], ref: string) {
  const option = options.find((item) => item.selection_ref === ref);
  return option ? splitModelLabel(option.label).name : modelRefName(ref);
}
/** About 330 tokens to a printed page, to two significant figures. */
function pages(tokens: number) {
  const value = tokens / 330;
  const scale = 10 ** Math.max(0, Math.floor(Math.log10(value)) - 1);
  return (Math.round(value / scale) * scale).toLocaleString();
}

export default function ModelsPanel({
  controller,
  session,
  initialProvider = '',
  openExternal,
}: {
  controller: ClientController;
  session: DefaultModelSession;
  initialProvider?: string;
  /** Opens a web page outside Row-Bot (the Ollama download). */
  openExternal?: (url: string) => void;
}) {
  const notify = useNotify();
  const navigate = useNavigate();
  const limitId = useId();
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
  const [limiting, setLimiting] = useState(false);
  const [customContext, setCustomContext] = useState('');
  const [cameras, setCameras] = useState<number[] | null>(null);
  // "Get models for this computer" only when Ollama isn't running (B117).
  const [ollamaRunning, setOllamaRunning] = useState<boolean | null>(null);
  const [catalogRefresh, setCatalogRefresh] = useState(0);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  // An unconfirmed save stays by its row until its receipt settles.
  const [brainNote, setBrainNote] = useState('');
  const [catalogNote, setCatalogNote] = useState('');
  const contextSelectedCap = state?.context.selected_cap;

  useEffect(() => {
    const abort = new AbortController();
    void Promise.all([
      controller.modelsSettings(abort.signal),
      controller.defaultModel(abort.signal),
      controller.agentRuntimeSettings(abort.signal),
    ]).then(
      ([settings, defaultModel, agentSettings]) => {
        if (abort.signal.aborted) return;
        setState(settings);
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
    const abort = new AbortController();
    controller.localRuntime(abort.signal).then(
      (runtime) => {
        if (!abort.signal.aborted && runtime)
          setOllamaRunning(runtime.state === 'running');
      },
      () => undefined,
    );
    return () => abort.abort();
  }, [controller]);

  useEffect(() => {
    setLimiting(contextSelectedCap != null);
    setCustomContext(
      contextSelectedCap == null ? '' : String(contextSelectedCap),
    );
  }, [contextSelectedCap]);

  async function reload() {
    const updated = await controller.modelsSettings();
    setState(updated);
  }
  function brainSaved(ref: string, previous: string) {
    const name = state ? optionName(state.brain.options, ref) : ref;
    notify(
      `Brain is now ${name}`,
      undefined,
      previous && previous !== ref
        ? {
            label: 'Undo',
            onAction: () => void brainDefault(previous, true).catch(() => {}),
          }
        : undefined,
    );
  }
  async function brainDefault(ref: string, undoing = false) {
    if (!session.active || pending || pinPending || busy)
      throw { code: 'operation_uncertain' };
    setBusy('brain');
    setError('');
    setBrainNote('');
    const previous = state?.brain.current_ref ?? '';
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
        // The composer's picker offers the new default at once.
        void controller.refreshChoices().catch(() => undefined);
        if (undoing) notify('Brain changed back');
        else brainSaved(ref, previous);
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
              brainSaved(ref, previous);
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
        setBrainNote('The original Brain save is still unconfirmed.');
      else {
        setPending(null);
        session.resolved();
        setSnapshot(receipt.selection);
        await reload();
        setBrainNote('');
        if (receipt.status === 'completed') notify('Brain saved');
        else setError('The Brain save was rejected.');
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
        setCatalogNote('The original pin change is still unconfirmed.');
      else {
        setPinPending(null);
        session.resolved();
        await reload();
        setCatalogRefresh((value) => value + 1);
        setCatalogNote('');
        if (result.status === 'completed') notify('Picker updated');
        else setError('Picker change rejected.');
      }
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }
  async function saveMedia(
    surface: Media,
    action: 'default' | 'enabled' | 'camera',
    value: string | boolean | number,
  ) {
    const previous = state;
    const label = surface[0].toUpperCase() + surface.slice(1);
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
      const saved = await controller.updateModelSurface(body);
      setState(saved);
      notify(
        action === 'enabled'
          ? `${label} turned ${value ? 'on' : 'off'}`
          : action === 'camera'
            ? 'Camera saved'
            : value
              ? `${label} is now ${optionName(saved[surface].options, value as string)}`
              : `${label} now follows the Brain`,
      );
    } catch (cause) {
      setError(clientError(cause).message);
      if (previous) setState(previous);
      await reload().catch(() => {});
    } finally {
      setBusy('');
    }
  }
  async function context(cap: number | null, undoing = false) {
    if (!state) return;
    const before = state.context.selected_cap ?? null;
    const kind = state.context.policy_kind;
    setBusy('context');
    setError('');
    try {
      setState(
        await controller.updateModelContext({
          policy_kind: kind,
          cap,
        }),
      );
      if (undoing) notify('Reading limit changed back');
      else
        notify(
          cap == null
            ? 'Reading limit: automatic'
            : `Reading limit: ${cap.toLocaleString()} tokens`,
          undefined,
          { label: 'Undo', onAction: () => void context(before, true) },
        );
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }
  async function saveAgents(
    reset = false,
    draft: Record<string, string> = agentDraft,
    undoing = false,
  ) {
    if (!agents) return;
    const before = agents;
    setBusy('agents');
    setError('');
    try {
      let saved: AgentRuntimeSettingsState;
      if (reset) saved = await controller.resetAgentRuntimeSettings();
      else {
        const values = Object.fromEntries(
          agentFields.map(({ key, label }) => {
            const raw = draft[key]?.trim() ?? '';
            if (!/^\d+$/.test(raw) || (!Number(raw) && !ZERO_OFF.has(key)))
              throw new Error(`${label} must be a whole number.`);
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
      if (undoing) notify('Limits changed back');
      else
        notify(
          reset
            ? 'Recommended limits restored'
            : 'Saved. New runs use these limits',
          undefined,
          {
            label: 'Undo',
            onAction: () => void saveAgents(false, fieldsFrom(before), true),
          },
        );
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }
  // Text and numbers save when the field is left or Enter is pressed.
  function commitCustomContext() {
    const cap = Number(customContext);
    if (!/^\d+$/.test(customContext) || cap < MIN_CONTEXT) return;
    if (cap === state?.context.selected_cap) return;
    void context(cap);
  }
  function commitAgents() {
    if (!agents || busy) return;
    const saved = fieldsFrom(agents);
    if (agentFields.every(({ key }) => (agentDraft[key] ?? '') === saved[key]))
      return;
    void saveAgents();
  }
  async function refreshCatalog() {
    setBusy('refresh');
    setError('');
    try {
      await controller.refreshModelsCatalog();
      let refreshStatus = await controller.liveProviderRefresh();
      for (let attempt = 0; attempt < 40 && refreshStatus.running; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        refreshStatus = await controller.liveProviderRefresh();
      }
      await reload();
      setCatalogRefresh((value) => value + 1);
      notify(
        refreshStatus.running
          ? 'Model catalog is still refreshing.'
          : refreshStatus.ok === false
            ? 'Model catalog refresh did not complete. Check your provider connection.'
            : 'Model catalog refreshed.',
        refreshStatus.ok === false ? 'warning' : undefined,
      );
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }
  // The camera list loads when its select is first opened (B229).
  async function loadCameras() {
    if (cameras !== null || busy === 'cameras') return;
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
  const brainName = currentBrain
    ? splitModelLabel(currentBrain.label).name
    : modelRefName(state.brain.current_ref);
  // One status line (B229): who is connected and how much there is to choose.
  const choices = [state.brain, state.vision, state.image, state.video]
    .flatMap((picker) => picker.options)
    .filter((option) => option.available);
  const providers = new Set(choices.map((option) => option.provider_id)).size;
  const models = new Set(choices.map((option) => option.selection_ref)).size;
  const brainReady = !!state.brain.current_ref && !!currentBrain?.available;
  const contextKind = state.context.policy_kind;
  const locked = !!busy || !!pending || !!pinPending;
  const catalogStatus =
    busy === 'refresh'
      ? 'Refreshing…'
      : state.generated_at
        ? `Updated ${relativeTime(new Date(state.generated_at * 1000))}${state.freshness === 'stale' ? ' · may be old' : ''}`
        : 'No saved catalog yet';
  const menu = [
    {
      label: 'Provider connections',
      icon: <Network size={16} aria-hidden />,
      onSelect: () => navigate('/settings/providers'),
    },
    {
      label: 'Re-read model settings',
      icon: <RefreshCw size={16} aria-hidden />,
      onSelect: () => void reload().catch(() => undefined),
    },
    ...(ollamaRunning === false && openExternal
      ? [
          {
            label: 'Get models for this computer…',
            icon: <Download size={16} aria-hidden />,
            onSelect: () => openExternal('https://ollama.com/download'),
          },
        ]
      : []),
  ];
  return (
    <div
      className="stack settings-snapshot-page settings-models-page"
      aria-label="Models settings"
    >
      <SettingsStatus
        tone={brainReady ? 'success' : 'warning'}
        more={[`${models} model${models === 1 ? '' : 's'} to choose from`]}
      >
        {providers} provider{providers === 1 ? '' : 's'} connected
      </SettingsStatus>
      <SettingsPageMenu label="More model actions" actions={menu} />
      {error && (
        <p role="alert" className="settings-page-alert">
          {error}
        </p>
      )}
      <SettingsGroup title="Jobs">
        <SettingsItem
          label="Brain"
          help="Chats, agents and workflows."
          icon={<Brain size={16} aria-hidden />}
          tone="accent"
          anchor="default-model"
          bind={false}
          status={
            state.brain.warning || brainNote ? (
              <StatusLine tone="warning">
                {brainNote || state.brain.warning?.replace('Chat', 'Brain')}
              </StatusLine>
            ) : undefined
          }
          control={
            <DefaultModelPicker
              ariaLabel="Brain model"
              dialogLabel="Choose the Brain model"
              current={state.brain.current_ref}
              options={state.brain.options}
              disabled={locked}
              onChoose={(ref) => void brainDefault(ref).catch(() => {})}
              onRefresh={() => void refreshCatalog()}
            />
          }
        >
          {pending && (
            <div className="settings-row-actions">
              <Button onClick={() => void checkBrainReceipt()}>
                Check the save
              </Button>
            </div>
          )}
        </SettingsItem>
        {media.map(({ surface, label, help, icon: Glyph, tone }) => {
          const picker = state[surface];
          const on = picker.enabled === true;
          return [
            <SettingsItem
              key={surface}
              label={label}
              help={help}
              icon={<Glyph size={16} aria-hidden />}
              tone={tone}
              off={!on}
              anchor={`${surface}-model`}
              bind={false}
              className="settings-job-row"
              status={
                picker.warning ? (
                  <StatusLine tone="warning">{picker.warning}</StatusLine>
                ) : undefined
              }
              control={
                <>
                  <Toggle
                    label={`Enable ${surface}`}
                    checked={on}
                    disabled={!!busy}
                    onChange={(event) =>
                      void saveMedia(surface, 'enabled', event.target.checked)
                    }
                  />
                  <DefaultModelPicker
                    ariaLabel={`${label} model`}
                    current={picker.current_ref}
                    options={picker.options}
                    // Most chat models see images too (decision 11).
                    follow={
                      surface === 'vision'
                        ? { label: 'Same as Brain', detail: brainName }
                        : undefined
                    }
                    dialogLabel={`Choose the ${surface} model`}
                    disabled={!!busy || !on}
                    onChoose={(ref) => void saveMedia(surface, 'default', ref)}
                    onRefresh={() => void refreshCatalog()}
                  />
                </>
              }
            />,
            surface === 'vision' && on && (
              <SettingsItem
                key="camera"
                label="Camera"
                help="Used when you ask Row-Bot to look."
                sub
                status={
                  cameras?.length === 0 ? (
                    <StatusLine tone="warning">No cameras found</StatusLine>
                  ) : busy === 'cameras' ? (
                    <StatusLine>Looking for cameras…</StatusLine>
                  ) : undefined
                }
                control={
                  <Select
                    className="settings-camera-select"
                    value={state.camera_index}
                    disabled={!!busy && busy !== 'cameras'}
                    onFocus={() => void loadCameras()}
                    onPointerDown={() => void loadCameras()}
                    onChange={(event) =>
                      void saveMedia(
                        'vision',
                        'camera',
                        Number(event.target.value),
                      )
                    }
                  >
                    {[...new Set([state.camera_index, ...(cameras ?? [])])].map(
                      (camera) => (
                        <option key={camera} value={camera}>
                          Camera {camera + 1}
                        </option>
                      ),
                    )}
                  </Select>
                }
              />
            ),
          ];
        })}
        <Disclosure
          className="settings-group-disclosure settings-divided"
          summary="Advanced context"
          meta="How much the Brain reads at once"
        >
          <SettingsItem
            label={
              contextKind === 'local'
                ? 'Models on this computer'
                : 'Reading limit'
            }
            sub
            bind={false}
            help={
              contextKind === 'local'
                ? `Automatic gives local models what your computer can hold${
                    state.context.effective_cap
                      ? `: about ${state.context.effective_cap.toLocaleString()} tokens now`
                      : ''
                  }.`
                : state.context.native_max
                  ? `${brainName} can read about ${state.context.native_max.toLocaleString()} tokens at once, roughly ${pages(state.context.native_max)} pages. ${
                      state.context.selected_cap == null
                        ? 'Row-Bot uses all of it.'
                        : `Row-Bot reads up to ${state.context.selected_cap.toLocaleString()}.`
                    }`
                  : `Row-Bot reads as much as ${brainName} allows.`
            }
            status={
              state.context.warning ? (
                <StatusLine tone="warning">{state.context.warning}</StatusLine>
              ) : undefined
            }
            control={
              <Segmented
                label="Reading limit"
                value={limiting ? 'limit' : 'auto'}
                onChange={(next) => {
                  if (next === 'auto') {
                    setLimiting(false);
                    if (state.context.selected_cap != null) void context(null);
                  } else setLimiting(true);
                }}
                options={[
                  { value: 'auto', label: 'Automatic', disabled: !!busy },
                  { value: 'limit', label: 'Limit…', disabled: !!busy },
                ]}
              />
            }
          >
            {limiting && (
              <div className="settings-context-limit">
                <label htmlFor={limitId}>Limit in tokens</label>
                <Input
                  id={limitId}
                  type="number"
                  min={MIN_CONTEXT}
                  max={10000000}
                  step={1000}
                  value={customContext}
                  aria-describedby={`${limitId}-pages`}
                  onChange={(event) => setCustomContext(event.target.value)}
                  onBlur={commitCustomContext}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') commitCustomContext();
                  }}
                />
                <small id={`${limitId}-pages`}>
                  {Number(customContext) >= MIN_CONTEXT
                    ? `About ${pages(Number(customContext))} pages.`
                    : `At least ${MIN_CONTEXT.toLocaleString()} tokens.`}
                </small>
              </div>
            )}
          </SettingsItem>
        </Disclosure>
      </SettingsGroup>
      <SettingsAdvanced
        summary="Limits for long work"
        meta={
          agents
            ? `${agents.max_iterations} steps per run · ${agents.max_concurrent_children} helper agents at a time`
            : undefined
        }
      >
        {agents ? (
          <SettingsGroup label="Limits for long work">
            {agentFields.map(({ key, label, help }) => (
              <SettingsItem
                key={key}
                label={label}
                help={help}
                control={
                  <Input
                    type="number"
                    min={ZERO_OFF.has(key) ? 0 : 1}
                    max={key === 'goal_max_turns' ? 1000 : 1000000}
                    step={1}
                    value={agentDraft[key] ?? ''}
                    onChange={(event) =>
                      setAgentDraft((value) => ({
                        ...value,
                        [key]: event.target.value,
                      }))
                    }
                    onBlur={commitAgents}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') commitAgents();
                    }}
                  />
                }
              />
            ))}
            <SettingsItem
              label="Recommended limits"
              help="Changes apply to new runs."
              bind={false}
              control={
                <Button
                  variant="ghost"
                  disabled={!!busy}
                  onClick={() => void saveAgents(true)}
                >
                  Restore recommended defaults
                </Button>
              }
            />
          </SettingsGroup>
        ) : (
          <Skeleton label="Loading agent limits" />
        )}
      </SettingsAdvanced>
      <SettingsGroup
        title="Catalog"
        anchor="model-catalog"
        meta={
          <>
            <span role="status">{catalogStatus}</span>
            <span className="settings-status-sep" aria-hidden>
              ·
            </span>
            <Button
              variant="ghost"
              className="settings-link"
              aria-label="Refresh catalog"
              disabled={busy === 'refresh'}
              onClick={() => void refreshCatalog()}
            >
              Refresh
            </Button>
          </>
        }
      >
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
              await saveMedia(surface, 'default', model.selection_ref);
          }}
          onPin={pin}
          onChanged={reload}
        />
        {(pinPending || catalogNote) && (
          <div className="settings-divided settings-catalog-note">
            {catalogNote && <p role="status">{catalogNote}</p>}
            {pinPending && (
              <Button onClick={() => void checkPinReceipt()}>
                Check picker
              </Button>
            )}
          </div>
        )}
      </SettingsGroup>
    </div>
  );
}
