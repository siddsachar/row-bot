import { useEffect, useRef, type ComponentType, type ReactNode } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  Activity,
  Bot,
  Brain,
  Calculator,
  Cloud,
  Cpu,
  FileText,
  Library,
  Mic,
  Plug,
  Puzzle,
  Radio,
  Settings2,
  Shield,
  SlidersHorizontal,
  Sparkles,
  Target,
  UserRound,
  Wrench,
  X,
} from 'lucide-react';
import { Field, Select } from '../../ui/primitives';
import { settingsLeaves } from './model';

type SettingsLeaf = (typeof settingsLeaves)[number];
type Icon = ComponentType<{ size?: number; 'aria-hidden'?: boolean }>;

const icons: Record<string, Icon> = {
  providers: Cloud,
  models: Cpu,
  voice: Mic,
  knowledge: Brain,
  documents: FileText,
  wiki: Library,
  tools: Wrench,
  skills: Sparkles,
  mcp: Plug,
  plugins: Puzzle,
  accounts: UserRound,
  channels: Radio,
  buddy: Bot,
  goals: Target,
  tracker: Activity,
  utilities: Calculator,
  preferences: SlidersHorizontal,
  system: Shield,
};

const descriptions: Record<string, string> = {
  providers:
    'Connect model providers, review credential sources, refresh catalogs, and check provider health. Model pinning and defaults live in the Models tab.',
  models: 'Choose defaults, input models, and pinned catalogue choices.',
  knowledge: 'Manage memory, graph health, and stored knowledge.',
  wiki: 'Keep the local Wiki Vault in sync with saved knowledge.',
  buddy: 'Tune companion visibility, behaviour, look, and motion.',
  goals: 'Manage conversation goals and reusable agent profiles.',
  voice:
    'Configure Talk, Dictation, Realtime Talk Voice, normal read-aloud, voice models, and diagnostics.',
  system:
    'Control local access, command execution, browser automation, tunnels, and logs.',
  tracker: 'Track recurring activities, habits, symptoms, and health events.',
  documents:
    'Upload files, choose embedding engines, rebuild indexes, and manage source material.',
  tools:
    'Configure capability loading, retrieval compression, and research tools.',
  skills: 'Browse, create, enable, pin, audit, and maintain local skills.',
  accounts:
    'Connect GitHub, Google, and X accounts without exposing credentials.',
  channels:
    'Connect Row-Bot to external messaging platforms. Tunnel credentials live in System.',
  utilities: 'Lightweight productivity tools available to the assistant.',
  mcp: 'Configure external MCP servers, runtimes, permissions, and tested tools.',
  plugins:
    'Manage installed plugins, provenance, permissions, and configuration.',
  preferences:
    'Customize identity, launch behaviour, background intelligence, updates, and migration.',
};

export function SettingIcon({ id, size = 18 }: { id: string; size?: number }) {
  const Icon = icons[id] ?? Settings2;
  return <Icon size={size} aria-hidden />;
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
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
  }, [location.pathname]);
  return (
    <section className="settings-shell" aria-label="Settings">
      <header className="settings-shell-header">
        <Settings2 size={20} aria-hidden />
        <h1>Settings</h1>
        <Link className="icon-button" to="/" aria-label="Close settings">
          <X size={18} aria-hidden />
        </Link>
      </header>
      <div className="settings-compact-picker">
        <Field label="Settings section">
          <Select
            value={leaf.id}
            onChange={(event) =>
              navigate(`/settings/${encodeURIComponent(event.target.value)}`)
            }
          >
            {settingsLeaves.map((item) => (
              <option value={item.id} key={item.id}>
                {item.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <div className="settings-shell-body">
        <nav
          className="settings-side-navigation"
          aria-label="Settings sections"
        >
          <ul>
            {settingsLeaves.map((item) => (
              <li key={item.id}>
                <Link
                  to={item.href}
                  aria-current={item.id === leaf.id ? 'page' : undefined}
                >
                  <SettingIcon id={item.id} />
                  <span>{item.label}</span>
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <div className="settings-page-content">
          <header className="settings-pane-header">
            <SettingIcon id={leaf.id} size={22} />
            <div>
              <h2 ref={heading} tabIndex={-1}>
                {leaf.label}
              </h2>
              <p>{descriptions[leaf.id]}</p>
            </div>
          </header>
          {children}
        </div>
      </div>
    </section>
  );
}
