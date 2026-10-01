/**
 * Lazily loaded syntax highlighting (Shiki, fine-grained bundle). Only the
 * JavaScript regex engine and the languages below are bundled, each grammar in
 * its own chunk that loads the first time a block uses it. Colours come from
 * CSS variables mapped onto the app's syntax tokens, so a theme change needs
 * no re-highlighting. Nothing is fetched from outside the app.
 */
import { createCssVariablesTheme, createHighlighterCore } from 'shiki/core';
import type { HighlighterCore, LanguageRegistration } from 'shiki/core';
import { createJavaScriptRegexEngine } from 'shiki/engine/javascript';
import { resolveLanguage } from './syntax-languages';

type Grammar = () => Promise<{ default: LanguageRegistration[] }>;

const GRAMMARS: Record<string, Grammar> = {
  javascript: () => import('shiki/langs/javascript.mjs'),
  typescript: () => import('shiki/langs/typescript.mjs'),
  jsx: () => import('shiki/langs/jsx.mjs'),
  tsx: () => import('shiki/langs/tsx.mjs'),
  json: () => import('shiki/langs/json.mjs'),
  python: () => import('shiki/langs/python.mjs'),
  shellscript: () => import('shiki/langs/shellscript.mjs'),
  powershell: () => import('shiki/langs/powershell.mjs'),
  sql: () => import('shiki/langs/sql.mjs'),
  html: () => import('shiki/langs/html.mjs'),
  css: () => import('shiki/langs/css.mjs'),
  scss: () => import('shiki/langs/scss.mjs'),
  xml: () => import('shiki/langs/xml.mjs'),
  yaml: () => import('shiki/langs/yaml.mjs'),
  toml: () => import('shiki/langs/toml.mjs'),
  ini: () => import('shiki/langs/ini.mjs'),
  markdown: () => import('shiki/langs/markdown.mjs'),
  diff: () => import('shiki/langs/diff.mjs'),
  dockerfile: () => import('shiki/langs/dockerfile.mjs'),
  go: () => import('shiki/langs/go.mjs'),
  rust: () => import('shiki/langs/rust.mjs'),
  java: () => import('shiki/langs/java.mjs'),
  kotlin: () => import('shiki/langs/kotlin.mjs'),
  swift: () => import('shiki/langs/swift.mjs'),
  c: () => import('shiki/langs/c.mjs'),
  cpp: () => import('shiki/langs/cpp.mjs'),
  csharp: () => import('shiki/langs/csharp.mjs'),
  php: () => import('shiki/langs/php.mjs'),
  ruby: () => import('shiki/langs/ruby.mjs'),
  lua: () => import('shiki/langs/lua.mjs'),
  r: () => import('shiki/langs/r.mjs'),
  graphql: () => import('shiki/langs/graphql.mjs'),
};

export type Token = { content: string; color?: string; fontStyle?: number };

const theme = createCssVariablesTheme({
  name: 'row-bot',
  variablePrefix: '--shiki-',
  fontStyle: true,
});

let highlighter: Promise<HighlighterCore> | null = null;
const loading = new Map<string, Promise<void>>();

function core() {
  highlighter ??= createHighlighterCore({
    themes: [theme],
    langs: [],
    engine: createJavaScriptRegexEngine({ forgiving: true }),
  });
  return highlighter;
}

async function ensure(id: string) {
  const instance = await core();
  if (instance.getLoadedLanguages().includes(id)) return instance;
  let pending = loading.get(id);
  if (!pending) {
    pending = GRAMMARS[id]().then((module) =>
      instance.loadLanguage(...module.default),
    );
    loading.set(id, pending);
    void pending.catch(() => loading.delete(id));
  }
  await pending;
  return instance;
}

/** Highlighted lines of tokens, or null when the language is not bundled. */
export async function highlight(
  code: string,
  language: string,
): Promise<Token[][] | null> {
  const id = resolveLanguage(language);
  if (!id) return null;
  const instance = await ensure(id);
  return instance.codeToTokens(code, { lang: id, theme: 'row-bot' }).tokens;
}
