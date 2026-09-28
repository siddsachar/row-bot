import { useId, useRef, useState, type RefObject } from 'react';
import * as Dropdown from '@radix-ui/react-dropdown-menu';
import {
  Bot,
  Check,
  ChevronRight,
  Cpu,
  FolderPlus,
  Paperclip,
  Plus,
  ShieldBan,
  ShieldCheck,
  ShieldQuestion,
  SlidersHorizontal,
  Sparkles,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type {
  ContextUsageView,
  ConversationComposer,
  ConversationControls,
  ReasoningSelectionValue,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useClientState, useRuntime } from '../../runtime';
import { Button, Hint, Menu } from '../../ui/primitives';
import ComposerSkills, {
  SkillsAnchor,
  type ComposerSkillAction,
} from './ComposerSkills';
import ModelPicker from './ModelPicker';
import {
  modelRefName,
  rememberRecentModel,
  splitModelLabel,
} from './model-choices';
import { describeContextUsage, Ring } from './ContextUsage';
import { currentProfileChoice, profileChoices } from './agent-profiles';

const APPROVAL_LABELS = { approve: 'Ask', block: 'Block', allow_all: 'Auto' };
const APPROVAL_ICONS = {
  approve: ShieldQuestion,
  block: ShieldBan,
  allow_all: ShieldCheck,
};

/**
 * The composer's left cluster: a + menu (attach, add resource, skills, agent
 * profile, mode), the model pill with its picker, and an approval shield whose
 * glyph shows Ask/Auto/Block. The server supplies exact-model choices;
 * presentation never invents efforts. A one-line composer (`singleLine`)
 * keeps only the +: the model, approvals and context usage move into it and
 * the picker opens above the field.
 */
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
  contextUsage,
  anchor,
}: {
  composer?: ConversationComposer;
  onSkillAction?: ComposerSkillAction;
  skillsOpen?: boolean;
  onSkillsOpenChange?(open: boolean): void;
  disabled?: boolean;
  onError: (error: string) => void;
  onAttach?: () => void;
  /** Receives the + trigger so the setup can return focus to it. */
  onAddResource?: (opener: HTMLElement | null) => void;
  attachDisabled?: boolean;
  modelPickerOpen?: boolean;
  onModelPickerOpenChange?(open: boolean): void;
  singleLine?: boolean;
  contextUsage?: ContextUsageView | null;
  /** The field the picker opens above in a one-line composer. */
  anchor?: RefObject<HTMLElement | null>;
}) {
  const state = useClientState();
  const { controller } = useRuntime();
  const navigate = useNavigate();
  const attachHintId = useId();
  const [saving, setSaving] = useState(false);
  const [localPickerOpen, setLocalPickerOpen] = useState(false);
  const pickerOpen = modelPickerOpen ?? localPickerOpen;
  const setPickerOpen = onModelPickerOpenChange ?? setLocalPickerOpen;
  const operation = useRef(false);
  const openSkillsAfterMenu = useRef(false);
  const openPickerAfterMenu = useRef(false);
  const plusRef = useRef<HTMLButtonElement>(null);
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
  const mode = controls.approval_mode ?? 'approve';
  const approval = APPROVAL_LABELS[mode];
  const Shield = APPROVAL_ICONS[mode];
  const currentModel = state.handshake?.models?.find(
    (model) => model.model_ref === controls.model_selection?.model_ref,
  );
  const modelName = currentModel
    ? splitModelLabel(currentModel.label).name
    : modelRefName(controls.model_selection?.model_ref) || 'Choose model';
  const context = describeContextUsage(contextUsage);
  async function save(patch: Partial<ConversationControls>) {
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
  const chooseThinking = (selection: ReasoningSelectionValue) =>
    reasoning &&
    save({
      reasoning: {
        model_ref: reasoning.model_ref,
        capability_revision: reasoning.capability_revision,
        selection,
      },
    });
  const thinkingLabel =
    reasoning?.choices.find(
      (item) =>
        JSON.stringify(item.selection) === JSON.stringify(reasoning.selection),
    )?.label ??
    (reasoning?.selection.kind === 'budget'
      ? `${reasoning.selection.budget} tokens`
      : 'Provider default');
  const trigger = (
    <Dropdown.Trigger asChild>
      <Button
        ref={plusRef}
        iconOnly
        variant="ghost"
        className="composer-plus"
        aria-label="Add files and more"
        disabled={state.status !== 'ready'}
      >
        <Plus size={18} aria-hidden />
      </Button>
    </Dropdown.Trigger>
  );
  return (
    <div
      className="composer-control-cluster"
      role="group"
      aria-label="Conversation controls"
      aria-busy={saving}
    >
      <Dropdown.Root>
        {composer ? (
          <ComposerSkills
            composer={composer}
            disabled={blocked}
            action={onSkillAction}
            open={skillsOpen}
            onOpenChange={onSkillsOpenChange}
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
            className="menu surface-effect composer-plus-menu"
            side="top"
            align="start"
            sideOffset={8}
            collisionPadding={12}
            onCloseAutoFocus={(event) => {
              if (openPickerAfterMenu.current) {
                openPickerAfterMenu.current = false;
                event.preventDefault();
                setPickerOpen(true);
                return;
              }
              if (!openSkillsAfterMenu.current) return;
              openSkillsAfterMenu.current = false;
              event.preventDefault();
              onSkillsOpenChange(true);
            }}
          >
            {singleLine && (
              <>
                <Dropdown.Label className="composer-plus-context">
                  <Ring
                    percent={context.percent}
                    threshold={context.threshold}
                  />
                  <span>{context.text}</span>
                </Dropdown.Label>
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
                  <Dropdown.SubTrigger className="menu-item" disabled={blocked}>
                    <Shield size={16} aria-hidden data-mode={mode} />
                    <span className="menu-item-label">Approvals</span>
                    <span className="menu-item-meta">{approval}</span>
                    <ChevronRight size={14} aria-hidden />
                  </Dropdown.SubTrigger>
                  <Dropdown.Portal>
                    <Dropdown.SubContent
                      className="menu surface-effect"
                      sideOffset={4}
                      collisionPadding={12}
                    >
                      <Dropdown.RadioGroup
                        value={mode}
                        onValueChange={(value) =>
                          void save({
                            approval_mode:
                              value as ConversationControls['approval_mode'],
                          })
                        }
                      >
                        {(['approve', 'allow_all', 'block'] as const).map(
                          (value) => (
                            <Dropdown.RadioItem
                              key={value}
                              value={value}
                              className="menu-item"
                            >
                              <span className="menu-item-label">
                                {APPROVAL_LABELS[value]}
                              </span>
                              <Dropdown.ItemIndicator>
                                <Check size={16} aria-hidden />
                              </Dropdown.ItemIndicator>
                            </Dropdown.RadioItem>
                          ),
                        )}
                      </Dropdown.RadioGroup>
                    </Dropdown.SubContent>
                  </Dropdown.Portal>
                </Dropdown.Sub>
                <Dropdown.Separator className="menu-separator" />
              </>
            )}
            {onAttach && (
              <Dropdown.Item
                className="menu-item"
                disabled={attachDisabled}
                onSelect={onAttach}
                aria-label="Attach file"
                aria-describedby={attachHintId}
              >
                <Paperclip size={16} aria-hidden />
                <span className="menu-item-label">Attach file</span>
                {/* The limit is said before anything is refused (U18). */}
                <span className="menu-item-meta" id={attachHintId}>
                  Up to 25 MB each
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
            {composer && (
              <Dropdown.Item
                className="menu-item"
                disabled={blocked}
                onSelect={() => {
                  openSkillsAfterMenu.current = true;
                }}
              >
                <Sparkles size={16} aria-hidden />
                <span className="menu-item-label">Skills…</span>
                <span className="menu-item-meta">
                  {composer.active_skills.length} active
                </span>
              </Dropdown.Item>
            )}
            <Dropdown.Separator className="menu-separator" />
            <Dropdown.Sub>
              <Dropdown.SubTrigger className="menu-item" disabled={blocked}>
                <Bot size={16} aria-hidden />
                <span className="menu-item-label">Agent profile</span>
                <span className="menu-item-meta">{profile}</span>
                <ChevronRight size={14} aria-hidden />
              </Dropdown.SubTrigger>
              <Dropdown.Portal>
                <Dropdown.SubContent
                  className="menu surface-effect"
                  sideOffset={4}
                  collisionPadding={12}
                >
                  <Dropdown.RadioGroup
                    value={currentProfile}
                    onValueChange={(value) => void save({ profile_id: value })}
                  >
                    {profiles.map((item) => (
                      <Dropdown.RadioItem
                        key={item.id}
                        value={item.id}
                        className="menu-item"
                      >
                        <span className="menu-item-label">{item.label}</span>
                        <Dropdown.ItemIndicator>
                          <Check size={16} aria-hidden />
                        </Dropdown.ItemIndicator>
                      </Dropdown.RadioItem>
                    ))}
                  </Dropdown.RadioGroup>
                </Dropdown.SubContent>
              </Dropdown.Portal>
            </Dropdown.Sub>
            <Dropdown.Sub>
              <Dropdown.SubTrigger className="menu-item" disabled={blocked}>
                <SlidersHorizontal size={16} aria-hidden />
                <span className="menu-item-label">Mode</span>
                <span className="menu-item-meta">
                  {controls.runtime_mode === 'agent' ? 'Agent' : 'Chat only'}
                </span>
                <ChevronRight size={14} aria-hidden />
              </Dropdown.SubTrigger>
              <Dropdown.Portal>
                <Dropdown.SubContent
                  className="menu surface-effect"
                  sideOffset={4}
                  collisionPadding={12}
                >
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
                      <Dropdown.RadioItem
                        key={value}
                        value={value}
                        className="menu-item"
                      >
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
                </Dropdown.SubContent>
              </Dropdown.Portal>
            </Dropdown.Sub>
          </Dropdown.Content>
        </Dropdown.Portal>
      </Dropdown.Root>
      <ModelPicker
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
        onConnect={() => navigate('/settings/providers')}
        onReconnect={() => navigate('/settings/providers')}
        onManage={() => navigate('/settings/models')}
        onSetup={() => navigate('/setup')}
        anchor={singleLine ? anchor : undefined}
        returnFocusTo={singleLine ? () => plusRef.current : undefined}
      />
      {!singleLine && (
        <Menu
          label="Approvals"
          hint={`Approvals: ${approval}`}
          variant="ghost"
          iconOnly
          className="composer-shield"
          disabled={blocked}
          actions={(['approve', 'allow_all', 'block'] as const).map(
            (value) => ({
              label: APPROVAL_LABELS[value],
              selected: mode === value,
              onSelect: () => void save({ approval_mode: value }),
            }),
          )}
        >
          <Shield size={17} aria-hidden data-mode={mode} />
        </Menu>
      )}
      {saving && (
        <small className="visually-hidden" role="status">
          Saving conversation controls…
        </small>
      )}
    </div>
  );
}
