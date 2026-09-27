import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from 'react';
import * as Popover from '@radix-ui/react-popover';
import { Check, ChevronDown, HardDrive, Search } from 'lucide-react';
import type {
  ModelChoice,
  ReasoningSelectionValue,
  ReasoningView,
} from '../../api/types';
import {
  Button,
  Disclosure,
  Hint,
  Input,
  Kbd,
  Segmented,
  Select,
  StatusDot,
} from '../../ui/primitives';
import {
  groupModels,
  matchesModel,
  modelRefName,
  readRecentModels,
  splitModelLabel,
} from './model-choices';

type Row = { key: string; model: ModelChoice };
type Section = {
  key: string;
  label: string;
  connected: boolean;
  local: boolean;
  rows: Row[];
};

function selectionKey(value: ReasoningSelectionValue) {
  return JSON.stringify(value);
}

/**
 * The composer's model pill and searchable picker: grouped by provider with a
 * status dot, recent choices on top, an on-device glyph for local providers,
 * unconnected providers muted with Connect, and Thinking in the footer.
 */
export default function ModelPicker({
  models,
  current,
  disabled,
  open,
  onOpenChange,
  onChoose,
  reasoning,
  thinkingLabel,
  onThinking,
  onConnect,
  onManage,
  anchor,
  returnFocusTo,
}: {
  models: readonly ModelChoice[];
  current: string | undefined;
  disabled: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChoose: (model: ModelChoice) => void;
  reasoning: ReasoningView | null;
  thinkingLabel: string;
  onThinking: (selection: ReasoningSelectionValue) => void;
  /** Open provider setup (for unconnected providers). */
  onConnect: () => void;
  /** Open the model catalog settings. */
  onManage: () => void;
  /**
   * Open over this element instead of from the pill: a one-line composer
   * keeps the model in its + menu, so the picker opens above the field.
   */
  anchor?: RefObject<HTMLElement | null>;
  /** Where focus goes when a triggerless picker closes. */
  returnFocusTo?: () => HTMLElement | null;
}) {
  const id = useId();
  const [query, setQuery] = useState('');
  const [active, setActive] = useState<string | null>(null);
  const [budget, setBudget] = useState('');
  const [recent, setRecent] = useState<string[]>([]);
  const list = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const selected = models.find((model) => model.model_ref === current);
  const name = selected
    ? splitModelLabel(selected.label).name
    : modelRefName(current) || 'Choose model';
  useEffect(() => {
    if (open) {
      setQuery('');
      setRecent(readRecentModels());
      setActive(null);
    }
  }, [open]);
  const sections = useMemo<Section[]>(() => {
    const matching = models.filter((model) => matchesModel(model, query));
    const result: Section[] = [];
    if (!query) {
      const recentModels = recent
        .map((ref) => models.find((model) => model.model_ref === ref))
        .filter((model): model is ModelChoice => Boolean(model?.available));
      if (recentModels.length)
        result.push({
          key: 'recent',
          label: 'Recent',
          connected: true,
          local: false,
          rows: recentModels.map((model) => ({
            key: `recent:${model.model_ref}`,
            model,
          })),
        });
    }
    const groups = groupModels(matching);
    const currentGroup = groups.findIndex((group) =>
      group.models.some((model) => model.model_ref === current),
    );
    if (currentGroup > 0) groups.unshift(...groups.splice(currentGroup, 1));
    for (const group of groups)
      result.push({
        key: `provider:${group.id}`,
        label: group.label,
        connected: group.connected,
        local: group.local,
        rows: group.models.map((model) => ({
          key: `${group.id}:${model.model_ref}`,
          model,
        })),
      });
    return result;
  }, [current, models, query, recent]);
  const rows = sections.flatMap((section) => section.rows);
  const enabled = rows.filter((row) => row.model.available);
  const activeRow =
    enabled.find((row) => row.key === active) ??
    (active === null
      ? (enabled.find((row) => row.model.model_ref === current) ?? enabled[0])
      : enabled[0]) ??
    null;
  const optionId = (key: string) =>
    `${id}-option-${rows.findIndex((row) => row.key === key)}`;
  useEffect(() => {
    if (!open || !activeRow) return;
    const target = optionId(activeRow.key);
    // Wait for placement to apply the height bound before revealing it.
    const frame = requestAnimationFrame(() =>
      list.current
        ?.querySelector<HTMLElement>(`[id="${target}"]`)
        ?.scrollIntoView?.({ block: 'nearest' }),
    );
    return () => cancelAnimationFrame(frame);
    // optionId is derived from rows, which the active key already tracks.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, activeRow?.key]);
  const choose = (model: ModelChoice) => {
    if (!model.available) return;
    onChoose(model);
    onOpenChange(false);
  };
  const keyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing || !enabled.length) return;
    const index = activeRow ? enabled.indexOf(activeRow) : -1;
    const move = (next: number) => {
      event.preventDefault();
      setActive(enabled[Math.max(0, Math.min(enabled.length - 1, next))].key);
    };
    if (event.key === 'ArrowDown')
      move(index + 1 >= enabled.length ? 0 : index + 1);
    else if (event.key === 'ArrowUp')
      move(index - 1 < 0 ? enabled.length - 1 : index - 1);
    else if (event.key === 'PageDown') move(index + 8);
    else if (event.key === 'PageUp') move(index - 8);
    else if (event.key === 'Enter') {
      event.preventDefault();
      if (activeRow) choose(activeRow.model);
    }
  };
  const choices = reasoning?.choices ?? [];
  const short = (label: string) =>
    label === 'Provider default' ? 'Default' : label;
  const segmented =
    choices.length > 1 &&
    choices.length <= 5 &&
    choices.every((choice) => short(choice.label).length <= 12);
  const currentThinking = reasoning ? selectionKey(reasoning.selection) : '';
  const hint = `Model: ${name}${reasoning?.available ? ` · Thinking: ${thinkingLabel}` : ''}`;
  return (
    <Popover.Root open={open} onOpenChange={onOpenChange}>
      {anchor ? (
        <Popover.Anchor virtualRef={anchor as RefObject<HTMLElement>} />
      ) : (
        <Hint label={hint}>
          <Popover.Trigger asChild>
            <Button
              variant="ghost"
              className="composer-model-pill"
              aria-label="Model"
              aria-description={hint}
              aria-haspopup="dialog"
              disabled={disabled}
            >
              <StatusDot
                tone={selected?.available === false ? 'warning' : 'success'}
                label={selected?.available === false ? 'Needs setup' : 'Ready'}
              />
              <span className="composer-model-name">{name}</span>
              <ChevronDown size={14} aria-hidden />
            </Button>
          </Popover.Trigger>
        </Hint>
      )}
      <Popover.Portal>
        <Popover.Content
          className="model-picker surface-effect"
          side="top"
          align="start"
          sideOffset={8}
          collisionPadding={12}
          aria-label="Choose a model"
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            input.current?.focus();
          }}
          onCloseAutoFocus={(event) => {
            const target = returnFocusTo?.();
            if (!target?.isConnected) return;
            event.preventDefault();
            target.focus({ preventScroll: true });
          }}
        >
          <div className="model-picker-search">
            <Search size={14} aria-hidden />
            <input
              ref={input}
              className="model-picker-input"
              role="combobox"
              aria-label="Search models"
              aria-expanded
              aria-controls={`${id}-listbox`}
              aria-autocomplete="list"
              aria-activedescendant={
                activeRow ? optionId(activeRow.key) : undefined
              }
              placeholder="Search models"
              value={query}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => {
                setQuery(event.target.value);
                setActive(null);
              }}
              onKeyDown={keyDown}
            />
            <span aria-hidden>
              <Kbd keys="Esc" />
            </span>
          </div>
          <div
            ref={list}
            id={`${id}-listbox`}
            role="listbox"
            aria-label="Models"
            className="model-picker-list"
          >
            {sections.map((section, sectionIndex) => (
              <div
                key={section.key}
                role="group"
                aria-labelledby={`${id}-group-${sectionIndex}`}
                className="model-picker-group"
                data-connected={section.connected ? 'true' : 'false'}
              >
                <div
                  id={`${id}-group-${sectionIndex}`}
                  className="model-picker-group-label"
                  role="presentation"
                >
                  <span>{section.label}</span>
                  {section.key !== 'recent' && (
                    <StatusDot
                      tone={section.connected ? 'success' : 'neutral'}
                      label={section.connected ? 'Connected' : 'Not connected'}
                      showLabel={!section.connected}
                    />
                  )}
                  {section.local && section.key !== 'recent' && (
                    <span
                      className="model-picker-local"
                      title="Runs on this device"
                    >
                      <HardDrive size={12} aria-hidden />
                      <span className="visually-hidden">
                        Runs on this device
                      </span>
                    </span>
                  )}
                </div>
                {section.rows.map((row) => {
                  const label = splitModelLabel(row.model.label);
                  const isCurrent = row.model.model_ref === current;
                  return (
                    <div
                      key={row.key}
                      id={optionId(row.key)}
                      role="option"
                      className="model-picker-option"
                      aria-selected={row.key === activeRow?.key}
                      aria-disabled={!row.model.available || undefined}
                      data-current={isCurrent ? 'true' : undefined}
                      onMouseDown={(event) => event.preventDefault()}
                      onMouseMove={() => {
                        if (row.model.available && active !== row.key)
                          setActive(row.key);
                      }}
                      onClick={() => choose(row.model)}
                    >
                      <span className="model-picker-option-name">
                        {label.name}
                      </span>
                      {section.key === 'recent' && label.provider && (
                        <span className="model-picker-option-provider">
                          {label.provider}
                        </span>
                      )}
                      {!row.model.available ? (
                        <button
                          type="button"
                          className="model-picker-connect"
                          tabIndex={-1}
                          onClick={(event) => {
                            event.stopPropagation();
                            onOpenChange(false);
                            onConnect();
                          }}
                        >
                          Connect
                        </button>
                      ) : (
                        isCurrent && (
                          <Check
                            className="model-picker-check"
                            size={14}
                            role="img"
                            aria-label="Current"
                          />
                        )
                      )}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
          {!rows.length && (
            <p className="model-picker-empty" role="status">
              {models.length
                ? 'No models match.'
                : 'No cached models. Open Models in Settings.'}
            </p>
          )}
          <div className="model-picker-footer">
            {reasoning?.available && (
              <div className="model-picker-thinking">
                <span className="model-picker-footer-label">Thinking</span>
                {segmented ? (
                  <Segmented
                    size="sm"
                    label="Thinking"
                    value={currentThinking}
                    onChange={(value) =>
                      onThinking(
                        choices.find(
                          (choice) => selectionKey(choice.selection) === value,
                        )!.selection,
                      )
                    }
                    options={choices.map((choice) => ({
                      value: selectionKey(choice.selection),
                      label: short(choice.label),
                    }))}
                  />
                ) : (
                  <Select
                    aria-label="Thinking"
                    value={currentThinking}
                    onChange={(event) =>
                      onThinking(
                        choices.find(
                          (choice) =>
                            selectionKey(choice.selection) ===
                            event.target.value,
                        )?.selection ?? reasoning.selection,
                      )
                    }
                  >
                    {!choices.some(
                      (choice) =>
                        selectionKey(choice.selection) === currentThinking,
                    ) && (
                      <option value={currentThinking}>{thinkingLabel}</option>
                    )}
                    {choices.map((choice) => (
                      <option
                        key={selectionKey(choice.selection)}
                        value={selectionKey(choice.selection)}
                      >
                        {choice.label}
                      </option>
                    ))}
                  </Select>
                )}
              </div>
            )}
            {reasoning?.available && reasoning.supports_budget && (
              <Disclosure
                summary="Thinking budget"
                className="model-picker-budget"
              >
                <div className="model-picker-budget-row">
                  <Input
                    type="number"
                    aria-label="Thinking budget"
                    min={reasoning.budget_min ?? 1}
                    max={reasoning.budget_max ?? undefined}
                    step={1}
                    placeholder={`${reasoning.budget_min ?? 1}–${reasoning.budget_max ?? 'max'} tokens`}
                    value={budget}
                    onChange={(event) => setBudget(event.target.value)}
                  />
                  <Button
                    disabled={
                      !Number.isSafeInteger(Number(budget)) ||
                      Number(budget) < (reasoning.budget_min ?? 1) ||
                      Number(budget) >
                        (reasoning.budget_max ?? Number.MAX_SAFE_INTEGER)
                    }
                    onClick={() =>
                      onThinking({ kind: 'budget', budget: Number(budget) })
                    }
                  >
                    Use budget
                  </Button>
                </div>
              </Disclosure>
            )}
            {reasoning?.stale && (
              <p role="status" className="composer-control-notice">
                The saved Thinking choice is no longer available. Provider
                default will be used.
              </p>
            )}
            <Button
              variant="ghost"
              className="model-picker-manage"
              onClick={() => {
                onOpenChange(false);
                onManage();
              }}
            >
              Manage models
            </Button>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
