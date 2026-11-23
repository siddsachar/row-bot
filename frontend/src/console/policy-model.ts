/**
 * The company policy in the console: its settings (defaults and choices come
 * from the published contract, contracts/policy/v1), the forms' values, and
 * the documents the server stores (only what they set).
 *
 * A company policy keeps every setting it already set and adds the ones the
 * admin changed; a setting left at its default and never set stays unset, so
 * a later default still reaches it. A group override holds only what it adds,
 * under its own names for the tools it switches back on.
 */
import schema from '../../../contracts/policy/v1/CompanyPolicy.schema.json';
import type { PolicyDocument } from './types';

export type Kind = 'bool' | 'enum' | 'int' | 'union' | 'remove';
export type Value = boolean | string | number | string[];
export type Values = Record<string, Value>;

export type Spec = {
  /** "models.allowed": the setting's section and its company policy name. */
  key: string;
  section: string;
  name: string;
  kind: Kind;
  fallback: Value;
  choices: string[];
  /** Its name in a group override (the tools it switches back on). */
  overrideName: string;
  maximum?: number;
};

type SchemaProperty = {
  type: string;
  default: Value;
  enum?: string[];
  maximum?: number;
};
const SECTIONS = schema.properties as unknown as Record<
  string,
  { properties?: Record<string, SchemaProperty> }
>;

export const SPECS: Spec[] = Object.entries(SECTIONS).flatMap(
  ([section, entry]) =>
    Object.entries(entry.properties ?? {}).map(([name, property]) => {
      const remove = property.type === 'array' && section === 'tools';
      return {
        key: `${section}.${name}`,
        section,
        name,
        kind:
          property.type === 'boolean'
            ? 'bool'
            : property.type === 'integer'
              ? 'int'
              : property.type === 'string'
                ? 'enum'
                : remove
                  ? 'remove'
                  : 'union',
        fallback: property.default,
        choices: property.enum ?? [],
        overrideName: remove ? name.replace('disabled_', 'enabled_') : name,
        maximum: property.maximum,
      } satisfies Spec;
    }),
);
export const SPEC = Object.fromEntries(SPECS.map((spec) => [spec.key, spec]));
export const ALL_MODELS = '*';

export const DEFAULTS: Values = Object.fromEntries(
  SPECS.map((spec) => [spec.key, spec.fallback]),
);

/** Whether two values are the same setting (lists in any order). */
export function same(a: Value | undefined, b: Value | undefined) {
  if (!Array.isArray(a) || !Array.isArray(b)) return a === b;
  const left = [...a].sort();
  const right = [...b].sort();
  return left.length === right.length && left.every((v, i) => v === right[i]);
}

function normal(value: Value): Value {
  if (!Array.isArray(value)) return value;
  const entries = [
    ...new Set(value.map((entry) => entry.trim()).filter(Boolean)),
  ];
  return entries.includes(ALL_MODELS) ? [ALL_MODELS] : entries.sort();
}

function read(document: PolicyDocument, override: boolean): Values {
  const values: Values = {};
  for (const spec of SPECS) {
    const section = document[spec.section];
    if (!section || typeof section !== 'object') continue;
    const name = override ? spec.overrideName : spec.name;
    if (name in section) values[spec.key] = section[name] as Value;
  }
  return values;
}

function write(values: Values, override: boolean, version: number) {
  const document: PolicyDocument = { version };
  for (const spec of SPECS) {
    if (!(spec.key in values)) continue;
    const section = (document[spec.section] ?? {}) as Record<string, unknown>;
    section[override ? spec.overrideName : spec.name] = normal(
      values[spec.key],
    );
    document[spec.section] = section;
  }
  return document;
}

/** What a company policy document sets (its version aside), by setting key. */
export const companySettings = (document: PolicyDocument) =>
  read(document, false);

/** Every setting's value under a company policy: what it sets, else the default. */
export function companyValues(document: PolicyDocument): Values {
  return { ...DEFAULTS, ...companySettings(document) };
}

/** The company policy to write from the form: what `original` set, and what changed. */
export function companyDocument(
  values: Values,
  original: PolicyDocument,
): PolicyDocument {
  const set = companySettings(original);
  const kept = Object.fromEntries(
    Object.entries(values).filter(
      ([key, value]) => key in set || !same(value, DEFAULTS[key]),
    ),
  );
  return write(kept, false, original.version ?? 0);
}

