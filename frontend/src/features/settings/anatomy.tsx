import {
  Children,
  cloneElement,
  createContext,
  Fragment,
  isValidElement,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ComponentType,
  type ReactElement,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { useLocation } from 'react-router-dom';
import { AlertTriangle, MoreHorizontal, RefreshCw } from 'lucide-react';
import {
  Disclosure,
  IconButton,
  Menu,
  type MenuAction,
  type Tone,
} from '../../ui/primitives';

/**
 * Settings page anatomy (B258; B229, B262 and B263 build on it):
 *
 *   header      icon tile, title, one plain line, then ONE status line
 *               (<SettingsStatus>) and the page's ⋯ (<SettingsPageMenu>);
 *               no summary chips or banners.
 *   groups      <SettingsGroup title note meta> — a small heading over one
 *               quiet surface; rows inside are divided by hairlines.
 *   rows        <SettingsItem label help status control> — label and
 *               one-line help on the left, the control on the right (32px
 *               on a fine pointer, 44px on touch; selects 280px wide); on a
 *               narrow page the control moves under its label.
 *   saves       confirmed by the floating notice ("Name saved · Undo"),
 *               never a line in the page.
 *   Advanced    <SettingsAdvanced meta> — one dashed disclosure at the end.
 *   Danger zone <SettingsDangerZone meta> — outlined in the danger tone,
 *               collapsed.
 *
 * The shell owns the header; pages portal their status line and ⋯ into it.
 */
export const SettingsHeaderSlot = createContext<HTMLElement | null | undefined>(
  undefined,
);
/** Where a page's status line goes: under the header's description. */
export const SettingsStatusSlot = createContext<HTMLElement | null | undefined>(
  undefined,
);

/** Status parts: a toned dot before the first, "·" between the rest. */
function StatusParts({
  tone,
  pulse,
  children,
  more = [],
}: {
  tone?: Tone;
  pulse?: boolean;
  children: ReactNode;
  more?: ReactNode[];
}) {
  return (
    <>
      <span className="status-indicator" data-tone={tone}>
        {tone && (
          <span
            className="status-indicator-dot"
            data-pulse={pulse ? 'true' : undefined}
            aria-hidden
          />
        )}
        <span className="settings-status-first">{children}</span>
      </span>
      {more
        .filter((part) => part != null && part !== false && part !== '')
        .map((part, index) => (
          <Fragment key={index}>
            <span className="settings-status-sep" aria-hidden>
              ·
            </span>
            <span>{part}</span>
          </Fragment>
        ))}
    </>
  );
}

/**
 * The page's one status line, under its description: `tone` colours the
 * dot before the first part (`children`); `more` adds muted parts joined by
 * "·" ("● Dream Cycle on · last ran today at 3:12 AM"). Outside the
 * Settings shell it shows inline.
 */
export function SettingsStatus({
  tone,
  pulse,
  children,
  more,
}: {
  tone?: Tone;
  pulse?: boolean;
  children: ReactNode;
  more?: ReactNode[];
}) {
  const slot = useContext(SettingsStatusSlot);
  const line = (
    <p className="settings-status-line">
      <StatusParts tone={tone} pulse={pulse} more={more}>
        {children}
      </StatusParts>
    </p>
  );
  if (slot === undefined) return line;
  if (!slot) return null;
  return createPortal(line, slot);
}

/**
 * A row's small status line under its help (same parts as
 * SettingsStatus): "● Not installed", "Last ran today · 14 merged".
 */
export function StatusLine({
  tone,
  pulse,
  children,
  more,
  action,
}: {
  tone?: Tone;
  pulse?: boolean;
  children: ReactNode;
  more?: ReactNode[];
  /** A trailing text action ("Install", "Show it again"). */
  action?: ReactNode;
}) {
  return (
    <span className="settings-row-status-line" data-tone={tone}>
      <StatusParts tone={tone} pulse={pulse} more={more}>
        {children}
      </StatusParts>
      {action && (
        <>
          <span className="settings-status-sep" aria-hidden>
            ·
          </span>
          {action}
        </>
      )}
    </span>
  );
}

/**
 * The page's ⋯ in the header, for rare page actions (provider links,
 * re-reading the page). `label` names the button.
 */
export function SettingsPageMenu({
  label,
  actions,
}: {
  label: string;
  actions: MenuAction[];
}) {
  return (
    <SettingsSummary>
      <Menu
        label={label}
        iconOnly
        variant="ghost"
        className="icon-action settings-page-menu"
        actions={actions}
      >
        <MoreHorizontal size={16} aria-hidden />
      </Menu>
    </SettingsSummary>
  );
}

/**
 * One quiet group of rows.
 * - `title`: the small heading (omit it for a lone group, then give `label`).
 * - `note`: one muted line beside the heading (under it on a phone).
 * - `meta`: the heading's right side: a count, a "Refresh" link, an icon.
 * - `anchor`: the settings-search target (`#anchor`).
 * - `surface={false}`: the children bring their own surface (a drop zone).
 */
export function SettingsGroup({
  title,
  label,
  note,
  meta,
  anchor,
  surface = true,
  className = '',
  children,
}: {
  title?: string;
  label?: string;
  note?: ReactNode;
  meta?: ReactNode;
  anchor?: string;
  surface?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section
      className={`settings-group ${className}`}
      aria-labelledby={title ? `${id}-title` : undefined}
      aria-label={title ? undefined : label}
      data-setting-anchor={anchor}
    >
      {(title || meta) && (
        <header className="settings-group-head">
          {title && <h3 id={`${id}-title`}>{title}</h3>}
          {note && <p>{note}</p>}
          {meta && <div className="settings-group-meta">{meta}</div>}
        </header>
      )}
      {surface ? (
        <div className="settings-group-surface">{children}</div>
      ) : (
        children
      )}
    </section>
  );
}

type ControlProps = { id?: string; 'aria-describedby'?: string };

/**
 * One settings row.
 * - `label`, `help`: the name (14 medium) and one plain line (13 muted).
 * - `status`: a small line under the help, usually a <StatusLine>.
 * - `control`: the control. When it is one element, the label names it and
 *   the help and status describe it; pass `bind={false}` for a control that
 *   names itself (a segmented choice, several buttons).
 * - `trailing`: actions after the control that the label does not name
 *   (Reset, Install, an icon button).
 * - `layout`: "row" (the control moves under the label on a narrow page),
 *   "inline" (stays beside it: switches, one small button) or "stacked"
 *   (always under it, full width: text areas, lists).
 * - `icon`: a tinted 32px tile before the label; `tone` picks its tint.
 * - `sub`: an indented sub-row (Camera under Vision); `off`: dimmed text for
 *   a job that is switched off; `modified`: the accent "changed" dot.
 * - `children`: full-width content under the row (a message, a confirm).
 */
export function SettingsItem({
  label,
  help,
  status,
  control,
  bind = true,
  trailing,
  layout = 'row',
  icon,
  tone,
  sub = false,
  off = false,
  modified = false,
  anchor,
  className = '',
  children,
}: {
  label: ReactNode;
  help?: ReactNode;
  status?: ReactNode;
  control?: ReactNode;
  bind?: boolean;
  trailing?: ReactNode;
  layout?: 'row' | 'inline' | 'stacked';
  icon?: ReactNode;
  tone?: 'accent' | 'neutral' | '1' | '2' | '3' | '4' | '5' | '6';
  sub?: boolean;
  off?: boolean;
  modified?: boolean;
  anchor?: string;
  className?: string;
  children?: ReactNode;
}) {
  const id = useId();
  const single =
    bind && isValidElement<ControlProps>(control) && control.type !== Fragment;
  const element = single ? (control as ReactElement<ControlProps>) : null;
  const controlId = element ? (element.props.id ?? `${id}-control`) : '';
  const helpId = help ? `${id}-help` : '';
  const statusId = status ? `${id}-status` : '';
  const bound = element
    ? cloneElement(element, {
        id: controlId,
        'aria-describedby':
          [element.props['aria-describedby'], helpId, statusId]
            .filter(Boolean)
            .join(' ') || undefined,
      })
    : control;
  const dot = modified ? (
    <span
      className="settings-modified-dot"
      aria-hidden
      title="Changed from default"
    />
  ) : null;
  return (
    <div
      className={`settings-row ${className}`}
      data-layout={layout}
      data-icon={icon ? 'true' : undefined}
      data-sub={sub ? 'true' : undefined}
      data-off={off ? 'true' : undefined}
      data-setting-anchor={anchor}
    >
      {icon && (
        <span className="settings-row-icon" data-tone={tone} aria-hidden>
          {icon}
        </span>
      )}
      <div className="settings-row-text">
        {element ? (
          <label className="settings-row-label" htmlFor={controlId}>
            {label}
            {dot}
          </label>
        ) : (
          <span className="settings-row-label">
            {label}
            {dot}
          </span>
        )}
        {help && (
          <p className="settings-row-help" id={helpId}>
            {help}
          </p>
        )}
        {status && (
          <div className="settings-row-status" id={statusId}>
            {status}
          </div>
        )}
      </div>
      {(control != null || trailing != null) && (
        <div className="settings-row-control">
          {bound}
          {trailing}
        </div>
      )}
      {Children.toArray(children).length > 0 && (
        <div className="settings-row-extra">{children}</div>
      )}
    </div>
  );
}

/**
 * The header's right side: the page's ⋯ or ↻ (and, on pages not yet on the
 * B258 anatomy, summary chips). Outside the Settings shell (e.g. a
 * component rendered on its own) it shows inline.
 */
export function SettingsSummary({ children }: { children: ReactNode }) {
  const slot = useContext(SettingsHeaderSlot);
  if (slot === undefined)
    return <div className="settings-summary-inline">{children}</div>;
  if (!slot) return null;
  return createPortal(children, slot);
}

export function SummaryChip({
  tone,
  children,
  title,
}: {
  tone?: Tone;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span className="settings-summary-chip" data-tone={tone} title={title}>
      {tone && <span className="settings-summary-dot" aria-hidden />}
      {children}
    </span>
  );
}

/** A header ↻ for pages where a manual re-read is meaningful. */
export function SettingsRefresh({
  label,
  busy = false,
  onRefresh,
}: {
  label: string;
  busy?: boolean;
  onRefresh: () => void;
}) {
  return (
    <IconButton
      label={label}
      size="sm"
      disabled={busy}
      aria-busy={busy || undefined}
      className="settings-refresh"
      onClick={onRefresh}
    >
      <RefreshCw size={15} aria-hidden data-spinning={busy || undefined} />
    </IconButton>
  );
}

type Icon = ComponentType<{ size?: number; 'aria-hidden'?: boolean }>;

/** A flat group of rows: a small heading, an optional line, then content. */
export function SettingsSection({
  title,
  description,
  icon: Icon,
  anchor,
  actions,
  children,
  className = '',
  headingLevel = 3,
}: {
  title: string;
  description?: ReactNode;
  icon?: Icon;
  anchor?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  headingLevel?: 3 | 4;
}) {
  const id = useId();
  const Heading = headingLevel === 3 ? 'h3' : 'h4';
  return (
    <section
      className={`settings-section-flat ${className}`}
      aria-labelledby={`${id}-title`}
      data-setting-anchor={anchor}
    >
      <header className="settings-section-head">
        {Icon && (
          <span className="settings-section-icon" aria-hidden>
            <Icon size={16} aria-hidden />
          </span>
        )}
        <div className="settings-section-text">
          <Heading id={`${id}-title`}>{title}</Heading>
          {description && <p>{description}</p>}
        </div>
        {actions && <div className="settings-section-actions">{actions}</div>}
      </header>
      <div className="settings-section-body">{children}</div>
    </section>
  );
}

/** Rarely used options: collapsed until asked for. */
export function SettingsAdvanced({
  summary = 'Advanced',
  meta,
  anchor,
  defaultOpen,
  open,
  onOpenChange,
  children,
}: {
  summary?: string;
  meta?: ReactNode;
  anchor?: string;
  defaultOpen?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: ReactNode;
}) {
  return (
    <div className="settings-advanced" data-setting-anchor={anchor}>
      <Disclosure
        summary={summary}
        meta={meta}
        defaultOpen={defaultOpen}
        open={open}
        onOpenChange={onOpenChange}
      >
        <div className="settings-advanced-body">{children}</div>
      </Disclosure>
    </div>
  );
}

/** Destructive actions: collapsed and outlined, never filled in the flow. */
export function SettingsDangerZone({
  meta,
  anchor = 'danger-zone',
  children,
  summary = 'Danger zone',
  open,
  onOpenChange,
}: {
  meta?: ReactNode;
  anchor?: string;
  children: ReactNode;
  summary?: string;
  /** Controlled open state, e.g. when a row starts a removal in here. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  return (
    <div className="settings-danger-zone" data-setting-anchor={anchor}>
      <Disclosure
        summary={
          <span className="settings-danger-summary">
            <AlertTriangle size={14} aria-hidden />
            {summary}
          </span>
        }
        meta={meta}
        open={open}
        onOpenChange={onOpenChange}
      >
        <div className="settings-danger-body">{children}</div>
      </Disclosure>
    </div>
  );
}

/** One destructive action inside a Danger zone: what it does, then the button. */
export function DangerAction({
  title,
  description,
  children,
}: {
  title: string;
  description: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="settings-danger-action">
      <div>
        <strong>{title}</strong>
        <p>{description}</p>
      </div>
      <div className="settings-danger-control">{children}</div>
    </div>
  );
}

const ANCHOR_EVENT = 'row-bot:settings-anchor';

function currentAnchor() {
  try {
    return decodeURIComponent(window.location.hash.slice(1));
  } catch {
    return '';
  }
}

/**
 * Tab to show when the URL points at a row inside another tab. Reads the
 * hash on mount and follows the shell's row jumps, so it also works outside
 * a router (tests, isolated owners).
 */
export function useTabForAnchor<T extends string>(
  fallback: T,
  anchors: Partial<Record<string, T>>,
): [T, (value: T) => void] {
  const [tab, setTab] = useState<T>(() => anchors[currentAnchor()] ?? fallback);
  const table = useRef(anchors);
  table.current = anchors;
  useEffect(() => {
    const follow = (event: Event) => {
      const target = table.current[String((event as CustomEvent).detail)];
      if (target) setTab(target);
    };
    window.addEventListener(ANCHOR_EVENT, follow);
    return () => window.removeEventListener(ANCHOR_EVENT, follow);
  }, []);
  return [tab, setTab];
}

const HIT_MS = 2400;
/** How long a jump keeps its row in view while the page around it loads. */
const SETTLE_MS = 2500;
/** How long a jump waits for its row to appear (a slow phone link). */
const WAIT_MS = 20000;

/** Whether the top of `target` shows inside the scrolling `root`. */
function inView(target: HTMLElement, root: HTMLElement) {
  const box = root.getBoundingClientRect();
  const rect = target.getBoundingClientRect();
  const top = Math.max(box.top, 0);
  const bottom = Math.min(box.bottom, window.innerHeight || box.bottom);
  return rect.top >= top - 1 && rect.top <= bottom - 48;
}

/**
 * Jump to `#anchor` once it renders: switch to its tab, open collapsed
 * ancestors, scroll it into view, highlight it briefly and move focus to it
 * when focus would otherwise be lost. Pages load asynchronously, so this
 * watches the content until the anchor appears, then keeps it in view while
 * late content above it settles (a phone keyboard closing, a list loading),
 * until the person scrolls themselves. Opening the same result again jumps
 * again.
 */
export function useSettingsAnchor(root: HTMLElement | null) {
  const location = useLocation();
  useEffect(() => {
    let anchor = '';
    try {
      anchor = decodeURIComponent(location.hash.slice(1));
    } catch {
      return;
    }
    if (!root || !anchor) return;
    let stopped = false;
    let target: HTMLElement | null = null;
    let settleUntil = 0;
    let frame = 0;
    let clearHit: ReturnType<typeof setTimeout> | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const observer = new MutationObserver(() => reveal());
    const resizes =
      typeof ResizeObserver === 'undefined'
        ? null
        : new ResizeObserver(() => reveal());
    const stop = () => {
      stopped = true;
      observer.disconnect();
      resizes?.disconnect();
      window.removeEventListener('resize', reveal);
      for (const type of USER_SCROLL)
        window.removeEventListener(type, release, true);
    };
    // The person takes over: stop holding the row in place.
    const release = () => {
      if (target) stop();
    };
    function hold() {
      if (frame || !target || !root) return;
      if (performance.now() > settleUntil) return stop();
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (stopped || !target || !root) return;
        if (!target.isConnected) {
          // The page re-rendered the row: find it again.
          target = null;
          return reveal();
        }
        if (!inView(target, root))
          target.scrollIntoView?.({ block: 'center', behavior: 'auto' });
      });
    }
    function reveal() {
      if (stopped || !root) return;
      if (target) return hold();
      const found = [
        ...root.querySelectorAll<HTMLElement>('[data-setting-anchor]'),
      ].find((element) => element.dataset.settingAnchor === anchor);
      if (!found) return;
      for (
        let parent = found.parentElement;
        parent && parent !== root;
        parent = parent.parentElement
      )
        if (parent instanceof HTMLDetailsElement) parent.open = true;
      const details = found.querySelector(':scope > .disclosure');
      if (details instanceof HTMLDetailsElement) details.open = true;
      // A connection's own panel is the anchor: it opens its connect sheet.
      if (found instanceof HTMLDetailsElement) found.open = true;
      if (found.closest('[hidden]')) {
        // Its tab is switching in; look again once it shows.
        clearTimeout(retry);
        retry = setTimeout(reveal, 60);
        return;
      }
      const hit = found;
      target = hit;
      settleUntil = performance.now() + SETTLE_MS;
      const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
      requestAnimationFrame(() => {
        if (stopped) return;
        hit.scrollIntoView?.({
          block: 'center',
          behavior: reduce ? 'auto' : 'smooth',
        });
        hit.dataset.searchHit = 'true';
        clearHit = setTimeout(() => {
          delete hit.dataset.searchHit;
        }, HIT_MS);
        // Focus follows the jump when it would otherwise be lost (the
        // result link closed) or still sits in search or on the title.
        const active = document.activeElement;
        if (
          !active ||
          active === document.body ||
          active.closest('.settings-navigation-search, .settings-pane-title')
        ) {
          if (!hit.hasAttribute('tabindex')) hit.tabIndex = -1;
          hit.focus({ preventScroll: true });
        }
        settleUntil = performance.now() + SETTLE_MS;
      });
    }
    window.dispatchEvent(new CustomEvent(ANCHOR_EVENT, { detail: anchor }));
    observer.observe(root, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['hidden', 'open'],
    });
    // Its own size (a phone keyboard) and its content's (late lists, images).
    for (const element of [root, ...root.children]) resizes?.observe(element);
    window.addEventListener('resize', reveal);
    // Anywhere: PageDown with focus outside the page still moves it.
    for (const type of USER_SCROLL)
      window.addEventListener(type, release, { capture: true, passive: true });
    reveal();
    const giveUp = setTimeout(() => {
      if (!target) stop();
    }, WAIT_MS);
    return () => {
      stop();
      cancelAnimationFrame(frame);
      clearTimeout(giveUp);
      clearTimeout(retry);
      if (clearHit) clearTimeout(clearHit);
    };
    // `key` changes when the same result is opened again.
  }, [location.hash, location.pathname, location.key, root]);
}

