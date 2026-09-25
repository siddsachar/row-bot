import {
  forwardRef,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  type RefObject,
  type SelectHTMLAttributes,
} from 'react';
import * as Tooltip from '@radix-ui/react-tooltip';
import * as Dropdown from '@radix-ui/react-dropdown-menu';
import * as Popover from '@radix-ui/react-popover';
import * as TabsPrimitive from '@radix-ui/react-tabs';
import {
  ChevronDown,
  ChevronRight,
  AlertCircle,
  Info,
  Check,
  MoreHorizontal,
  Search,
} from 'lucide-react';
import { ariaKeyShortcut, shortcutKeys, type ShortcutPlatform } from './format';
export { Brand } from './Brand';

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
    iconOnly?: boolean;
  }
>(function Button(
  {
    variant = 'secondary',
    iconOnly,
    className = '',
    type = 'button',
    ...props
  },
  ref,
) {
  const pointerFocusPrevented = useRef(false);
  return (
    <button
      ref={ref}
      type={type}
      className={`button ${variant} ${iconOnly ? 'icon-button' : ''} ${className}`}
      {...props}
      onPointerDown={(event) => {
        props.onPointerDown?.(event);
        pointerFocusPrevented.current = event.defaultPrevented;
        // WebKit does not focus pointer-clicked buttons. Record a real opener
        // before imperative dialogs run, respecting Radix's focus decisions.
        if (event.button === 0 && !event.defaultPrevented && !props.disabled)
          event.currentTarget.focus({ preventScroll: true });
      }}
      onClick={(event) => {
        // WebKit's native mousedown can blur the pointerdown focus. Capture
        // the opener at click time before an imperative overlay opens.
        if (
          event.detail > 0 &&
          event.button === 0 &&
          !pointerFocusPrevented.current &&
          !props.disabled
        )
          event.currentTarget.focus({ preventScroll: true });
        props.onClick?.(event);
      }}
    />
  );
});
export const CompactAction = forwardRef<
  HTMLButtonElement,
  Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label'> & {
    label: string;
    variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  }
>(function CompactAction(
  { label, children, variant = 'ghost', className = '', ...props },
  ref,
) {
  return (
    <Hint label={label}>
      <Button
        ref={ref}
        iconOnly
        aria-label={label}
        variant={variant}
        className={`compact-action ${className}`}
        {...props}
      >
        {children}
      </Button>
    </Hint>
  );
});
export const Input = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement>
>(function Input({ className = '', ...props }, ref) {
  return <input ref={ref} className={`input ${className}`} {...props} />;
});
export const Toggle = forwardRef<
  HTMLInputElement,
  Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & { label: string }
