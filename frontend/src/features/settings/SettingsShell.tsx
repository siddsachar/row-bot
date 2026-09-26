import {
  useEffect,
  useRef,
  useState,
  type ComponentType,
  type ReactNode,
} from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  Activity,
  ArrowUpCircle,
  Bot,
  Brain,
  Cloud,
  Cpu,
  Database,
  FileText,
  KeyRound,
  Mic,
  Palette,
  Plug,
  Puzzle,
  Radio,
  Search,
  Settings2,
  Shield,
  SlidersHorizontal,
  Sparkles,
  UserRound,
  UsersRound,
  Wrench,
  X,
} from 'lucide-react';
import { Field, Input, Kbd } from '../../ui/primitives';
import {
  searchSettings,
  searchSettingsRows,
  settingsGroups,
  settingsLeaves,
  settingsRowHref,
  type SettingsLeaf,
} from './model';
import { SettingsHeaderSlot, useSettingsAnchor } from './anatomy';

type Icon = ComponentType<{ size?: number; 'aria-hidden'?: boolean }>;

const icons: Record<string, Icon> = {
  preferences: SlidersHorizontal,
  appearance: Palette,
  buddy: Bot,
  providers: Cloud,
  models: Cpu,
  voice: Mic,
  knowledge: Brain,
  documents: FileText,
  tracker: Activity,
  tools: Wrench,
  skills: Sparkles,
  plugins: Puzzle,
  mcp: Plug,
  accounts: UserRound,
  channels: Radio,
  profiles: UsersRound,
  system: Shield,
  access: KeyRound,
  updates: ArrowUpCircle,
  data: Database,
};

const descriptions: Record<string, string> = {
  preferences: 'How Row-Bot introduces itself, opens and works overnight.',
  appearance: 'Theme, accent colour and density on this device.',
  buddy: 'Your companion’s visibility, personality, look and motion.',
  providers: 'Where Row-Bot’s models come from. Defaults live in Models.',
  models: 'Default, vision and image models, and the pinned catalog.',
  voice: 'Talk, dictation and read-aloud.',
  knowledge: 'The memory graph, the wiki vault and stored knowledge.',
  documents: 'Files Row-Bot can search, and how they are indexed.',
  tracker: 'Habits, symptoms and health events you track.',
  tools: 'Search, research and built-in tools the assistant can use.',
  skills: 'Reusable instructions: installed skills and public ones.',
  plugins: 'Installed plugins and the plugin marketplace.',
  mcp: 'External MCP servers, their tools, permissions and runtimes.',
  accounts: 'GitHub, Google and X accounts, without exposing credentials.',
  channels: 'Messaging platforms Row-Bot can talk through.',
  profiles: 'Profiles for delegated agents: built-in and your own.',
  system: 'Workspace folder, shell, browser, files and logs.',
  access: 'Remote access, tunnels, invitations and signed-in sessions.',
  updates: 'The installed version and how updates arrive.',
  data: 'Import from other assistants, and irreversible clean-up.',
};

export function SettingIcon({ id, size = 18 }: { id: string; size?: number }) {
  const Icon = icons[id] ?? Settings2;
  return <Icon size={size} aria-hidden />;
}

function typingTarget(target: EventTarget | null) {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable ||
      ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
  );
}

export default function SettingsShell({
  leaf,
  children,
}: {
  leaf: SettingsLeaf;
  children: ReactNode;
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const heading = useRef<HTMLHeadingElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const [content, setContent] = useState<HTMLDivElement | null>(null);
  const [slot, setSlot] = useState<HTMLDivElement | null>(null);
  const [query, setQuery] = useState('');
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
    if (content && !location.hash) content.scrollTop = 0;
    // Only a new page resets focus and scroll, not a new row anchor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname]);
  useSettingsAnchor(content);
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
  const searching = Boolean(query.trim());
  const pages = searching ? searchSettings(query) : [];
  const rows = searching ? searchSettingsRows(query) : [];
  const leafLabel = (id: string) =>
    settingsLeaves.find((item) => item.id === id)?.label ?? id;
  return (
    <section className="settings-shell" aria-label="Settings">
      <header className="settings-shell-header">
        <h1>Settings</h1>
        <Link className="icon-button" to="/" aria-label="Close settings">
          <X size={18} aria-hidden />
        </Link>
      </header>
      <div className="settings-compact-picker">
        <Field label="Settings section">
          <select
            className="input select"
            value={leaf.id}
            onChange={(event) =>
              navigate(`/settings/${encodeURIComponent(event.target.value)}`)
            }
          >
            {settingsGroups.map((group) => (
              <optgroup key={group.id} label={group.label}>
                {settingsLeaves
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
      <nav className="settings-side-navigation" aria-label="Settings sections">
        <label className="settings-navigation-search">
          <span className="visually-hidden">Find a setting</span>
          <Search size={14} aria-hidden className="settings-search-icon" />
          <Input
            ref={search}
            type="search"
            placeholder="Search settings"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape' && query) {
                event.preventDefault();
                setQuery('');
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
          <div className="settings-search-results">
            {pages.length > 0 && (
              <div className="settings-nav-group">
                <span className="settings-nav-label" aria-hidden>
                  Pages
                </span>
                <ul aria-label="Matching pages">
                  {pages.map((item) => (
                    <li key={item.id}>
                      <Link
                        to={item.href}
                        aria-current={item.id === leaf.id ? 'page' : undefined}
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
            {rows.length > 0 && (
              <div className="settings-nav-group">
                <span className="settings-nav-label" aria-hidden>
                  Settings
                </span>
                <ul aria-label="Matching settings">
                  {rows.map((row) => (
                    <li key={`${row.leaf}:${row.anchor}`}>
                      <Link
                        to={settingsRowHref(row)}
                        onClick={() => setQuery('')}
                      >
                        <SettingIcon id={row.leaf} size={16} />
                        <span>{row.label}</span>
                        <small aria-hidden>{leafLabel(row.leaf)}</small>
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {!pages.length && !rows.length && (
              <p className="muted settings-no-results">No settings found.</p>
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
                  {settingsLeaves
                    .filter((item) => item.group === group.id)
                    .map((item) => (
                      <li key={item.id}>
                        <Link
                          to={item.href}
                          aria-current={
                            item.id === leaf.id ? 'page' : undefined
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
      <div className="settings-page-content" ref={setContent}>
        <header className="settings-pane-header">
          <span className="settings-pane-icon" aria-hidden>
            <SettingIcon id={leaf.id} size={18} />
          </span>
          <div className="settings-pane-title">
            <h2 ref={heading} tabIndex={-1}>
              {leaf.label}
            </h2>
            <p>{descriptions[leaf.id]}</p>
          </div>
          <div className="settings-pane-summary" ref={setSlot} />
        </header>
        <SettingsHeaderSlot.Provider value={slot}>
          {children}
        </SettingsHeaderSlot.Provider>
      </div>
    </section>
  );
}
