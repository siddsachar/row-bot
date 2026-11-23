import { History } from 'lucide-react';
import { EmptyState, EntityList, EntityRow } from '../../../ui/primitives';
import { absoluteTime } from '../../../ui/format';
import { request } from '../../api';
import { useDirectory } from '../../directory';
import { Loaded, useLoad } from '../../load';
import {
  companySettings,
  describe,
  overrideSettings,
} from '../../policy-model';
import type { PolicyRevision } from '../../types';

export function PolicyHistory({ version }: { version: string }) {
  const { nameOf } = useDirectory();
  const history = useLoad(
    () =>
      request<{ entries: PolicyRevision[] }>(
        '/api/admin/policy/history?limit=100',
      ).then((answer) => answer.entries),
    version,
  );
  return (
    <Loaded
      load={history}
      label="Loading the policy history"
      what="the history"
    >
      {(entries) =>
        entries.length === 0 ? (
          <EmptyState title="No changes yet">
            Everyone has Row-Bot's default policy. Each change you save shows
            here, with who made it and when.
          </EmptyState>
        ) : (
          <EntityList label="Policy history">
            {entries.map((entry) => {
              const settings = Object.entries(
                entry.group
                  ? overrideSettings(entry.policy)
                  : companySettings(entry.policy),
              );
              return (
                <EntityRow
                  key={entry.version}
                  icon={<History size={18} />}
                  title={
                    entry.group ? `${entry.group}'s override` : 'Company policy'
                  }
                  meta={`Version ${entry.version} · ${nameOf(entry.changed_by)} · ${absoluteTime(entry.changed_at)}`}
                  details={
                    settings.length === 0 ? (
                      <p className="console-setting-note">
                        {entry.group
                          ? 'The override was taken away.'
                          : 'Everything at its default.'}
                      </p>
                    ) : (
                      <ul className="console-adds">
                        {settings.map(([key, value]) => (
                          <li key={key}>
                            {describe(key, value, Boolean(entry.group))}
                          </li>
                        ))}
                      </ul>
                    )
                  }
                />
              );
            })}
          </EntityList>
        )
      }
    </Loaded>
  );
}
