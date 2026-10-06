import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { spawnSync } from 'node:child_process';
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmdirSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

// Each test runs the packaging script in its own Node process, which a busy machine
// can take seconds to start.
vi.setConfig({ testTimeout: 30_000 });

const script = resolve('scripts/asset-manifest.mjs');
const ownedNames = [
  'index.html',
  'buddy-overlay.html',
  'asset-manifest.json',
  '.vite/manifest.json',
  'assets/index-abcdefgh.js',
  'assets/buddy-overlay-abcdefgh.js',
  'app.webmanifest',
  'service-worker.js',
  'icon-192.png',
  'icon-512.png',
  'private.txt',
];
// Vite emits worker bundles without a manifest entry of their own.
const worker = 'assets/layout.worker-abcdefgh.js';
let scratch: string;
let build: string;
let stage: string;

beforeEach(() => {
  scratch = mkdtempSync(join(tmpdir(), 'row-bot-client-assets-'));
  build = join(scratch, 'build');
  stage = join(scratch, 'stage');
  mkdirSync(join(build, 'assets'), { recursive: true });
  mkdirSync(join(build, '.vite'));
  writeFileSync(join(build, 'index.html'), '<html>fixture</html>');
  writeFileSync(join(build, 'buddy-overlay.html'), '<html>buddy</html>');
  for (const name of [
    'app.webmanifest',
    'service-worker.js',
    'icon-192.png',
    'icon-512.png',
  ])
    writeFileSync(join(build, name), `public ${name} fixture`);
  writeFileSync(
    join(build, 'assets/index-abcdefgh.js'),
    'export const fixture = true;',
  );
  writeFileSync(
    join(build, 'assets/buddy-overlay-abcdefgh.js'),
    'export const buddy = true;',
  );
  writeFileSync(
    join(build, '.vite/manifest.json'),
    JSON.stringify({
      'index.html': { file: 'assets/index-abcdefgh.js', isEntry: true },
      'buddy-overlay.html': {
        file: 'assets/buddy-overlay-abcdefgh.js',
        isEntry: true,
      },
    }),
  );
});

afterEach(() => {
  // Remove only known fixture files/directories; never recursively delete output.
  for (const root of [stage, build]) {
    for (const name of [...ownedNames, worker]) {
      const path = join(root, name);
      if (existsSync(path)) unlinkSync(path);
    }
    for (const path of [join(root, 'assets'), join(root, '.vite'), root]) {
      if (existsSync(path)) rmdirSync(path);
    }
  }
  if (existsSync(join(scratch, 'private.txt')))
    unlinkSync(join(scratch, 'private.txt'));
  rmdirSync(scratch);
});

const packageFixture = () =>
  spawnSync(process.execPath, [script, build, '--package-dir', stage], {
    encoding: 'utf8',
    timeout: 25_000,
    // Do not inherit credentials, Node preload options or provider settings.
    env: { SystemRoot: process.env.SystemRoot ?? '' },
  });

it('stages exactly the inventoried assets and both private manifests', () => {
  writeFileSync(join(build, 'private.txt'), 'unlisted fixture');
  expect(packageFixture().status).toBe(0);
  for (const name of ownedNames.slice(0, -1)) {
    expect(readFileSync(join(stage, name))).toEqual(
      readFileSync(join(build, name)),
    );
  }
  expect(existsSync(join(stage, 'private.txt'))).toBe(false);
});

it('inventories and stages the workers the built scripts start', () => {
  writeFileSync(
    join(build, 'assets/index-abcdefgh.js'),
    'const w=new Worker(new URL("/app-v2/assets/layout.worker-abcdefgh.js",import.meta.url),{type:"module"});',
  );
  writeFileSync(join(build, worker), 'self.onmessage=()=>{};');
  expect(packageFixture().status).toBe(0);
  const inventory = JSON.parse(
    readFileSync(join(build, 'asset-manifest.json'), 'utf8'),
  ) as { files: Record<string, { size: number }> };
  expect(inventory.files[worker].size).toBe(22);
  expect(readFileSync(join(stage, worker), 'utf8')).toBe(
    'self.onmessage=()=>{};',
  );
});

it('fails a build whose script starts a worker that is missing', () => {
  writeFileSync(
    join(build, 'assets/index-abcdefgh.js'),
    'const w=new Worker(new URL("/app-v2/assets/layout.worker-abcdefgh.js",import.meta.url),{type:"module"});',
  );
  expect(packageFixture().status).toBe(1);
  expect(existsSync(stage)).toBe(false);
});

it('refuses a build without the desktop Buddy document', () => {
  writeFileSync(
    join(build, '.vite/manifest.json'),
    JSON.stringify({
      'index.html': { file: 'assets/index-abcdefgh.js', isEntry: true },
    }),
  );
  const result = packageFixture();
  expect(result.status).toBe(1);
  expect(result.stderr).toContain(
    'Missing shell document entry: buddy-overlay.html',
  );
  expect(existsSync(stage)).toBe(false);
});

it('refuses to merge or delete an existing staging directory', () => {
  mkdirSync(stage);
  writeFileSync(join(stage, 'private.txt'), 'preserved fixture');
  expect(packageFixture().status).toBe(1);
  expect(readFileSync(join(stage, 'private.txt'), 'utf8')).toBe(
    'preserved fixture',
  );
  expect(existsSync(join(stage, 'index.html'))).toBe(false);
});

it('fails a missing source asset before creating staging', () => {
  unlinkSync(join(build, 'assets/index-abcdefgh.js'));
  expect(packageFixture().status).toBe(1);
  expect(existsSync(stage)).toBe(false);
});

it('rejects a traversal manifest before creating staging', () => {
  writeFileSync(join(scratch, 'private.txt'), 'private fixture sentinel');
  writeFileSync(
    join(build, '.vite/manifest.json'),
    JSON.stringify({
      'index.html': { file: '../private.txt', isEntry: true },
      'buddy-overlay.html': {
        file: 'assets/buddy-overlay-abcdefgh.js',
        isEntry: true,
      },
    }),
  );
  const result = packageFixture();
  expect(result.status).toBe(1);
  expect(result.stderr).toContain('Invalid generated asset path');
  expect(result.stderr).not.toContain('private fixture sentinel');
  expect(readFileSync(join(scratch, 'private.txt'), 'utf8')).toBe(
    'private fixture sentinel',
  );
  expect(existsSync(stage)).toBe(false);
});