>(function Toggle({ label, className = '', checked, ...props }, ref) {
  return (
    <span className={`toggle-control ${className}`}>
      <input
        ref={ref}
        type="checkbox"
        role="switch"
        aria-label={label}
        checked={checked}
        {...props}
      />
      <span className="toggle-track" aria-hidden="true" />
      <span className="toggle-state" aria-hidden="true">
        {checked ? 'On' : 'Off'}
      </span>
    </span>
  );
});
export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...props} className={`input select ${props.className ?? ''}`} />
  );
}
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
export function Hint({
  label,
  shortcut,
  children,
}: {
  label: string;
  /** Optional shortcut such as "Mod+K", shown as keycaps after the label. */
  shortcut?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const focusOwned = useRef(false);
  const pointerDown = useRef(false);
  const dismiss = () => {
    focusOwned.current = false;
    setOpen(false);
  };
  return (
    <Tooltip.Provider delayDuration={400}>
      <Tooltip.Root
        open={open}
        onOpenChange={(next) => {
          // Native focus can scroll a compact drawer after Radix opens the
          // tooltip. Its ancestor-scroll dismissal must not erase a keyboard
          // user's full label while that trigger still owns focus.
          if (next || !focusOwned.current) setOpen(next);
        }}
      >
        <Tooltip.Trigger
          asChild
          onFocus={() => {
            if (!pointerDown.current) {
              focusOwned.current = true;
              setOpen(true);
            }
          }}
          onBlur={() => {
            pointerDown.current = false;
            dismiss();
          }}
          onPointerDown={() => {
            pointerDown.current = true;
            dismiss();
          }}
          onPointerUp={() => {
            pointerDown.current = false;
          }}
          onPointerCancel={() => {
            pointerDown.current = false;
          }}
          onClick={dismiss}
        >
          {children}
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <div className="tooltip-layer">
            <Tooltip.Content
              className="tooltip"
              sideOffset={6}
              collisionPadding={12}
              onEscapeKeyDown={dismiss}
              onPointerDownOutside={dismiss}
            >
              {label}
              {shortcut && <Kbd keys={shortcut} className="tooltip-kbd" />}
            </Tooltip.Content>
          </div>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
export type MenuAction = {
  label: string;
  onSelect: (opener: HTMLButtonElement | null) => void;
  disabled?: boolean;
  danger?: boolean;
  selected?: boolean;
};
export function Menu({
  label,
  actions,
  children,
  triggerRef,
  focusAfterClose,
  disabled,
  className,
  variant,
  hint,
  iconOnly,
}: {
  label: string;
  actions: MenuAction[];
  children?: ReactNode;
  triggerRef?: RefObject<HTMLButtonElement | null>;
  focusAfterClose?: () => HTMLElement | null;
  disabled?: boolean;
  className?: string;
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  hint?: string;
  iconOnly?: boolean;
}) {
  const opener = useRef<HTMLButtonElement>(null);
  const trigger = (
    <Dropdown.Trigger asChild>
      <Button
        ref={(element) => {
          opener.current = element;
          if (triggerRef) triggerRef.current = element;
        }}
        aria-label={label}
        aria-description={hint}
        disabled={disabled}
        className={className}
        variant={variant}
        iconOnly={iconOnly}
      >
        {children ?? label}
        {!iconOnly && <ChevronDown size={16} aria-hidden />}
      </Button>
    </Dropdown.Trigger>
  );
  return (
    <Dropdown.Root>
      {hint ? <Hint label={hint}>{trigger}</Hint> : trigger}
      <Dropdown.Portal>
        <Dropdown.Content
          className="menu surface-effect"
          sideOffset={6}
          collisionPadding={12}
          ref={(node) => {
            // Long menus scroll inside the viewport (B3); reveal the current
            // choice once placement has applied the available-height bound.
            if (!node) return;
            requestAnimationFrame(() =>
              node
                .querySelector<HTMLElement>('[aria-current="true"]')
                ?.scrollIntoView?.({ block: 'nearest' }),
            );
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const target = focusAfterClose?.() ?? opener.current;
            if (target?.isConnected) target.focus({ preventScroll: true });
          }}
        >
          {actions.map((action) => (
            <Dropdown.Item
              key={action.label}
              className={`menu-item ${action.danger ? 'danger-text' : ''}`}
              disabled={action.disabled}
              aria-current={action.selected ? true : undefined}
              onSelect={() => {
                // A modal menu can trap focus until it unmounts. Pass the
                // connected trigger explicitly to any task opened by an item.
                action.onSelect(opener.current);
              }}
            >
              <span className="menu-item-label">{action.label}</span>
              {action.selected && <Check size={16} aria-hidden />}
            </Dropdown.Item>
          ))}
        </Dropdown.Content>
      </Dropdown.Portal>
    </Dropdown.Root>
  );
}
export function Popup({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <Button>{label}</Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          aria-label={label}
          className="popover surface-effect"
          sideOffset={8}
          collisionPadding={12}
        >
          {children}
          <Popover.Close asChild>
            <Button>Close {label.toLowerCase()}</Button>
          </Popover.Close>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
export function Tabs({
  label,
  value,
  onChange,
  items,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  items: { id: string; label: ReactNode; content: ReactNode }[];
}) {
  return (
    <TabsPrimitive.Root value={value} onValueChange={onChange}>
      <TabsPrimitive.List className="tabs" aria-label={label}>
        {items.map((item) => (
          <TabsPrimitive.Trigger className="tab" key={item.id} value={item.id}>
            {item.label}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
      {items.map((item) => (
        <TabsPrimitive.Content
          key={item.id}
          value={item.id}
          className="tab-content"
        >
          {item.content}
        </TabsPrimitive.Content>
      ))}
    </TabsPrimitive.Root>
  );
}
export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <Info size={24} aria-hidden />
      <h2>{title}</h2>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function ErrorState({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="state-message" role="alert">
      <AlertCircle size={20} aria-hidden />
      <div>
        <strong>{title}</strong>
        <p>{children}</p>
        {action}
      </div>
    </div>
  );
}
export function Skeleton({ label = 'Loading' }: { label?: string }) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setVisible(true), 150);
    return () => clearTimeout(timer);
  }, []);
  if (!visible)
    return (
      <span role="status" className="visually-hidden">
        {label}
      </span>
    );
  return (
    <div
      className="skeleton-group"
      role="status"
      aria-label={label}
      aria-busy="true"
    >
      <span className="visually-hidden">{label}</span>
      <div aria-hidden className="skeleton" />
      <div aria-hidden className="skeleton short" />
    </div>
  );
}
export function Progress({ label, value }: { label: string; value?: number }) {
  return (
    <label className="field">
      {label}
      <progress aria-label={label} max={100} value={value} />
    </label>
  );
}
export function Surface({
  children,
  elevated = false,
}: {
  children: ReactNode;
  elevated?: boolean;
}) {
  return (
    <section className={`surface ${elevated ? 'surface-effect' : ''}`}>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Polish foundation primitives. Quiet by default: icons for verbs with a
// tooltip and shortcut, status as shape plus text, detail behind disclosure.
// ---------------------------------------------------------------------------

/** Keycaps for a shortcut such as "Mod+K" (⌘K on macOS, Ctrl K elsewhere). */
export function Kbd({
  keys,
  platform,
  className = '',
}: {
  keys: string;
  platform?: ShortcutPlatform;
  className?: string;
}) {
  return (
    <kbd className={`kbd ${className}`}>
      {shortcutKeys(keys, platform).map((key, index) => (
        <kbd key={`${index}:${key}`}>{key}</kbd>
      ))}
    </kbd>
  );
}

export type IconButtonProps = Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  'aria-label'
> & {
  /** Required accessible name; also the tooltip text. */
  label: string;
  shortcut?: string;
  size?: 'sm' | 'md';
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  pressed?: boolean;
  /** Set false only when a surrounding control already shows the label. */
  tooltip?: boolean;
};

/** 28px (sm) or 32px (md) icon action on fine pointers, 44px on touch. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  function IconButton(
    {
      label,
      shortcut,
      size = 'md',
      variant = 'ghost',
      pressed,
      tooltip = true,
      className = '',
      children,
      ...props
    },
    ref,
  ) {
    const button = (
      <Button
        ref={ref}
        iconOnly
        variant={variant}
        aria-label={label}
        aria-keyshortcuts={shortcut ? ariaKeyShortcut(shortcut) : undefined}
        aria-pressed={pressed}
        className={`icon-action icon-action-${size} ${className}`}
        {...props}
      >
        {children}
      </Button>
    );
    return tooltip ? (
      <Hint label={label} shortcut={shortcut}>
        {button}
      </Hint>
    ) : (
      button
    );
  },
);

export type Tone =
  'neutral' | 'accent' | 'info' | 'success' | 'warning' | 'danger';

/** Status as shape, then word. The label is always available to assistive tech. */
export function StatusDot({
  tone = 'neutral',
  label,
  showLabel = false,
  pulse = false,
  className = '',
}: {
  tone?: Tone;
  label: string;
  showLabel?: boolean;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span className={`status-indicator ${className}`} data-tone={tone}>
      <span
        className="status-indicator-dot"
        data-pulse={pulse ? 'true' : undefined}
        aria-hidden
      />
      <span
        className={showLabel ? 'status-indicator-label' : 'visually-hidden'}
      >
        {label}
      </span>
    </span>
  );
}

export type SegmentedOption<T extends string> = {
  value: T;
  label: string;
  icon?: ReactNode;
  /** Icon-only option: the label stays as its accessible name and tooltip. */
  hideLabel?: boolean;
  disabled?: boolean;
};

/** A single-choice radio group styled as a compact segmented control. */
export function Segmented<T extends string>({
  label,
  value,
  onChange,
  options,
  size = 'md',
  className = '',
}: {
  label: string;
  value: T;
  onChange: (value: T) => void;
  options: SegmentedOption<T>[];
  size?: 'sm' | 'md';
  className?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const checkedIndex = options.findIndex((option) => option.value === value);
  const tabbable =
    checkedIndex >= 0 && !options[checkedIndex].disabled
      ? checkedIndex
      : options.findIndex((option) => !option.disabled);
  const move = (from: number, step: number | 'first' | 'last') => {
    const enabled = options
      .map((option, index) => (option.disabled ? -1 : index))
      .filter((index) => index >= 0);
    if (!enabled.length) return;
    let target: number;
    if (step === 'first') target = enabled[0];
    else if (step === 'last') target = enabled[enabled.length - 1];
    else {
      const position = Math.max(0, enabled.indexOf(from));
      target = enabled[(position + step + enabled.length) % enabled.length];
    }
    refs.current[target]?.focus();
    onChange(options[target].value);
  };
  const keyDown = (event: ReactKeyboardEvent, index: number) => {
    const step =
      event.key === 'ArrowRight' || event.key === 'ArrowDown'
        ? 1
        : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
          ? -1
          : event.key === 'Home'
            ? 'first'
            : event.key === 'End'
              ? 'last'
              : null;
    if (step === null) return;
    event.preventDefault();
    move(index, step);
  };
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={`segmented segmented-${size} ${className}`}
    >
      {options.map((option, index) => {
        const checked = option.value === value;
        const button = (
          <button
            key={option.value}
            ref={(element) => {
              refs.current[index] = element;
            }}
            type="button"
            role="radio"
            className="segmented-option"
            aria-checked={checked}
            aria-label={option.hideLabel ? option.label : undefined}
            tabIndex={index === tabbable ? 0 : -1}
            disabled={option.disabled}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => keyDown(event, index)}
          >
            {option.icon}
            {!option.hideLabel && <span>{option.label}</span>}
          </button>
        );
        return option.hideLabel ? (
          <Hint key={option.value} label={option.label}>
            {button}
          </Hint>
        ) : (
          button
        );
      })}
    </div>
  );
}

/** Native details/summary with a rotating chevron; use for "Advanced" sections. */
export function Disclosure({
  summary,
  meta,
  children,
  open,
  defaultOpen,
  onOpenChange,
  className = '',
}: {
  summary: ReactNode;
  meta?: ReactNode;
  children: ReactNode;
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  className?: string;
}) {
  return (
    <details
      className={`disclosure ${className}`}
      open={open ?? defaultOpen}
      onToggle={(event) => onOpenChange?.(event.currentTarget.open)}
    >
      <summary className="disclosure-summary">
        <ChevronRight className="disclosure-chevron" size={14} aria-hidden />
        {summary}
        {meta != null && <span className="disclosure-meta">{meta}</span>}
      </summary>
      <div className="disclosure-body">{children}</div>
    </details>
  );
}

/** Label and help on the left, one control on the right. */
export function SettingRow({
  label,
  description,
  htmlFor,
  control,
  children,
  modified = false,
  className = '',
}: {
  label: ReactNode;
  description?: ReactNode;
  /** Id of a native control so the visible label also names it. */
  htmlFor?: string;
  control?: ReactNode;
  children?: ReactNode;
  modified?: boolean;
  className?: string;
}) {
  const id = useId();
  return (
    <div
      className={`ui-setting-row ${className}`}
      role="group"
      aria-labelledby={`${id}-label`}
      aria-describedby={description ? `${id}-help` : undefined}
    >
      <div className="setting-row-text">
        {htmlFor ? (
          <label
            id={`${id}-label`}
            htmlFor={htmlFor}
            className="setting-row-label"
          >
            {label}
          </label>
        ) : (
          <span id={`${id}-label`} className="setting-row-label">
            {label}
          </span>
        )}
        {modified && (
          <StatusDot
            tone="accent"
            label="Modified"
            className="setting-row-modified"
          />
        )}
        {description && (
          <p id={`${id}-help`} className="setting-row-help">
            {description}
          </p>
        )}
      </div>
      <div className="setting-row-control">{control ?? children}</div>
    </div>
  );
}

export function EntityList({
  label,
  children,
  className = '',
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <ul className={`entity-list ${className}`} aria-label={label}>
      {children}
    </ul>
  );
}

/** Logo, name, status and meta, one primary action, a ⋯ menu and inline detail. */
export function EntityRow({
  title,
  icon,
  status,
  meta,
  action,
  menu,
  menuLabel,
  details,
  expanded,
  defaultExpanded = false,
  onExpandedChange,
  className = '',
}: {
  title: string;
  icon?: ReactNode;
  status?: { tone: Tone; label: string };
  meta?: ReactNode;
  action?: ReactNode;
  menu?: MenuAction[];
  menuLabel?: string;
  details?: ReactNode;
  expanded?: boolean;
  defaultExpanded?: boolean;
  onExpandedChange?: (expanded: boolean) => void;
  className?: string;
}) {
  const id = useId();
  const [localExpanded, setLocalExpanded] = useState(defaultExpanded);
  const open = expanded ?? localExpanded;
  const toggle = () => {
    setLocalExpanded(!open);
    onExpandedChange?.(!open);
  };
  return (
    <li
      className={`entity-row ${className}`}
      data-expanded={open ? 'true' : undefined}
    >
      <div className="entity-row-main">
        {icon && (
          <span className="entity-row-icon" aria-hidden>
            {icon}
          </span>
        )}
        <div className="entity-row-text">
          <span className="entity-row-title">{title}</span>
          {(status || meta) && (
            <span className="entity-row-meta">
              {status && <StatusDot {...status} showLabel />}
              {meta && <span>{meta}</span>}
            </span>
          )}
        </div>
        <div className="entity-row-actions">
          {action}
          {!!menu?.length && (
            <Menu
              label={menuLabel ?? `More actions for ${title}`}
              actions={menu}
              iconOnly
              variant="ghost"
              className="icon-action icon-action-sm"
            >
              <MoreHorizontal size={16} aria-hidden />
            </Menu>
          )}
          {details && (
            <IconButton
              size="sm"
              label={
                open ? `Hide details for ${title}` : `Show details for ${title}`
              }
              aria-expanded={open}
              aria-controls={`${id}-details`}
              onClick={toggle}
            >
              <ChevronDown
                className="entity-row-chevron"
                size={16}
                aria-hidden
              />
            </IconButton>
          )}
        </div>
      </div>
      {details && (
        <div id={`${id}-details`} className="entity-row-details" hidden={!open}>
          {details}
        </div>
      )}
    </li>
  );
}

export function StatGroup({
  label,
  children,
  className = '',
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <dl className={`stat-group ${className}`} aria-label={label}>
      {children}
    </dl>
  );
}

/** One metric inside a StatGroup: label, tabular value, optional delta. */
export function Stat({
  label,
  value,
  unit,
  delta,
  tone = 'neutral',
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  delta?: string;
  tone?: Tone;
}) {
  return (
    <div className="stat">
      <dt className="stat-label">{label}</dt>
      <dd className="stat-value">
        {value}
        {unit && <small>{unit}</small>}
        {delta && (
          <span className="stat-delta" data-tone={tone}>
            {delta}
          </span>
        )}
      </dd>
    </div>
  );
}

/** A one-line, muted empty state for sections inside dense surfaces. */
export function InlineEmpty({
  icon,
  children,
  action,
  className = '',
}: {
  icon?: ReactNode;
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`inline-empty ${className}`}>
      {icon && (
        <span className="inline-empty-icon" aria-hidden>
          {icon}
        </span>
      )}
      <span>{children}</span>
      {action}
    </div>
  );
}

const TOOLBAR_ITEMS = [
  'button:not([disabled]):not([role="radio"])',
  '[role="radio"][tabindex="0"]',
  'a[href]',
  'input:not([disabled])',
  'select:not([disabled])',
].join(', ');

/** Toolbar (floating glass on canvases); arrow keys move between controls. */
export function Toolbar({
  label,
  children,
  orientation = 'horizontal',
  floating = false,
  placement,
  className = '',
}: {
  label: string;
  children: ReactNode;
  orientation?: 'horizontal' | 'vertical';
  floating?: boolean;
  placement?:
    | 'top-left'
    | 'top-center'
    | 'top-right'
    | 'bottom-left'
    | 'bottom-center'
    | 'bottom-right';
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  return (
    <div
      ref={ref}
      role="toolbar"
      aria-label={label}
      aria-orientation={orientation}
      data-placement={placement}
      className={`ui-toolbar ${floating ? 'ui-toolbar-floating' : ''} ${className}`}
      onKeyDown={(event) => {
        const forward =
          orientation === 'horizontal' ? 'ArrowRight' : 'ArrowDown';
        const backward = orientation === 'horizontal' ? 'ArrowLeft' : 'ArrowUp';
        const step =
          event.key === forward
            ? 1
            : event.key === backward
              ? -1
              : event.key === 'Home'
                ? 'first'
                : event.key === 'End'
                  ? 'last'
                  : null;
        const target = event.target as HTMLElement;
        // Segmented groups and text fields keep their own arrow keys.
        if (
          step === null ||
          !ref.current ||
          target.closest('[role="radiogroup"]') ||
          target.matches('input, select, textarea')
        )
          return;
        const items = Array.from(
          ref.current.querySelectorAll<HTMLElement>(TOOLBAR_ITEMS),
        );
        const current = items.indexOf(target);
        if (current < 0) return;
        event.preventDefault();
        const next =
          step === 'first'
            ? 0
            : step === 'last'
              ? items.length - 1
              : (current + step + items.length) % items.length;
        items[next]?.focus();
      }}
    >
      {children}
    </div>
  );
}

export function ToolbarSeparator() {
  return <span className="ui-toolbar-separator" aria-hidden />;
}

export type ComboboxOption = {
  value: string;
  label: string;
  group?: string;
  description?: string;
  keywords?: string[];
  disabled?: boolean;
  icon?: ReactNode;
  meta?: ReactNode;
};

function comboboxMatches(option: ComboboxOption, query: string) {
  const terms = query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return true;
  const haystack = [
    option.label,
    option.group,
    option.description,
    ...(option.keywords ?? []),
  ]
    .filter(Boolean)
    .join(' ')
    .toLocaleLowerCase();
  return terms.every((term) => haystack.includes(term));
}

/**
 * Searchable single-choice picker for large sets (models, conversations).
 * Short enums keep the native `Select`.
 */
export function Combobox({
  label,
  value,
  onChange,
  options,
  placeholder,
  emptyText = 'No matches',
  disabled = false,
  className = '',
  icon,
  footer,
}: {
  label: string;
  value: string | null;
  onChange: (value: string) => void;
  options: ComboboxOption[];
  placeholder?: string;
  emptyText?: string;
  disabled?: boolean;
  className?: string;
  icon?: ReactNode;
  footer?: ReactNode;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const selected = options.find((option) => option.value === value);
  const filtered = useMemo(
    () => options.filter((option) => comboboxMatches(option, query)),
    [options, query],
  );
  const enabled = filtered.filter((option) => !option.disabled);
  const groups = useMemo(() => {
    const order: string[] = [];
    const byGroup = new Map<string, ComboboxOption[]>();
    for (const option of filtered) {
      const group = option.group ?? '';
      if (!byGroup.has(group)) {
        byGroup.set(group, []);
        order.push(group);
      }
      byGroup.get(group)!.push(option);
    }
    return order.map((group) => ({ group, items: byGroup.get(group)! }));
  }, [filtered]);
  const indexOf = useMemo(
    () => new Map(options.map((option, index) => [option.value, index])),
    [options],
  );
  const optionId = (optionValue: string) =>
    `${id}-option-${indexOf.get(optionValue) ?? 0}`;
  const activeOption =
    enabled.find((option) => option.value === active) ?? enabled[0] ?? null;
  const activeId = activeOption ? optionId(activeOption.value) : undefined;
  useEffect(() => {
    if (!open || !activeId) return;
    list.current
      ?.querySelector<HTMLElement>(`[id="${activeId}"]`)
      ?.scrollIntoView?.({ block: 'nearest' });
  }, [open, activeId]);
  const choose = (option: ComboboxOption) => {
    if (option.disabled) return;
    onChange(option.value);
    setOpen(false);
  };
  const keyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing || !enabled.length) return;
    const index = activeOption ? enabled.indexOf(activeOption) : -1;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive(
        enabled[(index + step + enabled.length) % enabled.length].value,
      );
    } else if (event.key === 'PageDown' || event.key === 'PageUp') {
      event.preventDefault();
      const step = event.key === 'PageDown' ? 8 : -8;
      setActive(
        enabled[Math.max(0, Math.min(enabled.length - 1, index + step))].value,
      );
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (activeOption) choose(activeOption);
    }
  };
  const renderOption = (option: ComboboxOption) => (
    <div
      key={option.value}
      id={optionId(option.value)}
      role="option"
      className="combobox-option"
      aria-selected={option.value === activeOption?.value}
      aria-disabled={option.disabled || undefined}
      data-current={option.value === value ? 'true' : undefined}
      onMouseDown={(event) => event.preventDefault()}
      onMouseMove={() => {
        if (!option.disabled && active !== option.value)
          setActive(option.value);
      }}
      onClick={() => choose(option)}
    >
      {option.icon && (
        <span className="combobox-option-icon" aria-hidden>
          {option.icon}
        </span>
      )}
      <span className="combobox-option-text">
        <span className="combobox-option-label">{option.label}</span>
        {option.description && (
          <small className="combobox-option-description">
            {option.description}
          </small>
        )}
      </span>
      {option.meta && (
        <span className="combobox-option-meta">{option.meta}</span>
      )}
      {option.value === value && (
        <Check
          className="combobox-option-check"
          size={14}
          role="img"
          aria-label="Current"
        />
      )}
    </div>
  );
  return (
    <Popover.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        setQuery('');
        setActive(next ? value : null);
      }}
    >
      <Popover.Trigger asChild>
        <Button
          variant="ghost"
          className={`combobox-trigger ${className}`}
          aria-label={label}
          aria-haspopup="listbox"
          aria-describedby={`${id}-value`}
          disabled={disabled}
        >
          {icon}
          <span id={`${id}-value`} className="combobox-value">
            {selected?.label ?? placeholder ?? 'Choose'}
          </span>
          <ChevronDown size={14} aria-hidden />
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          className="combobox-popover surface-effect"
          align="start"
          sideOffset={6}
          collisionPadding={12}
          aria-label={label}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            input.current?.focus();
          }}
        >
          <div className="combobox-search">
            <Search size={14} aria-hidden />
            <input
              ref={input}
              className="combobox-input"
              role="combobox"
              aria-label={`Search ${label.toLocaleLowerCase()}`}
              aria-expanded
              aria-controls={`${id}-listbox`}
              aria-autocomplete="list"
              aria-activedescendant={activeId}
              placeholder={`Search ${label.toLocaleLowerCase()}`}
              value={query}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => {
                setQuery(event.target.value);
                setActive(null);
              }}
              onKeyDown={keyDown}
            />
          </div>
          <div
            ref={list}
            id={`${id}-listbox`}
            role="listbox"
            aria-label={label}
            className="combobox-list"
          >
            {groups.map(({ group, items }, groupIndex) =>
              group ? (
                <div
                  key={group}
                  role="group"
                  aria-labelledby={`${id}-group-${groupIndex}`}
                  className="combobox-group"
                >
                  <div
                    id={`${id}-group-${groupIndex}`}
                    className="combobox-group-label"
                    role="presentation"
                  >
                    {group}
                  </div>
                  {items.map(renderOption)}
                </div>
              ) : (
                items.map(renderOption)
              ),
            )}
          </div>
          {!filtered.length && (
            <p className="combobox-empty" role="status">
              {emptyText}
            </p>
          )}
          {footer && <div className="combobox-footer">{footer}</div>}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
