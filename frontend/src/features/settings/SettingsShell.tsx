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
} from 'lucide-react';
import { Field, Select } from '../../ui/primitives';
import { settingsGroups, settingsLeaves } from './model';

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
        <span className="settings-page-icon" aria-hidden>
          <SettingIcon id={leaf.id} size={24} />
        </span>
        <div>
          <p className="eyebrow">Settings · {leaf.category}</p>
          <h1 ref={heading} tabIndex={-1}>
            {leaf.label}
          </h1>
          <p>
            Review saved local configuration. Actions run only when you choose
            them.
          </p>
        </div>
      </header>
      <div className="settings-compact-picker">
        <Field label="Settings section">
          <Select
            value={leaf.id}
            onChange={(event) =>
              navigate(`/settings/${encodeURIComponent(event.target.value)}`)
            }
          >
            {settingsGroups.map((group) => (
              <optgroup label={group.label} key={group.id}>
                {group.leaves.map((label) => {
                  const item = settingsLeaves.find(
                    (candidate) => candidate.label === label,
                  )!;
                  return (
                    <option value={item.id} key={item.id}>
                      {item.label}
                    </option>
                  );
                })}
              </optgroup>
            ))}
          </Select>
        </Field>
      </div>
      <div className="settings-shell-body">
        <nav
          className="settings-side-navigation"
          aria-label="Settings sections"
        >
          {settingsGroups.map((group) => (
            <section
              key={group.id}
              aria-labelledby={`settings-nav-${group.id}`}
            >
              <h2 id={`settings-nav-${group.id}`}>{group.label}</h2>
              <ul>
                {group.leaves.map((label) => {
                  const item = settingsLeaves.find(
                    (candidate) => candidate.label === label,
                  )!;
                  return (
                    <li key={item.id}>
                      <Link
                        to={item.href}
                        aria-current={item.id === leaf.id ? 'page' : undefined}
                      >
                        <SettingIcon id={item.id} />
                        <span>{item.label}</span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </nav>
        <div className="settings-page-content">{children}</div>
      </div>
    </section>
  );
}
