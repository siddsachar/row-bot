import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from 'react';
import * as Dropdown from '@radix-ui/react-dropdown-menu';
import {
  Blocks,
  Bot,
  Camera,
  Check,
  ChevronLeft,
  ChevronRight,
  Cpu,
  File as FileIcon,
  FolderOpen,
  FolderPlus,
  HatGlasses,
  Image as ImageIcon,
  Paperclip,
  Plus,
  Search,
  Settings2,
  SlidersHorizontal,
  Sparkles,
  type LucideIcon,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type {
  ContextUsageView,
  ConversationComposer,
  ConversationControlsPayload,
  ConversationView,
  ModelChoice,
  ReasoningSelectionValue,
  ResourceView,
} from '../../api/types';
import { clientError } from '../../api/errors';
import type { FilePickerOptions } from '../../platform/types';
import { useClientState, useRuntime } from '../../runtime';
import { Button, Hint, Menu, useMenuOpen } from '../../ui/primitives';
import ComposerSkills, {
  SkillsAnchor,
  type ComposerSkillAction,
} from './ComposerSkills';
import ModelPicker from './ModelPicker';
import type { ListedModel, VisibilityAction } from './ModelList';
import {
  modelRefName,
  providerSheetPath,
  rememberRecentModel,
  splitModelLabel,
} from './model-choices';
import {
  ContextDetailsPopover,
  describeContextUsage,
  Ring,
} from './ContextUsage';
import { AppIcon } from '../apps/parts';
import { currentProfileChoice, profileChoices } from './agent-profiles';
import {
  APPROVAL_DESCRIPTIONS,
  APPROVAL_ICONS,
  APPROVAL_LABELS,
  APPROVAL_LEVELS,
  SHARED_CONVERSATION_NOTE,
  approvalLevel,
  useConfirmFullAutonomy,
  type ApprovalLevel,
} from './approval-levels';
import { ATTACHMENT_LIMITS } from './attachment-limits';
import { useIncognitoSwitch } from './incognito';
import { currentThinkingLabel } from './slash-arguments';
import { targetGroups, type TargetKind } from './ResourceTargets';
import '../../ui/styles/phone-composer.css';
import { modelSettingsPath } from '../local-runtime/LocalModelSheet';

/**
 * The composer's left cluster: a + menu (attach, add resource, skills, agent
 * profile, mode), the model pill with its picker, and an approval shield whose
 * glyph shows Ask/Auto/Hands-off/Block. Hands-off reads as words, in
 * the warning tone, while it is on, and turning it on the first time asks
 * once. The server supplies exact-model choices; presentation never invents
 * efforts. A one-line composer (`singleLine`) keeps only the +: the model,
 * approvals and context usage move into it and the picker opens above the
 * field (Hands-off still shows beside it).
 *
 * On a phone (`phone`, B2 / D9 A) the + sheet holds Camera, Photos and Files
 * tiles, then Use an app, Skills and Chat settings ›. Chat settings is one
 * level down, in the same sheet, with ‹ back: Approvals, Agent, Mode, the
 * folder or design it works in, and Incognito. The model is in the chat's
 * header and the context line in Conversation details.
 */
/** How far a one-line composer's + submenus overlap the + menu (it sits at the screen's edge). */
const SUBMENU_OVERLAP = 232;

/** The phone's attach tiles and what each asks the picker for. */
const ATTACH_TILES: readonly [string, LucideIcon, FilePickerOptions][] = [
  ['Camera', Camera, { accept: 'image/*', capture: 'environment' }],
  ['Photos', ImageIcon, { accept: 'image/*' }],
  ['Files', FileIcon, {}],
];

/** Where the phone's + sheet is: +, Chat settings, or a choice below them. */
type SheetLevel =
  'plus' | 'settings' | 'apps' | 'approvals' | 'agent' | 'mode' | 'target';
const SHEET_TITLES: Record<Exclude<SheetLevel, 'plus'>, string> = {
  settings: 'Chat settings',
  apps: 'Use an app',
  approvals: 'Approvals',
  agent: 'Agent',
  mode: 'Mode',
  target: 'Work in a folder or design',
};
/** One level up: Chat settings' choices go back to it, the rest to +. */
const parentLevel = (level: SheetLevel): SheetLevel =>
  level === 'settings' || level === 'apps' ? 'plus' : 'settings';

/** The radio value of a target kind's None. */
const NO_TARGET = 'none';

// The start-up handshake just read the list; later opens re-read it.
let choicesReadAt = Date.now();
/** Re-read the model list when the picker opens, at most every 30 s. */
function refreshChoicesSoon(controller: {
  refreshChoices?: () => Promise<void>;
}) {
  const now = Date.now();
  if (!controller.refreshChoices || now - choicesReadAt < 30_000) return;
  choicesReadAt = now;
  void controller.refreshChoices().catch(() => undefined);
}

/** A phone sheet row: a glyph in a tile, its name with one line under it. */
function RowLabel({
  icon,
  title,
  detail,
}: {
  icon: ReactNode;
  title: string;
  detail?: string;
}) {
  return (
    <>
      <span className="composer-sheet-icon" aria-hidden>
        {icon}
      </span>
      <span className="menu-item-label">
        {title}
        {detail && <small>{detail}</small>}
      </span>
    </>
  );
}

/** The chat's Incognito switch (N11), the one the phone's ⋯ menu had. */
function IncognitoItem({
  conversation,
  disabled,
}: {
  conversation: Pick<ConversationView, 'id' | 'revision' | 'incognito'>;
  disabled: boolean;
}) {
  const turn = useIncognitoSwitch();
  const [saving, setSaving] = useState(false);
  const on = Boolean(conversation.incognito);
  return (
    <Dropdown.CheckboxItem
      className="menu-item composer-sheet-row composer-sheet-switch"
      checked={on}
      disabled={disabled || saving}
      // The switch flips in place; the sheet stays open.
      onSelect={(event) => event.preventDefault()}
      onCheckedChange={async () => {
        setSaving(true);
        try {
          await turn(conversation);
        } finally {
          setSaving(false);
        }
      }}
    >
      <RowLabel
        icon={<HatGlasses size={18} />}
        title="Incognito"
        detail={
          on
            ? 'On: nothing here is saved to memory'
            : 'Keep this chat out of memory'
        }
      />
      <span className="toggle-track" aria-hidden />
    </Dropdown.CheckboxItem>
  );
}

export default function ComposerControls({
  composer,
  onSkillAction = async () => undefined,
  skillsOpen = false,
  onSkillsOpenChange = () => undefined,
  disabled = false,
  onError,
  onAttach,
  onAddResource,
  attachDisabled = false,
  modelPickerOpen,
  onModelPickerOpenChange,
  singleLine = false,
  phone = false,
  targets,
  contextUsage,
  anchor,
}: {
  composer?: ConversationComposer;
  onSkillAction?: ComposerSkillAction;
  skillsOpen?: boolean;
  onSkillsOpenChange?(open: boolean): void;
  disabled?: boolean;
  onError: (error: string) => void;
  /** Opens the file picker; the phone's tiles ask for images or the camera. */
  onAttach?: (picker?: FilePickerOptions) => void;
  /** Receives the + trigger so the setup can return focus to it. */
  onAddResource?: (opener: HTMLElement | null) => void;
  attachDisabled?: boolean;
  modelPickerOpen?: boolean;
  onModelPickerOpenChange?(open: boolean): void;
  singleLine?: boolean;
  /** The phone layout (below 768px): the + sheet of D9 A. */
  phone?: boolean;
  /** The chat's design and folder write targets, chosen in Chat settings on a phone. */
  targets?: {
    resources: readonly ResourceView[];
    selected: readonly string[];
    onChange(kind: TargetKind, bindingId: string | null): void;
  };
  contextUsage?: ContextUsageView | null;
  /** The field the picker opens above in a one-line composer. */
  anchor?: RefObject<HTMLElement | null>;
}) {
  const state = useClientState();
  const { controller } = useRuntime();
  const navigate = useNavigate();
  const attachHintId = useId();
  const levelTitleId = useId();
  const [saving, setSaving] = useState(false);
  const [localPickerOpen, setLocalPickerOpen] = useState(false);
  const pickerOpen = modelPickerOpen ?? localPickerOpen;
  const setPickerOpen = onModelPickerOpenChange ?? setLocalPickerOpen;
  const operation = useRef(false);
  const openSkillsAfterMenu = useRef(false);
  const openPickerAfterMenu = useRef(false);
  const openContextAfterMenu = useRef(false);
  const [contextOpen, setContextOpen] = useState(false);
  const confirmFullAfterMenu = useRef(false);
  const plusRef = useRef<HTMLButtonElement>(null);
  const shieldRef = useRef<HTMLButtonElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);
  const confirmFullAutonomy = useConfirmFullAutonomy();
  // The phone's + sheet shows one level at a time.
  const [level, setLevel] = useState<SheetLevel>('plus');
  // Escape and ← read the level shown now: Radix may still hold the
  // previous render's handler when a second key follows at once.
  const levelNow = useRef<SheetLevel>('plus');
  const goTo = (next: SheetLevel) => {
    levelNow.current = next;
    setLevel(next);
  };
  // Typing in the picker also searches every connected provider's chat models.
  const searchModels = useCallback(
    async (query: string, signal: AbortSignal): Promise<ListedModel[]> => {
      const page = await controller.modelCatalogPage(
        'chat',
        undefined,
        query,
        undefined,
        signal,
      );
      return (page?.items ?? []).map((row) => ({
        provider_id: row.provider_id,
        model_ref: row.selection_ref,
        label: `${row.display_name} - ${row.provider_display_name}`,
        available:
          row.configured && row.runtime_ready && row.installed !== false,
        reason: row.status_reason || undefined,
        billing: null,
        personal: false,
      }));
    },
    [controller],
  );
  // Connect and Reconnect open that provider's sheet in Settings › Models.
  const providerSheet = (providerId?: string) =>
    navigate(providerSheetPath(providerId));
  // The + menu rises as a sheet on a phone: it opens on the tap (useMenuOpen).
  const plusMenu = useMenuOpen();
  // A level change keeps the focus in the sheet, on the sheet itself, as
  // when it opened from a tap.
  const levelShown = useRef(level);
  useEffect(() => {
    if (levelShown.current === level) return;
    levelShown.current = level;
    sheetRef.current?.focus();
  }, [level]);
  const workspace = state.workspace;
  const controls = workspace?.controls;
  const id = state.selectedConversationId;
  if (!controls || !workspace || workspace.conversation_id !== id) return null;
  const blocked = disabled || saving || state.status !== 'ready';
  const reasoning =
    workspace.reasoning?.model_ref === controls.model_selection?.model_ref
      ? workspace.reasoning
      : null;
  const profiles = profileChoices(workspace.profiles);
  const currentProfile = currentProfileChoice(
    workspace.profiles,
    controls.profile_id,
  );
  const profile =
    profiles.find((item) => item.id === currentProfile)?.label ?? 'Default';
  const mode = approvalLevel(controls.approval_mode);
  const approval = APPROVAL_LABELS[mode];
  const Shield = APPROVAL_ICONS[mode];
  const runtimeMode = controls.runtime_mode === 'agent' ? 'Agent' : 'Chat only';
  // Shared channel conversations run on Auto at most; the first Hands-off asks once.
  const fullAvailable = workspace.approval?.full_autonomy_available !== false;
  const fullConfirmed = workspace.approval?.full_autonomy_confirmed === true;
  const currentModel = state.handshake?.models?.find(
    (model) => model.model_ref === controls.model_selection?.model_ref,
  );
  const modelName = currentModel
    ? splitModelLabel(currentModel.label).name
    : modelRefName(controls.model_selection?.model_ref) || 'Choose model';
  const context = describeContextUsage(contextUsage);
  const measured = !!contextUsage && contextUsage.state !== 'unknown';
  const apps = composer?.apps ?? [];
  const switchable = apps.filter((app) => app.switchable !== false);
  const appsOn = switchable.filter((app) => app.on && app.available).length;
  const conversation =
    state.conversation?.id === id ? state.conversation : null;
  const groups = targets
    ? targetGroups(targets.resources, targets.selected)
    : [];
  const targetNames = groups
    .map((group) => group.currentName)
    .filter((name) => name !== 'None')
    .join(' · ');
  /** One app on or off for this chat only; the agent profile still decides what it may use. */
  async function switchApp(itemId: string, on: boolean) {
    if (operation.current || blocked || !id) return;
    operation.current = true;
    setSaving(true);
    try {
      await controller.intent(
        id,
        'conversation.apps',
        { item_id: itemId, on },
        workspace!.revision,
      );
      onError('');
    } catch (cause) {
      onError(clientError(cause).message);
    } finally {
      operation.current = false;
      setSaving(false);
    }
  }
  async function save(patch: Partial<ConversationControlsPayload>) {
    if (operation.current || blocked || !id || !controls) return;
    operation.current = true;
    setSaving(true);
    const selectionVersion = controller.getSelectionVersion();
    try {
      await controller.intent(
        id,
        'conversation.controls',
        {
          model_selection: controls.model_selection,
          runtime_mode: controls.runtime_mode,
          profile_id: controls.profile_id,
          approval_mode: controls.approval_mode,
          ...patch,
        },
        workspace!.revision,
      );
      if (controller.getSelectionVersion() === selectionVersion) onError('');
    } catch (cause) {
      if (controller.getSelectionVersion() === selectionVersion)
        onError(clientError(cause).message);
    } finally {
      operation.current = false;
      setSaving(false);
    }
  }
  /** Star or unstar a model from the picker. It never changes the chat's model. */
  async function changeVisibility(item: ModelChoice, action: VisibilityAction) {
    try {
      await controller.updateModelVisibility({
        action,
        model_ref: item.model_ref,
      });
    } catch (cause) {
      // The composer's own error line, like a model change that didn't save.
      const name = splitModelLabel(item.label).name;
      onError(
        `Couldn't save the change to ${name}. ${clientError(cause).message}`,
      );
    }
  }
  /** Turns a level on; Hands-off asks once on this install, after which it never asks again. */
  function chooseLevel(level: ApprovalLevel, opener?: HTMLElement | null) {
    if (level === mode) return;
    if (level !== 'full' || fullConfirmed) {
      void save({ approval_mode: level });
      return;
    }
    confirmFullAutonomy(
      () => void save({ approval_mode: 'full', confirm_full_autonomy: true }),
      opener ?? shieldRef.current ?? plusRef.current,
    );
  }
  // A narrow composer's + menu leaves no room beside it: its submenus open over it
  // instead of running off the screen.
  const submenuOffset = singleLine ? -SUBMENU_OVERLAP : 4;
  const levelActions = APPROVAL_LEVELS.map((value) => {
    const unavailable = value === 'full' && !fullAvailable;
    const Icon = APPROVAL_ICONS[value];
    return {
      label: APPROVAL_LABELS[value],
      description: unavailable
        ? SHARED_CONVERSATION_NOTE
        : APPROVAL_DESCRIPTIONS[value],
      icon: <Icon size={16} />,
      selected: mode === value,
      disabled: unavailable,
      // The dialog opens once the menu has let go of focus.
      onSelect: () => {
        if (value === 'full' && !fullConfirmed && mode !== 'full') return;
        chooseLevel(value);
      },
      afterClose:
        value === 'full' && !fullConfirmed && mode !== 'full'
          ? () => chooseLevel('full', shieldRef.current)
          : undefined,
    };
  });
  const chooseThinking = (selection: ReasoningSelectionValue) =>
    reasoning &&
    save({
      reasoning: {
        model_ref: reasoning.model_ref,
        capability_revision: reasoning.capability_revision,
        selection,
      },
    });
  const thinkingLabel = currentThinkingLabel(reasoning);
  const trigger = (
    <Dropdown.Trigger asChild>
      <Button
        ref={plusRef}
        iconOnly
        variant="ghost"
        className="composer-plus"
        aria-label="Add files and more"
        disabled={state.status !== 'ready'}
        {...plusMenu.triggerProps}
      >
        <Plus size={18} aria-hidden />
      </Button>
    </Dropdown.Trigger>
  );
  // The choices behind each submenu: in a submenu on a computer, one level
  // down in the same sheet on a phone. A submenu there would open over the
  // sheet it came from, and on a narrow window with a mouse the item it
  // covers takes the focus back and closes it.
  const approvalsChoices = (
    <Dropdown.RadioGroup
      value={mode}
      onValueChange={(value) => {
        const level = value as ApprovalLevel;
        // The dialog waits until the menu has closed.
        if (level === 'full' && !fullConfirmed)
          confirmFullAfterMenu.current = true;
        else chooseLevel(level);
      }}
    >
      {levelActions.map((action, index) => (
        <Dropdown.RadioItem
          key={APPROVAL_LEVELS[index]}
          value={APPROVAL_LEVELS[index]}
          disabled={action.disabled}
          className="menu-item menu-item-rich"
        >
          <span className="menu-item-icon" aria-hidden>
            {action.icon}
          </span>
          <span className="menu-item-label">
            {action.label}
            <small>{action.description}</small>
          </span>
          <Dropdown.ItemIndicator>
            <Check size={16} aria-hidden />
          </Dropdown.ItemIndicator>
        </Dropdown.RadioItem>
      ))}
    </Dropdown.RadioGroup>
  );
  const appsChoices = (
    <>
      {switchable.map((app) => (
        <Dropdown.CheckboxItem
          key={app.item_id}
          className="menu-item"
          checked={app.on && app.available}
          disabled={!app.available || blocked}
          onSelect={(event) => event.preventDefault()}
          onCheckedChange={(checked) =>
            void switchApp(app.item_id, checked === true)
          }
        >
          <AppIcon icon={app.icon} size={18} />
          <span className="menu-item-label">
            {app.name}
            {!app.available && <small>{app.reason}</small>}
          </span>
          <Dropdown.ItemIndicator>
            <Check size={16} aria-hidden />
          </Dropdown.ItemIndicator>
        </Dropdown.CheckboxItem>
      ))}
      {apps
        .filter((app) => app.switchable === false)
        .map((app) => (
          // Part of Row-Bot without chat tools: listed as in Your apps, with why it has no switch.
          <Dropdown.Item key={app.item_id} className="menu-item" disabled>
            <AppIcon icon={app.icon} size={18} />
            <span className="menu-item-label">
              {app.name}
              <small>{app.reason}</small>
            </span>
          </Dropdown.Item>
        ))}
      {apps.length > 0 && <Dropdown.Separator className="menu-separator" />}
      <Dropdown.Item
        className="menu-item"
        onSelect={() => navigate('/settings/apps')}
      >
        <Search size={16} aria-hidden />
        <span className="menu-item-label">Find more apps</span>
      </Dropdown.Item>
    </>
  );
  const agentChoices = (
    <Dropdown.RadioGroup
      value={currentProfile}
      onValueChange={(value) => void save({ profile_id: value })}
    >
      {profiles.map((item) => (
        <Dropdown.RadioItem key={item.id} value={item.id} className="menu-item">
          <span className="menu-item-label">
            {item.label}
            {item.description && (
              // One muted line, cut short within the menu's width.
              <small
                title={item.description}
                style={{
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {item.description}
              </small>
            )}
          </span>
          <Dropdown.ItemIndicator>
            <Check size={16} aria-hidden />
          </Dropdown.ItemIndicator>
        </Dropdown.RadioItem>
      ))}
    </Dropdown.RadioGroup>
  );
  const modeChoices = (
    <Dropdown.RadioGroup
      value={controls.runtime_mode ?? 'agent'}
      onValueChange={(value) =>
        void save({
          runtime_mode: value as 'agent' | 'chat_only',
        })
      }
    >
      {(
        [
          ['agent', 'Agent', 'Uses tools and agents'],
          ['chat_only', 'Chat only', 'Answers without tools'],
        ] as const
      ).map(([value, label, description]) => (
        <Dropdown.RadioItem key={value} value={value} className="menu-item">
          <span className="menu-item-label">
            {label}
            <small>{description}</small>
          </span>
          <Dropdown.ItemIndicator>
            <Check size={16} aria-hidden />
          </Dropdown.ItemIndicator>
        </Dropdown.RadioItem>
      ))}
    </Dropdown.RadioGroup>
  );
  // Which design or folder the chat writes to: the composer's target chips
  // on a computer.
  const targetChoices = targets && (
    <>
      {groups.map((group) => (
        <Dropdown.Group key={group.kind} aria-label={`${group.label} target`}>
          <Dropdown.Label className="composer-sheet-group-label">
            {group.label}
          </Dropdown.Label>
          <Dropdown.RadioGroup
            value={
              group.choices.find((choice) => choice.selected)?.bindingId ??
              NO_TARGET
            }
            onValueChange={(value) =>
              targets.onChange(group.kind, value === NO_TARGET ? null : value)
            }
          >
            {group.choices.map((choice) => (
              <Dropdown.RadioItem
                key={choice.bindingId ?? NO_TARGET}
                value={choice.bindingId ?? NO_TARGET}
                disabled={choice.disabled}
                className="menu-item"
              >
                <span className="menu-item-label">{choice.label}</span>
                <Dropdown.ItemIndicator>
                  <Check size={16} aria-hidden />
                </Dropdown.ItemIndicator>
              </Dropdown.RadioItem>
            ))}
          </Dropdown.RadioGroup>
        </Dropdown.Group>
      ))}
      {onAddResource && (
        <>
          <Dropdown.Separator className="menu-separator" />
          <Dropdown.Item
            className="menu-item"
            onSelect={() => onAddResource(plusRef.current)}
          >
            <FolderPlus size={16} aria-hidden />
            <span className="menu-item-label">Add a folder or design…</span>
          </Dropdown.Item>
        </>
      )}
    </>
  );
  const submenu = (
    children: ReactNode,
    label?: string,
    className = 'menu surface-effect',
  ) => (
    <Dropdown.Portal>
      <Dropdown.SubContent
        className={className}
        sideOffset={submenuOffset}
        collisionPadding={12}
        aria-label={label}
      >
        {children}
      </Dropdown.SubContent>
    </Dropdown.Portal>
  );
  const skillsItem = (children: ReactNode, className = 'menu-item') =>
    composer && (
      <Dropdown.Item
        className={className}
        disabled={blocked}
        onSelect={() => {
          openSkillsAfterMenu.current = true;
        }}
      >
        {children}
      </Dropdown.Item>
    );
  const chevron = <ChevronRight size={16} aria-hidden />;
  const phoneRow = 'menu-item composer-sheet-row';
  /** A phone row that opens the next level of the sheet. */
  const levelRow = (to: SheetLevel, label: ReactNode, rowDisabled = false) => (
    <Dropdown.Item
      className={phoneRow}
      disabled={rowDisabled}
      onSelect={(event) => {
        event.preventDefault();
        goTo(to);
      }}
    >
      {label}
      {chevron}
    </Dropdown.Item>
  );
  // The phone's + level (D9 A): tiles to attach, then what the chat can use.
  const phonePlus = (
    <>
      {onAttach && (
        <>
          <Dropdown.Group className="composer-attach-tiles" aria-label="Attach">
            {ATTACH_TILES.map(([label, Icon, picker]) => (
              <Dropdown.Item
                key={label}
                className="composer-attach-tile"
                disabled={attachDisabled}
                onSelect={() => onAttach(picker)}
                aria-describedby={attachHintId}
              >
                <Icon size={22} aria-hidden />
                <span>{label}</span>
              </Dropdown.Item>
            ))}
          </Dropdown.Group>
          {/* The limit is said before anything is refused (U18). */}
          <Dropdown.Label className="composer-attach-limit" id={attachHintId}>
            Up to {ATTACHMENT_LIMITS.fileMegabytes} MB each
          </Dropdown.Label>
        </>
      )}
      {composer &&
        levelRow(
          'apps',
          <RowLabel
            icon={<Blocks size={18} />}
            title="Use an app"
            detail={
              apps.length
                ? `${appsOn} on in this chat`
                : 'Connect GitHub, Gmail and more'
            }
          />,
          blocked,
        )}
      {skillsItem(
        <>
          <RowLabel
            icon={<Sparkles size={18} />}
            title="Skills"
            detail={`${composer?.active_skills.length ?? 0} on in this chat`}
          />
          {chevron}
        </>,
        phoneRow,
      )}
      {levelRow(
        'settings',
        <RowLabel
          icon={<Settings2 size={18} />}
          title="Chat settings"
          detail={`Approvals: ${approval} · Agent: ${profile} · Mode: ${runtimeMode}`}
        />,
      )}
    </>
  );
  // Chat settings, one level down: the same controls the + menu had.
  const phoneSettings = (
    <>
      {levelRow(
        'approvals',
        <>
          <RowLabel
            icon={<Shield size={18} data-mode={mode} />}
            title="Approvals"
            detail={APPROVAL_DESCRIPTIONS[mode]}
          />
          <span className="menu-item-meta">{approval}</span>
        </>,
        blocked,
      )}
      {levelRow(
        'agent',
        <>
          <RowLabel icon={<Bot size={18} />} title="Agent" />
          <span className="menu-item-meta">{profile}</span>
        </>,
        blocked,
      )}
      {levelRow(
        'mode',
        <>
          <RowLabel icon={<SlidersHorizontal size={18} />} title="Mode" />
          <span className="menu-item-meta">{runtimeMode}</span>
        </>,
        blocked,
      )}
      {groups.length
        ? levelRow(
            'target',
            <RowLabel
              icon={<FolderOpen size={18} />}
              title="Work in a folder or design"
              detail={targetNames || 'Not writing to one'}
            />,
          )
        : onAddResource && (
            <Dropdown.Item
              className={phoneRow}
              onSelect={() => onAddResource(plusRef.current)}
            >
              <RowLabel
                icon={<FolderOpen size={18} />}
                title="Work in a folder or design"
              />
              {chevron}
            </Dropdown.Item>
          )}
      {conversation && (
        <IncognitoItem
          conversation={conversation}
          disabled={state.status !== 'ready'}
        />
      )}
    </>
  );
  const phoneSheet = {
    plus: phonePlus,
    settings: phoneSettings,
    apps: appsChoices,
    approvals: approvalsChoices,
    agent: agentChoices,
    mode: modeChoices,
    target: targetChoices,
  }[level];
  // Below +, a heading with ‹ back up one level and where the sheet is.
  const phoneHeading = level !== 'plus' && (
    <div className="composer-sheet-heading">
      {/* No tooltip: one opened by the keyboard's focus landing here
          would take the first Escape, which goes back. */}
      <Dropdown.Item
        className="menu-item composer-sheet-back"
        aria-label="Back"
        onSelect={(event) => {
          event.preventDefault();
          goTo(parentLevel(level));
        }}
      >
        <ChevronLeft size={20} aria-hidden />
      </Dropdown.Item>
      <Dropdown.Label className="composer-sheet-title" id={levelTitleId}>
        {SHEET_TITLES[level]}
      </Dropdown.Label>
    </div>
  );
  return (
    <div
      className="composer-control-cluster"
      role="group"
      aria-label="Conversation controls"
      aria-busy={saving}
    >
      <Dropdown.Root
        open={plusMenu.open}
        onOpenChange={(open) => {
          // The sheet opens on + again next time.
          if (!open) goTo('plus');
          plusMenu.onOpenChange(open);
        }}
      >
        {composer ? (
          <ComposerSkills
            composer={composer}
            disabled={blocked}
            action={onSkillAction}
            open={skillsOpen}
            onOpenChange={onSkillsOpenChange}
            onFindMore={() => navigate('/settings/skills')}
          >
            <Hint label="Add files and more">
              <SkillsAnchor asChild>{trigger}</SkillsAnchor>
            </Hint>
          </ComposerSkills>
        ) : (
          <Hint label="Add files and more">{trigger}</Hint>
        )}
        <Dropdown.Portal>
          <Dropdown.Content
            ref={sheetRef}
            className={`menu surface-effect composer-plus-menu${phone ? ' composer-plus-sheet' : ''}`}
            data-level={phone ? level : undefined}
            // Named by its trigger; below +, by the level's title.
            {...(phone &&
              level !== 'plus' && { 'aria-labelledby': levelTitleId })}
            side="top"
            align="start"
            sideOffset={8}
            collisionPadding={12}
            // Below +, Escape, a swipe down or ← goes up one level, as out
            // of a submenu.
            onEscapeKeyDown={(event) => {
              if (!phone || levelNow.current === 'plus') return;
              event.preventDefault();
              goTo(parentLevel(levelNow.current));
            }}
            onKeyDown={(event) => {
              if (
                phone &&
                levelNow.current !== 'plus' &&
                event.key === 'ArrowLeft'
              ) {
                event.preventDefault();
                goTo(parentLevel(levelNow.current));
              }
            }}
            onCloseAutoFocus={(event) => {
              if (confirmFullAfterMenu.current) {
                confirmFullAfterMenu.current = false;
                event.preventDefault();
                chooseLevel('full', plusRef.current);
                return;
              }
              if (openPickerAfterMenu.current) {
                openPickerAfterMenu.current = false;
                event.preventDefault();
                setPickerOpen(true);
                return;
              }
              if (openContextAfterMenu.current) {
                openContextAfterMenu.current = false;
                event.preventDefault();
                setContextOpen(true);
                return;
              }
              if (!openSkillsAfterMenu.current) return;
              openSkillsAfterMenu.current = false;
              event.preventDefault();
              onSkillsOpenChange(true);
            }}
          >
            {phone ? (
              <>
                {phoneHeading}
                {phoneSheet}
              </>
            ) : (
              <>
                {singleLine && (
                  <>
                    {measured ? (
                      // Opens what fills the context once the menu has closed.
                      <Dropdown.Item
                        className="menu-item composer-plus-context"
                        onSelect={() => {
                          openContextAfterMenu.current = true;
                        }}
                      >
                        <Ring
                          percent={context.percent}
                          threshold={context.threshold}
                        />
                        <span className="menu-item-label">{context.text}</span>
                        <ChevronRight size={14} aria-hidden />
                      </Dropdown.Item>
                    ) : (
                      <Dropdown.Label className="composer-plus-context">
                        <Ring
                          percent={context.percent}
                          threshold={context.threshold}
                        />
                        <span>{context.text}</span>
                      </Dropdown.Label>
                    )}
                    <Dropdown.Item
                      className="menu-item"
                      disabled={blocked}
                      onSelect={() => {
                        openPickerAfterMenu.current = true;
                      }}
                    >
                      <Cpu size={16} aria-hidden />
                      <span className="menu-item-label">Model</span>
                      <span className="menu-item-meta">{modelName}</span>
                    </Dropdown.Item>
                    <Dropdown.Sub>
                      <Dropdown.SubTrigger
                        className="menu-item"
                        disabled={blocked}
                      >
                        <Shield size={16} aria-hidden data-mode={mode} />
                        <span className="menu-item-label">Approvals</span>
                        <span className="menu-item-meta">{approval}</span>
                        <ChevronRight size={14} aria-hidden />
                      </Dropdown.SubTrigger>
                      {submenu(approvalsChoices)}
                    </Dropdown.Sub>
                    <Dropdown.Separator className="menu-separator" />
                  </>
                )}
                {onAttach && (
                  <Dropdown.Item
                    className="menu-item"
                    disabled={attachDisabled}
                    onSelect={() => onAttach()}
                    aria-label="Attach file"
                    aria-describedby={attachHintId}
                  >
                    <Paperclip size={16} aria-hidden />
                    <span className="menu-item-label">Attach file</span>
                    {/* The limit is said before anything is refused (U18). */}
                    <span className="menu-item-meta" id={attachHintId}>
                      Up to {ATTACHMENT_LIMITS.fileMegabytes} MB each
                    </span>
                  </Dropdown.Item>
                )}
                {onAddResource && (
                  <Dropdown.Item
                    className="menu-item"
                    onSelect={() => onAddResource(plusRef.current)}
                  >
                    <FolderPlus size={16} aria-hidden />
                    <span className="menu-item-label">Add resource…</span>
                  </Dropdown.Item>
                )}
                {skillsItem(
                  <>
                    <Sparkles size={16} aria-hidden />
                    <span className="menu-item-label">Skills…</span>
                    <span className="menu-item-meta">
                      {composer?.active_skills.length} active
                    </span>
                  </>,
                )}
                {composer && (
                  <Dropdown.Sub>
                    <Dropdown.SubTrigger
                      className="menu-item"
                      disabled={blocked}
                    >
                      <Blocks size={16} aria-hidden />
                      <span className="menu-item-label">Apps</span>
                      <span className="menu-item-meta">
                        {apps.length ? `${appsOn} on` : 'None yet'}
                      </span>
                      <ChevronRight size={14} aria-hidden />
                    </Dropdown.SubTrigger>
                    {submenu(
                      appsChoices,
                      'Apps in this chat',
                      'menu surface-effect composer-apps-menu',
                    )}
                  </Dropdown.Sub>
                )}
                <Dropdown.Separator className="menu-separator" />
                <Dropdown.Sub>
                  <Dropdown.SubTrigger className="menu-item" disabled={blocked}>
                    <Bot size={16} aria-hidden />
                    <span className="menu-item-label">Agent</span>
                    <span className="menu-item-meta">{profile}</span>
                    <ChevronRight size={14} aria-hidden />
                  </Dropdown.SubTrigger>
                  {submenu(agentChoices)}
                </Dropdown.Sub>
                <Dropdown.Sub>
                  <Dropdown.SubTrigger className="menu-item" disabled={blocked}>
                    <SlidersHorizontal size={16} aria-hidden />
                    <span className="menu-item-label">Mode</span>
                    <span className="menu-item-meta">{runtimeMode}</span>
                    <ChevronRight size={14} aria-hidden />
                  </Dropdown.SubTrigger>
                  {submenu(modeChoices)}
                </Dropdown.Sub>
              </>
            )}
          </Dropdown.Content>
        </Dropdown.Portal>
      </Dropdown.Root>
      {singleLine && !phone && (
        <ContextDetailsPopover
          usage={contextUsage}
          open={contextOpen}
          onOpenChange={setContextOpen}
          anchor={plusRef}
        />
      )}
      <ModelPicker
        key={id}
        models={state.handshake?.models ?? []}
        current={controls.model_selection?.model_ref}
        status={workspace.model_status}
        runtimeMode={controls.runtime_mode}
        disabled={blocked}
        open={pickerOpen}
        onOpenChange={(next) => {
          // The list can change after start-up (a provider connected, a new
          // default); re-read it at most every 30 s when the picker opens.
          if (next) refreshChoicesSoon(controller);
          setPickerOpen(next);
        }}
        onChoose={(item) => {
          rememberRecentModel(item.model_ref);
          if (item.model_ref === controls.model_selection?.model_ref) return;
          void save({
            model_selection: {
              provider_id: item.provider_id,
              model_ref: item.model_ref,
            },
          });
        }}
        reasoning={reasoning?.available ? reasoning : null}
        thinkingLabel={thinkingLabel}
        onThinking={(selection) => void chooseThinking(selection)}
        onConnect={providerSheet}
        onReconnect={() => providerSheet(controls.model_selection?.provider_id)}
        onManage={() => navigate('/settings/models')}
        onModelSettings={(modelRef) => navigate(modelSettingsPath(modelRef))}
        onSetup={() => navigate('/setup')}
        onVisibility={(item, action) => void changeVisibility(item, action)}
        search={searchModels}
        anchor={singleLine ? anchor : undefined}
        returnFocusTo={singleLine ? () => plusRef.current : undefined}
      />
      {!singleLine && !phone && (
        <Menu
          // The level in its name under every level: "Approvals: Ask" (U7).
          label={`Approvals: ${approval}`}
          hint={
            mode === 'full'
              ? 'Hands-off is on: nothing asks before it runs'
              : `Approvals: ${approval}`
          }
          variant="ghost"
          iconOnly={mode !== 'full'}
          triggerRef={shieldRef}
          className={`composer-shield${mode === 'full' ? ' composer-shield-full' : ''}`}
          disabled={blocked}
          actions={levelActions}
        >
          <Shield size={17} aria-hidden data-mode={mode} />
          {mode === 'full' && <span>{approval}</span>}
        </Menu>
      )}
      {(singleLine || phone) && mode === 'full' && (
        // Unmistakable while on, even when the control sits in the + menu.
        <span className="composer-full-autonomy" role="status">
          <Shield size={14} aria-hidden data-mode={mode} />
          Hands-off
        </span>
      )}
      {saving && (
        <small className="visually-hidden" role="status">
          Saving conversation controls…
        </small>
      )}
    </div>
  );
}
