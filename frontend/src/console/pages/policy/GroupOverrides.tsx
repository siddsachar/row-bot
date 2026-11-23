import { useState } from 'react';
import { Pencil, Users } from 'lucide-react';
import {
  Button,
  EmptyState,
  Field,
  InlineEmpty,
  Input,
  Segmented,
  Select,
  SettingRow,
  Toggle,
} from '../../../ui/primitives';
import { useNotify } from '../../../ui/overlays';
import { request } from '../../api';
import { FormDialog } from '../../form-dialog';
import {
  canWiden,
  CHOICES,
  describe,
  labelOf,
  MODES,
  overrideDocument,
  overrideSettings,
  SPEC,
  SPECS,
  type Value,
  type Values,
} from '../../policy-model';
import type { PolicyDocument, PolicyWrite } from '../../types';
import { ListInput } from './controls';

const SAME = '';

/** Toggles for entries: each one on adds it to the override. */
function EntryToggles({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: string[];
  value: string[];
  onChange: (value: string[]) => void;
}) {
  return (
    <ul className="console-checklist" aria-label={label}>
      {options.map((name) => (
        <li key={name}>
          <span>{name}</span>
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
  );
}

/** The editor of one setting in an override: only choices that add something. */
function Widen({
  settingKey,
  company,
  catalog,
  value,
  onChange,
}: {
  settingKey: string;
  company: Values;
  catalog: string[];
  value: Value | undefined;
  onChange: (value: Value | undefined) => void;
}) {
  const spec = SPEC[settingKey];
  const label = labelOf(settingKey, true);
  const current = company[settingKey];
  if (spec.kind === 'bool')
    return (
      <Toggle
        label={label}
        checked={value === true}
        onChange={(event) => onChange(event.target.checked || undefined)}
      />
    );
  if (spec.kind === 'enum') {
    const higher = spec.choices.slice(
      spec.choices.indexOf(String(current)) + 1,
    );
    return (
      <Segmented
        label={label}
        size="sm"
        value={value === undefined ? SAME : String(value)}
        onChange={(choice) => onChange(choice === SAME ? undefined : choice)}
        options={[
          { value: SAME, label: `As everyone (${CHOICES[String(current)]})` },
          ...higher.map((choice) => ({
            value: choice,
            label: CHOICES[choice],
          })),
        ]}
      />
    );
  }
  if (spec.kind === 'int')
    return (
      <Input
        aria-label={label}
        className="console-number"
        type="number"
        min={Number(current) + 1}
        max={spec.maximum}
        placeholder={String(current)}
        value={value === undefined ? '' : String(value)}
        onChange={(event) =>
          onChange(
            event.target.value === '' ? undefined : Number(event.target.value),
          )
        }
      />
    );
  const list = (value as string[] | undefined) ?? [];
  const changeList = (next: string[]) =>
    onChange(next.length ? next : undefined);
  if (spec.kind === 'remove')
    return (
      <EntryToggles
        label={label}
        options={current as string[]}
        value={list}
        onChange={changeList}
      />
    );
  if (settingKey === 'models.allowed') {
    const options = [
      ...new Set(
        [...catalog, ...list].filter(
          (name) => !(current as string[]).includes(name),
        ),
      ),
    ];
    return options.length ? (
      <EntryToggles
        label={label}
        options={options}
        value={list}
        onChange={changeList}
      />
    ) : (
      <p className="console-setting-note">
        Everyone may already use every model in the catalog.
      </p>
    );
  }
  return (
    <ListInput
      label={label}
      placeholder="Names, separated by commas"
      value={list}
      onChange={changeList}
    />
  );
}

const WIDEN_HELP: Record<string, string> = {
  'models.allowed': 'Models this group may use on top of everyone’s.',
  'tools.disabled_groups':
    'Tool groups switched off for everyone that this group may use.',
  'tools.disabled_tools':
    'Single tools switched off for everyone that this group may use.',
  'offline.max_days': 'More days than everyone gets.',
};

function OverrideForm({
  group,
  document,
  company,
  catalog,
  onClose,
  onSaved,
}: {
  group: string;
  document: PolicyDocument | undefined;
  company: Values;
  catalog: string[];
  onClose: () => void;
  onSaved: (document: PolicyDocument) => void;
}) {
  const [values, setValues] = useState<Values>(() =>
    document ? overrideSettings(document) : {},
  );
  // An app's allow-list adds something only while that app is "Only these".
  const shown = (key: string) => {
    const mode = MODES[SPEC[key].name];
    return (
      !mode ||
      (values[`apps.${mode}`] ?? company[`apps.${mode}`]) === 'allow-list'
    );
  };
  const keys = SPECS.map((spec) => spec.key).filter(
    (key) => key in values || (canWiden(key, company) && shown(key)),
  );
  async function save() {
    const answer = await request<PolicyWrite>(
      `/api/admin/policy/groups/${encodeURIComponent(group)}`,
      { method: 'PUT', body: overrideDocument(values, document?.version ?? 0) },
    );
    onSaved(answer.policy);
  }
  return (
    <FormDialog
      title={`What ${group} gets`}
      description="Choose what this group gets on top of the company policy. Anything left as everyone’s follows the company policy."
      submitLabel="Save"
      onClose={onClose}
      onSubmit={save}
    >
      <div className="console-override-rows">
        {keys.map((key) => (
          <SettingRow
            key={key}
            label={labelOf(key, true)}
            description={
              WIDEN_HELP[key] ??
              (SPEC[key].kind === 'bool'
                ? 'Allowed for this group, though not for everyone.'
                : SPEC[key].kind === 'union'
                  ? 'Names this group may also use, separated by commas.'
                  : undefined)
            }
            control={
              <Widen
                settingKey={key}
                company={company}
                catalog={catalog}
                value={values[key]}
                onChange={(value) =>
                  setValues((current) => {
                    const next = { ...current };
                    if (value === undefined) delete next[key];
                    else next[key] = value;
                    return next;
                  })
                }
              />
            }
          />
        ))}
      </div>
    </FormDialog>
  );
}

export function GroupOverrides({
  company,
  groups,
  knownGroups,
  catalog,
  onSaved,
}: {
  company: Values;
  groups: Record<string, PolicyDocument>;
  knownGroups: string[];
  catalog: string[];
  onSaved: (group: string, document: PolicyDocument) => void;
}) {
  const notify = useNotify();
  const names = [...new Set([...knownGroups, ...Object.keys(groups)])].sort();
  const [group, setGroup] = useState(
    () =>
      names.find(
        (name) => Object.keys(overrideSettings(groups[name] ?? {})).length,
      ) ??
      names[0] ??
      '',
  );
  const [editing, setEditing] = useState(false);
  if (names.length === 0)
    return (
      <EmptyState title="No groups yet">
        Groups come from your identity provider when people sign in. Once
        someone in a group has signed in, you can give that group more than the
        company policy.
      </EmptyState>
    );
  const adds = Object.entries(overrideSettings(groups[group] ?? {}));
  return (
    <div className="console-policy">
      <p className="console-setting-note">
        An override can only add to the company policy. Restrictions belong in
        the company policy.
      </p>
      <div className="console-filters">
        <Field label="Group">
          <Select
            value={group}
            onChange={(event) => setGroup(event.target.value)}
          >
            {names.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
        </Field>
        <Button onClick={() => setEditing(true)}>
          <Pencil size={16} aria-hidden />
          Change what {group} gets
        </Button>
      </div>
      <section
        className="console-section surface"
        aria-labelledby="override-adds"
      >
        <header className="console-section-header">
          <h2 id="override-adds">What {group} gets on top</h2>
        </header>
        {adds.length === 0 ? (
          <InlineEmpty icon={<Users size={16} />}>
            Nothing: {group} gets the company policy.
          </InlineEmpty>
        ) : (
          <ul className="console-adds">
            {adds.map(([key, value]) => (
              <li key={key}>{describe(key, value, true)}</li>
            ))}
          </ul>
        )}
      </section>
      {editing && (
        <OverrideForm
          group={group}
          document={groups[group]}
          company={company}
          catalog={catalog}
          onClose={() => setEditing(false)}
          onSaved={(document) => {
            onSaved(group, document);
            notify(`${group}'s override is saved`);
          }}
        />
      )}
    </div>
  );
}
