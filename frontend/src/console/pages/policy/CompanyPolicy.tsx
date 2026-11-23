import { useState } from 'react';
import {
  Button,
  Disclosure,
  Segmented,
  SettingRow,
  Toggle,
} from '../../../ui/primitives';
import { useNotify } from '../../../ui/overlays';
import { request } from '../../api';
import { Notice } from '../../page';
import {
  ALL_MODELS,
  companyDocument,
  companyValues,
  grantText,
  same,
  type Value,
  type Values,
} from '../../policy-model';
import type { PolicyDocument, PolicyWrite } from '../../types';
import { problem } from '../../words';
import { ListInput, SettingControl } from './controls';

type Row = { key: string; label: string; help: string };
type Section = {
  id: string;
  title: string;
  description: string;
  rows: Row[];
  /** Rarely used settings, behind "More". */
  more?: Row[];
};

export const SECTIONS: Section[] = [
  {
    id: 'models',
    title: 'Models',
    description: 'Which models people may use, and where they may get others.',
    rows: [
      {
        key: 'models.personal_api_keys',
        label: 'Personal API keys',
        help: 'People may add their own API keys and custom endpoints.',
      },
      {
        key: 'models.consumer_sign_ins',
        label: 'Consumer sign-ins',
        help: 'People may sign in with a consumer subscription, such as a Claude plan or ChatGPT.',
      },
      {
        key: 'models.local_models',
        label: 'Local models',
        help: 'People may run models on their own computer, such as with Ollama or llama.cpp.',
      },
    ],
  },
  {
    id: 'tools',
    title: 'Tools and approvals',
    description:
      'What Row-Bot may do on people’s computers, and how far without asking.',
    rows: [
      {
        key: 'tools.disabled_groups',
        label: 'Tools switched off',
        help: 'Tool groups nobody may use, such as shell, browser or computer_use. Separate names with commas.',
      },
      {
        key: 'approvals.max_level',
        label: 'Highest approval level',
        help: 'The most people may let Row-Bot do without asking. Hands-off acts without asking at all.',
      },
    ],
    more: [
      {
        key: 'tools.disabled_tools',
        label: 'Single tools switched off',
        help: 'Individual tools nobody may use, such as run_command.',
      },
    ],
  },
  {
    id: 'apps',
    title: 'Apps',
    description:
      'What people may add to Row-Bot: none, only the ones you name, or any.',
    rows: [
      {
        key: 'apps.plugins',
        label: 'Plugins',
        help: 'Plugins run third-party code on people’s computers.',
      },
      {
        key: 'apps.mcp_servers',
        label: 'MCP servers',
        help: 'MCP servers connect Row-Bot to other tools and data, and can run code.',
      },
      {
        key: 'apps.skills_hub',
        label: 'Skills Hub',
        help: 'Skills people may install from the Skills Hub.',
      },
      {
        key: 'apps.channels',
        label: 'Channels',
        help: 'Where people may reach their Row-Bot, such as Slack, Discord, Telegram or SMS.',
      },
    ],
  },
  {
    id: 'data',
    title: 'Data',
    description: 'What people may take out of Row-Bot or offer to the company.',
    rows: [
      {
        key: 'data.chat_export',
        label: 'Chat export',
        help: 'People may export their chats and data.',
      },
      {
        key: 'data.share_to_company',
        label: 'Share to company',
        help: 'People may offer a document, fact or answer to the company. A space owner still approves it.',
      },
    ],
  },
  {
    id: 'offline',
    title: 'Offline',
    description:
      'How long Row-Bot keeps working when it can’t reach the company server.',
    rows: [
      {
        key: 'offline.max_days',
        label: 'Offline days',
        help: 'After this many days without the server, Row-Bot only chats with local models. 0 locks it down at once.',
      },
    ],
  },
];

/** The allow-list each app setting uses when it is "Only these". */
export const APP_LISTS: Record<string, string> = {
  'apps.plugins': 'apps.allowed_plugins',
  'apps.mcp_servers': 'apps.allowed_mcp_servers',
  'apps.skills_hub': 'apps.allowed_skills',
  'apps.channels': 'apps.allowed_channels',
};

function ModelsAllowed({
  value,
  catalog,
  onChange,
}: {
  value: string[];
  catalog: string[];
  onChange: (value: string[]) => void;
}) {
  const every = value.includes(ALL_MODELS);
  // Named in the policy but not in the catalog (yet): kept, and said so.
  const names = [...new Set([...catalog, ...value])].filter(
    (name) => name !== ALL_MODELS,
  );
  return (
    <div className="console-setting-stack">
      <Segmented<'every' | 'some'>
        label="Models people may use"
        size="sm"
        value={every ? 'every' : 'some'}
        onChange={(choice) =>
          onChange(choice === 'every' ? [ALL_MODELS] : [...catalog])
        }
        options={[
          { value: 'every', label: 'Every catalog model' },
          { value: 'some', label: 'Only these' },
        ]}
      />
      {!every &&
        (names.length === 0 ? (
          <p className="console-setting-note">
            The catalog has no models yet: add them under Models, then choose
            them here.
          </p>
        ) : (
          <ul className="console-checklist" aria-label="Allowed models">
            {names.map((name) => (
              <li key={name}>
                <span>
                  {name}
                  {!catalog.includes(name) && (
                    <small> Not in the catalog</small>
                  )}
                </span>
                <Toggle
                  label={name}
                  checked={value.includes(name)}
                  onChange={(event) =>
                    onChange(
                      event.target.checked
                        ? [...value, name]
                        : value.filter((item) => item !== name),
                    )
                  }
                />
              </li>
            ))}
          </ul>
        ))}
    </div>
  );
}

