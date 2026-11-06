import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from 'react';
import { Check, HardDrive, Search, Star } from 'lucide-react';
import type { ModelChoice } from '../../api/types';
import { usePlace } from '../../api/place';
import { Kbd, StatusDot } from '../../ui/primitives';
import {
  groupModels,
  matchesModel,
  readRecentModels,
  splitModelLabel,
} from './model-choices';

/**
 * A list row: a model choice, with the plain reason when it is unavailable
 * and an optional muted note after its name.
 */
export type ListedModel = ModelChoice & { reason?: string; detail?: string };
/** What a row's star changes: your models are the starred ones. */
export type VisibilityAction = 'favourite' | 'unfavourite';
type Row = { key: string; kind: 'model'; model: ListedModel };
type Section = {
  key: string;
  label: string;
  connected: boolean;
  local: boolean;
  billing: ModelChoice['billing'];
  /** Rows from several providers, so each names its own. */
  mixed?: boolean;
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

const NO_ROWS: readonly ListedModel[] = [];
const isFavourite = (model: ModelChoice) =>
  model.favourite_rank != null && model.available;
const byRank = (a: ModelChoice, b: ModelChoice) =>
  (a.favourite_rank ?? 0) - (b.favourite_rank ?? 0);
const modelRows = (prefix: string, models: readonly ListedModel[]): Row[] =>
  models.map((model) => ({
    key: `${prefix}:${model.model_ref}`,
    kind: 'model',
    model,
  }));

/**
 * The one searchable model list (U12): the composer's picker, the default
 * models in Settings and Setup all use it. Your starred models first, then
 * recent choices, then the providers with a status dot and a billing tag,
 * on-device providers marked and providers that are not connected muted with
 * Connect. The current model is always in view.
 */
export default function ModelList({
  models,
  leading = NO_ROWS,
  current,
  onChoose,
  onConnect,
  onVisibility,
  inputRef,
  showRecent = true,
  emptyText = 'No cached models. Open Models in Settings.',
}: {
  models: readonly ListedModel[];
  /** Choices listed first, outside the provider groups ("Same as Brain"). */
  leading?: readonly ListedModel[];
  current: string | undefined;
  onChoose: (model: ModelChoice) => void;
  /** Open provider setup (for providers that are not connected). */
  onConnect?: () => void;
  /** Star or unstar a model from its row; rows have no star without it. */
  onVisibility?: (model: ModelChoice, action: VisibilityAction) => void;
  inputRef?: RefObject<HTMLInputElement | null>;
  showRecent?: boolean;
  emptyText?: string;
}) {
  const place = usePlace();
  const runsHere =
    place.kind === 'this_computer'
      ? 'Runs on this device'
      : `Runs on ${place.name}`;
  const id = useId();
  const ownInput = useRef<HTMLInputElement>(null);
  const input = inputRef ?? ownInput;
  const [query, setQuery] = useState('');
  const [active, setActive] = useState<string | null>(null);
  const [recent] = useState<string[]>(() =>
    showRecent ? readRecentModels() : [],
  );
  const sections = useMemo<Section[]>(() => {
    const matching = models.filter((model) => matchesModel(model, query));
    const result: Section[] = [];
    const first = leading.filter((model) => matchesModel(model, query));
    if (first.length)
      result.push({
        key: 'leading',
        label: '',
        connected: true,
        local: false,
        billing: null,
        rows: modelRows('leading', first),
      });
    const favourites = matching.filter(isFavourite).sort(byRank);
    if (favourites.length)
      result.push({
        key: 'favourites',
        label: 'Your models',
        connected: true,
        local: false,
        billing: null,
        mixed: true,
        rows: modelRows('favourite', favourites),
      });
    if (!query) {
      const recentModels = recent
        .map((ref) => models.find((model) => model.model_ref === ref))
        .filter(
          (model): model is ListedModel =>
            Boolean(model?.available) && !isFavourite(model!),
        );
      if (recentModels.length)
        result.push({
          key: 'recent',
          label: 'Recent',
          connected: true,
          local: false,
          billing: null,
          mixed: true,
          rows: modelRows('recent', recentModels),
        });
    }
    const groups = groupModels(matching.filter((model) => !isFavourite(model)));
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
        rows: modelRows(group.id, group.models),
      });
    return result;
  }, [current, leading, models, query, recent]);
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
  const choose = (row: Row) => {
    if (row.model.available) onChoose(row.model);
  };
  const toggleStar = (row: Row) => {
    if (!onVisibility) return;
    // The highlight moves on to the next row, as the changed one moves away.
    if (row.key === activeRow?.key) {
      const index = enabled.indexOf(row);
      setActive((enabled[index + 1] ?? enabled[index - 1])?.key ?? null);
    }
    onVisibility(
      row.model,
      row.model.favourite_rank != null ? 'unfavourite' : 'favourite',
    );
  };
  const hasStar = (section: Section, row: Row) =>
    !!onVisibility && section.key !== 'leading' && !!row.model.model_ref;
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
      if (activeRow) choose(activeRow);
    } else if (
      (event.key === 'ContextMenu' ||
        (event.key === 'F10' && event.shiftKey)) &&
      activeRow &&
      onVisibility &&
      activeRow.model.model_ref &&
      !activeRow.key.startsWith('leading:')
    ) {
      event.preventDefault();
      toggleStar(activeRow);
    }
  };
  const option = (section: Section, row: Row) => {
    const label = splitModelLabel(row.model.label);
    const isCurrent = row.model.model_ref === current;
    const choice = (
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
          if (row.model.available && active !== row.key) setActive(row.key);
        }}
        onClick={() => choose(row)}
      >
        <span className="model-picker-option-name">{label.name}</span>
        {row.model.personal && (
          // Learning (preview, D11): one of the person's own trained models.
          <span className="model-billing" data-billing="personal">
            Personal
          </span>
        )}
        {row.model.detail && (
          <span className="model-picker-option-provider">
            {row.model.detail}
          </span>
        )}
        {!row.model.available && row.model.reason && (
          <span className="model-picker-option-reason" title={row.model.reason}>
            {row.model.reason}
          </span>
        )}
        {section.mixed && label.provider && (
          <span className="model-picker-option-provider">
            {label.provider}
            {/* "Ollama Local" already says it runs here, for free. */}
            {row.model.billing && row.model.billing !== 'local'
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
    if (!hasStar(section, row)) return choice;
    // The star sits beside the option, not inside it: an option holds no
    // controls. It is the pointer's way in; the keyboard's is Shift+F10 (said
    // on the search field), so it stays out of the accessibility tree.
    const starred = row.model.favourite_rank != null;
    return (
      <div
        key={row.key}
        className="model-picker-row"
        role="presentation"
        data-starred={starred ? 'true' : undefined}
      >
        {choice}
        <span className="model-picker-row-menu" aria-hidden>
          <button
            type="button"
            className="model-picker-row-menu-button"
            tabIndex={-1}
            aria-label={
              starred
                ? `Remove ${label.name} from your models`
                : `Add ${label.name} to your models`
            }
            title={starred ? 'Remove from your models' : 'Add to your models'}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => {
              toggleStar(row);
              input.current?.focus();
            }}
          >
            <Star size={14} fill={starred ? 'currentColor' : 'none'} />
          </button>
        </span>
      </div>
    );
  };
  return (
    <>
      <div className="model-picker-search">
        <Search size={14} aria-hidden />
        <input
          ref={input}
          className="model-picker-input"
          role="combobox"
          aria-label="Search models"
          aria-description={
            onVisibility
              ? 'Shift+F10 stars or unstars the highlighted model.'
              : undefined
          }
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
        {sections.map((section, sectionIndex) =>
          section.key === 'leading' ? (
            section.rows.map((row) => option(section, row))
          ) : (
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
                {section.key.startsWith('provider:') && (
                  <StatusDot
                    tone={section.connected ? 'success' : 'neutral'}
                    label={section.connected ? 'Connected' : 'Not connected'}
                    showLabel={!section.connected}
                  />
                )}
                {section.local && section.key.startsWith('provider:') && (
                  <span className="model-picker-local" title={runsHere}>
                    <HardDrive size={12} aria-hidden />
                    <span className="visually-hidden">{runsHere}</span>
                  </span>
                )}
                {section.key.startsWith('provider:') && (
                  <BillingTag billing={section.billing} />
                )}
              </div>
              {section.rows.map((row) => option(section, row))}
            </div>
          ),
        )}
      </div>
      {!rows.length && (
        <p className="model-picker-empty" role="status">
          {models.length || leading.length ? 'No models match.' : emptyText}
        </p>
      )}
    </>
  );
}
