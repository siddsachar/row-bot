import { Link } from 'react-router-dom';
import { settingsGroups, resolveSetting } from './model';
export default function SettingsIndex() {
  return (
    <section
      className="route-surface stack capability-page"
      aria-label="Settings index"
    >
      <header className="capability-header">
        <div>
          <p className="eyebrow">Local application</p>
          <h1>Settings</h1>
          <p>Choose a capability to review its saved configuration.</p>
        </div>
      </header>
      {settingsGroups.map((group) => (
        <section
          className="stack capability-section"
          aria-labelledby={`settings-group-${group.id}`}
          key={group.id}
        >
          <h2 id={`settings-group-${group.id}`}>{group.label}</h2>
          <ul className="settings-results">
            {group.leaves.map((label) => (
              <li key={label}>
                <Link to={resolveSetting(label)!.href}>{label}</Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </section>
  );
}
