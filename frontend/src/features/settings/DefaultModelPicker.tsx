import { useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import * as Popover from '@radix-ui/react-popover';
import { ChevronDown, Cloud, HardDrive, RefreshCw } from 'lucide-react';
import type { ModelPickerOption } from '../../api/types';
import { Button } from '../../ui/primitives';
import ModelList, { billingLabel, type ListedModel } from '../shell/ModelList';
import {
  isLocalProvider,
  modelRefName,
  splitModelLabel,
} from '../shell/model-choices';

/** A Models page option as a row of the shared list, with its reason. */
export function pickerModel(option: ModelPickerOption): ListedModel {
  return {
    provider_id: option.provider_id || 'unknown',
    model_ref: option.selection_ref,
    label: option.label,
    available: option.available,
    unavailable_reason: option.unavailable_reason ?? null,
    billing: option.billing ?? null,
    reason: option.available ? undefined : option.reason,
  };
}

/**
 * The default model in Settings › Models: the same searchable list as the
 * composer's picker (U12), with billing tags so one model reached through
 * a subscription and a pay-per-use route is never confused (U13). It lists
 * the page's own options, the one list the save accepts (B226).
 */
export default function DefaultModelPicker({
  id,
  'aria-describedby': describedBy,
  current,
  options,
  disabled,
  onChoose,
  onRefresh,
}: {
  id?: string;
  'aria-describedby'?: string;
  current: string;
  options: readonly ModelPickerOption[];
  disabled?: boolean;
  onChoose: (ref: string) => void;
  /** Refresh the saved catalog, offered while a model has no saved details. */
  onRefresh?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const models = useMemo(() => options.map(pickerModel), [options]);
  const selected = models.find((model) => model.model_ref === current);
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
  const needsRefresh =
    !!onRefresh &&
    models.some((model) => model.unavailable_reason === 'metadata_missing');
  const needsSetup = !models.some((model) => model.available);
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
            models={models}
            current={current}
            inputRef={input}
            showRecent={false}
            emptyText="No models yet."
            onChoose={(model) => {
              setOpen(false);
              if (model.model_ref !== current) onChoose(model.model_ref);
            }}
          />
          {(needsRefresh || needsSetup) && (
            <div className="model-picker-footer">
              {needsRefresh && (
                <Button
                  className="small"
                  onClick={() => {
                    setOpen(false);
                    onRefresh?.();
                  }}
                >
                  <RefreshCw size={14} aria-hidden />
                  Refresh the catalog
                </Button>
              )}
              {needsSetup && (
                <Link className="button small" to="/setup">
                  Set up a model
                </Link>
              )}
            </div>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
