/**
 * Which GraphQL operations the relay passes on: exactly the ones row-bot.ai/railway/ makes.
 *
 * A request is one operation (a query or a mutation; no subscriptions, fragments or top-level
 * directives) whose top-level fields are all in the allow-list for its kind. Nested selections are
 * free: GraphQL only acts through top-level fields, so these names are what a request can do.
 *
 * Plain JavaScript with no Worker APIs, so the page's own tests can check every call it makes
 * against the same list (tests/docs/railway_page_runtime_test.cjs).
 */

export const ALLOWED = Object.freeze({
  query: Object.freeze(['me', 'regions', 'project', 'domains', 'deployment']),
  mutation: Object.freeze([
    'projectCreate',
    'serviceCreate',
    'volumeCreate',
    'serviceInstanceUpdate',
    'serviceInstanceLimitsUpdate',
    'serviceDomainCreate',
    'environmentPatchCommit',
    'serviceInstanceDeployV2',
  ]),
});

const PUNCTUATORS = new Set(['!', '$', '&', '(', ')', ':', '=', '@', '[', ']', '{', '|', '}']);
const NAME_START = /[A-Za-z_]/;
const NAME_PART = /[A-Za-z0-9_]/;
const NUMBER_PART = /[-+0-9.eE]/;
const IGNORED = new Set(['﻿', ' ', '\t', '\n', '\r', ',']);

class Refused extends Error {}

/** The document's tokens: names, punctuators, and placeholders for strings and numbers. */
function tokenize(source) {
  const tokens = [];
  let at = 0;
  while (at < source.length) {
    const char = source[at];
    if (IGNORED.has(char)) {
      at += 1;
    } else if (char === '#') {
      while (at < source.length && source[at] !== '\n' && source[at] !== '\r') at += 1;
    } else if (source.startsWith('...', at)) {
      tokens.push({ kind: 'punct', value: '...' });
      at += 3;
    } else if (PUNCTUATORS.has(char)) {
      tokens.push({ kind: 'punct', value: char });
      at += 1;
    } else if (source.startsWith('"""', at)) {
      at += 3;
      for (;;) {
        if (at >= source.length) throw new Refused('unterminated block string');
        if (source.startsWith('\\"""', at)) at += 4;
        else if (source.startsWith('"""', at)) { at += 3; break; }
        else at += 1;
      }
      tokens.push({ kind: 'string' });
    } else if (char === '"') {
      at += 1;
      for (;;) {
        if (at >= source.length || source[at] === '\n' || source[at] === '\r') throw new Refused('unterminated string');
        if (source[at] === '\\') at += 2;
        else if (source[at] === '"') { at += 1; break; }
        else at += 1;
      }
      tokens.push({ kind: 'string' });
    } else if (NAME_START.test(char)) {
      const start = at;
      while (at < source.length && NAME_PART.test(source[at])) at += 1;
      tokens.push({ kind: 'name', value: source.slice(start, at) });
    } else if (char === '-' || (char >= '0' && char <= '9')) {
      while (at < source.length && NUMBER_PART.test(source[at])) at += 1;
      tokens.push({ kind: 'number' });
    } else {
      throw new Refused('unexpected character');
    }
  }
  return tokens;
}

/**
 * The document's one operation as `{kind, fields}` (its top-level field names), or null when it
 * isn't a single plain query or mutation the relay can read.
 */
export function operationOf(source) {
  if (typeof source !== 'string') return null;
  try {
    const tokens = tokenize(source);
    let at = 0;
    const peek = () => tokens[at];
    const take = () => {
      if (at >= tokens.length) throw new Refused('unexpected end');
      return tokens[at++];
    };
    const isPunct = (token, value) => token !== undefined && token.kind === 'punct' && token.value === value;
    const skipBalanced = (open, close) => {
      let depth = 0;
      do {
        const token = take();
        if (isPunct(token, open)) depth += 1;
        else if (isPunct(token, close)) depth -= 1;
      } while (depth > 0);
    };

    let kind = 'query';
    if (peek()?.kind === 'name') {
      const keyword = take().value;
      if (keyword !== 'query' && keyword !== 'mutation') return null;
      kind = keyword;
      if (peek()?.kind === 'name') take();
      if (isPunct(peek(), '(')) skipBalanced('(', ')');
    }
    if (!isPunct(take(), '{')) return null;
    const fields = [];
    while (!isPunct(peek(), '}')) {
      let field = take();
      if (field.kind !== 'name') return null;
      if (isPunct(peek(), ':')) {
        take();
        field = take();
        if (field.kind !== 'name') return null;
      }
      fields.push(field.value);
      if (isPunct(peek(), '(')) skipBalanced('(', ')');
      if (isPunct(peek(), '@')) return null;
      if (isPunct(peek(), '{')) skipBalanced('{', '}');
    }
    take();
    if (at !== tokens.length || fields.length === 0) return null;
    return { kind, fields };
  } catch (error) {
    if (error instanceof Refused) return null;
    throw error;
  }
}

/** True when the document is one operation whose every top-level field the page uses. */
export function isAllowed(source) {
  const operation = operationOf(source);
  return operation !== null && operation.fields.every((field) => ALLOWED[operation.kind].includes(field));
}