/** What a group override adds, by setting key (its own names read back). */
export const overrideSettings = (document: PolicyDocument) =>
  read(document, true);

/** The group override to write: only the settings it adds. */
export function overrideDocument(values: Values, version: number) {
  return write(values, true, version);
}

/** Whether a group may be given more than the company policy for `key`. */
export function canWiden(key: string, company: Values): boolean {
  const spec = SPEC[key];
  const current = company[key];
  if (spec.kind === 'bool') return current === false;
  if (spec.kind === 'enum')
    return spec.choices.indexOf(String(current)) < spec.choices.length - 1;
  if (spec.kind === 'int') return Number(current) < (spec.maximum ?? Infinity);
  if (spec.kind === 'remove') return (current as string[]).length > 0;
  if (key === 'models.allowed')
    return !(current as string[]).includes(ALL_MODELS);
  const mode = MODES[spec.name];
  return company[`apps.${mode}`] !== 'any';
}

// -- plain words --------------------------------------------------------------------------------

/** The mode setting of each app allow-list. */
export const MODES: Record<string, string> = {
  allowed_plugins: 'plugins',
  allowed_mcp_servers: 'mcp_servers',
  allowed_skills: 'skills_hub',
  allowed_channels: 'channels',
};

export const LABELS: Record<string, string> = {
  'models.allowed': 'Models people may use',
  'models.personal_api_keys': 'Personal API keys',
  'models.consumer_sign_ins': 'Consumer sign-ins',
  'models.local_models': 'Local models',
  'tools.disabled_groups': 'Tool groups switched off',
  'tools.disabled_tools': 'Single tools switched off',
  'approvals.max_level': 'Highest approval level',
  'apps.plugins': 'Plugins',
  'apps.allowed_plugins': 'Allowed plugins',
  'apps.mcp_servers': 'MCP servers',
  'apps.allowed_mcp_servers': 'Allowed MCP servers',
  'apps.skills_hub': 'Skills Hub',
  'apps.allowed_skills': 'Allowed Skills Hub skills',
  'apps.channels': 'Channels',
  'apps.allowed_channels': 'Allowed channels',
  'data.chat_export': 'Chat export',
  'data.share_to_company': 'Share to company',
  'offline.max_days': 'Offline days',
};

const OVERRIDE_LABELS: Record<string, string> = {
  'tools.disabled_groups': 'Tool groups switched back on',
  'tools.disabled_tools': 'Single tools switched back on',
};

export const CHOICES: Record<string, string> = {
  block: 'Block',
  approve: 'Ask',
  auto: 'Auto',
  full: 'Hands-off',
  none: 'None',
  'allow-list': 'Only these',
  any: 'Any',
};

export function valueText(key: string, value: Value): string {
  const spec = SPEC[key];
  if (spec.kind === 'bool') return value ? 'Allowed' : 'Not allowed';
  if (spec.kind === 'enum') return CHOICES[String(value)] ?? String(value);
  if (spec.kind === 'int')
    return `${value} ${Number(value) === 1 ? 'day' : 'days'}`;
  const entries = value as string[];
  if (entries.includes(ALL_MODELS)) return 'Every catalog model';
  return entries.length ? entries.join(', ') : 'None';
}

/** A setting's name, as a company policy or a group override words it. */
export const labelOf = (key: string, override = false) =>
  (override && OVERRIDE_LABELS[key]) || LABELS[key] || key;

/** One setting as a line: "Personal API keys: Allowed". */
export function describe(key: string, value: Value, override = false): string {
  return `${labelOf(key, override)}: ${valueText(key, value)}`;
}

/**
 * The server's `still_granted_by` entries ("models.personal_api_keys",
 * "tools.enabled_groups: shell") in plain words.
 */
export function grantText(path: string): string {
  const [setting, entry] = path.split(/: (.*)/s);
  const spec = SPECS.find(
    (item) => `${item.section}.${item.overrideName}` === setting,
  );
  if (!spec) return path;
  const label = OVERRIDE_LABELS[spec.key] ?? LABELS[spec.key];
  if (entry === undefined) return label;
  return `${label}: ${CHOICES[entry] ?? entry}`;
}

/** Comma-separated entries from a text field ("shell, browser"). */
export const entries = (text: string) =>
  text
    .split(/[,\s]+/)
    .map((entry) => entry.trim())
    .filter(Boolean);
