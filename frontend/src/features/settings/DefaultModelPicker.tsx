import { useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import * as Popover from '@radix-ui/react-popover';
import { ChevronDown, Cloud, HardDrive } from 'lucide-react';
import type { ModelChoice, ModelPickerOption } from '../../api/types';
import { Button } from '../../ui/primitives';
import ModelList, { billingLabel } from '../shell/ModelList';
import {
  isLocalProvider,
  modelRefName,
  splitModelLabel,
} from '../shell/model-choices';

function providerOf(ref: string) {
  return ref.startsWith('model:') ? (ref.split(':')[1] ?? '') : '';
}

/**
 * The default model in Settings › Models: the same searchable list as the
 * composer's picker (U12), with billing tags so one model reached through
 * a subscription and a pay-per-use route is never confused (U13).
 */
export default function DefaultModelPicker({
  id,
  'aria-describedby': describedBy,
  current,
  models,
  options,
  disabled,
  onChoose,
}: {
  id?: string;
  'aria-describedby'?: string;
  current: string;
  /** The composer's model list (pinned choices). */
  models: readonly ModelChoice[];
  /** The Models page's own options, so the saved default is always listed. */
  options: readonly ModelPickerOption[];
  disabled?: boolean;
  onChoose: (ref: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const merged = useMemo(() => {
    const byRef = new Map(models.map((model) => [model.model_ref, model]));
    const billing = new Map(
      models
        .filter((model) => model.billing)
        .map((model) => [model.provider_id, model.billing]),
    );
    for (const option of options)
      if (!byRef.has(option.selection_ref)) {
        const provider = providerOf(option.selection_ref);
        byRef.set(option.selection_ref, {
          provider_id: provider || 'unknown',
          model_ref: option.selection_ref,
          label: option.label,
          available: option.available,
          billing: billing.get(provider) ?? null,
        });
      }
    return [...byRef.values()];
  }, [models, options]);
  const selected = merged.find((model) => model.model_ref === current);
  const name = selected
    ? splitModelLabel(selected.label).name
    : modelRefName(current) || 'Choose a model';
  const Glyph =
    selected && isLocalProvider(selected.provider_id) ? HardDrive : Cloud;
  const detail = selected
    ? [splitModelLabel(selected.label).provider, billingLabel(selected.billing)]
        .filter(Boolean)
        .join(' · ')
    : '';
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <Button
          id={id}
          aria-describedby={describedBy}
          className="settings-default-model"
          aria-haspopup="dialog"
          disabled={disabled}
        >
          {current && <Glyph size={14} aria-hidden />}
          <span className="settings-default-model-name">{name}</span>
          {detail && (
            <span className="settings-default-model-detail">{detail}</span>
          )}
          <ChevronDown size={14} aria-hidden />
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          className="model-picker surface-effect"
          side="bottom"
          align="end"
          sideOffset={6}
          collisionPadding={12}
          aria-label="Choose the default model"
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            input.current?.focus();
          }}
        >
          <ModelList
            models={merged}
            current={current}
            inputRef={input}
            showRecent={false}
            emptyText="No models yet."
            onChoose={(model) => {
              setOpen(false);
              if (model.model_ref !== current) onChoose(model.model_ref);
            }}
          />
          {!merged.some((model) => model.available) && (
            <div className="model-picker-footer">
              <Link className="button small" to="/setup">
                Set up a model
              </Link>
            </div>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
