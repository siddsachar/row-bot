import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { usePlace } from '../../api/place';
import { ChevronLeft, Search, X } from 'lucide-react';
import { Field, Input, Kbd } from '../../ui/primitives';
import { settingsReturnPath } from './return-path';
import { PHONE_LAYOUT, useMedia } from '../../ui/use-media';
import {
  agentProfileLibrary,
  searchFindsAgentProfiles,
  searchSettings,
  searchSettingsRows,
  settingsGroups,
  settingsRowHref,
  visibleLeaves,
  type SettingsLeaf,
} from './model';
import {
  SettingsHeaderSlot,
  SettingsStatusSlot,
  useSettingsAnchor,
} from './anatomy';
import { useWorkspaceActions } from '../shell/workspace-actions';
import { SettingIcon } from './settings-icons';
import PhoneSettingsList, {
  phoneParent,
  phoneTitle,
} from './PhoneSettingsList';

const descriptions: Record<string, string> = {
  preferences: 'How Row-Bot introduces itself, opens and tidies up overnight.',
  appearance: 'Theme, accent colour and density on this device.',
  buddy: 'Your companion’s look, personality and motion.',
  proactive:
    'When something is worth your attention, Row-Bot gets it ready and tells you at a good moment.',
  learning:
    'How Row-Bot learns from your work, and the personal model it can train.',
  providers: 'Where Row-Bot’s models come from. Defaults live in Models.',
  models: 'What Row-Bot thinks with, and where it comes from.',
  voice: 'Talk, dictation and read-aloud.',
  knowledge:
    'Memory, graph health and the wiki vault. Memories live in Knowledge.',
  documents: 'Files Row-Bot can search and learn from.',
  tracker: 'Habits, symptoms and health events you track.',
  tools: 'Search, research and built-in tools the assistant can use.',
  apps: 'Services Row-Bot can work in for you. Connect one in about a minute.',
  skills: 'Ways of doing a job that Row-Bot follows when they fit.',
  company: 'Your company’s Row-Bot server, when your work uses one.',
  system: 'Where Row-Bot works on this computer, and what it may do there.',
  activity: 'Everything Row-Bot did, and who allowed it.',
  access:
    'Use Row-Bot on a phone or another computer, and see what is signed in.',
  updates: 'The version you have and how new ones arrive.',
  data: 'Back up and restore, import from other assistants, and irreversible clean-up.',
};

/** A phone's words where its page differs from the computer's (C3). */
const phoneDescriptions: Record<string, string> = {
  buddy: 'Your companion, and when Row-Bot brings something up before you ask.',
  knowledge: 'What Row-Bot remembers about you, and how it remembers.',
};

function typingTarget(target: EventTarget | null) {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable ||
      ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
  );
}

