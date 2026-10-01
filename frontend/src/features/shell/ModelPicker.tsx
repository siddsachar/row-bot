import { useRef, useState, type RefObject } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { AlertTriangle, ChevronDown, Cloud, HardDrive } from 'lucide-react';
import type {
  ConversationModelStatus,
  ModelChoice,
  ReasoningSelectionValue,
  ReasoningView,
} from '../../api/types';
import {
  Button,
  Disclosure,
  Hint,
  Input,
  Segmented,
  Select,
  StatusDot,
} from '../../ui/primitives';
import ModelList from './ModelList';
import {
  isLocalProvider,
  modelRefName,
  splitModelLabel,
} from './model-choices';

function selectionKey(value: ReasoningSelectionValue) {
  return JSON.stringify(value);
}

/**
 * The composer's model pill and picker (parity row 6): a local/cloud glyph,
 * "Chat only" when tools are off, and the unavailable state with its reason
 * and one fix ("Reconnect", "Choose another model"); never "Ready" for a
 * model that cannot answer (B115). The list itself is the shared ModelList.
 */
export default function ModelPicker({
  models,
  current,
  status,
  runtimeMode,
  disabled,
  open,
  onOpenChange,
  onChoose,
  reasoning,
  thinkingLabel,
  onThinking,
  onConnect,
  onReconnect,
  onManage,
  onSetup,
  anchor,
  returnFocusTo,
}: {
  models: readonly ModelChoice[];
  current: string | undefined;
  /** The server's view of the conversation's model (ready, unavailable, missing). */
  status?: ConversationModelStatus | null;
  runtimeMode?: string;
  disabled: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChoose: (model: ModelChoice) => void;
  reasoning: ReasoningView | null;
  thinkingLabel: string;
  onThinking: (selection: ReasoningSelectionValue) => void;
  /** Open provider setup (for unconnected providers). */
  onConnect: () => void;
  /** Reconnect the conversation's provider (defaults to onConnect). */
  onReconnect?: () => void;
  /** Open the model catalog settings. */
  onManage: () => void;
  /** Open Setup when no model can be chosen yet (a fresh profile). */
  onSetup?: () => void;
  /**
   * Open over this element instead of from the pill: a one-line composer
   * keeps the model in its + menu, so the picker opens above the field.
   */
  anchor?: RefObject<HTMLElement | null>;
  /** Where focus goes when a triggerless picker closes. */
  returnFocusTo?: () => HTMLElement | null;
}) {
  const [budget, setBudget] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const selected = models.find((model) => model.model_ref === current);
  const missing = status?.state === 'missing' || !current;
  const unavailable =
    status?.state === 'unavailable' ||
    (!status && selected?.available === false);
  const name = missing
    ? 'Choose a model'
    : selected
      ? splitModelLabel(selected.label).name
      : modelRefName(current) || 'Choose a model';
  const local =
    status?.local ?? (selected ? isLocalProvider(selected.provider_id) : false);
  const chatOnly = runtimeMode === 'chat_only';
  const choices = reasoning?.choices ?? [];
  const short = (label: string) =>
    label === 'Provider default' ? 'Default' : label;
  const segmented =
    choices.length > 1 &&
    choices.length <= 5 &&
    choices.every((choice) => short(choice.label).length <= 12);
  const currentThinking = reasoning ? selectionKey(reasoning.selection) : '';
  const reason = unavailable
    ? `Unavailable — ${status?.reason || "it isn't ready right now"}`
    : '';
  const hint = missing
    ? 'No model chosen yet'
    : `Model: ${name}${chatOnly ? ' · Chat only' : ''}${reason ? ` · ${reason}` : ''}${reasoning?.available ? ` · Thinking: ${thinkingLabel}` : ''}`;
  const Glyph = local ? HardDrive : Cloud;
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
              data-state-model={
                missing ? 'missing' : unavailable ? 'unavailable' : 'ready'
              }
            >
              {missing ? (
                <StatusDot tone="neutral" label="No model yet" />
              ) : unavailable ? (
                <AlertTriangle
                  className="composer-model-warning"
                  size={13}
                  role="img"
                  aria-label="Unavailable"
                />
              ) : (
                <Glyph
                  className="composer-model-glyph"
                  size={13}
                  role="img"
                  aria-label={local ? 'Runs on this device' : 'Cloud model'}
                />
              )}
              <span className="composer-model-name">{name}</span>
              {chatOnly && !missing && (
                <span className="composer-model-tag">Chat only</span>
              )}
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
          {unavailable && (
            <div className="model-picker-unavailable" role="status">
              <AlertTriangle size={14} aria-hidden />
              <p>
                <strong>{name}</strong> · {reason}
              </p>
              <div className="model-picker-unavailable-actions">
                {status?.fix === 'reconnect' && (
                  <Button
                    className="small"
                    onClick={() => {
                      onOpenChange(false);
                      (onReconnect ?? onConnect)();
                    }}
                  >
                    Reconnect
                  </Button>
                )}
                <Button
                  className="small"
                  variant="ghost"
                  onClick={() => input.current?.focus()}
                >
                  Choose another model
                </Button>
              </div>
            </div>
          )}
          <ModelList
            models={models}
            current={current}
            inputRef={input}
            emptyText={
              onSetup
                ? 'No models yet. Choose how Row-Bot thinks in Setup.'
                : undefined
            }
            onChoose={(model) => {
              onChoose(model);
              onOpenChange(false);
            }}
            onConnect={() => {
              onOpenChange(false);
              onConnect();
            }}
          />
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
            {onSetup && !models.some((model) => model.available) ? (
              <Button
                variant="ghost"
                className="model-picker-manage"
                onClick={() => {
                  onOpenChange(false);
                  onSetup();
                }}
              >
                Set up a model
              </Button>
            ) : (
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
            )}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
