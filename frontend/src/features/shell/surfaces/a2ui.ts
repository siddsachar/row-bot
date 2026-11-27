/**
 * The A2UI 1.0 core a surface needs: reading the checked tree, JSON Pointer
 * paths into its data model, template scopes, and the basic catalog's
 * functions and checks. The server has already checked every tree
 * (`application/surfaces.py`), so this only draws what it was given: no
 * value here can run code or load anything.
 */

export type DataModel = Record<string, unknown>;

export type SurfaceComponent = {
  id: string;
  component: string;
  [property: string]: unknown;
};

export type SurfaceSpec = {
  surfaceId: string;
  title: string;
  catalogId: string;
  components: SurfaceComponent[];
  dataModel: DataModel;
  /** Private bindings: data paths Row-Bot fills in on this computer. */
  sources?: Record<string, { '@call': string; args?: Record<string, unknown> }>;
};

/** Where a template item's relative paths start: an absolute pointer and its index. */
export type Scope = { base: string; index: number } | undefined;

export type ValidationResult = { valid: boolean; message?: string };

export function readSurface(json: string): SurfaceSpec | null {
  try {
    const value = JSON.parse(json) as Partial<SurfaceSpec>;
    if (
      !value ||
      typeof value.surfaceId !== 'string' ||
      !Array.isArray(value.components) ||
      !value.components.some((component) => component?.id === 'root')
    )
      return null;
    return {
      surfaceId: value.surfaceId,
      title: typeof value.title === 'string' ? value.title : 'Interface',
      catalogId: String(value.catalogId ?? ''),
      components: value.components,
      dataModel:
        value.dataModel && typeof value.dataModel === 'object'
          ? (value.dataModel as DataModel)
          : {},
      sources:
        value.sources && typeof value.sources === 'object'
          ? value.sources
          : undefined,
    };
  } catch {
    return null;
  }
}

function parts(path: string): string[] {
  const trimmed = path.replace(/^\/+|\/+$/g, '');
  if (!trimmed) return [];
  return trimmed
    .split('/')
    .map((part) => part.replaceAll('~1', '/').replaceAll('~0', '~'));
}

/** A path made absolute: relative paths ("name") start at the template item. */
export function absolute(path: string, scope: Scope): string {
  if (path.startsWith('/') || !scope)
    return path.startsWith('/') ? path : `/${path}`;
  return `${scope.base.replace(/\/+$/, '')}/${path}`;
}

export function readPath(model: unknown, path: string, scope?: Scope): unknown {
  let node: unknown = model;
  for (const part of parts(absolute(path, scope))) {
    if (Array.isArray(node)) {
      const index = Number(part);
      node = Number.isInteger(index) ? node[index] : undefined;
    } else if (node && typeof node === 'object') {
      node = Object.hasOwn(node, part)
        ? (node as Record<string, unknown>)[part]
        : undefined;
    } else return undefined;
  }
  return node;
}

/** A copy of the model with the value at the path; missing objects are made on the way. */
export function writePath(
  model: DataModel,
  path: string,
  value: unknown,
  scope?: Scope,
): DataModel {
  const keys = parts(absolute(path, scope));
  if (!keys.length)
    return value && typeof value === 'object' && !Array.isArray(value)
      ? (value as DataModel)
      : model;
  const write = (node: unknown, index: number): unknown => {
    const key = keys[index];
    const last = index === keys.length - 1;
    if (Array.isArray(node)) {
      const position = Number(key);
      if (!Number.isInteger(position) || position < 0) return node;
      const copy = [...node];
      copy[position] = last ? value : write(copy[position], index + 1);
      return copy;
    }
    const base =
      node && typeof node === 'object' ? (node as Record<string, unknown>) : {};
    return {
      ...base,
      [key]: last ? value : write(base[key], index + 1),
    };
  };
  return write(model, 0) as DataModel;
}

type Call = { '@call': string; args?: Record<string, unknown> };

