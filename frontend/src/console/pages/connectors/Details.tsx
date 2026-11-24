import { Link2, Pencil } from 'lucide-react';
import {
  EntityList,
  EntityRow,
  IconButton,
  Stat,
  StatGroup,
} from '../../../ui/primitives';
import { When } from '../../../ui/When';
import { useDirectory } from '../../directory';
import { count } from '../../words';
import { Notice } from '../../page';
import type { ConnectionTest, Connector } from './types';
import { SOURCE, keyKind, keyName, noteWithoutChecked, plural } from './words';

export type TestShown = ConnectionTest | 'testing';

function TestNotice({ test }: { test: TestShown }) {
  if (test === 'testing') return <Notice title="Testing the connection…" />;
  return test.ok ? (
    <Notice title="The connection works">
      Row-Bot signed in and can read what this connector reads.
    </Notice>
  ) : (
    <Notice tone="warning" title="The connection test failed">
      {test.problem ?? 'The connection didn’t work.'}
    </Notice>
  );
}

function reads(connector: Connector) {
  const folder = connector.folder_id ? `folder ${connector.folder_id}` : '';
  if (connector.kind === 'microsoft')
    return folder
      ? `Drive ${connector.drive_id}, ${folder}`
      : `Drive ${connector.drive_id}, the whole drive`;
  const drive = connector.drive_id ? `Shared drive ${connector.drive_id}` : '';
  return [drive, folder].filter(Boolean).join(', ') || 'Not set';
}

/**
 * One connector's details: what needs a look first (a test's result, a
 * stopped or paused connector's permissions, files only admins can read,
 * files failing, skipped or left out at a limit), then what it holds, where
 * it reads and as whom, and who Row-Bot can't match, each with a way to map
 * them to a Row-Bot group.
 */
export function Details({
  connector,
  space,
  test,
  onMap,
}: {
  connector: Connector;
  space: string;
  test?: TestShown;
  /** Open the principal map editor, at this key when one is given. */
  onMap: (key: string | null) => void;
}) {
  const { nameOf } = useDirectory();
  const source = SOURCE[connector.kind];
  const map = connector.principal_map;
  const unmatched = new Set(connector.unmapped.map((item) => item.key));
  const mappedBefore = Object.keys(map).filter((key) => !unmatched.has(key));
  return (
    <div className="connector-details">
      {test && <TestNotice test={test} />}
      {connector.permissions_note && (
        <Notice
          tone={connector.state === 'error' ? 'warning' : 'info'}
          title={noteWithoutChecked(connector.permissions_note)}
        />
      )}
      {connector.unmapped_files > 0 && (
        <Notice
          tone="warning"
          title={`${plural(connector.unmapped_files, 'file')} only admins can read`}
        >
          {connector.unmapped_files === 1 ? 'It is' : 'They are'} shared with
          people or groups Row-Bot can’t match. Map each one below to a Row-Bot
          group, and its people can find the files again.
        </Notice>
      )}
      {connector.failing > 0 && (
        <Notice
          tone="warning"
          title={`${plural(connector.failing, 'file')} couldn’t be fetched`}
        >
          Row-Bot tries each again in a few minutes. A file that fails three
          times is skipped, and its document removed.
        </Notice>
      )}
      {connector.limit_warning && (
        <Notice tone="warning" title="Some files were left out">
          {connector.limit_warning}
        </Notice>
      )}
      {connector.skipped > 0 && (
        <Notice title={`${plural(connector.skipped, 'file')} skipped`}>
          Row-Bot reads text, Markdown and Word files
          {connector.kind === 'google' ? ' and Google Docs' : ''}, up to the
          upload limit. Other files, and files it couldn’t fetch, are counted
          but not read.
        </Notice>
      )}

      <StatGroup label={`What ${connector.name} holds`}>
        <Stat label="Files" value={count(connector.files)} />
        <Stat label="Documents" value={count(connector.documents)} />
        <Stat label="Skipped" value={count(connector.skipped)} />
        <Stat
          label="Admins only"
          value={count(connector.unmapped_files)}
          tone={connector.unmapped_files ? 'warning' : 'neutral'}
        />
        <Stat
          label="Failing"
          value={count(connector.failing)}
          tone={connector.failing ? 'warning' : 'neutral'}
        />
      </StatGroup>

      <dl className="console-details">
        <div>
          <dt>Who finds its files</dt>
          <dd>
            Only the people who can open a file in {source} can find it in
            Row-Bot.
          </dd>
        </div>
        <div>
          <dt>Permissions last checked</dt>
          <dd>
            <When value={connector.permissions_checked_at} fallback="Not yet" />
          </dd>
        </div>
        <div>
          <dt>Reads</dt>
          <dd>{reads(connector)}</dd>
        </div>
        <div>
          <dt>Space</dt>
          <dd>{space}</dd>
        </div>
        <div>
          <dt>Signs in</dt>
          <dd>
            {connector.kind === 'microsoft'
              ? `App ${connector.client_id} in tenant ${connector.tenant_id} · ${
                  connector.has_secret ? 'Secret saved' : 'No secret saved'
                }`
              : `${connector.client_id} · ${
                  connector.has_secret ? 'Key file saved' : 'No key file saved'
                }`}
          </dd>
        </div>
        {connector.subject && (
          <div>
            <dt>Acts as</dt>
            <dd>{connector.subject}</dd>
          </div>
        )}
        <div>
          <dt>Syncs as</dt>
          <dd>{nameOf(connector.run_as)}</dd>
        </div>
        <div>
          <dt>Last synced</dt>
          <dd>
            <When value={connector.last_sync_at} fallback="Not yet" />
          </dd>
        </div>
        {connector.state === 'active' && (
          <div>
            <dt>Next sync</dt>
            <dd>
              <When value={connector.next_sync_at} fallback="Soon" />
            </dd>
          </div>
        )}
      </dl>

      {connector.unmapped.length > 0 && (
        <section className="connector-section">
          <h3>People and groups Row-Bot can’t match</h3>
          <EntityList label={`Who ${connector.name} can’t match`}>
            {connector.unmapped.map((item) => (
              <EntityRow
                key={item.key}
                title={item.name}
                meta={
                  map[item.key]
                    ? `${keyKind(item.key)} · Mapped to ${map[item.key]}: applies when its files are read again`
                    : `${keyKind(item.key)} · ${plural(item.files, 'file')}`
                }
                action={
                  <IconButton
                    size="sm"
                    label={`Map ${item.name} to a Row-Bot group`}
                    onClick={() => onMap(item.key)}
                  >
                    <Link2 size={16} aria-hidden />
                  </IconButton>
                }
              />
            ))}
          </EntityList>
        </section>
      )}
      {mappedBefore.length > 0 && (
        <section className="connector-section">
          <div className="connector-section-header">
            <h3>Mapped to Row-Bot groups</h3>
            <IconButton
              size="sm"
              label="Change the mapped groups"
              onClick={() => onMap(null)}
            >
              <Pencil size={16} aria-hidden />
            </IconButton>
          </div>
          <ul className="connector-mapped">
            {mappedBefore.map((key) => (
              <li key={key}>
                {keyName(key)} → {map[key]}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
