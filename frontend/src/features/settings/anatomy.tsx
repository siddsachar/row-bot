import {
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ComponentType,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { useLocation } from 'react-router-dom';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { Disclosure, IconButton, type Tone } from '../../ui/primitives';

/**
 * Settings page anatomy: header (icon, title, one line, summary chips, ↻) →
 * Essentials → lists → Advanced (collapsed) → Danger zone (collapsed,
 * outlined). The shell owns the header; pages portal their summary into it.
 */
export const SettingsHeaderSlot = createContext<HTMLElement | null | undefined>(
  undefined,
);

/**
 * Summary chips and an optional refresh, shown in the page header. Outside
 * the Settings shell (e.g. a component rendered on its own) they show inline.
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
}: {
  meta?: ReactNode;
  anchor?: string;
  children: ReactNode;
  summary?: string;
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

/**
 * Jump to `#anchor` once it renders: switch to its tab, open collapsed
 * ancestors, scroll it into view and highlight it briefly. Pages load
 * asynchronously, so this watches the content until the anchor is visible
 * (or gives up after a few seconds).
 */
export function useSettingsAnchor(root: HTMLElement | null) {
  const location = useLocation();
  useEffect(() => {
    const anchor = decodeURIComponent(location.hash.slice(1));
    if (!root || !anchor) return;
    let done = false;
    let clearHit: ReturnType<typeof setTimeout> | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const observer = new MutationObserver(() => reveal());
    function reveal() {
      if (done || !root) return;
      const target = [
        ...root.querySelectorAll<HTMLElement>('[data-setting-anchor]'),
      ].find((element) => element.dataset.settingAnchor === anchor);
      if (!target) return;
      for (
        let parent = target.parentElement;
        parent && parent !== root;
        parent = parent.parentElement
      )
        if (parent instanceof HTMLDetailsElement) parent.open = true;
      const details = target.querySelector(':scope > .disclosure');
      if (details instanceof HTMLDetailsElement) details.open = true;
      if (target.closest('[hidden]')) {
        // Its tab is switching in; look again once it shows.
        clearTimeout(retry);
        retry = setTimeout(reveal, 60);
        return;
      }
      done = true;
      observer.disconnect();
      const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
      requestAnimationFrame(() => {
        target.scrollIntoView?.({
          block: 'center',
          behavior: reduce ? 'auto' : 'smooth',
        });
        target.dataset.searchHit = 'true';
        clearHit = setTimeout(() => {
          delete target.dataset.searchHit;
        }, HIT_MS);
      });
    }
    window.dispatchEvent(new CustomEvent(ANCHOR_EVENT, { detail: anchor }));
    observer.observe(root, { childList: true, subtree: true });
    reveal();
    const giveUp = setTimeout(() => {
      done = true;
      observer.disconnect();
    }, 6000);
    return () => {
      done = true;
      observer.disconnect();
      clearTimeout(giveUp);
      clearTimeout(retry);
      if (clearHit) clearTimeout(clearHit);
    };
  }, [location.hash, location.pathname, root]);
}

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
