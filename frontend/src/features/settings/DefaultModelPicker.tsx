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
 * A default model in Settings › Models (Brain, Vision, Image, Video): the
 * same searchable list as the composer's picker (U12, B227), with billing
 * tags so one model reached through a subscription and a pay-per-use route
 * is never confused (U13). It lists the page's own options, the one list
 * the save accepts (B226).
 */
export default function DefaultModelPicker({
  id,
  'aria-describedby': describedBy,
  ariaLabel,
  current,
  options,
  follow,
  dialogLabel = 'Choose the default model',
  disabled,
  onChoose,
  onRefresh,
}: {
  id?: string;
  'aria-describedby'?: string;
  /** The button's name when no visible label names it ("Vision model"). */
  ariaLabel?: string;
  current: string;
  options: readonly ModelPickerOption[];
  /** A first choice saved as "" (Vision's "Same as Brain", decision 11). */
  follow?: { label: string; detail: string };
  dialogLabel?: string;
  disabled?: boolean;
  onChoose: (ref: string) => void;
  /** Refresh the saved catalog, offered while a model has no saved details. */
  onRefresh?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const models = useMemo(() => options.map(pickerModel), [options]);
  const followLabel = follow?.label;
  const followDetail = follow?.detail;
  const leading = useMemo<ListedModel[]>(
    () =>
      followLabel === undefined
        ? []
        : [
            {
              provider_id: '',
              model_ref: '',
              label: followLabel,
              detail: followDetail,
              available: true,
            },
          ],
    [followLabel, followDetail],
  );
  const selected = [...leading, ...models].find(
    (model) => model.model_ref === current,
  );
  const name = selected
    ? splitModelLabel(selected.label).name
    : modelRefName(current) || 'Choose a model';
  const Glyph =
    selected && isLocalProvider(selected.provider_id) ? HardDrive : Cloud;
  const detail = selected
    ? (selected.detail ??
      [splitModelLabel(selected.label).provider, billingLabel(selected.billing)]
        .filter(Boolean)
        .join(' · '))
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
          aria-label={ariaLabel}
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
          aria-label={dialogLabel}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            input.current?.focus();
          }}
        >
          <ModelList
            models={models}
            leading={leading}
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