export default function SettingsShell({
  leaf,
  more = false,
  learning = false,
  children,
}: {
  /** The open page; none for a phone's list of pages (D6). */
  leaf?: SettingsLeaf;
  /** A phone's More settings: the pages outside This phone and Row-Bot. */
  more?: boolean;
  /** A build with Learning (preview): its page is listed (O34). */
  learning?: boolean;
  children?: ReactNode;
}) {
  const place = usePlace();
  const navigate = useNavigate();
  const location = useLocation();
  const compactControls = useWorkspaceActions()?.compactControls;
  // A phone lists the pages first and opens one at a time, with its way
  // back to the list; the tab bar replaces Close (D6). Its header is one
  // row, "‹ Models", and the app's Search finds settings (D7).
  const phone = useMedia(PHONE_LAYOUT);
  const mode = phone ? (leaf ? 'page' : more ? 'more' : 'list') : undefined;
  const [listRoot, setListRoot] = useState<HTMLDivElement | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const [content, setContent] = useState<HTMLDivElement | null>(null);
  const [slot, setSlot] = useState<HTMLDivElement | null>(null);
  const [statusSlot, setStatusSlot] = useState<HTMLDivElement | null>(null);
  const [query, setQuery] = useState('');
  // The result Enter opens; arrow keys move it.
  const [active, setActive] = useState(0);
  const resultId = useId();
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
    if (content && !location.hash) content.scrollTop = 0;
    // Only a new page resets focus and scroll, not a new row anchor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname, more]);
  // A phone's list holds This phone's rows (#notifications from Home).
  useSettingsAnchor(leaf ? content : listRoot);
  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if (
        event.key !== '/' ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        typingTarget(event.target) ||
        !search.current
      )
        return;
      event.preventDefault();
      search.current.focus();
    };
    window.addEventListener('keydown', focusSearch);
    return () => window.removeEventListener('keydown', focusSearch);
  }, []);
  const leaves = visibleLeaves(learning);
  const searching = Boolean(query.trim());
  const pages = searching ? searchSettings(query, learning) : [];
  // A hosted Row-Bot's host runs its network: Devices has no Network or
  // Public link section to find.
  const rows = (searching ? searchSettingsRows(query, learning) : []).filter(
    (row) =>
      place.kind !== 'hosted' ||
      row.leaf !== 'access' ||
      !['network', 'tunnel'].includes(row.anchor),
  );
  const profileLibrary = searching && searchFindsAgentProfiles(query);
  const leafLabel = (id: string) =>
    leaves.find((item) => item.id === id)?.label ?? id;
  // Every result in reading order: pages, then rows, then the Agents dialog.
  const results = [
    ...pages.map((item) => item.href),
    ...rows.map(settingsRowHref),
    ...(profileLibrary ? [agentProfileLibrary.href] : []),
  ];
  const current = results.length
    ? Math.min(Math.max(active, 0), results.length - 1)
    : -1;
  const optionId = (index: number) => `${resultId}-${index}`;
  const optionProps = (index: number) => ({
    id: optionId(index),
    'data-active': index === current ? 'true' : undefined,
  });
  useEffect(() => {
    if (current >= 0)
      document
        .getElementById(optionId(current))
        ?.scrollIntoView?.({ block: 'nearest' });
    // optionId only changes with the result position.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, query]);
  const openResult = (href: string) => {
    setQuery('');
    navigate(href);
  };
  return (
    // The frame is a size container, so the shell also compacts when the
    // settings area is narrow in a wide window (sidebar open, 200% zoom).
    <div className="settings-shell-frame">
      <section
        className="settings-shell"
        aria-label="Settings"
        data-mode={mode}
      >
        {/* The workspace's own skip link targets a conversation; this one
            passes the settings list and lands on the open page. */}
        <a
          className="skip-link"
          href="#settings-page"
          onClick={(event) => {
            event.preventDefault();
            heading.current?.focus();
          }}
        >
          Skip to settings
        </a>
        <header
          className="settings-shell-header"
          data-compact-controls={compactControls ? 'true' : undefined}
        >
          {mode === 'page' || mode === 'more' ? (
            <Link
              className="icon-button settings-back"
              to={leaf ? phoneParent(leaf.id).href : '/settings'}
              aria-label={
                leaf ? phoneParent(leaf.id).label : 'Back to Settings'
              }
            >
              <ChevronLeft size={20} aria-hidden />
            </Link>
          ) : (
            compactControls?.navigation
          )}
          {mode === 'page' && leaf ? (
            <h1 ref={heading} tabIndex={-1}>
              {phoneTitle(leaf)}
            </h1>
          ) : mode === 'more' ? (
            <h1 ref={heading} tabIndex={-1}>
              More settings
            </h1>
          ) : (
            <h1>Settings</h1>
          )}
          {compactControls?.commands}
          {!phone && (
            <Link
              className="icon-button"
              to={settingsReturnPath()}
              aria-label="Close settings"
            >
              <X size={18} aria-hidden />
            </Link>
          )}
        </header>
        {leaf && !phone && (
          <div className="settings-compact-picker">
            <Field label="Settings section">
              <select
                className="input select"
                value={leaf.id}
                onChange={(event) =>
                  navigate(
                    `/settings/${encodeURIComponent(event.target.value)}`,
                  )
                }
              >
                {settingsGroups.map((group) => (
                  <optgroup key={group.id} label={group.label}>
                    {leaves
                      .filter((item) => item.group === group.id)
                      .map((item) => (
                        <option value={item.id} key={item.id}>
                          {item.label}
                        </option>
                      ))}
                  </optgroup>
                ))}
              </select>
            </Field>
          </div>
        )}
        {mode === 'list' || mode === 'more' ? (
          // A phone's grouped list (D6 A); it scrolls under the header.
          <div className="phone-settings-frame" key={mode} ref={setListRoot}>
            <PhoneSettingsList more={more} learning={learning} />
          </div>
        ) : null}
        {/* A phone has no second search: the app header's Search finds
            settings (D7). */}
        {!phone && (
          <nav
            className="settings-side-navigation"
            aria-label="Settings sections"
          >
            <label className="settings-navigation-search">
              <span className="visually-hidden">Find a setting</span>
              <Search size={14} aria-hidden className="settings-search-icon" />
              <Input
                ref={search}
                type="search"
                placeholder="Search settings"
                value={query}
                aria-controls={searching ? `${resultId}-results` : undefined}
                aria-activedescendant={
                  current >= 0 ? optionId(current) : undefined
                }
                onChange={(event) => {
                  setQuery(event.target.value);
                  setActive(0);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Escape' && query) {
                    event.preventDefault();
                    setQuery('');
                  } else if (
                    (event.key === 'ArrowDown' || event.key === 'ArrowUp') &&
                    results.length
                  ) {
                    event.preventDefault();
                    const step = event.key === 'ArrowDown' ? 1 : -1;
                    setActive(
                      (current + step + results.length) % results.length,
                    );
                  } else if (event.key === 'Enter' && current >= 0) {
                    event.preventDefault();
                    openResult(results[current]);
                  }
                }}
              />
              {!query && (
                <span className="settings-search-kbd" aria-hidden>
                  <Kbd keys="/" />
                </span>
              )}
            </label>
            {searching ? (
              <div
                className="settings-search-results"
                id={`${resultId}-results`}
              >
                {pages.length > 0 && (
                  <div className="settings-nav-group">
                    <span className="settings-nav-label" aria-hidden>
                      Pages
                    </span>
                    <ul aria-label="Matching pages">
                      {pages.map((item, index) => (
                        <li key={item.id}>
                          <Link
                            {...optionProps(index)}
                            to={item.href}
                            aria-current={
                              item.id === leaf?.id ? 'page' : undefined
                            }
                            onClick={() => setQuery('')}
                          >
                            <SettingIcon id={item.id} size={16} />
                            <span>{item.label}</span>
                            <small aria-hidden>{item.category}</small>
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {(rows.length > 0 || profileLibrary) && (
                  <div className="settings-nav-group">
                    <span className="settings-nav-label" aria-hidden>
                      Settings
                    </span>
                    <ul aria-label="Matching settings">
                      {rows.map((row, index) => (
                        <li key={`${row.leaf}:${row.anchor}`}>
                          <Link
                            {...optionProps(pages.length + index)}
                            to={settingsRowHref(row)}
                            onClick={() => setQuery('')}
                          >
                            <SettingIcon id={row.leaf} size={16} />
                            <span>{row.label}</span>
                            <small aria-hidden>{leafLabel(row.leaf)}</small>
                          </Link>
                        </li>
                      ))}
                      {profileLibrary && (
                        // The sidebar's Agents dialog (B260).
                        <li>
                          <Link
                            {...optionProps(pages.length + rows.length)}
                            to={agentProfileLibrary.href}
                            onClick={() => setQuery('')}
                          >
                            <SettingIcon id="profiles" size={16} />
                            <span>{agentProfileLibrary.label}</span>
                            <small aria-hidden>Agents</small>
                          </Link>
                        </li>
                      )}
                    </ul>
                  </div>
                )}
                {!pages.length && !rows.length && !profileLibrary && (
                  <p className="muted settings-no-results">
                    No settings found.
                  </p>
                )}
              </div>
            ) : (
              <div className="settings-category-list">
                {settingsGroups.map((group) => (
                  <div className="settings-nav-group" key={group.id}>
                    {/* A label, not a heading: group names ("System", "Models")
                    repeat page titles. The list carries the name. */}
                    <span className="settings-nav-label" aria-hidden>
                      {group.label}
                    </span>
                    <ul aria-label={group.label}>
                      {leaves
                        .filter((item) => item.group === group.id)
                        .map((item) => (
                          <li key={item.id}>
                            <Link
                              to={item.href}
                              aria-current={
                                item.id === leaf?.id ? 'page' : undefined
                              }
                            >
                              <SettingIcon id={item.id} size={16} />
                              <span>{item.label}</span>
                            </Link>
                          </li>
                        ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </nav>
        )}
        {leaf && (
          <div
            className="settings-page-content"
            id="settings-page"
            ref={setContent}
          >
            <header className="settings-pane-header">
              {/* A phone names the page once, in the header row (D7). */}
              {!phone && (
                <span className="settings-pane-icon" aria-hidden>
                  <SettingIcon id={leaf.id} size={18} />
                </span>
              )}
              <div className="settings-pane-title">
                {!phone && (
                  <h2 ref={heading} tabIndex={-1}>
                    {leaf.label}
                  </h2>
                )}
                <p>
                  {leaf.id === 'system' && place.kind !== 'this_computer'
                    ? `Where Row-Bot works ${place.on}, and what it may do there.`
                    : (phone && phoneDescriptions[leaf.id]) ||
                      descriptions[leaf.id]}
                </p>
                <div className="settings-pane-status" ref={setStatusSlot} />
              </div>
              <div className="settings-pane-summary" ref={setSlot} />
            </header>
            <SettingsHeaderSlot.Provider value={slot}>
              <SettingsStatusSlot.Provider value={statusSlot}>
                {children}
              </SettingsStatusSlot.Provider>
            </SettingsHeaderSlot.Provider>
          </div>
        )}
      </section>
    </div>
  );
}