/** Input that means the person is moving the page (or acting) themselves. */
const USER_SCROLL = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const;

/**
 * Page tabs (Installed | Discover). Every panel stays mounted so searches,
 * drafts and retained reviews survive a tab switch; arrow keys move between
 * tabs.
 */
export function SettingsTabs<T extends string>({
  label,
  value,
  onChange,
  tabs,
}: {
  label: string;
  value: T;
  onChange: (value: T) => void;
  tabs: { id: T; label: string; meta?: ReactNode; content: ReactNode }[];
}) {
  const id = useId();
  const move = (from: number, step: number) => {
    const next = tabs[(from + step + tabs.length) % tabs.length];
    onChange(next.id);
    requestAnimationFrame(() =>
      document.getElementById(`${id}-tab-${next.id}`)?.focus(),
    );
  };
  return (
    <div className="settings-tabs">
      <div className="settings-tab-list" role="tablist" aria-label={label}>
        {tabs.map((tab, index) => (
          <button
            key={tab.id}
            id={`${id}-tab-${tab.id}`}
            type="button"
            role="tab"
            className="settings-tab"
            aria-selected={tab.id === value}
            aria-controls={`${id}-panel-${tab.id}`}
            tabIndex={tab.id === value ? 0 : -1}
            onClick={() => onChange(tab.id)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowRight') move(index, 1);
              else if (event.key === 'ArrowLeft') move(index, -1);
              else return;
              event.preventDefault();
            }}
          >
            {tab.label}
            {tab.meta != null && (
              <span className="settings-tab-meta">{tab.meta}</span>
            )}
          </button>
        ))}
      </div>
      {tabs.map((tab) => (
        <div
          key={tab.id}
          id={`${id}-panel-${tab.id}`}
          role="tabpanel"
          className="settings-tab-panel"
          aria-labelledby={`${id}-tab-${tab.id}`}
          hidden={tab.id !== value}
        >
          {tab.content}
        </div>
      ))}
    </div>
  );
}
