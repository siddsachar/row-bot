import { useSearchParams } from 'react-router-dom';
import { Tabs } from '../../ui/primitives';
import { ApiError, request } from '../api';
import { useDirectory } from '../directory';
import { Loaded, useLoad } from '../load';
import { Page } from '../page';
import { companyValues, overrideSettings } from '../policy-model';
import type { CatalogModel, PolicyDocument } from '../types';
import { CompanyPolicy } from './policy/CompanyPolicy';
import { GroupOverrides } from './policy/GroupOverrides';
import { PolicyHistory } from './policy/PolicyHistory';

type Current = {
  company: PolicyDocument;
  groups: Record<string, PolicyDocument>;
};

async function loadPolicy() {
  const [current, catalog] = await Promise.all([
    request<Current>('/api/admin/policy'),
    // A server without the model gateway has no catalog.
    request<{ models: CatalogModel[] }>('/api/admin/models').catch(
      (error: unknown) => {
        if (error instanceof ApiError && error.status === 404)
          return { models: [] };
        throw error;
      },
    ),
  ]);
  return {
    ...current,
    catalog: catalog.models.map((model) => model.name),
  };
}

const TABS = ['company', 'groups', 'history'];

export default function PolicyPage() {
  const { groups: knownGroups } = useDirectory();
  const [params, setParams] = useSearchParams();
  const tab = TABS.includes(params.get('tab') ?? '')
    ? (params.get('tab') as string)
    : 'company';
  const policy = useLoad(loadPolicy);

  return (
    <Page
      title="Policy"
      description="What everyone's Row-Bot may do. Connected Row-Bots fetch it at sign-in and every few minutes."
    >
      <Loaded load={policy} label="Loading the policy" what="the policy">
        {(current) => {
          const versions = [
            current.company.version ?? 0,
            ...Object.values(current.groups).map((group) => group.version ?? 0),
          ].join('.');
          const overrides = Object.values(current.groups).filter(
            (group) => Object.keys(overrideSettings(group)).length > 0,
          ).length;
          return (
            <Tabs
              label="Policy"
              value={tab}
              onChange={(value) => setParams({ tab: value }, { replace: true })}
              items={[
                {
                  id: 'company',
                  label: 'Company policy',
                  content: (
                    <CompanyPolicy
                      document={current.company}
                      catalog={current.catalog}
                      groupsWithOverrides={overrides}
                      onSaved={(company) =>
                        policy.update((data) => ({ ...data, company }))
                      }
                    />
                  ),
                },
                {
                  id: 'groups',
                  label: 'Group overrides',
                  content: (
                    <GroupOverrides
                      company={companyValues(current.company)}
                      groups={current.groups}
                      knownGroups={knownGroups}
                      catalog={current.catalog}
                      onSaved={(group, document) =>
                        policy.update((data) => ({
                          ...data,
                          groups: { ...data.groups, [group]: document },
                        }))
                      }
                    />
                  ),
                },
                {
                  id: 'history',
                  label: 'History',
                  content: <PolicyHistory version={versions} />,
                },
              ]}
            />
          );
        }}
      </Loaded>
    </Page>
  );
}
