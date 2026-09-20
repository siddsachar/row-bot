import { useEffect, useRef, useState } from 'react';
import * as Popover from '@radix-ui/react-popover';
import {
  Brain,
  ChevronDown,
  Cpu,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
} from 'lucide-react';
import type {
  ConversationControls,
  ReasoningSelectionValue,
  SkillSummary,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useClientState, useRuntime } from '../../runtime';
import { Button, Field, Hint, Input, Menu } from '../../ui/primitives';

const SLASH_DISCOVERY = [
  { token: '/skills', label: 'Skills', description: 'Ask about chat skills.' },
  { token: '/help', label: 'Help', description: 'Ask for command help.' },
  {
    token: '/status',
    label: 'Status',
    description: 'Ask for a status summary.',
  },
  { token: '/tools', label: 'Tools', description: 'Ask about enabled tools.' },
] as const;

function skillSlash(value: string) {
  return `/${value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')}`;
}

/** The server supplies exact-model choices; presentation never invents efforts. */
export default function ComposerControls({
  disabled = false,
  onError,
}: {
  disabled?: boolean;
  onError: (error: string) => void;
}) {
  const state = useClientState();
  const { controller } = useRuntime();
  const [saving, setSaving] = useState(false);
  const operation = useRef(false);
  const [thinkingOpen, setThinkingOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [skillsState, setSkillsState] = useState<
    'idle' | 'loading' | 'ready' | 'unavailable'
  >('idle');
  const [skillQuery, setSkillQuery] = useState('');
  const skillsRequest = useRef<AbortController | null>(null);
  const [budget, setBudget] = useState('');
  useEffect(
    () => () => {
      skillsRequest.current?.abort();
    },
    [],
  );
  const workspace = state.workspace;
  const controls = workspace?.controls;
  const id = state.selectedConversationId;
  if (!controls || !workspace || workspace.conversation_id !== id) return null;
  const blocked = disabled || saving || state.status !== 'ready';
  const reasoning =
    workspace.reasoning?.model_ref === controls.model_selection?.model_ref
      ? workspace.reasoning
      : null;
  const model = state.handshake?.models.find(
    (item) => item.model_ref === controls.model_selection?.model_ref,
  );
  const selectedModelRef = controls.model_selection?.model_ref;
  const selectedModelName = selectedModelRef?.startsWith('model:')
    ? selectedModelRef.split(':').slice(2).join(':') || selectedModelRef
    : selectedModelRef;
  const profile =
    workspace.profiles.find((item) => item.id === controls.profile_id)?.label ??
    'Default';
  const approval = { approve: 'Ask', block: 'Block', allow_all: 'Auto' }[
    controls.approval_mode ?? 'approve'
  ];
  async function loadSkills() {
    if (skillsState === 'loading') return;
    skillsRequest.current?.abort();
    const abort = new AbortController();
    skillsRequest.current = abort;
    setSkillsState('loading');
    try {
      const page = await controller.skills(
        '',
        undefined,
        undefined,
        abort.signal,
      );
      if (abort.signal.aborted) return;
      setSkills(
        page.items.filter((item) => item.available && !item.tool_guide),
      );
      setSkillsState(
        page.availability === 'available' ? 'ready' : 'unavailable',
      );
    } catch (cause) {
      if (abort.signal.aborted) return;
      setSkillsState('unavailable');
      onError(clientError(cause).message);
    }
  }
  function insertDiscovery(token: string) {
    if (!id) return;
    const draft = controller.getDraft(id);
    const spacing = draft.text && !/\s$/.test(draft.text) ? ' ' : '';
    controller.setDraft(id, {
      ...draft,
      text: `${draft.text}${spacing}${token} `,
    });
    onError('');
    setSkillsOpen(false);
  }
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
      if (controller.getSelectionVersion() === selectionVersion) {
        onError('');
        setThinkingOpen(false);
      }
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
  return (
    <div
      className="composer-control-cluster"
      role="group"
      aria-label="Conversation controls"
      aria-busy={saving}
    >
      <Menu
        label="Model"
        hint={`Model: ${model?.label ?? selectedModelName ?? 'Choose model'}`}
        className="composer-control"
        variant="ghost"
        disabled={blocked}
        actions={
          state.handshake?.models.length
            ? state.handshake.models.map((item) => ({
                label: item.label,
                disabled: !item.available,
                selected: item.model_ref === selectedModelRef,
                onSelect: () =>
                  void save({
                    model_selection: {
                      provider_id: item.provider_id,
                      model_ref: item.model_ref,
                    },
                  }),
              }))
            : [
                {
                  label: 'No cached models. Open Models in Settings.',
                  disabled: true,
                  onSelect: () => {},
                },
              ]
        }
      >
        <Cpu size={18} aria-hidden />
        <span title={model?.label ?? selectedModelName ?? 'Choose model'}>
          {model?.label ?? selectedModelName ?? 'Choose model'}
        </span>
      </Menu>
      {reasoning?.available && (
        <Popover.Root open={thinkingOpen} onOpenChange={setThinkingOpen}>
          <Hint label={`Thinking: ${thinkingLabel}`}>
            <Popover.Trigger asChild>
              <Button
                variant="ghost"
                className="composer-control"
                disabled={blocked}
                aria-label="Thinking"
                aria-description={thinkingLabel}
              >
                <Brain size={18} aria-hidden />
                <span>Thinking · {thinkingLabel}</span>
                <ChevronDown size={14} aria-hidden />
              </Button>
            </Popover.Trigger>
          </Hint>
          <Popover.Portal>
            <Popover.Content
              className="popover surface-effect thinking-menu"
              sideOffset={6}
              collisionPadding={12}
              aria-label="Thinking"
            >
              <strong className="thinking-menu-title">Thinking</strong>
              {reasoning.choices.map((choice) => (
                <Button
                  variant="ghost"
                  className="thinking-option"
                  key={JSON.stringify(choice.selection)}
                  disabled={blocked}
                  aria-pressed={choice.label === thinkingLabel}
                  onClick={() => void chooseThinking(choice.selection)}
                >
                  {choice.label}
                </Button>
              ))}
              {reasoning.supports_budget && (
                <>
                  <Field
                    label="Thinking budget"
                    hint={`${reasoning.budget_min ?? 1}–${reasoning.budget_max ?? 'maximum'} tokens`}
                  >
                    <Input
                      type="number"
                      min={reasoning.budget_min ?? 1}
                      max={reasoning.budget_max ?? undefined}
                      step={1}
                      value={budget}
                      onChange={(event) => setBudget(event.target.value)}
                    />
                  </Field>
                  <Button
                    disabled={
                      blocked ||
                      !Number.isSafeInteger(Number(budget)) ||
                      Number(budget) < (reasoning.budget_min ?? 1) ||
                      Number(budget) >
                        (reasoning.budget_max ?? Number.MAX_SAFE_INTEGER)
                    }
                    onClick={() =>
                      void chooseThinking({
                        kind: 'budget',
                        budget: Number(budget),
                      })
                    }
                  >
                    Use budget
                  </Button>
                </>
              )}
              {reasoning.stale && (
                <p role="status" className="composer-control-notice">
                  The saved Thinking choice is no longer available. Provider
                  default will be used.
                </p>
              )}
              <Popover.Close asChild>
                <Button variant="ghost">Close Thinking</Button>
              </Popover.Close>
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>
      )}
      <Popover.Root
        open={skillsOpen}
        onOpenChange={(open) => {
          setSkillsOpen(open);
          if (open && skillsState === 'idle') void loadSkills();
        }}
      >
        <Hint label="Skills and slash commands">
          <Popover.Trigger asChild>
            <Button
              variant="ghost"
              className="composer-control"
              disabled={blocked}
              aria-label="Skills and slash commands"
            >
              <Sparkles size={18} aria-hidden />
              <span>Skills</span>
              <ChevronDown size={14} aria-hidden />
            </Button>
          </Popover.Trigger>
        </Hint>
        <Popover.Portal>
          <Popover.Content
            className="popover surface-effect thinking-menu"
            sideOffset={6}
            collisionPadding={12}
            aria-label="Skills and slash commands"
          >
            <strong className="thinking-menu-title">
              Skills and slash commands
            </strong>
            <small>
              Choosing an item inserts text in your draft. It is sent as part of
              your message only after you review and send it.
            </small>
            <Field label="Filter skills">
              <Input
                value={skillQuery}
                placeholder="Search saved skills"
                onChange={(event) => setSkillQuery(event.target.value)}
              />
            </Field>
            {skillsState === 'loading' && (
              <small role="status">Loading skills…</small>
            )}
            {skillsState === 'unavailable' && (
              <small role="status">Saved skills are unavailable.</small>
            )}
            {skills
              .filter((item) =>
                `${item.display_name} ${item.description} ${item.id}`
                  .toLowerCase()
                  .includes(skillQuery.trim().toLowerCase()),
              )
              .slice(0, 12)
              .map((item) => {
                const token = skillSlash(item.id);
                return (
                  <Button
                    key={item.id}
                    variant="ghost"
                    className="thinking-option"
                    onClick={() => insertDiscovery(token)}
                  >
                    {item.icon} {item.display_name} · insert {token}
                  </Button>
                );
              })}
            <strong className="thinking-menu-title">Slash starters</strong>
            {SLASH_DISCOVERY.map((item) => (
              <Button
                key={item.token}
                variant="ghost"
                className="thinking-option"
                title={item.description}
                onClick={() => insertDiscovery(item.token)}
              >
                {item.token} · {item.label}
              </Button>
            ))}
            <Popover.Close asChild>
              <Button variant="ghost">Close skills</Button>
            </Popover.Close>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
      <Menu
        label="Approvals"
        hint={`Approvals: ${approval}`}
        variant="ghost"
        className="composer-control"
        disabled={blocked}
        actions={(['approve', 'block', 'allow_all'] as const).map((value) => ({
          label: { approve: 'Ask', block: 'Block', allow_all: 'Auto' }[value],
          selected: (controls.approval_mode ?? 'approve') === value,
          onSelect: () => void save({ approval_mode: value }),
        }))}
      >
        <ShieldCheck size={18} aria-hidden />
        <span>{approval}</span>
      </Menu>
      <Menu
        label="More conversation controls"
        hint={`Runtime: ${controls.runtime_mode === 'agent' ? 'Agent' : 'Chat only'}; Profile: ${profile}`}
        variant="ghost"
        className="composer-control composer-overflow"
        disabled={blocked}
        actions={[
          ...(['agent', 'chat_only'] as const).map((value) => ({
            label: `Runtime: ${value === 'agent' ? 'Agent' : 'Chat only'}${controls.runtime_mode === value ? ' (selected)' : ''}`,
            onSelect: () => void save({ runtime_mode: value }),
          })),
          ...[{ id: '', label: 'Default' }, ...workspace.profiles].map(
            (item) => ({
              label: `Profile: ${item.label}${controls.profile_id === item.id ? ' (selected)' : ''}`,
              onSelect: () => void save({ profile_id: item.id }),
            }),
          ),
        ]}
      >
        <SlidersHorizontal size={18} aria-hidden />
        <span>
          {controls.runtime_mode === 'agent' ? 'Agent' : 'Chat only'} ·{' '}
          {profile}
        </span>
      </Menu>
      {saving && (
        <small className="composer-control-status" role="status">
          Saving conversation controls…
        </small>
      )}
    </div>
  );
}
