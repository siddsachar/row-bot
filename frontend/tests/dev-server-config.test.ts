import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

// Source-level contracts: importing vite.config would load esbuild inside
// jsdom, and the fonts are CSS files the test environment does not bundle.
const read = (path: string) =>
  readFileSync(new URL(path, import.meta.url), 'utf8');

it('proxies the API, remote access and packaged renderer runtimes to the same validated loopback backend', () => {
  const config = read('../vite.config.ts');
  const proxy = config.slice(
    config.indexOf('proxy: {'),
    config.indexOf('},', config.indexOf('proxy: {')),
  );
  expect(proxy).toContain("'/api/v1': loopbackProxy,");
  expect(proxy).toContain("'/api/access': loopbackProxy,");
  expect(proxy).toContain("'/app-v2/runtime/': loopbackProxy,");
  expect(proxy.match(/': /g)).toHaveLength(3);
  expect(config).toMatch(
    /const loopbackProxy: ProxyOptions = \{\s+target: backend\.origin,/,
  );
  expect(config).toContain(
    "throw new Error('ROW_BOT_DEV_BACKEND must be a plain HTTP loopback origin')",
  );
});

it('loads bundled Geist fonts from local package files, never a remote origin', () => {
  const css = read('../src/ui/styles/fonts.css');
  const sources = [...css.matchAll(/url\('([^']+)'\)/g)].map(
    (match) => match[1],
  );
  expect(sources).toHaveLength(4);
  for (const source of sources)
    expect(source).toMatch(/^@fontsource-variable\/geist(-mono)?\/files\//);
  expect(css).not.toMatch(/https?:|\/\/fonts\./);
  expect(css.match(/font-display: swap/g)).toHaveLength(4);
  const entry = read('../src/ui/styles/index.css');
  expect([...entry.matchAll(/@import '([^']+)'/g)].map((m) => m[1])).toEqual([
    './fonts.css',
    './tokens.css',
    '../styles.css',
    './primitives.css',
    './chat.css',
    './shell.css',
    './settings.css',
    './home.css',
    './panels.css',
    './setup.css',
  ]);
});