function isPath(value: unknown): value is { '@path': string } {
  return (
    !!value &&
    typeof value === 'object' &&
    typeof (value as { '@path'?: unknown })['@path'] === 'string'
  );
}

function isCall(value: unknown): value is Call {
  return (
    !!value &&
    typeof value === 'object' &&
    typeof (value as { '@call'?: unknown })['@call'] === 'string'
  );
}

/** The path a value is bound to, for inputs that write back. */
export function boundPath(value: unknown): string | null {
  return isPath(value) ? value['@path'] : null;
}

/** A value as shown in words: numbers grouped, nothing for null. */
export function asText(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number')
    return Number.isFinite(value)
      ? value.toLocaleString(undefined, { maximumFractionDigits: 4 })
      : '';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  try {
    return JSON.stringify(value);
  } catch {
    return '';
  }
}

const MAX_PATTERN = 256;
const MAX_CHECKED = 4096;

function safeRegex(pattern: string): RegExp | null {
  // The spec's limits: short patterns only, and no nested quantifiers (catastrophic backtracking).
  if (
    pattern.length > MAX_PATTERN ||
    /\([^)]*[+*}][^)]*\)\s*[+*{]/.test(pattern)
  )
    return null;
  try {
    return new RegExp(pattern, 'u');
  } catch {
    return null;
  }
}

function number(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function result(valid: boolean, message?: string): ValidationResult {
  return valid ? { valid: true } : { valid: false, message };
}

const MONTHS_LONG = (date: Date) =>
  date.toLocaleString(undefined, { month: 'long' });
const MONTHS_SHORT = (date: Date) =>
  date.toLocaleString(undefined, { month: 'short' });

/** A TR35 date pattern (the tokens A2UI lists), written out for one date. */
export function formatDate(value: unknown, format: string): string {
  const date =
    value instanceof Date
      ? value
      : new Date(typeof value === 'number' ? value : String(value ?? ''));
  if (Number.isNaN(date.getTime())) return asText(value);
  if (!format) return date.toLocaleString();
  const pad = (part: number) => String(part).padStart(2, '0');
  const hour12 = date.getHours() % 12 || 12;
  const tokens: Record<string, () => string> = {
    yyyy: () => String(date.getFullYear()),
    yy: () => pad(date.getFullYear() % 100),
    MMMM: () => MONTHS_LONG(date),
    MMM: () => MONTHS_SHORT(date),
    MM: () => pad(date.getMonth() + 1),
    M: () => String(date.getMonth() + 1),
    dd: () => pad(date.getDate()),
    d: () => String(date.getDate()),
    EEEE: () => date.toLocaleString(undefined, { weekday: 'long' }),
    E: () => date.toLocaleString(undefined, { weekday: 'short' }),
    HH: () => pad(date.getHours()),
    H: () => String(date.getHours()),
    hh: () => pad(hour12),
    h: () => String(hour12),
    mm: () => pad(date.getMinutes()),
    ss: () => pad(date.getSeconds()),
    a: () => (date.getHours() < 12 ? 'AM' : 'PM'),
  };
  return format.replace(
    /'([^']*)'|yyyy|yy|MMMM|MMM|MM|M|dd|d|EEEE|E|HH|H|hh|h|mm|ss|a/g,
    (token, quoted?: string) =>
      quoted !== undefined ? quoted : (tokens[token]?.() ?? token),
  );
}

/** Splits "a:1, b:'x, y'" at its top-level commas. */
function splitArgs(source: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let quote = '';
  let start = 0;
  for (let index = 0; index < source.length; index++) {
    const char = source[index];
    if (quote) {
      if (char === quote) quote = '';
    } else if (char === "'" || char === '"') quote = char;
    else if (char === '(' || char === '{') depth++;
    else if (char === ')' || char === '}') depth--;
    else if (char === ',' && depth === 0) {
      out.push(source.slice(start, index));
      start = index + 1;
    }
  }
  out.push(source.slice(start));
  return out.map((part) => part.trim()).filter(Boolean);
}

