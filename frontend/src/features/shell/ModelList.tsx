import {
  useEffect,
  useId,
  useMemo,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from 'react';
import { Check, HardDrive, Search } from 'lucide-react';
import type { ModelChoice } from '../../api/types';
import { Kbd, StatusDot } from '../../ui/primitives';
import {
  groupModels,
  matchesModel,
  readRecentModels,
  splitModelLabel,
} from './model-choices';

/** A list row: a model choice, with the plain reason when it is unavailable. */
export type ListedModel = ModelChoice & { reason?: string };
type Row = { key: string; model: ListedModel };
type Section = {
  key: string;
  label: string;
  connected: boolean;
  local: boolean;
  billing: ModelChoice['billing'];
  rows: Row[];
};

const BILLING_LABELS: Record<NonNullable<ModelChoice['billing']>, string> = {
  subscription: 'Subscription',
  pay_per_use: 'Pay per use',
  credits: 'Credits',
  local: 'Local · free',
};

/** How a provider is paid for, so one model reached two ways never surprises. */
export function billingLabel(billing: ModelChoice['billing']): string {
  return billing ? BILLING_LABELS[billing] : '';
}

export function BillingTag({ billing }: { billing: ModelChoice['billing'] }) {
  if (!billing) return null;
  return (
    <span className="model-billing" data-billing={billing}>
      {BILLING_LABELS[billing]}
    </span>
  );
}

/**
 * The one searchable model list (U12): the composer's picker, the default
 * model in Settings and Setup all use it. Grouped by provider with a status
 * dot and a billing tag, recent choices first, on-device providers marked,
 * and providers that are not connected muted with Connect.
 */
export default function ModelList({
  models,
  current,
  onChoose,
  onConnect,
  inputRef,
  showRecent = true,
  emptyText = 'No cached models. Open Models in Settings.',
}: {
  models: readonly ListedModel[];
  current: string | undefined;
  onChoose: (model: ModelChoice) => void;
  /** Open provider setup (for providers that are not connected). */
  onConnect?: () => void;
  inputRef?: RefObject<HTMLInputElement | null>;
  showRecent?: boolean;
  emptyText?: string;
}) {
  const id = useId();
  const [query, setQuery] = useState('');
  const [active, setActive] = useState<string | null>(null);
  const [recent] = useState<string[]>(() =>
    showRecent ? readRecentModels() : [],
  );
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
          billing: null,
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
        billing: group.models.find((model) => model.billing)?.billing ?? null,
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
    if (!activeRow) return;
    const target = optionId(activeRow.key);
    // Wait for placement to apply the height bound before revealing it.
    const frame = requestAnimationFrame(() =>
      document.getElementById(target)?.scrollIntoView?.({ block: 'nearest' }),
    );
    return () => cancelAnimationFrame(frame);
    // optionId is derived from rows, which the active key already tracks.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRow?.key]);
  const choose = (model: ModelChoice) => {
    if (model.available) onChoose(model);
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
  return (
    <>
      <div className="model-picker-search">
        <Search size={14} aria-hidden />
        <input
          ref={inputRef}
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
                  <span className="visually-hidden">Runs on this device</span>
                </span>
              )}
              {section.key !== 'recent' && (
                <BillingTag billing={section.billing} />
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
                  <span className="model-picker-option-name">{label.name}</span>
                  {!row.model.available && row.model.reason && (
                    <span
                      className="model-picker-option-reason"
                      title={row.model.reason}
                    >
                      {row.model.reason}
                    </span>
                  )}
                  {section.key === 'recent' && label.provider && (
                    <span className="model-picker-option-provider">
                      {label.provider}
                      {row.model.billing
                        ? ` · ${billingLabel(row.model.billing)}`
                        : ''}
                    </span>
                  )}
                  {!row.model.available && onConnect ? (
                    <button
                      type="button"
                      className="model-picker-connect"
                      tabIndex={-1}
                      onClick={(event) => {
                        event.stopPropagation();
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
          {models.length ? 'No models match.' : emptyText}
        </p>
      )}
    </>
  );
}
