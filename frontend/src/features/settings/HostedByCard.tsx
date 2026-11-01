import { Activity, CircleHelp, ExternalLink, Server } from 'lucide-react';
import type { SettingsSnapshot } from '../../api/types';
import { sizeWords } from '../../api/server-memory';
import { Hint } from '../../ui/primitives';
import { ROW_BOT_HELP } from '../shell/hosted-links';
import { SettingsGroup, SettingsItem } from './anatomy';

type System = SettingsSnapshot['system'];

/** Only a web page opens from the card, whatever a link says. */
function webPage(url: string | null | undefined): url is string {
  try {
    return ['https:', 'http:'].includes(new URL(url ?? '').protocol);
  } catch {
    return false;
  }
}

/** Memory in use as people read it: "1.4 GB". */
function inUse(bytes: number) {
  return `${Math.max(0.1, bytes / 2 ** 30).toFixed(1)} GB`;
}

/**
 * U5: the top of Settings › System on a Row-Bot that a known host runs (H13).
 * Who runs it, its version and who updates it, the memory in use of the
 * server's limit (H12, when the limit is known), and the host's own pages
 * (each only when the host confirmed it) as icon buttons. The host runs the
 * machine, billing and backups; Row-Bot's help covers the rest.
 */
export default function HostedByCard({
  host,
  memory,
  version,
}: {
  host: NonNullable<System['hosted_by']>;
  memory: System['memory'];
  version: string;
}) {
  const links = [
    {
      url: host.manage_url,
      label: `Manage your pod in ${host.name}`,
      Icon: ExternalLink,
    },
    { url: host.support_url, label: `${host.name} support`, Icon: CircleHelp },
    { url: host.status_url, label: `${host.name} status`, Icon: Activity },
  ].filter((link) => webPage(link.url));
  return (
    <SettingsGroup
      label={`Hosted by ${host.name}`}
      anchor="hosted-by"
      className="hosted-by"
    >
      <SettingsItem
        label={`Hosted by ${host.name}`}
        icon={<Server size={16} aria-hidden />}
        tone="neutral"
        help={`Row-Bot ${version} · updated by ${host.name}`}
        status={
          memory && (
            <span className="hosted-by-memory">
              <span>
                Memory {inUse(memory.used_bytes)} of{' '}
                {sizeWords(memory.limit_bytes)}
              </span>
              <progress
                aria-label="Memory in use"
                max={memory.limit_bytes}
                value={Math.min(memory.used_bytes, memory.limit_bytes)}
              />
            </span>
          )
        }
        bind={false}
        control={
          links.length > 0 && (
            <span className="hosted-by-links">
              {links.map(({ url, label, Icon }) => (
                <Hint key={label} label={label}>
                  <a
                    className="button ghost icon-button icon-action icon-action-md"
                    href={url!}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={label}
                  >
                    <Icon size={16} aria-hidden />
                  </a>
                </Hint>
              ))}
            </span>
          )
        }
      >
        <p className="hosted-by-note">
          {host.name} runs the server, billing and backups. For anything inside
          Row-Bot, use{' '}
          <a href={ROW_BOT_HELP} target="_blank" rel="noopener noreferrer">
            Row-Bot’s help
          </a>
          .
        </p>
      </SettingsItem>
    </SettingsGroup>
  );
}