/** One `${…}` expression of formatString: a path, or a function call with named arguments. */
function expression(
  source: string,
  model: DataModel,
  scope: Scope,
  depth: number,
): unknown {
  const text = source.trim();
  const call = /^(@?[A-Za-z_]\w*)\(([\s\S]*)\)$/.exec(text);
  if (!call) return readPath(model, text, scope);
  const args: Record<string, unknown> = {};
  for (const part of splitArgs(call[2])) {
    const colon = part.indexOf(':');
    if (colon < 0) continue;
    const key = part.slice(0, colon).trim();
    const raw = part.slice(colon + 1).trim();
    const nested = /^\$\{([\s\S]*)\}$/.exec(raw);
    args[key] = nested
      ? expression(nested[1], model, scope, depth + 1)
      : /^'.*'$|^".*"$/.test(raw)
        ? raw.slice(1, -1)
        : raw === 'true' || raw === 'false'
          ? raw === 'true'
          : (number(raw) ?? raw);
  }
  return evaluate({ '@call': call[1], args }, model, scope, depth + 1, true);
}

function interpolate(
  template: string,
  model: DataModel,
  scope: Scope,
  depth: number,
): string {
  let out = '';
  let index = 0;
  while (index < template.length) {
    if (template.startsWith('\\${', index)) {
      out += '${';
      index += 3;
      continue;
    }
    if (template.startsWith('${', index)) {
      let level = 1;
      let end = index + 2;
      while (end < template.length && level > 0) {
        if (template.startsWith('${', end)) {
          level++;
          end += 2;
          continue;
        }
        if (template[end] === '}') level--;
        end++;
      }
      out += asText(
        expression(template.slice(index + 2, end - 1), model, scope, depth),
      );
      index = end;
      continue;
    }
    out += template[index];
    index++;
  }
  return out;
}

/**
 * A value of the surface: a literal, a path into the data model, or a
 * catalog function call. Arguments are evaluated first. A function the
 * catalog doesn't have, or one used where it isn't allowed, is null.
 */
export function evaluate(
  value: unknown,
  model: DataModel,
  scope: Scope,
  depth = 0,
  literalArgs = false,
): unknown {
  if (depth > 16) return null;
  if (isPath(value)) return readPath(model, value['@path'], scope);
  if (!isCall(value)) {
    if (Array.isArray(value))
      return value.map((item) => evaluate(item, model, scope, depth + 1));
    return value;
  }
  const raw = value.args ?? {};
  const arg = (key: string) =>
    literalArgs ? raw[key] : evaluate(raw[key], model, scope, depth + 1);
  const text = (key: string) => asText(arg(key));
  switch (value['@call']) {
    case '@index':
      return (scope?.index ?? 0) + (number(arg('offset')) ?? 0);
    case 'formatString':
      return interpolate(String(raw.value ?? ''), model, scope, depth);
    case 'formatNumber': {
      const amount = number(arg('value'));
      if (amount === null) return text('value');
      const decimals = number(arg('decimals'));
      return amount.toLocaleString(undefined, {
        useGrouping: arg('grouping') !== false,
        ...(decimals !== null
          ? { minimumFractionDigits: decimals, maximumFractionDigits: decimals }
          : { maximumFractionDigits: 2 }),
      });
    }
    case 'formatCurrency': {
      const amount = number(arg('value'));
      if (amount === null) return text('value');
      const decimals = number(arg('decimals'));
      try {
        return new Intl.NumberFormat(undefined, {
          style: 'currency',
          currency: text('currency') || 'USD',
          currencyDisplay: 'narrowSymbol',
          useGrouping: arg('grouping') !== false,
          ...(decimals !== null
            ? {
                minimumFractionDigits: decimals,
                maximumFractionDigits: decimals,
              }
            : {}),
        }).format(amount);
      } catch {
        return `${text('currency')} ${amount.toLocaleString()}`.trim();
      }
    }
    case 'formatDate':
      return formatDate(arg('value'), text('format'));
    case 'pluralize': {
      const count = number(arg('value')) ?? 0;
      const category = new Intl.PluralRules().select(count);
      const chosen = arg(category);
      return asText(
        chosen ?? (count === 0 ? arg('zero') : undefined) ?? arg('other'),
      );
    }
    case 'required': {
      const checked = arg('value');
      return result(
        !(
          checked === null ||
          checked === undefined ||
          checked === '' ||
          (Array.isArray(checked) && !checked.length)
        ),
      );
    }
    case 'regex': {
      const checked = text('value');
      const pattern = safeRegex(text('pattern'));
      return result(
        !!pattern && checked.length <= MAX_CHECKED && pattern.test(checked),
      );
    }
    case 'length': {
      const size = [...text('value')].length;
      const min = number(arg('min'));
      const max = number(arg('max'));
      return result(
        (min === null || size >= min) && (max === null || size <= max),
      );
    }
    case 'numeric': {
      const amount = number(arg('value'));
      const min = number(arg('min'));
      const max = number(arg('max'));
      return result(
        amount !== null &&
          (min === null || amount >= min) &&
          (max === null || amount <= max),
      );
    }
    case 'email':
      return result(/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(text('value')));
    case 'and': {
      const values = arg('values');
      return Array.isArray(values) && values.every((item) => truthy(item));
    }
    case 'or': {
      const values = arg('values');
      return Array.isArray(values) && values.some((item) => truthy(item));
    }
    case 'not':
      return !truthy(arg('value'));
    default:
      return null;
  }
}