export function CompanyPolicy({
  document,
  catalog,
  groupsWithOverrides,
  onSaved,
}: {
  document: PolicyDocument;
  /** The catalog's model names. */
  catalog: string[];
  groupsWithOverrides: number;
  onSaved: (document: PolicyDocument) => void;
}) {
  const notify = useNotify();
  const [base, setBase] = useState(document);
  const saved = companyValues(base);
  const [values, setValues] = useState<Values>(saved);
  // Remounts the fields after Discard and Save, so typed text follows the values.
  const [generation, setGeneration] = useState(0);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PolicyWrite | null>(null);
  const changed = Object.keys(values).filter(
    (key) => !same(values[key], saved[key]),
  ).length;
  const set = (key: string, value: Value) =>
    setValues((current) => ({ ...current, [key]: value }));

  async function save() {
    setBusy(true);
    try {
      const answer = await request<PolicyWrite>('/api/admin/policy', {
        method: 'PUT',
        body: companyDocument(values, base),
      });
      setBase(answer.policy);
      setValues(companyValues(answer.policy));
      setGeneration((count) => count + 1);
      setResult(answer);
      onSaved(answer.policy);
      notify(
        'The company policy is saved. Row-Bots pick it up within minutes.',
      );
    } catch (error) {
      notify(problem(error), 'danger');
    } finally {
      setBusy(false);
    }
  }

  const stillGranted = Object.entries(result?.still_granted_by ?? {});
  const row = ({ key, label, help }: Row) => (
    <SettingRow
      key={key}
      label={label}
      description={help}
      modified={!same(values[key], saved[key])}
      control={
        <div className="console-setting-stack">
          <SettingControl
            settingKey={key}
            label={label}
            value={values[key]}
            onChange={(value) => set(key, value)}
          />
          {APP_LISTS[key] && values[key] === 'allow-list' && (
            <ListInput
              label={`Allowed ${label.toLowerCase()}`}
              placeholder="Names, separated by commas"
              value={values[APP_LISTS[key]] as string[]}
              onChange={(value) => set(APP_LISTS[key], value)}
            />
          )}
        </div>
      }
    />
  );

  return (
    <div className="console-policy" key={generation}>
      {stillGranted.length > 0 && (
        <Notice title="Some groups still get what you just took away">
          <p>Their overrides add it back. Change them under Group overrides.</p>
          <ul>
            {stillGranted.map(([group, paths]) => (
              <li key={group}>
                <strong>{group}</strong>: {paths.map(grantText).join('; ')}
              </li>
            ))}
          </ul>
        </Notice>
      )}
      {result && result.unknown_models.length > 0 && (
        <Notice tone="warning" title="Some models aren't in the catalog yet">
          {result.unknown_models.join(', ')}: people can use them once you add
          them to the catalog under Models.
        </Notice>
      )}
      {SECTIONS.map((section) => (
        <section
          key={section.id}
          className="console-section surface"
          aria-labelledby={`policy-${section.id}`}
        >
          <header className="console-section-header">
            <h2 id={`policy-${section.id}`}>{section.title}</h2>
            <p>{section.description}</p>
          </header>
          {section.id === 'models' && (
            <SettingRow
              label="Models people may use"
              description="Every model in the company catalog, or only the ones you choose."
              modified={
                !same(values['models.allowed'], saved['models.allowed'])
              }
              control={
                <ModelsAllowed
                  value={values['models.allowed'] as string[]}
                  catalog={catalog}
                  onChange={(value) => set('models.allowed', value)}
                />
              }
            />
          )}
          {section.rows.map(row)}
          {section.more && (
            <Disclosure summary="More">{section.more.map(row)}</Disclosure>
          )}
        </section>
      ))}
      {groupsWithOverrides > 0 && (
        <p className="console-footnote">
          {groupsWithOverrides === 1
            ? 'One group gets more than this through its override.'
            : `${groupsWithOverrides} groups get more than this through their overrides.`}
        </p>
      )}
      {changed > 0 && (
        <div
          className="console-savebar"
          role="region"
          aria-label="Unsaved changes"
        >
          <span>
            {changed === 1 ? '1 unsaved change' : `${changed} unsaved changes`}
          </span>
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => {
              setValues(saved);
              setGeneration((count) => count + 1);
            }}
          >
            Discard
          </Button>
          <Button variant="primary" disabled={busy} onClick={() => void save()}>
            {busy ? 'Saving…' : 'Save policy'}
          </Button>
        </div>
      )}
    </div>
  );
}
