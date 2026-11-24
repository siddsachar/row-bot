import { useId, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight, Moon, Settings2 } from 'lucide-react';
import { ThemeChoice } from './Appearance';
import DeviceNotifications from './DeviceNotifications';
import {
  settingsGroups,
  settingsLeaves,
  type SettingsLeaf,
  type SettingsLeafId,
} from './model';
import { SettingIcon } from './settings-icons';
import '../../ui/styles/phone-settings.css';

/**
 * The Row-Bot group of a phone's Settings list (D6 A), in this order: what a
 * phone is for. Buddy's row opens Buddy & Proactive.
 */
export const PHONE_ROW_BOT_PAGES: SettingsLeafId[] = [
  'models',
  'voice',
  'knowledge',
  'apps',
  'activity',
  'buddy',
];

/** Pages a phone reaches from a Row-Bot row, not from More settings. */
const UNDER_ROW_BOT = new Set<SettingsLeafId>([
  ...PHONE_ROW_BOT_PAGES,
  'proactive',
]);

/** A page's name on a phone: Buddy's page also leads to Proactive. */
export function phoneTitle(leaf: SettingsLeaf): string {
  return leaf.id === 'buddy' ? 'Buddy & Proactive' : leaf.label;
}

/**
 * Where a page's ‹ goes on a phone: the list it is reached from. Proactive
 * is reached from Buddy & Proactive, and the pages outside Row-Bot from More
 * settings.
 */
export function phoneParent(id: SettingsLeafId): {
  href: string;
  label: string;
} {
  if (id === 'proactive')
    return { href: '/settings/buddy', label: 'Back to Buddy & Proactive' };
  if (UNDER_ROW_BOT.has(id))
    return { href: '/settings', label: 'Back to Settings' };
  return { href: '/settings?view=more', label: 'Back to More settings' };
}

/**
 * One row of a phone's list that opens a page: its icon, its name, an
 * optional line under it, and a chevron. The line describes the link; it is
 * not part of its name.
 */
export function PhoneLinkRow({
  to,
  icon,
  title,
  description,
}: {
  to: string;
  icon: ReactNode;
  title: string;
  description?: string;
}) {
  const id = useId();
  return (
    <li className="phone-row">
      <Link
        className="phone-row-main"
        to={to}
        aria-label={description ? title : undefined}
        aria-describedby={description ? id : undefined}
      >
        <span className="phone-row-icon" aria-hidden>
          {icon}
        </span>
        <span className="phone-row-text">
          <span className="phone-row-title">{title}</span>
          {description && (
            <span className="phone-row-meta" id={id}>
              {description}
            </span>
          )}
        </span>
        <ChevronRight
          className="phone-settings-chevron"
          size={18}
          aria-hidden
        />
      </Link>
    </li>
  );
}

function PageRow({ leaf }: { leaf: SettingsLeaf }) {
  return (
    <PhoneLinkRow
      to={leaf.href}
      icon={<SettingIcon id={leaf.id} size={18} />}
      title={phoneTitle(leaf)}
    />
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section className="phone-settings-group" aria-labelledby={id}>
      <h2 id={id}>{title}</h2>
      <ul className="phone-chat-list-rows" aria-labelledby={id}>
        {children}
      </ul>
    </section>
  );
}

/**
 * A phone's Settings (C3, D6 A, D13 A): This phone (notifications and
 * theme), the six Row-Bot pages, and one row to More settings, which lists
 * every other page, grouped as on the computer. Every page stays open on
 * the phone.
 */
export default function PhoneSettingsList({
  more = false,
}: {
  /** The second list: every page outside This phone and Row-Bot. */
  more?: boolean;
}) {
  const leaves = settingsLeaves;
  if (more)
    return (
      <div className="phone-settings">
        {settingsGroups.map((group) => {
          const pages = leaves.filter(
            (item) => item.group === group.id && !UNDER_ROW_BOT.has(item.id),
          );
          return pages.length ? (
            <Group key={group.id} title={group.label}>
              {pages.map((item) => (
                <PageRow key={item.id} leaf={item} />
              ))}
            </Group>
          ) : null;
        })}
      </div>
    );
  return (
    <div className="phone-settings">
      <Group title="This phone">
        <li className="phone-row">
          <DeviceNotifications variant="row" />
        </li>
        <li className="phone-row phone-settings-theme-row">
          <div className="phone-settings-theme-head" aria-hidden>
            <span className="phone-row-icon">
              <Moon size={18} />
            </span>
            <span className="phone-row-title">Theme</span>
          </div>
          <ThemeChoice />
        </li>
      </Group>
      <Group title="Row-Bot">
        {PHONE_ROW_BOT_PAGES.map((id) => (
          <PageRow key={id} leaf={leaves.find((item) => item.id === id)!} />
        ))}
      </Group>
      <ul
        className="phone-chat-list-rows phone-settings-more"
        aria-label="More"
      >
        <PhoneLinkRow
          to="/settings?view=more"
          icon={<Settings2 size={18} />}
          title="More settings"
          description="Devices, tools, skills, data…"
        />
      </ul>
    </div>
  );
}