/** A boolean or a validation result, as true or false. */
export function truthy(value: unknown): boolean {
  if (value && typeof value === 'object' && 'valid' in value)
    return Boolean((value as ValidationResult).valid);
  return value === true;
}

/** A component's checks: the first that fails, with its message. */
export function failedCheck(
  component: SurfaceComponent,
  model: DataModel,
  scope: Scope,
): string | null {
  const checks = component['@checks'];
  if (!Array.isArray(checks)) return null;
  for (const rule of checks as { condition: unknown; message?: string }[]) {
    const outcome = evaluate(rule.condition, model, scope);
    if (!truthy(outcome)) {
      const own =
        outcome && typeof outcome === 'object' && 'message' in outcome
          ? (outcome as ValidationResult).message
          : undefined;
      return rule.message || own || 'Check this value.';
    }
  }
  return null;
}

/** The URL a surface may draw media from: data URLs only. Anything else stays a link. */
export function drawableMedia(url: string, kind: 'image' | 'video' | 'audio') {
  return url.startsWith(`data:${kind}/`);
}

/** A link the person may open: http(s) and mailto only. */
export function openableUrl(url: string): URL | null {
  try {
    const parsed = new URL(url);
    return ['http:', 'https:', 'mailto:'].includes(parsed.protocol)
      ? parsed
      : null;
  } catch {
    return null;
  }
}

/**
 * Whether a button's context value would carry something Row-Bot fills in on
 * this computer (a source). Deliberately wide, the same rule as the server's
 * `surfaces.reads_source`: any mention of a source's path, and inside a list
 * repeated over a source, any binding or template at all. A press leaves such
 * values out, so they never reach the model.
 */
export function readsSource(
  value: unknown,
  sources: readonly string[],
  scope: Scope,
): boolean {
  const text = JSON.stringify(value) ?? '';
  for (const key of sources) {
    let at = text.indexOf(key);
    while (at >= 0) {
      if (!/[\w-]/.test(text[at + key.length] ?? '')) return true;
      at = text.indexOf(key, at + 1);
    }
  }
  const inSource =
    !!scope &&
    sources.some(
      (key) => scope.base === key || scope.base.startsWith(`${key}/`),
    );
  return inSource && (text.includes('"@path"') || text.includes('${'));
}
